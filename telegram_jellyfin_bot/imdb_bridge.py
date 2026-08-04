from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from .config import Config


async def _stop_process(process: asyncio.subprocess.Process | None) -> None:
    """Ensure a timed-out or cancelled search process is fully stopped."""
    if process is None or process.returncode is not None:
        return
    try:
        process.kill()
    except ProcessLookupError:
        pass
    await process.wait()


class ImdbFuzzySearchBridge:
    """Optional subprocess adapter; the main bot never imports the IMDb tool."""

    def __init__(self, config: Config):
        self.config = config
        self.active = False

    def build_command(
        self, query: str, limit: int = 8, media_type: str = "any"
    ) -> list[str]:
        if len(query.strip()) < 2:
            raise ValueError("Enter at least two letters to search.")
        if len(query) > 200:
            raise ValueError("Search text is too long.")
        if media_type not in {"any", "movie", "series"}:
            raise ValueError("IMDb media type must be any, movie, or series.")
        if len(self.config.fuzzy_search_command) < 2:
            raise ValueError("fuzzy_search_command is not valid in config.json.")
        root = Path(__file__).resolve().parent.parent
        command = list(self.config.fuzzy_search_command[:2])
        for index in (0, 1):
            path = Path(command[index])
            if not path.is_absolute():
                path = (root / path).resolve()
            if not path.is_file():
                raise FileNotFoundError(f"IMDb fuzzy search tool not found: {path}")
            command[index] = str(path)
        command.extend(
            [
                "search", query.strip(), "--limit", str(max(1, min(limit, 10))),
                "--timeout", str(self.config.fuzzy_search_timeout_seconds),
                "--media-type", media_type,
                "--json",
            ]
        )
        return command

    async def search(
        self, query: str, limit: int = 8, media_type: str = "any"
    ) -> tuple[list[dict], str]:
        if self.active:
            raise RuntimeError("Another IMDb search is already running.")
        command = self.build_command(query, limit, media_type)
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
                timeout=self.config.fuzzy_search_timeout_seconds + 5,
            )
            text = stdout.decode("utf-8", errors="replace").strip()
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                detail = stderr.decode("utf-8", errors="replace")[-500:]
                raise RuntimeError(f"IMDb tool returned an invalid response: {detail}") from exc
            if process.returncode not in {0, 1} or not payload.get("ok"):
                raise RuntimeError(payload.get("error", "IMDb search failed"))
            return payload.get("results", []), str(payload.get("source", "unknown"))
        except asyncio.TimeoutError as exc:
            await _stop_process(process)
            raise RuntimeError("IMDb search timed out; enter the name manually.") from exc
        except asyncio.CancelledError:
            await _stop_process(process)
            raise
        except Exception:
            await _stop_process(process)
            raise
        finally:
            self.active = False


_RELEASE_CUTOFF = re.compile(
    r"(?i)\b(?:2160p|1080p|720p|576p|480p|4k|uhd|hdr10?|dv|dolby[ ._-]*vision|"
    r"blu[ ._-]*ray|b[rd]rip|web[ ._-]*(?:dl|rip)|hdtv|remux|x26[45]|h[ ._-]*26[45]|"
    r"hevc|av1|aac|dts|truehd|atmos|proper|repack)\b"
)


def movie_query_from_filename(filename: str) -> str:
    """Remove common release metadata while retaining title and release year."""
    stem = Path(filename).stem
    stem = re.sub(r"^\s*(?:\[[^\]]+\]\s*)+", "", stem)
    stem = re.sub(r"[._]+", " ", stem)
    match = _RELEASE_CUTOFF.search(stem)
    if match:
        stem = stem[:match.start()]
    stem = re.sub(r"\s+-\s+[A-Za-z0-9]{2,20}$", "", stem)
    stem = re.sub(r"\[[^\]]*\]", " ", stem)
    stem = re.sub(r"\([^)]*(?:rip|web|bluray|codec|audio)[^)]*\)", " ", stem, flags=re.I)
    return re.sub(r"\s+", " ", stem).strip(" -._")
