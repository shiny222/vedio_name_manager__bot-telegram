from __future__ import annotations

import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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
    animation_series = os.environ.get(
        "LIBRARY_ANIMATION_SERIES_PATH", "/media/animation-serise"
    ).strip()
    animation_movie = os.environ.get(
        "LIBRARY_ANIMATION_MOVIE_PATH", "/media/animation-movie"
    ).strip()
    video_series = os.environ.get(
        "LIBRARY_VIDEO_SERIES_PATH", "/media/video-serise"
    ).strip()
    video_movie = os.environ.get(
        "LIBRARY_VIDEO_MOVIE_PATH", "/media/video-movie"
    ).strip()
    anime_series = os.environ.get("LIBRARY_ANIME_SERIES_PATH", "").strip()
    anime_movie = os.environ.get("LIBRARY_ANIME_MOVIE_PATH", "").strip()
    legacy_series = os.environ.get("JELLYFIN_LIBRARY_PATH", "").strip()
    legacy_movie = os.environ.get("JELLYFIN_MOVIE_LIBRARY_PATH", "").strip()
    libraries = [
        {
            "key": "animation_series",
            "name": os.environ.get(
                "LIBRARY_ANIMATION_SERIES_NAME", "Animation Series"
            ).strip(),
            "media_kind": "series",
            "path": animation_series,
            "metadata_provider": "imdb",
        },
        {
            "key": "animation_movies",
            "name": os.environ.get(
                "LIBRARY_ANIMATION_MOVIE_NAME", "Animation Movies"
            ).strip(),
            "media_kind": "movie",
            "path": animation_movie,
            "metadata_provider": "imdb",
        },
        {
            "key": "video_series",
            "name": os.environ.get(
                "LIBRARY_VIDEO_SERIES_NAME", "Video Series"
            ).strip(),
            "media_kind": "series",
            "path": video_series,
            "metadata_provider": "imdb",
        },
        {
            "key": "video_movies",
            "name": os.environ.get(
                "LIBRARY_VIDEO_MOVIE_NAME", "Video Movies"
            ).strip(),
            "media_kind": "movie",
            "path": video_movie,
            "metadata_provider": "imdb",
        },
    ]
    if anime_series:
        libraries.append(
            {
                "key": "anime_series",
                "name": os.environ.get(
                    "LIBRARY_ANIME_SERIES_NAME", "Anime Series"
                ).strip(),
                "media_kind": "series",
                "path": anime_series,
                "metadata_provider": "anilist",
            }
        )
    if anime_movie:
        libraries.append(
            {
                "key": "anime_movies",
                "name": os.environ.get(
                    "LIBRARY_ANIME_MOVIE_NAME", "Anime Movies"
                ).strip(),
                "media_kind": "movie",
                "path": anime_movie,
                "metadata_provider": "anilist",
            }
        )
    has_multi_library_env = any(
        os.environ.get(name, "").strip()
        for name in (
            "LIBRARY_ANIMATION_SERIES_PATH",
            "LIBRARY_ANIMATION_MOVIE_PATH",
            "LIBRARY_VIDEO_SERIES_PATH",
            "LIBRARY_VIDEO_MOVIE_PATH",
            "LIBRARY_ANIME_SERIES_PATH",
            "LIBRARY_ANIME_MOVIE_PATH",
        )
    )
    # An older Docker .env may only have the single series/movie variables.
    # Keep that two-root layout instead of inventing extra destinations.
    if not has_multi_library_env:
        libraries = []
        if legacy_series:
            libraries.append(
                {
                    "key": "series",
                    "name": "Series",
                    "media_kind": "series",
                    "path": legacy_series,
                }
            )
        if legacy_movie:
            libraries.append(
                {
                    "key": "movies",
                    "name": "Movies",
                    "media_kind": "movie",
                    "path": legacy_movie,
                }
            )
    movie_entry = next(
        (item for item in libraries if item["media_kind"] == "movie"), None
    )
    series_entry = next(
        (item for item in libraries if item["media_kind"] == "series"), None
    )
    legacy_series_match = next(
        (
            item
            for item in libraries
            if legacy_series and str(item["path"]) == legacy_series
        ),
        None,
    )
    legacy_movie_match = next(
        (
            item
            for item in libraries
            if legacy_movie and str(item["path"]) == legacy_movie
        ),
        None,
    )
    explicit_default_key = os.environ.get("DEFAULT_LIBRARY_KEY", "").strip()
    explicit_default = next(
        (item for item in libraries if item["key"] == explicit_default_key), None
    )
    default_series_entry = (
        explicit_default
        if explicit_default and explicit_default["media_kind"] == "series"
        else legacy_series_match or series_entry
    )
    default_movie_entry = (
        explicit_default
        if explicit_default and explicit_default["media_kind"] == "movie"
        else legacy_movie_match or movie_entry
    )
    movie_library = str(movie_entry["path"]) if movie_entry else ""
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
        "jellyfin_library_path": str(series_entry["path"] if series_entry else ""),
        "jellyfin_movie_library_path": movie_library,
        "media_libraries": libraries,
        "default_library_key": os.environ.get(
            "DEFAULT_LIBRARY_KEY",
            str((legacy_series_match or series_entry or {}).get("key", "")),
        ).strip(),
        "default_series_library_key": str(
            (default_series_entry or {}).get("key", "")
        ),
        "default_movie_library_key": str(
            (default_movie_entry or {}).get("key", "")
        ),
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
        "scan_after_ai_series_sort": _env_bool(
            "SCAN_AFTER_AI_SERIES_SORT", True
        ),
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
        "anilist_search_command": [
            python,
            str(PROJECT_ROOT / "anilist_search" / "anilist_tool.py"),
        ],
        "anilist_search_timeout_seconds": int(
            os.environ.get("ANILIST_SEARCH_TIMEOUT_SECONDS", "20")
        ),
        "n8n_agent_enabled": _env_bool("N8N_AGENT_ENABLED", False),
        "n8n_agent_url": os.environ.get(
            "N8N_AGENT_URL", "http://n8n:5678/webhook/media-identify"
        ).strip(),
        "n8n_agent_secret": os.environ.get("N8N_AGENT_SECRET", "").strip(),
        "n8n_agent_timeout_seconds": int(
            os.environ.get("N8N_AGENT_TIMEOUT_SECONDS", "45")
        ),
    }


def _path(value: str, base: Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (base / path).resolve()


def _optional_path(value: Any, base: Path) -> Path | None:
    text = str(value or "").strip()
    return _path(text, base) if text else None


@dataclass(frozen=True)
class MediaLibrary:
    """One selectable Jellyfin destination exposed to the Telegram bot."""

    key: str
    name: str
    media_kind: str
    path: Path
    metadata_provider: str = "imdb"


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
    media_libraries: tuple[MediaLibrary, ...]
    default_library_key: str
    default_series_library_key: str
    default_movie_library_key: str
    movie_staging_path: Path | None
    data_path: Path
    logs_path: Path
    sorter_command: list[str]
    sorter_timeout_seconds: int
    movie_sorter_command: list[str]
    movie_sorter_timeout_seconds: int
    scan_after_movie_import: bool
    scan_after_ai_series_sort: bool
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
    anilist_search_command: list[str]
    anilist_search_timeout_seconds: int
    n8n_agent_enabled: bool
    n8n_agent_url: str
    n8n_agent_secret: str
    n8n_agent_timeout_seconds: int

    @property
    def api_root(self) -> str:
        return f"{self.local_bot_api_base_url.rstrip('/')}{self.bot_token}"

    @property
    def file_root(self) -> str:
        return f"{self.local_bot_api_base_file_url.rstrip('/')}{self.bot_token}"

    def library(self, key: str | None = None, media_kind: str | None = None) -> MediaLibrary:
        if key is None and media_kind == "series":
            requested = self.default_series_library_key
        elif key is None and media_kind == "movie":
            requested = self.default_movie_library_key
        else:
            requested = key or self.default_library_key
        requested = requested.strip()
        for library in self.media_libraries:
            if library.key == requested:
                if media_kind and library.media_kind != media_kind:
                    if key is not None:
                        raise ValueError(
                            f"Library {library.name!r} is for {library.media_kind}, not {media_kind}."
                        )
                    break
                return library
        if key:
            raise ValueError(f"Unknown library key: {key}")
        for library in self.media_libraries:
            if media_kind is None or library.media_kind == media_kind:
                return library
        raise ValueError(f"No {media_kind or 'media'} library is configured.")

    def libraries_for(self, media_kind: str) -> tuple[MediaLibrary, ...]:
        return tuple(
            library
            for library in self.media_libraries
            if library.media_kind == media_kind
        )

    def target_path(self, folder_name: str, library_key: str | None = None) -> Path:
        library = self.library(library_key, "series")
        return safe_child(library.path, folder_name)

    @property
    def movies_configured(self) -> bool:
        return bool(self.libraries_for("movie") and self.movie_staging_path)

    def movie_target_path(
        self, folder_name: str, library_key: str | None = None
    ) -> Path:
        library = self.library(library_key, "movie")
        return safe_child(library.path, folder_name)

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
    anilist_search_command = raw.get(
        "anilist_search_command",
        [
            sys.executable,
            str(PROJECT_ROOT / "anilist_search" / "anilist_tool.py"),
        ],
    )
    if not isinstance(anilist_search_command, list) or not all(
        isinstance(x, str) for x in anilist_search_command
    ):
        raise ValueError("anilist_search_command must be a list of arguments.")
    default_folder = str(raw.get("default_target_folder", "")).strip()
    raw_libraries = raw.get("media_libraries")
    libraries: list[MediaLibrary] = []
    if raw_libraries is not None:
        if not isinstance(raw_libraries, list):
            raise ValueError("media_libraries must be a list.")
        seen_keys: set[str] = set()
        for entry in raw_libraries:
            if not isinstance(entry, dict):
                raise ValueError("Each media_libraries entry must be an object.")
            key = str(entry.get("key", "")).strip()
            name = str(entry.get("name", "")).strip()
            media_kind = str(entry.get("media_kind", "")).strip().casefold()
            metadata_provider = str(
                entry.get("metadata_provider", "imdb")
            ).strip().casefold()
            path_text = str(entry.get("path", "")).strip()
            if not re.fullmatch(r"[a-z0-9_]{1,40}", key):
                raise ValueError(
                    "Library keys may contain only lowercase letters, numbers, and underscores."
                )
            if key in seen_keys:
                raise ValueError(f"Duplicate media library key: {key}")
            if not name or media_kind not in {"series", "movie"} or not path_text:
                raise ValueError(
                    f"Library {key!r} needs name, media_kind (series/movie), and path."
                )
            if metadata_provider not in {"imdb", "anilist"}:
                raise ValueError(
                    f"Library {key!r} metadata_provider must be imdb or anilist."
                )
            seen_keys.add(key)
            libraries.append(
                MediaLibrary(
                    key,
                    name,
                    media_kind,
                    _path(path_text, base),
                    metadata_provider,
                )
            )
    if not libraries:
        libraries.append(
            MediaLibrary(
                "series", "Series", "series", _path(str(raw["jellyfin_library_path"]), base)
            )
        )
        movie_path = _optional_path(raw.get("jellyfin_movie_library_path"), base)
        if movie_path is not None:
            libraries.append(MediaLibrary("movies", "Movies", "movie", movie_path))
    default_library_key = str(raw.get("default_library_key", libraries[0].key)).strip()
    if default_library_key not in {library.key for library in libraries}:
        raise ValueError(f"default_library_key is not configured: {default_library_key}")
    default_series = next(
        (library for library in libraries if library.media_kind == "series"), None
    )
    default_movie = next(
        (library for library in libraries if library.media_kind == "movie"), None
    )
    if default_series is None:
        raise ValueError("At least one series media library is required.")
    default_series_library_key = str(
        raw.get("default_series_library_key", default_series.key)
    ).strip()
    default_movie_library_key = str(
        raw.get(
            "default_movie_library_key",
            default_movie.key if default_movie else "",
        )
    ).strip()
    by_key = {library.key: library for library in libraries}
    if (
        default_series_library_key not in by_key
        or by_key[default_series_library_key].media_kind != "series"
    ):
        raise ValueError("default_series_library_key must identify a series library.")
    if default_movie_library_key and (
        default_movie_library_key not in by_key
        or by_key[default_movie_library_key].media_kind != "movie"
    ):
        raise ValueError("default_movie_library_key must identify a movie library.")
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
        jellyfin_library_path=default_series.path,
        jellyfin_movie_library_path=(default_movie.path if default_movie else None),
        media_libraries=tuple(libraries),
        default_library_key=default_library_key,
        default_series_library_key=default_series_library_key,
        default_movie_library_key=default_movie_library_key,
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
        scan_after_ai_series_sort=bool(
            raw.get("scan_after_ai_series_sort", True)
        ),
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
        anilist_search_command=anilist_search_command,
        anilist_search_timeout_seconds=max(
            2, int(raw.get("anilist_search_timeout_seconds", 20))
        ),
        n8n_agent_enabled=bool(raw.get("n8n_agent_enabled", False)),
        n8n_agent_url=str(raw.get("n8n_agent_url", "")).strip(),
        n8n_agent_secret=str(raw.get("n8n_agent_secret", "")).strip(),
        n8n_agent_timeout_seconds=max(
            2, int(raw.get("n8n_agent_timeout_seconds", 45))
        ),
    )
    if default_folder:
        cfg.target_path(default_folder)
    if cfg.jellyfin_server_url and not cfg.jellyfin_server_url.lower().startswith(
        ("http://", "https://")
    ):
        raise ValueError("jellyfin_server_url must start with http:// or https://.")
    if cfg.n8n_agent_enabled:
        parsed_n8n = urlparse(cfg.n8n_agent_url)
        if parsed_n8n.scheme not in {"http", "https"} or not parsed_n8n.netloc:
            raise ValueError("n8n_agent_url must be a complete http:// or https:// URL.")
        normalized_path = parsed_n8n.path.rstrip("/")
        if "/workflow/" in normalized_path or normalized_path.startswith("/workflow/"):
            raise ValueError(
                "n8n_agent_url points to the n8n editor. Use the Webhook node's "
                "production URL ending in /webhook/media-identify."
            )
        if not normalized_path.endswith(("/webhook/media-identify", "/webhook-test/media-identify")):
            raise ValueError(
                "n8n_agent_url must be the media-identify Webhook URL, not the workflow editor URL."
            )
    if (not cfg.libraries_for("movie")) != (cfg.movie_staging_path is None):
        raise ValueError(
            "Movie libraries and movie_staging_path must either both be configured "
            "or both be empty."
        )
    if cfg.movies_configured:
        assert cfg.movie_staging_path is not None
        roots = {
            **{
                f"library {library.name!r}": library.path.resolve()
                for library in cfg.media_libraries
            },
            "movie staging": cfg.movie_staging_path.resolve(),
        }
        root_items = list(roots.items())
        for index, (left_name, left) in enumerate(root_items):
            for right_name, right in root_items[index + 1:]:
                if left == right or left in right.parents or right in left.parents:
                    raise ValueError(
                        f"{left_name} and {right_name} must be separate, non-nested folders."
                    )
    directories = [
        *(library.path for library in cfg.media_libraries),
        cfg.data_path,
        cfg.logs_path,
    ]
    if cfg.movies_configured:
        directories.append(cfg.movie_staging_path)
    for directory in directories:
        assert directory is not None
        directory.mkdir(parents=True, exist_ok=True)
    return cfg
