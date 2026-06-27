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
            await notify("یک دانلود دیگر در حال اجرا است.")
            return
        self.running = True
        self.cancel_event.clear()
        semaphore = asyncio.Semaphore(self.config.max_parallel_downloads)

        async def guarded(item: dict) -> None:
            async with semaphore:
                if not self.cancel_event.is_set():
                    await self._download_one(item, notify)

        try:
            await notify("دانلود شروع شد.")
            await asyncio.gather(*(guarded(item) for item in items))
            completed = sum(
                1 for item in items
                if (self.queue.store.get_item(item["pending_id"]) or {}).get("status") == "completed"
            )
            if self.cancel_event.is_set():
                await notify(f"عملیات لغو شد. {completed} فایل کامل شده است.")
            else:
                await notify(
                    f"دانلودها پایان یافت. {completed} از {len(items)} فایل کامل شد.\n"
                    "برای مرتب‌سازی از /sort_latest استفاده کن."
                )
        finally:
            self.running = False

    def _destination(self, item: dict) -> tuple[Path, str] | None:
        folder_name = item.get("target_folder")
        if not folder_name:
            return None
        folder = self.config.target_path(folder_name)
        filename = validate_original_filename(item["original_filename"])
        return folder / filename, folder_name

    async def _download_one(self, item: dict, notify: Notify) -> None:
        pending_id = int(item["pending_id"])
        try:
            result = self._destination(item)
            if result is None:
                self.queue.set_status(pending_id, "failed", "فولدر مقصد مشخص نیست.")
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
                        "فایل مقصد وجود دارد؛ منتظر تصمیم کاربر.",
                    )
                    await notify(
                        f"فایل #{pending_id} از قبل وجود دارد:\n{destination.name}\n"
                        f"یکی را بفرست:\n/resolve {pending_id} skip\n"
                        f"/resolve {pending_id} overwrite\n"
                        f"/resolve {pending_id} save_with_suffix"
                    )
                    return
                else:
                    self.queue.set_status(pending_id, "skipped", "فایل از قبل وجود دارد.")
                    await notify(f"فایل #{pending_id} رد شد؛ از قبل وجود دارد.")
                    return

            self.queue.set_status(pending_id, "downloading", None)
            file_info = await self.api_call("getFile", file_id=item["file_id"])
            file_path = str(file_info.get("file_path", ""))
            if not file_path:
                raise RuntimeError("Local Bot API مسیر فایل را برنگرداند.")
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
                self.queue.set_status(pending_id, "cancelled", "دانلود لغو شد.")
                return
            if destination.exists() and policy != "overwrite":
                raise FileExistsError(f"فایل مقصد هنگام دانلود ایجاد شد: {destination}")
            if destination.exists():
                destination.unlink()
            part.replace(destination)
            self.queue.set_status(
                pending_id, "completed", None, downloaded_path=str(destination)
            )
            self.queue.store.set_setting("latest_downloaded_folder", folder_name)
            self.queue.store.set_setting("latest_downloaded_file", str(destination))
            await notify(f"دانلود کامل شد: {destination.name}")
        except asyncio.CancelledError:
            self.queue.set_status(pending_id, "cancelled", "دانلود لغو شد.")
            raise
        except Exception as exc:
            LOG.exception("Download failed for pending_id=%s", pending_id)
            self.queue.set_status(pending_id, "failed", str(exc))
            await notify(f"خطا در دانلود فایل #{pending_id}: {exc}")

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
