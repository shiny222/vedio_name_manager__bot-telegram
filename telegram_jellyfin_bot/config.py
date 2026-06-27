from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .utils import safe_child

PROJECT_DIR = Path(__file__).resolve().parent


def _path(value: str, base: Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (base / path).resolve()


@dataclass(frozen=True)
class Config:
    bot_token: str
    telegram_api_id: int
    telegram_api_hash: str
    telegram_bot_api_exe_path: Path
    local_bot_api_host: str
    local_bot_api_port: int
    local_bot_api_base_url: str
    local_bot_api_base_file_url: str
    jellyfin_library_path: Path
    temp_download_path: Path
    data_path: Path
    logs_path: Path
    sorter_command: list[str]
    sorter_timeout_seconds: int
    allowed_chat_ids: set[int]
    allowed_video_extensions: set[str]
    max_parallel_downloads: int
    default_target_folder: str
    confirm_before_download: bool
    keep_original_filenames: bool
    ask_before_overwrite: bool
    auto_sort_after_download: bool

    @property
    def api_root(self) -> str:
        return f"{self.local_bot_api_base_url.rstrip('/')}{self.bot_token}"

    @property
    def file_root(self) -> str:
        return f"{self.local_bot_api_base_file_url.rstrip('/')}{self.bot_token}"

    def target_path(self, folder_name: str) -> Path:
        return safe_child(self.jellyfin_library_path, folder_name)


def load_config(path: Path | None = None, create_from_example: bool = True) -> Config:
    config_path = (path or PROJECT_DIR / "config.json").resolve()
    example = PROJECT_DIR / "config.example.json"
    if not config_path.exists():
        if create_from_example and example.exists():
            shutil.copy2(example, config_path)
            raise FileNotFoundError(
                f"{config_path} ساخته شد. اطلاعات آن را کامل کنید و دوباره اجرا کنید."
            )
        raise FileNotFoundError(f"فایل تنظیمات پیدا نشد: {config_path}")
    try:
        raw: dict[str, Any] = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"config.json معتبر نیست: {exc}") from exc

    required = ("bot_token", "telegram_api_id", "telegram_api_hash", "jellyfin_library_path")
    missing = [key for key in required if not raw.get(key)]
    if missing:
        raise ValueError("مقادیر الزامی config خالی هستند: " + ", ".join(missing))
    if raw["bot_token"].startswith("PUT_"):
        raise ValueError("bot_token را در config.json تنظیم کنید.")

    base = config_path.parent
    host = str(raw.get("local_bot_api_host", "127.0.0.1"))
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("Local Bot API برای امنیت باید فقط روی 127.0.0.1 باشد.")
    port = int(raw.get("local_bot_api_port", 8081))
    extensions = {
        str(ext).lower() if str(ext).startswith(".") else "." + str(ext).lower()
        for ext in raw.get("allowed_video_extensions", [])
    }
    command = raw.get("sorter_command", [])
    if not isinstance(command, list) or not all(isinstance(x, str) for x in command):
        raise ValueError("sorter_command باید آرایه‌ای از آرگومان‌ها باشد.")
    default_folder = str(raw.get("default_target_folder", "")).strip()
    cfg = Config(
        bot_token=str(raw["bot_token"]),
        telegram_api_id=int(raw["telegram_api_id"]),
        telegram_api_hash=str(raw["telegram_api_hash"]),
        telegram_bot_api_exe_path=_path(str(raw.get("telegram_bot_api_exe_path", "")), base),
        local_bot_api_host=host,
        local_bot_api_port=port,
        local_bot_api_base_url=str(raw.get("local_bot_api_base_url", f"http://{host}:{port}/bot")),
        local_bot_api_base_file_url=str(raw.get("local_bot_api_base_file_url", f"http://{host}:{port}/file/bot")),
        jellyfin_library_path=_path(str(raw["jellyfin_library_path"]), base),
        temp_download_path=_path(str(raw.get("temp_download_path", "temp")), base),
        data_path=_path(str(raw.get("data_path", "data")), base),
        logs_path=_path(str(raw.get("logs_path", "logs")), base),
        sorter_command=command,
        sorter_timeout_seconds=max(1, int(raw.get("sorter_timeout_seconds", 1800))),
        allowed_chat_ids={int(x) for x in raw.get("allowed_chat_ids", [])},
        allowed_video_extensions=extensions or {".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v"},
        max_parallel_downloads=max(1, int(raw.get("max_parallel_downloads", 1))),
        default_target_folder=default_folder,
        confirm_before_download=bool(raw.get("confirm_before_download", True)),
        keep_original_filenames=bool(raw.get("keep_original_filenames", True)),
        ask_before_overwrite=bool(raw.get("ask_before_overwrite", True)),
        auto_sort_after_download=bool(raw.get("auto_sort_after_download", False)),
    )
    if not cfg.keep_original_filenames:
        raise ValueError("keep_original_filenames باید true باشد.")
    if default_folder:
        cfg.target_path(default_folder)
    for directory in (cfg.jellyfin_library_path, cfg.temp_download_path, cfg.data_path, cfg.logs_path):
        directory.mkdir(parents=True, exist_ok=True)
    return cfg
