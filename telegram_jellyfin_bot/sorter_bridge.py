from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from .config import Config
from .state_store import StateStore

LOG = logging.getLogger(__name__)


class SorterBridge:
    def __init__(self, config: Config, store: StateStore):
        self.config = config
        self.store = store
        self.active = False

    def build_command(self, folder: Path, dry_run: bool = False) -> list[str]:
        if not self.config.sorter_command:
            raise ValueError("sorter_command در config.json تنظیم نشده است.")
        safe_folder = folder.resolve()
        library = self.config.jellyfin_library_path.resolve()
        if safe_folder != library and library not in safe_folder.parents:
            raise ValueError("فولدر sorter خارج از Library است.")
        command = [
            part.replace("{folder}", str(safe_folder)).replace(
                "{mode}", "dry-run" if dry_run else "run"
            )
            for part in self.config.sorter_command
        ]
        return command

    async def run(self, folder: Path, dry_run: bool = False) -> tuple[bool, str]:
        if self.active:
            return False, "یک عملیات مرتب‌سازی در حال اجرا است."
        command = self.build_command(folder, dry_run)
        self.active = True
        run_id = self.store.create_sorter_run(str(folder), json.dumps(command, ensure_ascii=False))
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(Path(__file__).resolve().parent.parent),
            )
            try:
                output_bytes, _ = await asyncio.wait_for(
                    process.communicate(), timeout=self.config.sorter_timeout_seconds
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                output = "Sorter به‌علت پایان زمان مجاز متوقف شد."
                self.store.finish_sorter_run(run_id, "timeout", output)
                return False, output
            output = output_bytes.decode("utf-8", errors="replace")
            status = "completed" if process.returncode == 0 else "failed"
            self.store.finish_sorter_run(run_id, status, output)
            LOG.info("Sorter run %s finished with code %s\n%s", run_id, process.returncode, output)
            return process.returncode == 0, output[-3000:] or "(بدون خروجی)"
        finally:
            self.active = False
