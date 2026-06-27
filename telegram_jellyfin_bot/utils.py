from __future__ import annotations

import logging
import re
from pathlib import Path

WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
INVALID_WINDOWS_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_folder_name(value: str) -> str:
    """Return one safe relative folder component or raise ValueError."""
    value = value.strip()
    if not value or value in {".", ".."}:
        raise ValueError("نام فولدر خالی یا نامعتبر است.")
    if Path(value).is_absolute() or "/" in value or "\\" in value:
        raise ValueError("فقط نام فولدر را بفرستید، نه مسیر کامل.")
    cleaned = INVALID_WINDOWS_CHARS.sub("_", value).rstrip(" .")
    if not cleaned or cleaned.upper() in WINDOWS_RESERVED:
        raise ValueError("این نام فولدر در ویندوز مجاز نیست.")
    return cleaned


def validate_original_filename(value: str) -> str:
    """Keep Telegram's filename exactly; reject names unsafe on Windows."""
    if not value or value in {".", ".."}:
        raise ValueError("نام اصلی فایل موجود نیست.")
    if Path(value).name != value or INVALID_WINDOWS_CHARS.search(value):
        raise ValueError("نام اصلی فایل برای ذخیره امن در ویندوز معتبر نیست.")
    if value.endswith((" ", ".")) or Path(value).stem.upper() in WINDOWS_RESERVED:
        raise ValueError("نام اصلی فایل در ویندوز مجاز نیست.")
    return value


def safe_child(base: Path, folder_name: str) -> Path:
    base = base.resolve()
    target = (base / sanitize_folder_name(folder_name)).resolve()
    if target.parent != base:
        raise ValueError("مسیر مقصد خارج از Library است.")
    return target


def format_size(size: int | None) -> str:
    if not size:
        return "نامشخص"
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def setup_logging(logs_path: Path) -> None:
    logs_path.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(logs_path / "bot.log", encoding="utf-8"),
        ],
    )
