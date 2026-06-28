from __future__ import annotations

import asyncio
import json
from pathlib import Path

from .config import Config


class ImdbFuzzySearchBridge:
    """Optional subprocess adapter; the main bot never imports the IMDb tool."""

    def __init__(self, config: Config):
        self.config = config
        self.active = False

    def build_command(self, query: str, limit: int = 8) -> list[str]:
        if len(query.strip()) < 2:
            raise ValueError("حداقل دو حرف برای جستجو وارد کنید.")
        if len(query) > 200:
            raise ValueError("عبارت جستجو بیش از حد طولانی است.")
        if len(self.config.fuzzy_search_command) < 2:
            raise ValueError("fuzzy_search_command در config.json معتبر نیست.")
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
                "--json",
            ]
        )
        return command

    async def search(self, query: str, limit: int = 8) -> tuple[list[dict], str]:
        if self.active:
            raise RuntimeError("یک جستجوی IMDb دیگر در حال اجرا است.")
        command = self.build_command(query, limit)
        self.active = True
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
                raise RuntimeError(f"پاسخ ابزار IMDb معتبر نیست: {detail}") from exc
            if process.returncode not in {0, 1} or not payload.get("ok"):
                raise RuntimeError(payload.get("error", "IMDb search failed"))
            return payload.get("results", []), str(payload.get("source", "unknown"))
        except asyncio.TimeoutError as exc:
            raise RuntimeError("مهلت جستجوی IMDb تمام شد؛ نام را دستی وارد کنید.") from exc
        finally:
            self.active = False
