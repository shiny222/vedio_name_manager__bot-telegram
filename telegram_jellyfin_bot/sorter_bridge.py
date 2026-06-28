from __future__ import annotations

import asyncio
import json
import logging
import re
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
        return self._resolve_program_paths(command)

    def _resolve_program_paths(self, command: list[str]) -> list[str]:
        # On Windows, CreateProcess may resolve a relative executable against
        # the bot's current directory before subprocess applies cwd. Resolve
        # trusted configured program paths explicitly.
        project_root = Path(__file__).resolve().parent.parent
        executable = Path(command[0])
        if not executable.is_absolute():
            executable = (project_root / executable).resolve()
        if not executable.is_file():
            raise FileNotFoundError(f"Sorter Python executable not found: {executable}")
        command[0] = str(executable)

        if len(command) > 1 and command[1].lower().endswith(".py"):
            script = Path(command[1])
            if not script.is_absolute():
                script = (project_root / script).resolve()
            if not script.is_file():
                raise FileNotFoundError(f"Sorter script not found: {script}")
            command[1] = str(script)
        return command

    def build_undo_command(self, batch_id: str | None = None) -> list[str]:
        if not self.config.sorter_command:
            raise ValueError("sorter_command در config.json تنظیم نشده است.")
        # Telegram command names cannot inject arguments because subprocess is
        # invoked without a shell; validation also prevents accidental garbage.
        if batch_id is not None and not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", batch_id):
            raise ValueError("Batch ID نامعتبر است.")
        prefix = list(self.config.sorter_command[:2])
        if len(prefix) < 2:
            raise ValueError("sorter_command باید Python و organizer.py را مشخص کند.")
        command = prefix + (
            ["undo-batch", batch_id, "--library", str(self.config.jellyfin_library_path)]
            if batch_id is not None
            else ["undo-last", "--library", str(self.config.jellyfin_library_path)]
        )
        return self._resolve_program_paths(command)

    def build_rename_command(self, folder: Path, new_name: str) -> list[str]:
        safe_folder = folder.resolve()
        library = self.config.jellyfin_library_path.resolve()
        if safe_folder != library and library not in safe_folder.parents:
            raise ValueError("فولدر تغییر نام خارج از Library است.")
        if not self.config.sorter_command or len(self.config.sorter_command) < 2:
            raise ValueError("sorter_command باید Python و organizer.py را مشخص کند.")
        command = list(self.config.sorter_command[:2]) + [
            "rename-folder",
            str(safe_folder),
            new_name,
        ]
        return self._resolve_program_paths(command)

    def build_series_action_command(self, action: str, folder: Path) -> list[str]:
        allowed = {"resort-existing", "sort-history", "sort-back", "sort-forward"}
        if action not in allowed:
            raise ValueError("Unsupported sorter action.")
        safe_folder = folder.resolve()
        library = self.config.jellyfin_library_path.resolve()
        if safe_folder == library or library not in safe_folder.parents:
            raise ValueError("Series folder must be inside the Jellyfin library.")
        if not self.config.sorter_command or len(self.config.sorter_command) < 2:
            raise ValueError("sorter_command must specify Python and organizer.py.")
        return self._resolve_program_paths(
            list(self.config.sorter_command[:2]) + [action, str(safe_folder)]
        )

    async def run(self, folder: Path, dry_run: bool = False) -> tuple[bool, str]:
        command = self.build_command(folder, dry_run)
        return await self._execute(folder, command)

    async def undo_batch(self, batch_id: str) -> tuple[bool, str]:
        command = self.build_undo_command(batch_id)
        return await self._execute(self.config.jellyfin_library_path, command)

    async def undo_last(self) -> tuple[bool, str]:
        command = self.build_undo_command()
        return await self._execute(self.config.jellyfin_library_path, command)

    async def rename_folder(
        self, folder: Path, new_name: str
    ) -> tuple[bool, str]:
        command = self.build_rename_command(folder, new_name)
        return await self._execute(folder, command)

    async def series_action(self, action: str, folder: Path) -> tuple[bool, str]:
        command = self.build_series_action_command(action, folder)
        return await self._execute(folder, command)

    async def _execute(self, folder: Path, command: list[str]) -> tuple[bool, str]:
        if self.active:
            return False, "یک عملیات مرتب‌سازی در حال اجرا است."
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
