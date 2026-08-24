"""Optional subprocess bridge for the independent AniList search tool."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from .config import Config


async def _stop_process(process: asyncio.subprocess.Process | None) -> None:
    if process is None or process.returncode is not None:
        return
    try:
        process.kill()
    except ProcessLookupError:
        pass
    await process.wait()


class AniListSearchBridge:
    def __init__(self, config: Config):
        self.config = config
        self.active = False
        self._lock = asyncio.Lock()

    def build_command(
        self, query: str, limit: int = 8, media_type: str = "any"
    ) -> list[str]:
        if len(query.strip()) < 2:
            raise ValueError("Enter at least two letters to search.")
        if len(query) > 200:
            raise ValueError("Search text is too long.")
        if media_type not in {"any", "movie", "series"}:
            raise ValueError("AniList media type must be any, movie, or series.")
        if len(self.config.anilist_search_command) < 2:
            raise ValueError("anilist_search_command is not valid in config.")
        root = Path(__file__).resolve().parent.parent
        command = list(self.config.anilist_search_command[:2])
        for index in (0, 1):
            path = Path(command[index])
            if not path.is_absolute():
                path = (root / path).resolve()
            if not path.is_file():
                raise FileNotFoundError(f"AniList search tool not found: {path}")
            command[index] = str(path)
        command.extend(
            [
                "search",
                query.strip(),
                "--limit",
                str(max(1, min(limit, 10))),
                "--timeout",
                str(self.config.anilist_search_timeout_seconds),
                "--media-type",
                media_type,
                "--json",
            ]
        )
        return command

    async def search(
        self, query: str, limit: int = 8, media_type: str = "any"
    ) -> tuple[list[dict], str]:
        command = self.build_command(query, limit, media_type)
        await self._lock.acquire()
        self.active = True
        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(Path(__file__).resolve().parent.parent),
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self.config.anilist_search_timeout_seconds + 5,
            )
            text = stdout.decode("utf-8", errors="replace").strip()
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                detail = stderr.decode("utf-8", errors="replace")[-500:]
                raise RuntimeError(
                    f"AniList tool returned an invalid response: {detail}"
                ) from exc
            if process.returncode not in {0, 1} or not payload.get("ok"):
                raise RuntimeError(payload.get("error", "AniList search failed"))
            return payload.get("results", []), str(
                payload.get("source", "unknown")
            )
        except asyncio.TimeoutError as exc:
            await _stop_process(process)
            raise RuntimeError(
                "AniList search timed out; enter the name manually."
            ) from exc
        except asyncio.CancelledError:
            await _stop_process(process)
            raise
        except Exception:
            await _stop_process(process)
            raise
        finally:
            self.active = False
            self._lock.release()
