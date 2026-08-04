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

    def cancel(self) -> bool:
        if not self.running:
            return False
        self.cancel_event.set()
        return True

    async def run(self, items: list[dict], notify: Notify) -> None:
        if self.running:
            await notify("Another download is already running.")
            return
        self.running = True
        self.cancel_event.clear()
        semaphore = asyncio.Semaphore(self.config.max_parallel_downloads)

        async def guarded(item: dict) -> None:
            async with semaphore:
                if not self.cancel_event.is_set():
                    await self._download_one(item, notify)

        try:
            await notify("Download started.")
            await asyncio.gather(*(guarded(item) for item in items))
            completed = sum(
                1 for item in items
                if (self.queue.store.get_item(item["pending_id"]) or {}).get("status") == "completed"
            )
            if self.cancel_event.is_set():
                await notify(f"Operation cancelled. {completed} file(s) completed.")
            else:
                series_count = sum(
                    1 for item in items if item.get("media_kind", "series") == "series"
                )
                suffix = (
                    "\nUse /sort_latest to organize the latest downloaded series folder."
                    if series_count
                    else "\nDownloaded movies will now be imported automatically."
                )
                await notify(
                    f"Downloads finished. {completed} of {len(items)} file(s) completed."
                    + suffix
                )
        finally:
            self.running = False

    def _destination(self, item: dict) -> tuple[Path, str] | None:
        folder_name = item.get("target_folder")
        if not folder_name:
            return None
        if item.get("media_kind", "series") == "movie":
            folder = self.config.movie_staging_job_path(int(item["pending_id"]))
        else:
            folder = self.config.target_path(folder_name)
        filename = validate_original_filename(item["original_filename"])
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
            file_info = await self.api_call("getFile", file_id=item["file_id"])
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
                self.queue.store.set_setting("latest_downloaded_movie_id", str(pending_id))
                self.queue.store.set_setting("latest_downloaded_movie_file", str(destination))
            else:
                self.queue.store.set_setting("latest_downloaded_folder", folder_name)
                self.queue.store.set_setting("latest_downloaded_file", str(destination))
            await notify(f"Download completed: {destination.name}")
        except asyncio.CancelledError:
            self.queue.set_status(pending_id, "cancelled", "Download cancelled.")
            raise
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
        async with self.session.get(url) as response:
            response.raise_for_status()
            with destination.open("wb") as output:
                async for chunk in response.content.iter_chunked(1024 * 1024):
                    if self.cancel_event.is_set():
                        return
                    output.write(chunk)

    @staticmethod
    def _unique_path(path: Path) -> Path:
        counter = 1
        while True:
            candidate = path.with_name(f"{path.stem} ({counter}){path.suffix}")
            if not candidate.exists():
                return candidate
            counter += 1
