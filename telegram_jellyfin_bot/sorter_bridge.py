from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path

from .config import Config
from .state_store import StateStore

LOG = logging.getLogger(__name__)
BATCH_ID_RE = re.compile(
    r"(?:Resort |Metadata )?Batch ID:\s*([A-Za-z0-9._-]{1,100})",
    re.IGNORECASE,
)


async def _stop_process(process: asyncio.subprocess.Process | None) -> None:
    """Ensure a child process cannot outlive a cancelled or failed bot task."""
    if process is None or process.returncode is not None:
        return
    try:
        process.kill()
    except ProcessLookupError:
        pass
    await process.wait()


class SorterBridge:
    def __init__(self, config: Config, store: StateStore):
        self.config = config
        self.store = store
        self.active = False

    def build_command(
        self,
        folder: Path,
        dry_run: bool = False,
        library_key: str | None = None,
        replace_episodes: set[tuple[int, int]] | None = None,
        episode_overrides: dict[str, tuple[int, int]] | None = None,
    ) -> list[str]:
        if not self.config.sorter_command:
            raise ValueError("sorter_command is not configured in config.json.")
        safe_folder = folder.resolve()
        library = self.config.library(library_key, "series").path.resolve()
        if safe_folder != library and library not in safe_folder.parents:
            raise ValueError("Sorter folder is outside the library.")
        command = [
            part.replace("{folder}", str(safe_folder)).replace(
                "{mode}", "dry-run" if dry_run else "run"
            )
            for part in self.config.sorter_command
        ]
        for season, episode in sorted(replace_episodes or set()):
            if season < 1 or episode < 1:
                raise ValueError("Replacement season and episode must be positive.")
            command.extend(
                ["--replace-episode", f"S{season:02d}E{episode:02d}"]
            )
        for filename, (season, episode) in sorted(
            (episode_overrides or {}).items(), key=lambda entry: entry[0].casefold()
        ):
            if Path(filename).name != filename or filename in {"", ".", ".."}:
                raise ValueError("Episode override filename must be one file name.")
            if season < 1 or episode < 1:
                raise ValueError("Episode override values must be positive.")
            command.extend(
                ["--episode-override", filename, f"S{season:02d}E{episode:02d}"]
            )
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

    def build_undo_command(
        self, batch_id: str | None = None, library_key: str | None = None
    ) -> list[str]:
        if not self.config.sorter_command:
            raise ValueError("sorter_command is not configured in config.json.")
        # Telegram command names cannot inject arguments because subprocess is
        # invoked without a shell; validation also prevents accidental garbage.
        if batch_id is not None and not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", batch_id):
            raise ValueError("Invalid Batch ID.")
        prefix = list(self.config.sorter_command[:2])
        if len(prefix) < 2:
            raise ValueError("sorter_command must specify Python and organizer.py.")
        library = self.config.library(library_key, "series").path
        command = prefix + (
            ["undo-batch", batch_id, "--library", str(library)]
            if batch_id is not None
            else ["undo-last", "--library", str(library)]
        )
        return self._resolve_program_paths(command)

    def build_rename_command(
        self, folder: Path, new_name: str, library_key: str | None = None
    ) -> list[str]:
        safe_folder = folder.resolve()
        library = self.config.library(library_key, "series").path.resolve()
        if safe_folder != library and library not in safe_folder.parents:
            raise ValueError("Rename folder is outside the library.")
        if not self.config.sorter_command or len(self.config.sorter_command) < 2:
            raise ValueError("sorter_command must specify Python and organizer.py.")
        command = list(self.config.sorter_command[:2]) + [
            "rename-folder",
            str(safe_folder),
            new_name,
        ]
        return self._resolve_program_paths(command)

    def build_series_action_command(
        self, action: str, folder: Path, library_key: str | None = None
    ) -> list[str]:
        allowed = {
            "resort-existing",
            "sort-history",
            "sort-back",
            "sort-forward",
            "recover-folder",
            "fix-metadata",
        }
        if action not in allowed:
            raise ValueError("Unsupported sorter action.")
        safe_folder = folder.resolve()
        library = self.config.library(library_key, "series").path.resolve()
        if safe_folder == library or library not in safe_folder.parents:
            raise ValueError("Series folder must be inside the Jellyfin library.")
        if not self.config.sorter_command or len(self.config.sorter_command) < 2:
            raise ValueError("sorter_command must specify Python and organizer.py.")
        return self._resolve_program_paths(
            list(self.config.sorter_command[:2]) + [action, str(safe_folder)]
        )

    async def run(
        self,
        folder: Path,
        dry_run: bool = False,
        chat_id: int | None = None,
        library_key: str | None = None,
        replace_episodes: set[tuple[int, int]] | None = None,
        episode_overrides: dict[str, tuple[int, int]] | None = None,
    ) -> tuple[bool, str]:
        command = self.build_command(
            folder,
            dry_run,
            library_key,
            replace_episodes,
            episode_overrides,
        )
        return await self._execute(
            folder, command, chat_id, "series", library_key=library_key
        )

    async def undo_batch(
        self,
        batch_id: str,
        chat_id: int | None = None,
        library_key: str | None = None,
    ) -> tuple[bool, str]:
        command = self.build_undo_command(batch_id, library_key)
        library = self.config.library(library_key, "series")
        return await self._execute(
            library.path,
            command,
            chat_id,
            "series_undo",
            library_key=library.key,
        )

    async def undo_last(
        self, chat_id: int | None = None, library_key: str | None = None
    ) -> tuple[bool, str]:
        command = self.build_undo_command(library_key=library_key)
        library = self.config.library(library_key, "series")
        return await self._execute(
            library.path,
            command,
            chat_id,
            "series_undo",
            library_key=library.key,
        )

    async def rename_folder(
        self,
        folder: Path,
        new_name: str,
        chat_id: int | None = None,
        library_key: str | None = None,
    ) -> tuple[bool, str]:
        command = self.build_rename_command(folder, new_name, library_key)
        return await self._execute(
            folder,
            command,
            chat_id,
            "series_maintenance",
            library_key=library_key,
        )

    async def series_action(
        self,
        action: str,
        folder: Path,
        chat_id: int | None = None,
        library_key: str | None = None,
    ) -> tuple[bool, str]:
        command = self.build_series_action_command(action, folder, library_key)
        kind = "series" if action in {"resort-existing", "fix-metadata"} else "series_maintenance"
        return await self._execute(
            folder, command, chat_id, kind, library_key=library_key
        )

    async def _execute(
        self,
        folder: Path,
        command: list[str],
        chat_id: int | None = None,
        operation_kind: str = "series",
        library_key: str | None = None,
    ) -> tuple[bool, str]:
        if self.active:
            return False, "A sorter operation is already running."
        self.active = True
        run_id = self.store.create_sorter_run(
            str(folder),
            json.dumps(command, ensure_ascii=False),
            chat_id=chat_id,
            operation_kind=operation_kind,
            library_key=library_key,
        )
        process: asyncio.subprocess.Process | None = None
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
                await _stop_process(process)
                output = "Sorter stopped because it reached the timeout."
                self.store.finish_sorter_run(run_id, "timeout", output)
                return False, output
            output = output_bytes.decode("utf-8", errors="replace")
            status = "completed" if process.returncode == 0 else "failed"
            batch_match = BATCH_ID_RE.search(output)
            batch_id = batch_match.group(1) if batch_match else None
            self.store.finish_sorter_run(run_id, status, output, batch_id=batch_id)
            LOG.info("Sorter run %s finished with code %s\n%s", run_id, process.returncode, output)
            return process.returncode == 0, output[-3000:] or "(no output)"
        except asyncio.CancelledError:
            await _stop_process(process)
            output = "Sorter stopped because the bot task was cancelled."
            self.store.finish_sorter_run(run_id, "cancelled", output)
            LOG.warning("Sorter run %s was cancelled.", run_id)
            raise
        except Exception as exc:
            await _stop_process(process)
            output = f"Sorter process failed: {exc}"
            self.store.finish_sorter_run(run_id, "failed", output)
            raise
        finally:
            self.active = False
