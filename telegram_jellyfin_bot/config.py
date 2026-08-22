from __future__ import annotations

import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .utils import safe_child

PROJECT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PROJECT_DIR.parent


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false.")


def _env_list(name: str) -> list[str]:
    value = os.environ.get(name, "").strip()
    return [item for item in re.split(r"[\s,;]+", value) if item]


def _environment_config() -> dict[str, Any]:
    """Build the normal configuration shape from Docker environment values."""
    python = sys.executable
    movie_library = os.environ.get("JELLYFIN_MOVIE_LIBRARY_PATH", "").strip()
    movie_staging = os.environ.get("MOVIE_STAGING_PATH", "").strip()
    if movie_library and not movie_staging:
        movie_staging = "/app/staging/movies"
    return {
        "bot_token": os.environ.get("BOT_TOKEN", "").strip(),
        # The separate Local Bot API container needs these two values. They
        # remain optional here for compatibility with the Windows runner.
        "telegram_api_id": int(os.environ.get("TELEGRAM_API_ID", "0") or 0),
        "telegram_api_hash": os.environ.get("TELEGRAM_API_HASH", "").strip(),
        "telegram_bot_api_exe_path": os.environ.get(
            "TELEGRAM_BOT_API_EXE_PATH", "/usr/local/bin/telegram-bot-api"
        ),
        "local_bot_api_host": "127.0.0.1",
        "local_bot_api_port": int(os.environ.get("LOCAL_BOT_API_PORT", "8081")),
        "local_bot_api_base_url": os.environ.get(
            "LOCAL_BOT_API_BASE_URL", "http://telegram-bot-api:8081/bot"
        ),
        "local_bot_api_base_file_url": os.environ.get(
            "LOCAL_BOT_API_BASE_FILE_URL", "http://telegram-bot-api:8081/file/bot"
        ),
        "telegram_download_read_timeout_seconds": int(
            os.environ.get("TELEGRAM_DOWNLOAD_READ_TIMEOUT_SECONDS", "1800")
        ),
        "jellyfin_library_path": os.environ.get("JELLYFIN_LIBRARY_PATH", "").strip(),
        "jellyfin_movie_library_path": movie_library,
        "movie_staging_path": movie_staging,
        "data_path": os.environ.get("DATA_PATH", "/app/data").strip(),
        "logs_path": os.environ.get("LOGS_PATH", "/app/logs").strip(),
        "sorter_command": [
            python,
            str(PROJECT_ROOT / "organizer" / "organizer.py"),
            "{mode}",
            "--series-folder",
            "{folder}",
        ],
        "sorter_timeout_seconds": int(
            os.environ.get("SORTER_TIMEOUT_SECONDS", "1800")
        ),
        "movie_sorter_command": [
            python,
            str(PROJECT_ROOT / "movie_organizer" / "movie_organizer.py"),
        ],
        "movie_sorter_timeout_seconds": int(
            os.environ.get("MOVIE_SORTER_TIMEOUT_SECONDS", "1800")
        ),
        "scan_after_movie_import": _env_bool("SCAN_AFTER_MOVIE_IMPORT", True),
        "allowed_chat_ids": _env_list("ALLOWED_CHAT_IDS"),
        "allowed_video_extensions": _env_list("ALLOWED_VIDEO_EXTENSIONS"),
        "max_parallel_downloads": int(os.environ.get("MAX_PARALLEL_DOWNLOADS", "1")),
        "default_target_folder": os.environ.get("DEFAULT_TARGET_FOLDER", "").strip(),
        "confirm_before_download": _env_bool("CONFIRM_BEFORE_DOWNLOAD", True),
        "ask_before_overwrite": _env_bool("ASK_BEFORE_OVERWRITE", True),
        "jellyfin_server_url": os.environ.get(
            "JELLYFIN_SERVER_URL", "http://host.docker.internal:8096"
        ).strip(),
        "jellyfin_api_key": os.environ.get("JELLYFIN_API_KEY", "").strip(),
        "jellyfin_request_timeout_seconds": int(
            os.environ.get("JELLYFIN_REQUEST_TIMEOUT_SECONDS", "30")
        ),
        "jellyfin_scan_poll_interval_seconds": int(
            os.environ.get("JELLYFIN_SCAN_POLL_INTERVAL_SECONDS", "5")
        ),
        "jellyfin_scan_monitor_timeout_seconds": int(
            os.environ.get("JELLYFIN_SCAN_MONITOR_TIMEOUT_SECONDS", "3600")
        ),
        "fuzzy_search_command": [
            python,
            str(PROJECT_ROOT / "fuzzy_search" / "imdb_tool.py"),
        ],
        "fuzzy_search_timeout_seconds": int(
            os.environ.get("FUZZY_SEARCH_TIMEOUT_SECONDS", "20")
        ),
    }


def _path(value: str, base: Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (base / path).resolve()


def _optional_path(value: Any, base: Path) -> Path | None:
    text = str(value or "").strip()
    return _path(text, base) if text else None


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
    telegram_download_read_timeout_seconds: int
    jellyfin_library_path: Path
    jellyfin_movie_library_path: Path | None
    movie_staging_path: Path | None
    data_path: Path
    logs_path: Path
    sorter_command: list[str]
    sorter_timeout_seconds: int
    movie_sorter_command: list[str]
    movie_sorter_timeout_seconds: int
    scan_after_movie_import: bool
    allowed_chat_ids: set[int]
    allowed_video_extensions: set[str]
    max_parallel_downloads: int
    default_target_folder: str
    confirm_before_download: bool
    ask_before_overwrite: bool
    jellyfin_server_url: str
    jellyfin_api_key: str
    jellyfin_request_timeout_seconds: int
    jellyfin_scan_poll_interval_seconds: int
    jellyfin_scan_monitor_timeout_seconds: int
    fuzzy_search_command: list[str]
    fuzzy_search_timeout_seconds: int

    @property
    def api_root(self) -> str:
        return f"{self.local_bot_api_base_url.rstrip('/')}{self.bot_token}"

    @property
    def file_root(self) -> str:
        return f"{self.local_bot_api_base_file_url.rstrip('/')}{self.bot_token}"

    def target_path(self, folder_name: str) -> Path:
        return safe_child(self.jellyfin_library_path, folder_name)

    @property
    def movies_configured(self) -> bool:
        return bool(self.jellyfin_movie_library_path and self.movie_staging_path)

    def movie_target_path(self, folder_name: str) -> Path:
        if self.jellyfin_movie_library_path is None:
            raise ValueError("jellyfin_movie_library_path is not configured.")
        return safe_child(self.jellyfin_movie_library_path, folder_name)

    def movie_staging_job_path(self, pending_id: int) -> Path:
        if self.movie_staging_path is None:
            raise ValueError("movie_staging_path is not configured.")
        if pending_id <= 0:
            raise ValueError("Movie queue ID is invalid.")
        return safe_child(self.movie_staging_path, f"job-{pending_id}")


def load_config(path: Path | None = None, create_from_example: bool = True) -> Config:
    mode = os.environ.get("VIDEO_MANAGER_CONFIG_MODE", "json").strip().casefold()
    if path is None and mode == "env":
        raw = _environment_config()
        base = PROJECT_ROOT
        required = ("bot_token", "jellyfin_library_path")
        source_name = "environment configuration"
    else:
        if path is None and mode not in {"", "json"}:
            raise ValueError("VIDEO_MANAGER_CONFIG_MODE must be env or json.")
        config_path = (path or PROJECT_DIR / "config.json").resolve()
        example = PROJECT_DIR / "config.example.json"
        if not config_path.exists():
            if create_from_example and example.exists():
                shutil.copy2(example, config_path)
                raise FileNotFoundError(
                    f"{config_path} was created. Fill it in, then run the bot again."
                )
            raise FileNotFoundError(f"Config file was not found: {config_path}")
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"config.json is not valid: {exc}") from exc
        base = config_path.parent
        required = (
            "bot_token",
            "telegram_api_id",
            "telegram_api_hash",
            "jellyfin_library_path",
        )
        source_name = "config.json"

    missing = [key for key in required if not raw.get(key)]
    if missing:
        raise ValueError("Required config values are empty: " + ", ".join(missing))
    if raw["bot_token"].startswith("PUT_"):
        raise ValueError(f"Set bot_token in {source_name}.")

    host = str(raw.get("local_bot_api_host", "127.0.0.1"))
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("For safety, Local Bot API must run only on 127.0.0.1.")
    port = int(raw.get("local_bot_api_port", 8081))
    extensions = {
        str(ext).lower() if str(ext).startswith(".") else "." + str(ext).lower()
        for ext in raw.get("allowed_video_extensions", [])
    }
    command = raw.get("sorter_command", [])
    if not isinstance(command, list) or not all(isinstance(x, str) for x in command):
        raise ValueError("sorter_command must be a list of arguments.")
    movie_sorter_command = raw.get(
        "movie_sorter_command",
        [
            r"movie_organizer\.venv\Scripts\python.exe",
            r"movie_organizer\movie_organizer.py",
        ],
    )
    if not isinstance(movie_sorter_command, list) or not all(
        isinstance(x, str) for x in movie_sorter_command
    ):
        raise ValueError("movie_sorter_command must be a list of arguments.")
    fuzzy_search_command = raw.get(
        "fuzzy_search_command",
        raw.get(
            "organisation_command",  # Backward compatibility with early config.
            [
                r"fuzzy_search\.venv\Scripts\python.exe",
                r"fuzzy_search\imdb_tool.py",
            ],
        ),
    )
    if not isinstance(fuzzy_search_command, list) or not all(
        isinstance(x, str) for x in fuzzy_search_command
    ):
        raise ValueError("fuzzy_search_command must be a list of arguments.")
    default_folder = str(raw.get("default_target_folder", "")).strip()
    cfg = Config(
        bot_token=str(raw["bot_token"]),
        telegram_api_id=int(raw.get("telegram_api_id", 0)),
        telegram_api_hash=str(raw.get("telegram_api_hash", "")),
        telegram_bot_api_exe_path=_path(str(raw.get("telegram_bot_api_exe_path", "")), base),
        local_bot_api_host=host,
        local_bot_api_port=port,
        local_bot_api_base_url=str(raw.get("local_bot_api_base_url", f"http://{host}:{port}/bot")),
        local_bot_api_base_file_url=str(raw.get("local_bot_api_base_file_url", f"http://{host}:{port}/file/bot")),
        telegram_download_read_timeout_seconds=max(
            60, int(raw.get("telegram_download_read_timeout_seconds", 1800))
        ),
        jellyfin_library_path=_path(str(raw["jellyfin_library_path"]), base),
        jellyfin_movie_library_path=_optional_path(
            raw.get("jellyfin_movie_library_path"), base
        ),
        movie_staging_path=_optional_path(raw.get("movie_staging_path"), base),
        data_path=_path(str(raw.get("data_path", "data")), base),
        logs_path=_path(str(raw.get("logs_path", "logs")), base),
        sorter_command=command,
        sorter_timeout_seconds=max(1, int(raw.get("sorter_timeout_seconds", 1800))),
        movie_sorter_command=movie_sorter_command,
        movie_sorter_timeout_seconds=max(
            1, int(raw.get("movie_sorter_timeout_seconds", 1800))
        ),
        scan_after_movie_import=bool(raw.get("scan_after_movie_import", True)),
        allowed_chat_ids={int(x) for x in raw.get("allowed_chat_ids", [])},
        allowed_video_extensions=extensions or {".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v"},
        max_parallel_downloads=max(1, int(raw.get("max_parallel_downloads", 1))),
        default_target_folder=default_folder,
        confirm_before_download=bool(raw.get("confirm_before_download", True)),
        ask_before_overwrite=bool(raw.get("ask_before_overwrite", True)),
        jellyfin_server_url=str(raw.get("jellyfin_server_url", "")).strip().rstrip("/"),
        jellyfin_api_key=str(raw.get("jellyfin_api_key", "")).strip(),
        jellyfin_request_timeout_seconds=max(
            1, int(raw.get("jellyfin_request_timeout_seconds", 30))
        ),
        jellyfin_scan_poll_interval_seconds=max(
            1, int(raw.get("jellyfin_scan_poll_interval_seconds", 5))
        ),
        jellyfin_scan_monitor_timeout_seconds=max(
            30, int(raw.get("jellyfin_scan_monitor_timeout_seconds", 3600))
        ),
        fuzzy_search_command=fuzzy_search_command,
        fuzzy_search_timeout_seconds=max(
            2,
            int(
                raw.get(
                    "fuzzy_search_timeout_seconds",
                    raw.get("organisation_timeout_seconds", 20),
                )
            ),
        ),
    )
    if default_folder:
        cfg.target_path(default_folder)
    if cfg.jellyfin_server_url and not cfg.jellyfin_server_url.lower().startswith(
        ("http://", "https://")
    ):
        raise ValueError("jellyfin_server_url must start with http:// or https://.")
    if (cfg.jellyfin_movie_library_path is None) != (cfg.movie_staging_path is None):
        raise ValueError(
            "jellyfin_movie_library_path and movie_staging_path must either both "
            "be configured or both be empty."
        )
    if cfg.movies_configured:
        assert cfg.jellyfin_movie_library_path is not None
        assert cfg.movie_staging_path is not None
        roots = {
            "shows library": cfg.jellyfin_library_path.resolve(),
            "movies library": cfg.jellyfin_movie_library_path.resolve(),
            "movie staging": cfg.movie_staging_path.resolve(),
        }
        root_items = list(roots.items())
        for index, (left_name, left) in enumerate(root_items):
            for right_name, right in root_items[index + 1:]:
                if left == right or left in right.parents or right in left.parents:
                    raise ValueError(
                        f"{left_name} and {right_name} must be separate, non-nested folders."
                    )
    directories = [cfg.jellyfin_library_path, cfg.data_path, cfg.logs_path]
    if cfg.movies_configured:
        directories.extend(
            [cfg.jellyfin_movie_library_path, cfg.movie_staging_path]
        )
    for directory in directories:
        assert directory is not None
        directory.mkdir(parents=True, exist_ok=True)
    return cfg
