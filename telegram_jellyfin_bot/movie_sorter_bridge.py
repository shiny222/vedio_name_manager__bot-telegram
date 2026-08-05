from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path

from .config import Config
from .state_store import StateStore

LOG = logging.getLogger(__name__)


async def _stop_process(process: asyncio.subprocess.Process | None) -> None:
    if process is None or process.returncode is not None:
        return
    try:
        process.kill()
    except ProcessLookupError:
        pass
    await process.wait()


class MovieSorterBridge:
    """Optional subprocess adapter for the independent movie organizer."""

    def __init__(self, config: Config, store: StateStore):
        self.config = config
        self.store = store
        self.active = False

    @property
    def configured(self) -> bool:
        return self.config.movies_configured and len(self.config.movie_sorter_command) >= 2

    def _prefix(self) -> list[str]:
        if not self.config.movies_configured:
            raise ValueError(
                "Movies are not configured. Set jellyfin_movie_library_path and "
                "movie_staging_path in config.json."
            )
        if len(self.config.movie_sorter_command) < 2:
            raise ValueError("movie_sorter_command must specify Python and movie_organizer.py.")
        root = Path(__file__).resolve().parent.parent
        command = list(self.config.movie_sorter_command[:2])
        for index in (0, 1):
            path = Path(command[index])
            if not path.is_absolute():
                path = (root / path).resolve()
            if not path.is_file():
                label = "Python executable" if index == 0 else "movie organizer script"
                raise FileNotFoundError(f"Movie {label} not found: {path}")
            command[index] = str(path)
        return command

    def build_import_command(self, item: dict, dry_run: bool = False) -> list[str]:
        if not self.config.movies_configured:
            self._prefix()  # Raises the detailed configuration error.
        source_text = str(item.get("downloaded_path") or "")
        if not source_text:
            raise ValueError("The movie has not completed downloading.")
        source = Path(source_text).resolve()
        staging = self.config.movie_staging_path
        library = self.config.jellyfin_movie_library_path
        assert staging is not None and library is not None
        staging = staging.resolve()
        if source == staging or staging not in source.parents:
            raise ValueError("Movie source is outside the configured staging folder.")
        title = str(item.get("movie_title") or "").strip()
        if not title:
            raise ValueError("Movie title has not been confirmed.")
        command = self._prefix() + [
            "dry-run" if dry_run else "import",
            "--source", str(source),
            "--library", str(library.resolve()),
            "--title", title,
        ]
        year = item.get("movie_year")
        if isinstance(year, int) and not isinstance(year, bool):
            command.extend(["--year", str(year)])
        imdb_id = str(item.get("imdb_id") or "").strip()
        if imdb_id:
            command.extend(["--imdb-id", imdb_id])
        command.append("--json")
        return command

    def build_undo_command(self, batch_id: str | None = None) -> list[str]:
        library = self.config.jellyfin_movie_library_path
        if library is None:
            self._prefix()
            raise AssertionError("unreachable")
        if batch_id is not None and not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", batch_id):
            raise ValueError("Invalid movie batch ID.")
        command = self._prefix() + (
            ["undo-batch", batch_id, "--library", str(library.resolve()), "--json"]
            if batch_id is not None
            else ["undo-last", "--library", str(library.resolve()), "--json"]
        )
        return command

    async def import_movie(self, item: dict, dry_run: bool = False) -> dict:
        return await self._execute(
            self.build_import_command(item, dry_run),
            str(item.get("downloaded_path") or ""),
            chat_id=int(item["chat_id"]),
        )

    async def undo_last(self, chat_id: int | None = None) -> dict:
        return await self._execute(
            self.build_undo_command(),
            "movie library",
            chat_id=chat_id,
            allow_partial=True,
        )

    async def undo_batch(self, batch_id: str, chat_id: int | None = None) -> dict:
        return await self._execute(
            self.build_undo_command(batch_id),
            "movie library",
            chat_id=chat_id,
            allow_partial=True,
        )

    async def _execute(
        self,
        command: list[str],
        label: str,
        *,
        chat_id: int | None = None,
        allow_partial: bool = False,
    ) -> dict:
        if self.active:
            raise RuntimeError("Another movie organizer operation is already running.")
        self.active = True
        run_id = self.store.create_sorter_run(
            label,
            json.dumps(command, ensure_ascii=False),
            chat_id=chat_id,
            operation_kind="movie",
        )
        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(Path(__file__).resolve().parent.parent),
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=self.config.movie_sorter_timeout_seconds,
                )
            except asyncio.TimeoutError as exc:
                await _stop_process(process)
                output = "Movie organizer stopped because it reached the timeout."
                self.store.finish_sorter_run(run_id, "timeout", output)
                raise RuntimeError(output) from exc
            stdout_text = stdout.decode("utf-8", errors="replace").strip()
            stderr_text = stderr.decode("utf-8", errors="replace").strip()
            try:
                payload = json.loads(stdout_text)
            except json.JSONDecodeError as exc:
                output = (stderr_text or stdout_text or "no output")[-3000:]
                self.store.finish_sorter_run(run_id, "failed", output)
                raise RuntimeError(f"Movie organizer returned an invalid response: {output}") from exc
            ok = process.returncode == 0 and bool(payload.get("ok"))
            output = json.dumps(payload, ensure_ascii=False)
            if stderr_text:
                output += "\n" + stderr_text[-2000:]
            self.store.finish_sorter_run(run_id, "completed" if ok else "failed", output)
            if not ok:
                if allow_partial and {
                    "restored", "skipped"
                }.issubset(payload):
                    return payload
                raise RuntimeError(str(payload.get("error") or stderr_text or "Movie import failed."))
            return payload
        except asyncio.CancelledError:
            await _stop_process(process)
            self.store.finish_sorter_run(
                run_id, "cancelled", "Movie organizer was cancelled."
            )
            raise
        except Exception as exc:
            await _stop_process(process)
            self.store.finish_sorter_run(run_id, "failed", str(exc))
            raise
        finally:
            self.active = False
