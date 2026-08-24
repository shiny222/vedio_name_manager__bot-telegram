from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import quote

import aiohttp

from .config import Config
from .queue_manager import QueueManager
from .utils import validate_original_filename

LOG = logging.getLogger(__name__)
Notify = Callable[[str], Awaitable[None]]


class DownloadManager:
    def __init__(
        self,
        config: Config,
        queue: QueueManager,
        api_call: Callable[..., Awaitable[dict]],
        session: aiohttp.ClientSession,
    ):
        self.config = config
        self.queue = queue
        self.api_call = api_call
        self.session = session
        self.cancel_event = asyncio.Event()
        self.running = False
        self.running_chat_id: int | None = None

    def cancel(self, chat_id: int | None = None) -> bool:
        if not self.running or (
            chat_id is not None and self.running_chat_id != int(chat_id)
        ):
            return False
        self.cancel_event.set()
        return True

    async def run(self, items: list[dict], notify: Notify) -> None:
        if self.running:
            await notify("Another download is already running.")
            return
        chat_ids = {int(item["chat_id"]) for item in items}
        if len(chat_ids) != 1:
            raise ValueError("A download batch must belong to exactly one chat.")
        self.running = True
        self.running_chat_id = next(iter(chat_ids))
        self.cancel_event.clear()
        semaphore = asyncio.Semaphore(self.config.max_parallel_downloads)

        async def guarded(item: dict) -> None:
            async with semaphore:
                if not self.cancel_event.is_set():
                    await self._download_one(item, notify)

        try:
            await notify("Download started.")
            await asyncio.gather(*(guarded(item) for item in items))
            completed_items = [
                item
                for item in items
                if (self.queue.store.get_item(item["pending_id"]) or {}).get(
                    "status"
                ) == "completed"
            ]
            completed = len(completed_items)
            if self.cancel_event.is_set():
                await notify(f"Operation cancelled. {completed} file(s) completed.")
            else:
                completed_series = sum(
                    1
                    for item in completed_items
                    if item.get("media_kind", "series") == "series"
                )
                completed_movies = completed - completed_series
                notes = []
                if completed == 0:
                    notes.append(
                        "No files were completed or imported. Fix the reported "
                        "error, then use /download to retry."
                    )
                else:
                    automatic_series = sum(
                        1
                        for item in completed_items
                        if item.get("media_kind", "series") == "series"
                        and item.get("series_episode")
                    )
                    manual_series = completed_series - automatic_series
                    if automatic_series:
                        notes.append(
                            "AI-identified series files will now be organized automatically."
                        )
                    if manual_series:
                        notes.append(
                            "Use /sort_latest to organize the latest downloaded "
                            "series folder."
                        )
                    if completed_movies:
                        notes.append(
                            "Completed movies will now be imported automatically."
                        )
                    if completed < len(items):
                        notes.append(
                            "Some files did not complete; use /download to retry them."
                        )
                await notify(
                    f"Downloads finished. {completed} of {len(items)} file(s) completed."
                    + "\n" + "\n".join(notes)
                )
        finally:
            self.running = False
            self.running_chat_id = None

    def _destination(self, item: dict) -> tuple[Path, str] | None:
        folder_name = item.get("target_folder")
        if not folder_name:
            return None
        if item.get("media_kind", "series") == "movie":
            folder = self.config.movie_staging_job_path(int(item["pending_id"]))
        else:
            folder = self.config.target_path(
                folder_name, str(item.get("library_key") or "") or None
            )
        # Series downloads must reach the organizer with Telegram's real
        # filename intact. Season/episode assignments are passed separately;
        # encoding them in a synthetic filename would break truthful rollback.
        filename = validate_original_filename(
            item["original_filename"]
            if item.get("media_kind", "series") == "series"
            else item.get("download_filename") or item["original_filename"]
        )
        return folder / filename, folder_name

    async def _download_one(self, item: dict, notify: Notify) -> None:
        pending_id = int(item["pending_id"])
        try:
            result = self._destination(item)
            if result is None:
                self.queue.set_status(pending_id, "failed", "Target folder is not set.")
                return
            destination, folder_name = result
            destination.parent.mkdir(parents=True, exist_ok=True)
            policy = item.get("overwrite_policy")
            if destination.exists():
                if policy == "overwrite":
                    pass
                elif policy == "save_with_suffix":
                    destination = self._unique_path(destination)
                elif self.config.ask_before_overwrite:
                    self.queue.set_status(
                        pending_id, "waiting_overwrite",
                        "Destination file exists; waiting for user decision.",
                    )
                    await notify(
                        f"File #{pending_id} already exists:\n{destination.name}\n"
                        f"Send one of these:\n/resolve {pending_id} skip\n"
                        f"/resolve {pending_id} overwrite\n"
                        f"/resolve {pending_id} save_with_suffix"
                    )
                    return
                else:
                    self.queue.set_status(pending_id, "skipped", "File already exists.")
                    await notify(f"File #{pending_id} skipped; it already exists.")
                    return

            self.queue.set_status(pending_id, "downloading", None)
            file_info = await self.api_call(
                "getFile",
                file_id=item["file_id"],
                _request_timeout=self._download_timeout(),
            )
            file_path = str(file_info.get("file_path", ""))
            if not file_path:
                raise RuntimeError("Local Bot API did not return a file path.")
            part = destination.with_name(destination.name + ".part")
            if part.exists():
                LOG.warning("Restarting incomplete download: %s", part)
                part.unlink()

            local_source = Path(file_path)
            if local_source.is_absolute() and local_source.is_file():
                await asyncio.to_thread(self._copy_local, local_source, part)
            else:
                await self._download_http(file_path, part)
            if self.cancel_event.is_set():
                self.queue.set_status(pending_id, "cancelled", "Download cancelled.")
                return
            expected_size = item.get("file_size")
            if (
                isinstance(expected_size, int)
                and not isinstance(expected_size, bool)
                and expected_size >= 0
            ):
                actual_size = part.stat().st_size
                if actual_size != expected_size:
                    raise IOError(
                        "Downloaded size mismatch: "
                        f"expected {expected_size} bytes, found {actual_size} bytes."
                    )
            if destination.exists() and policy != "overwrite":
                raise FileExistsError(f"Destination file appeared during download: {destination}")
            # Path.replace uses os.replace: on the same volume it atomically
            # installs the completed .part file. If replacement fails, the
            # existing destination remains in place.
            part.replace(destination)
            self.queue.set_status(
                pending_id, "completed", None, downloaded_path=str(destination)
            )
            if item.get("media_kind", "series") == "movie":
                self.queue.store.set_chat_setting(
                    int(item["chat_id"]), "latest_downloaded_movie_id", str(pending_id)
                )
                self.queue.store.set_chat_setting(
                    int(item["chat_id"]), "latest_downloaded_movie_file", str(destination)
                )
            else:
                self.queue.store.set_chat_setting(
                    int(item["chat_id"]),
                    "latest_downloaded_library_key",
                    str(item.get("library_key") or ""),
                )
                self.queue.store.set_chat_setting(
                    int(item["chat_id"]), "latest_downloaded_folder", folder_name
                )
                self.queue.store.set_chat_setting(
                    int(item["chat_id"]), "latest_downloaded_file", str(destination)
                )
            await notify(f"Download completed: {destination.name}")
        except asyncio.CancelledError:
            self.queue.set_status(pending_id, "cancelled", "Download cancelled.")
            raise
        except asyncio.TimeoutError:
            seconds = self.config.telegram_download_read_timeout_seconds
            error = (
                f"Telegram stopped sending data for {seconds} seconds. "
                "Any incomplete .part file was kept; use /download to retry."
            )
            LOG.warning("Download timed out for pending_id=%s", pending_id)
            self.queue.set_status(pending_id, "failed", error)
            await notify(f"Download timeout for file #{pending_id}: {error}")
        except Exception as exc:
            LOG.exception("Download failed for pending_id=%s", pending_id)
            self.queue.set_status(pending_id, "failed", str(exc))
            await notify(f"Download error for file #{pending_id}: {exc}")

    def _copy_local(self, source: Path, destination: Path) -> None:
        with source.open("rb") as src, destination.open("wb") as dst:
            while True:
                if self.cancel_event.is_set():
                    return
                chunk = src.read(1024 * 1024)
                if not chunk:
                    break
                dst.write(chunk)
            dst.flush()

    async def _download_http(self, file_path: str, destination: Path) -> None:
        url = f"{self.config.file_root}/{quote(file_path.lstrip('/'), safe='/')}"
        async with self.session.get(
            url, timeout=self._download_timeout()
        ) as response:
            response.raise_for_status()
            with destination.open("wb") as output:
                async for chunk in response.content.iter_chunked(1024 * 1024):
                    if self.cancel_event.is_set():
                        return
                    output.write(chunk)

    def _download_timeout(self) -> aiohttp.ClientTimeout:
        """Allow large Local Bot API transfers to pause without timing out early."""
        return aiohttp.ClientTimeout(
            total=None,
            connect=15,
            sock_read=self.config.telegram_download_read_timeout_seconds,
        )

    @staticmethod
    def _unique_path(path: Path) -> Path:
        counter = 1
        while True:
            candidate = path.with_name(f"{path.stem} ({counter}){path.suffix}")
            if not candidate.exists():
                return candidate
            counter += 1
