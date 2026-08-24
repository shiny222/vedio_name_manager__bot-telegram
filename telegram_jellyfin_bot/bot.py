from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import mimetypes
import re
import sys
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any

import aiohttp


CURRENT_MESSAGE_THREAD_ID: ContextVar[int | None] = ContextVar(
    "telegram_message_thread_id", default=None
)
CURRENT_DIRECT_MESSAGES_TOPIC_ID: ContextVar[int | None] = ContextVar(
    "telegram_direct_messages_topic_id", default=None
)

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from telegram_jellyfin_bot.config import Config, MediaLibrary, load_config
    from telegram_jellyfin_bot.downloader import DownloadManager
    from telegram_jellyfin_bot.episode_catalog import (
        EpisodeCatalog, detect_episode, format_series_inventory
    )
    from telegram_jellyfin_bot.jellyfin_bridge import JellyfinBridge
    from telegram_jellyfin_bot.anilist_bridge import AniListSearchBridge
    from telegram_jellyfin_bot.imdb_bridge import (
        ImdbFuzzySearchBridge, movie_query_from_filename
    )
    from telegram_jellyfin_bot.movie_sorter_bridge import MovieSorterBridge
    from telegram_jellyfin_bot.n8n_bridge import (
        MediaIdentification,
        N8nMediaIdentifier,
    )
    from telegram_jellyfin_bot.localization import (
        LANGUAGE_MENU,
        language_code,
        reply_category_action,
        translate_markup,
        translate_text,
    )
    from telegram_jellyfin_bot.queue_manager import QueueManager
    from telegram_jellyfin_bot.sorter_bridge import SorterBridge
    from telegram_jellyfin_bot.state_store import StateStore
    from telegram_jellyfin_bot.utils import (
        format_size, sanitize_folder_name, setup_logging, validate_original_filename
    )
else:
    from .config import Config, MediaLibrary, load_config
    from .downloader import DownloadManager
    from .episode_catalog import EpisodeCatalog, detect_episode, format_series_inventory
    from .jellyfin_bridge import JellyfinBridge
    from .anilist_bridge import AniListSearchBridge
    from .imdb_bridge import ImdbFuzzySearchBridge, movie_query_from_filename
    from .movie_sorter_bridge import MovieSorterBridge
    from .n8n_bridge import MediaIdentification, N8nMediaIdentifier
    from .localization import (
        LANGUAGE_MENU,
        language_code,
        reply_category_action,
        translate_markup,
        translate_text,
    )
    from .queue_manager import QueueManager
    from .sorter_bridge import SorterBridge
    from .state_store import StateStore
    from .utils import format_size, sanitize_folder_name, setup_logging, validate_original_filename

LOG = logging.getLogger(__name__)
SERIES_BATCH_WINDOW_SECONDS = 2.0
MOVIE_BATCH_WINDOW_SECONDS = 2.0
IMDB_FOLDER_ID_RE = re.compile(r"\[imdbid-(tt\d+)\]", re.IGNORECASE)
ANILIST_FOLDER_ID_RE = re.compile(r"\[anilistid-(\d+)\]", re.IGNORECASE)
FOLDER_YEAR_RE = re.compile(r"\s*[\(\[]((?:19|20)\d{2})[\)\]]\s*$")
IMPORTANT_OPEN = "\ue000"
IMPORTANT_CLOSE = "\ue001"
IMPORTANT_RE = re.compile(
    f"{re.escape(IMPORTANT_OPEN)}(.*?){re.escape(IMPORTANT_CLOSE)}",
    re.DOTALL,
)
TELEGRAM_TEXT_LIMIT = 4000
DOWNLOAD_REVIEW_SETTING = "download_review_items"
REMOVE_PROMPT_SETTING = "remove_review_pending"


def _important(value: Any) -> str:
    """Mark a trusted display value for bold rendering after localization."""
    text = str(value)
    # Prevent an unusual filename from closing or opening our internal marker.
    text = text.replace(IMPORTANT_OPEN, "").replace(IMPORTANT_CLOSE, "")
    return f"{IMPORTANT_OPEN}{text}{IMPORTANT_CLOSE}"


def _escaped_prefix(value: str, limit: int) -> tuple[str, bool]:
    """Escape as much text as fits without cutting an HTML entity."""
    output: list[str] = []
    used = 0
    for character in value:
        encoded = escape(character, quote=False)
        if used + len(encoded) > limit:
            return "".join(output), False
        output.append(encoded)
        used += len(encoded)
    return "".join(output), True


def _telegram_html(value: str) -> str:
    """Escape a complete message and render only explicit important values bold."""
    segments: list[tuple[str, bool]] = []
    position = 0
    for match in IMPORTANT_RE.finditer(value):
        segments.append((value[position:match.start()], False))
        segments.append((match.group(1), True))
        position = match.end()
    segments.append((value[position:], False))

    output: list[str] = []
    used = 0
    for raw, bold in segments:
        # Unmatched private markers are never intended Telegram content.
        raw = raw.replace(IMPORTANT_OPEN, "").replace(IMPORTANT_CLOSE, "")
        wrapper_size = 7 if bold and raw else 0  # <b> + </b>
        remaining = TELEGRAM_TEXT_LIMIT - used - wrapper_size
        if remaining <= 0:
            break
        rendered, complete = _escaped_prefix(raw, remaining)
        if rendered:
            if bold:
                rendered = f"<b>{rendered}</b>"
            output.append(rendered)
            used += len(rendered)
        if not complete:
            break
    return "".join(output)


def _series_file_title(folder_name: str) -> str:
    """Return the episode-file title used by the standalone organizer."""
    title = re.sub(
        r"\s*\[(?:imdbid|tmdbid|tvdbid|anilistid)-[^\]]+\]\s*",
        " ",
        folder_name,
        flags=re.IGNORECASE,
    )
    title = FOLDER_YEAR_RE.sub(" ", title)
    return re.sub(r"\s+", " ", title).strip() or folder_name


def _normalized_title(value: str) -> str:
    """Normalize a title only for conservative existing-folder matching."""
    value = re.sub(
        r"\s*\[(?:imdbid|tmdbid|tvdbid|anilistid)-[^\]]+\]\s*",
        " ",
        value,
        flags=re.IGNORECASE,
    )
    value = FOLDER_YEAR_RE.sub(" ", value)
    return re.sub(r"[^\w]+", "", value, flags=re.UNICODE).casefold()


def _release_year_from_filename(filename: str) -> int | None:
    """Return one unambiguous release year embedded in a source filename."""
    years = {
        int(value)
        for value in re.findall(
            r"(?<!\d)((?:18|19|20)\d{2})(?!\d)", Path(filename).stem
        )
        if 1878 <= int(value) <= datetime.now().year + 5
    }
    return next(iter(years)) if len(years) == 1 else None


HELP = """Commands:
/start - Choose a language on first use or reopen help/menu
/menu - Show the button menu
/libraries - Choose one of the configured media libraries
/use_library KEY - Select a library directly
/setfolder NAME - Set the target folder
/folders - Pick from existing folders
/usefolder NAME - Use an existing folder by name
/renamefolder NAME - Rename the current folder safely
/folder - Show the current folder
/unsetfolder - Clear the current folder
/queue - Show the download queue
/remove - Ask for a temporary /download list number and remove that item
/clearqueue - Clear the queue
/download - Review and prepare downloads
/confirm_download - Confirm and start downloading
/status - Show bot/download status
/cancel - Cancel the current operation
/resolve ID skip|overwrite|save_with_suffix - Resolve an existing-file conflict
/sort_current - Sort new loose files in the current folder
/resort_current - Rename already sorted files to match the current folder name
/sort_history - Show numbered sort revisions
/sort_back - Move one sort revision back
/sort_forward - Move one sort revision forward
/recover_current - Manually recover incomplete operations in the current folder
/fix_metadata_current - Rename episode NFO/artwork in the current folder
/sort_latest - Sort the latest downloaded folder
/sort_folder NAME - Sort a specific folder
/sort_status - Show sorter status
/undo_sort_last - Undo the latest sorter batch
/undo_sort_batch ID - Undo a specific sorter batch
/jellyfin_scan - Trigger a Jellyfin library scan
/jellyfin_status - Check Jellyfin connection
/episodes [NAME] - Show episodes for one series
/library_episodes - Show a summary of all series
/imdb_search NAME - Search the selected library's title provider
/imdb_fix_current [NAME] - Rename the current folder using its provider
/movie_mode - Send new files as independent movie jobs
/series_mode - Return to TV-series episode mode
/movie_current - Show the latest movie job
/movie_cancel - Cancel the current unprocessed movie
/movie_import [ID] - Retry a downloaded movie import
/movie_undo_last - Undo the latest movie import batch
/movie_undo_batch ID - Undo a specific movie import batch
/chatid - Show this chat ID
/language - Choose English or Persian
/guide - Show the English/Persian usage guide
/help - Show this help"""

HELP_FA = """دستورها:
/start - انتخاب زبان در اولین اجرا یا باز کردن دوباره راهنما و منو
/menu - نمایش منوی دکمه‌ای
/libraries - انتخاب یکی از کتابخانه‌های رسانه
/use_library KEY - انتخاب مستقیم کتابخانه
/help - نمایش راهنمای دستورها
/guide - نمایش راهنمای استفاده
/language - انتخاب زبان فارسی یا انگلیسی
/status - نمایش وضعیت ربات و دانلود
/chatid - نمایش شناسه این چت
/setfolder NAME - تنظیم یا ساخت پوشه سریال
/folders - انتخاب پوشه سریال موجود
/usefolder NAME - استفاده از پوشه موجود
/renamefolder NAME - تغییر امن نام پوشه فعلی
/folder - نمایش پوشه فعلی
/unsetfolder - پاک کردن پوشه فعلی
/queue - نمایش صف دانلود
/remove - درخواست شماره موقت فهرست /download و حذف همان مورد
/clearqueue - پاک کردن صف
/download - بررسی و آماده‌سازی دانلودها
/confirm_download - تأیید و شروع دانلود
/cancel - لغو عملیات فعلی
/resolve ID skip|overwrite|save_with_suffix - رفع تداخل فایل
/sort_current - مرتب‌سازی فایل‌های جدید پوشه فعلی
/resort_current - اصلاح نام قسمت‌های مرتب‌شده
/sort_latest - مرتب‌سازی آخرین پوشه دانلودشده
/sort_folder NAME - مرتب‌سازی یک پوشه مشخص
/sort_status - نمایش وضعیت مرتب‌ساز
/sort_history - نمایش نسخه‌های مرتب‌سازی
/sort_back - یک نسخه به عقب
/sort_forward - یک نسخه به جلو
/recover_current - بازیابی عملیات ناقص پوشه فعلی
/fix_metadata_current - اصلاح نام متادیتای قسمت‌ها
/undo_sort_last - بازگردانی آخرین دسته مرتب‌سازی
/undo_sort_batch ID - بازگردانی یک دسته مشخص
/movie_mode - ورود به حالت فیلم
/series_mode - بازگشت به حالت سریال
/movie_current - نمایش آخرین عملیات فیلم
/movie_cancel - لغو فیلم پردازش‌نشده
/movie_import [ID] - تلاش دوباره برای انتقال فیلم staging
/movie_undo_last - بازگردانی آخرین فیلم
/movie_undo_batch ID - بازگردانی یک دسته فیلم
/jellyfin_scan - شروع اسکن کتابخانه Jellyfin
/jellyfin_status - بررسی اتصال و وضعیت Jellyfin
/episodes [NAME] - نمایش قسمت‌های یک سریال
/library_episodes - خلاصه قسمت‌های کل کتابخانه
/imdb_search NAME - جستجوی نام رسمی در سرویس کتابخانه
/imdb_fix_current [NAME] - اصلاح نام پوشه فعلی با سرویس کتابخانه"""

GUIDE_EN = """How to use the Telegram Jellyfin Bot

The bot has two workflows:
• Normal — AI-assisted identification and the shortest everyday path.
• Advanced — manual correction, sorting, repair, and undo without AI.

MAIN MENU
Send /menu. The main buttons are Downloads, Episodes, Jellyfin, Bot,
Choose Library, and Advanced. Each chat remembers its own library, queue,
confirmations, and history. Opening a menu never creates a Telegram topic.

NORMAL WORKFLOW — AI ASSISTED
1. Press Choose Library once and select Animation Series, Animation Movies,
   Video Series, or Video Movies. It stays selected until you change it.
   AI is never allowed to choose or change this destination.
2. Send one or several supported videos. A short series or movie burst is
   identified as one compact batch. One status is edited with the useful
   identity summary and /download instead of per-file progress messages.
3. When the n8n connection is enabled, AI reads the untrusted filename/caption
   and suggests movie title/year or series title/season/episode. The existing
   IMDb tool finds the official Jellyfin identity.
4. An existing reliable series-folder match is used automatically. A new
   series asks once for all matching episodes. This identity approval is
   separate from the one final download approval. Download review shows the
   final saved filenames, temporary per-batch IDs, file count, and size. Each
   ID remains attached to that movie/episode while you review the batch. Press
   Remove one item (or send /remove), reply with its ID, then reopen /download.
   After /confirm_download starts the batch, the next batch starts at 1. A
   failed AI/IMDb request must not change the library.
   Movies with an exact high-confidence AI/IMDb title and year are queued
   automatically only when a clear year in the filename also agrees.
   Ambiguous/year-mismatched movies still ask. A movie or episode identity
   already in the library/queue shows both filenames and asks Replace or Cancel
   before download. Replace keeps the old media in rollback backup.
5. Open Downloads: Queue → Download → Confirm.
6. Movies are staged and imported safely. AI-confirmed series episodes are
   organized automatically after their downloads finish.
7. Use Jellyfin → Scan library if an automatic scan did not run. Its one status
   message becomes `✅ Jellyfin is ready.` and remains visible. Use Episodes to
   inspect the current series or the series libraries.

ADVANCED WORKFLOW — NO AI REQUIRED
Use Advanced for unusual filenames, incorrect identity, manual organization,
conflicts, or interrupted operations.

Folders:
• /folder, /folders, /setfolder NAME, /usefolder NAME
• /renamefolder NAME, /unsetfolder

Manual identification:
• /imdb_search NAME
• /imdb_fix_current [NAME]
• For a waiting movie, choose Enter name manually.

Sorting and metadata:
• /sort_current, /sort_latest, /sort_folder PATH, /sort_status
• /resort_current renames old episodes after correcting the series folder.
• /fix_metadata_current aligns episode NFO and artwork names.

Queue and conflicts:
• /remove (then reply with the /download list number), /clearqueue
• /resolve ID skip|save_with_suffix|overwrite
• /movie_current, /movie_import ID, /movie_cancel ID

Undo and recovery:
• /sort_history, /sort_back, /sort_forward, /undo_sort_batch ID
• /movie_undo_last, /movie_undo_batch ID
• /recover_current checks only the selected series folder after interruption.

SAFETY
Verify the selected library before download and /folder before manual series
work. Nothing is overwritten silently. Keep staging and .rename_history.json
files while import or rollback may need them.

If n8n is unavailable, the bot keeps the item undownloaded and offers the
existing filename/manual IMDb or current-folder fallback."""

GUIDE_FA = """راهنمای استفاده از ربات تلگرام Jellyfin

ربات دو روش استفاده دارد:
• عادی — تشخیص با کمک هوش مصنوعی و مسیر کوتاه روزمره.
• پیشرفته — اصلاح دستی، مرتب‌سازی، بازیابی و بازگردانی بدون نیاز به AI.

منوی اصلی
/menu را بفرستید. دکمه‌ها: دانلودها، قسمت‌ها، Jellyfin، ربات، انتخاب کتابخانه
و پیشرفته. هر چت کتابخانه، صف، تأییدها و تاریخچه مستقل خودش را دارد. باز کردن
منو هیچ Topic جدیدی ایجاد نمی‌کند.

روش عادی — با کمک هوش مصنوعی
۱. انتخاب کتابخانه را یک بار بزنید و Animation Series، Animation Movies،
   Video Series یا Video Movies را انتخاب کنید. انتخاب تا تغییر بعدی باقی
   می‌ماند. AI اجازه انتخاب یا تغییر مقصد را ندارد.
۲. یک یا چند ویدیو بفرستید. فایل‌های سریال یا فیلم که با فاصله کوتاه ارسال
   شوند در یک دسته کم‌پیام شناسایی می‌شوند. یک پیام با خلاصه هویت و مرحله بعد
   /download ویرایش می‌شود و برای هر فایل پیام پیشرفت جدا نمی‌آید.
۳. پس از فعال شدن اتصال n8n، AI از نام فایل/کپشن نام و سال فیلم یا نام سریال
   و فصل و قسمت را پیشنهاد می‌دهد. ابزار IMDb نام رسمی Jellyfin را پیدا می‌کند.
۴. پوشه سریال موجود با تطبیق معتبر خودکار استفاده می‌شود. برای سریال جدید فقط
   یک بار برای همه قسمت‌های مطابق تأیید هویت گرفته می‌شود. این تأیید از تأیید
   نهایی دانلود جدا است. بررسی دانلود نام نهایی فایل‌ها، تعداد و حجم را نشان
   می‌دهد و هر فایل یک شناسه موقت همان دسته دارد که هنگام بررسی ثابت می‌ماند.
   دکمه حذف یک مورد یا /remove را بزنید، شناسه را بفرستید و /download را دوباره
   باز کنید. پس از شروع دسته با /confirm_download، دسته بعدی دوباره از ۱ شروع
   می‌شود. خرابی AI یا IMDb نباید کتابخانه
   را تغییر دهد.
   فیلم با نام و سال دقیق و اطمینان بالای AI/IMDb خودکار آماده می‌شود؛ نتیجه
   مبهم یا سال ناسازگار سؤال می‌پرسد و سال واضح نام فایل نیز باید برابر باشد.
   اگر هویت فیلم یا قسمت از قبل در کتابخانه/صف باشد، نام هر دو فایل نمایش داده
   می‌شود و پیش از دانلود جایگزینی یا لغو می‌پرسد. جایگزینی، فایل قدیمی را برای
   بازگردانی نگه می‌دارد.
۵. دانلودها را باز کنید: صف ← دانلود ← تأیید.
۶. فیلم ابتدا وارد staging و سپس امن وارد کتابخانه می‌شود و نتیجه چند فیلم در
   یک خلاصه نمایش داده می‌شود. قسمت‌های سریال تأییدشده با AI نیز پس از دانلود
   خودکار مرتب می‌شوند.
۷. اگر اسکن خودکار اجرا نشد، Jellyfin ← اسکن کتابخانه را بزنید. همان پیام وضعیت
   به `✅ Jellyfin آماده است.` تغییر می‌کند و در چت باقی می‌ماند. از قسمت‌ها
   برای بررسی سریال فعلی یا کتابخانه‌های سریال استفاده کنید.

روش پیشرفته — بدون نیاز به AI
برای نام غیرعادی، تشخیص اشتباه، کار دستی، تداخل یا عملیات ناقص استفاده کنید.

پوشه‌ها:
• /folder، /folders، /setfolder NAME، /usefolder NAME
• /renamefolder NAME، /unsetfolder

تشخیص دستی:
• /imdb_search NAME
• /imdb_fix_current [NAME]
• برای فیلم منتظر تشخیص، وارد کردن نام دستی را بزنید.

مرتب‌سازی و متادیتا:
• /sort_current، /sort_latest، /sort_folder PATH، /sort_status
• /resort_current نام قسمت‌های قبلی را بعد از اصلاح پوشه تغییر می‌دهد.
• /fix_metadata_current نام NFO و تصاویر قسمت را هماهنگ می‌کند.

صف و تداخل:
• /remove (سپس شماره فهرست /download را بفرستید)، /clearqueue
• /resolve ID skip|save_with_suffix|overwrite
• /movie_current، /movie_import ID، /movie_cancel ID

بازگردانی و بازیابی:
• /sort_history، /sort_back، /sort_forward، /undo_sort_batch ID
• /movie_undo_last، /movie_undo_batch ID
• /recover_current فقط پوشه سریال انتخاب‌شده را پس از توقف ناقص بررسی می‌کند.

ایمنی
پیش از دانلود کتابخانه و پیش از کار دستی سریال /folder را بررسی کنید. هیچ چیز
بدون اجازه بازنویسی نمی‌شود. تا وقتی انتقال یا بازگردانی ممکن است لازم باشد،
فایل‌های staging و .rename_history.json را حذف نکنید.

اگر n8n در دسترس نباشد، فایل دانلود نمی‌شود و ربات روش نام فایل/IMDb/نام دستی
یا پوشه فعلی را به‌عنوان جایگزین امن پیشنهاد می‌دهد."""

GUIDE_LANGUAGE_MENU = {
    "inline_keyboard": [
        [
            {"text": "🇬🇧 English", "callback_data": "guide:en"},
            {"text": "🇮🇷 فارسی", "callback_data": "guide:fa"},
        ],
        [
            {"text": "⚡ Quick menu", "callback_data": "menu:open"},
        ],
    ]
}

BOT_COMMANDS = [
    # General and quick access
    {"command": "menu", "description": "General: Show quick-access buttons"},
    {"command": "help", "description": "General: Show command help"},
    {"command": "guide", "description": "General: How to use the bot (EN/FA)"},
    {"command": "status", "description": "General: Show bot and download status"},
    {"command": "chatid", "description": "General: Show this chat ID"},
    {"command": "language", "description": "General: Choose English or Persian"},
    # Folder selection and naming
    {"command": "folder", "description": "Folders: Show the current folder"},
    {"command": "folders", "description": "Folders: Pick an existing folder"},
    {"command": "setfolder", "description": "Folders: Set or create a target folder"},
    {"command": "usefolder", "description": "Folders: Use an existing folder by name"},
    {"command": "renamefolder", "description": "Folders: Rename the current folder"},
    {"command": "unsetfolder", "description": "Folders: Clear the current folder"},
    {"command": "libraries", "description": "Folders: Choose a media library"},
    {"command": "use_library", "description": "Folders: Select a library by key"},
    # Queue and downloads
    {"command": "queue", "description": "Downloads: Show the queue"},
    {"command": "download", "description": "Downloads: Prepare queued files"},
    {"command": "confirm_download", "description": "Downloads: Confirm and start"},
    {"command": "remove", "description": "Downloads: Remove one queued file"},
    {"command": "clearqueue", "description": "Downloads: Clear the queue"},
    {"command": "resolve", "description": "Downloads: Resolve an existing-file conflict"},
    {"command": "cancel", "description": "Downloads: Cancel the current operation"},
    # File organization
    {"command": "sort_current", "description": "Sorting: Sort the current folder"},
    {"command": "sort_latest", "description": "Sorting: Sort the latest download"},
    {"command": "sort_folder", "description": "Sorting: Sort a specific folder"},
    {"command": "resort_current", "description": "Sorting: Rename organized episodes"},
    {"command": "fix_metadata_current", "description": "Sorting: Fix episode metadata names"},
    {"command": "sort_status", "description": "Sorting: Show the latest sorter run"},
    # Rollback and recovery
    {"command": "sort_history", "description": "History: Show current-folder revisions"},
    {"command": "sort_back", "description": "History: Move one revision back"},
    {"command": "sort_forward", "description": "History: Move one revision forward"},
    {"command": "recover_current", "description": "History: Recover the current folder"},
    {"command": "undo_sort_last", "description": "History: Undo the latest library batch"},
    {"command": "undo_sort_batch", "description": "History: Undo a batch by ID"},
    # Independent movie workflow
    {"command": "movie_mode", "description": "Movies: Enter movie mode"},
    {"command": "series_mode", "description": "Movies: Return to series mode"},
    {"command": "movie_current", "description": "Movies: Show the latest movie job"},
    {"command": "movie_cancel", "description": "Movies: Cancel the current movie job"},
    {"command": "movie_import", "description": "Movies: Retry a staged movie import"},
    {"command": "movie_undo_last", "description": "Movies: Undo the latest movie import"},
    {"command": "movie_undo_batch", "description": "Movies: Undo a movie batch by ID"},
    # Jellyfin
    {"command": "jellyfin_scan", "description": "Jellyfin: Start a library scan"},
    {"command": "jellyfin_status", "description": "Jellyfin: Check the connection"},
    # Episode inventory
    {"command": "episodes", "description": "Episodes: Show one series"},
    {"command": "library_episodes", "description": "Episodes: Show the library summary"},
    # Optional IMDb title tools
    {"command": "imdb_search", "description": "Titles: Search the selected provider"},
    {"command": "imdb_fix_current", "description": "Titles: Fix the current folder name"},
]

CHANNEL_MENU = {
    "inline_keyboard": [
        [
            {"text": "📥 Downloads", "callback_data": "nav:downloads"},
            {"text": "📺 Episodes", "callback_data": "nav:episodes"},
        ],
        [
            {"text": "🎬 Jellyfin", "callback_data": "nav:jellyfin"},
            {"text": "⚙️ Bot", "callback_data": "nav:bot"},
        ],
        [
            {"text": "🗄 Choose Library", "callback_data": "menu:libraries"},
        ],
        [
            {"text": "🧰 Advanced", "callback_data": "nav:advanced"},
        ],
    ]
}

# This persistent keyboard appears beside the message input in private chats,
# groups, and supergroups. Telegram does not support reply keyboards in channels,
# so CHANNEL_MENU presents the same small main menu as inline buttons.
PERSISTENT_CATEGORY_KEYBOARD = {
    "keyboard": [
        [
            {"text": "📥 Downloads"},
            {"text": "📺 Episodes"},
        ],
        [
            {"text": "🎬 Jellyfin"},
            {"text": "⚙️ Bot"},
        ],
        [
            {"text": "🗄 Choose Library"},
            {"text": "🧰 Advanced"},
        ],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
    "input_field_placeholder": "Choose a bot category or send a video…",
}

CATEGORY_MENU = {
    "inline_keyboard": [
        [
            {"text": "📥 Downloads", "callback_data": "nav:downloads"},
            {"text": "📺 Episodes", "callback_data": "nav:episodes"},
        ],
        [
            {"text": "🎬 Jellyfin", "callback_data": "nav:jellyfin"},
            {"text": "⚙️ Bot", "callback_data": "nav:bot"},
        ],
        [
            {"text": "🗄 Choose Library", "callback_data": "menu:libraries"},
        ],
        [
            {"text": "🧰 Advanced", "callback_data": "nav:advanced"},
        ],
    ]
}

SUBMENU_FOOTER = [
    {"text": "⬅️ Main menu", "callback_data": "nav:categories"},
]

ADVANCED_SUBMENU_FOOTER = [
    {"text": "⬅️ Advanced", "callback_data": "nav:advanced"},
    {"text": "🎛 Main menu", "callback_data": "nav:categories"},
]

DOWNLOAD_MENU = {
    "inline_keyboard": [
        [
            {"text": "📋 Queue", "callback_data": "menu:queue"},
            {"text": "⬇️ Download", "callback_data": "menu:download"},
        ],
        [
            {"text": "✅ Confirm", "callback_data": "menu:confirm"},
            {"text": "📊 Status", "callback_data": "menu:status"},
        ],
        [
            {"text": "🗑 Clear queue", "callback_data": "menu:clearqueue"},
            {"text": "⛔ Cancel", "callback_data": "menu:cancel"},
        ],
        [
            {"text": "🗑 Remove one item", "callback_data": "menu:remove"},
            {"text": "📋 Copy /resolve", "copy_text": {"text": "/resolve "}},
        ],
        SUBMENU_FOOTER,
    ]
}

FOLDER_MENU = {
    "inline_keyboard": [
        [
            {"text": "🗄 Choose media library", "callback_data": "menu:libraries"},
        ],
        [
            {"text": "📁 Current folder", "callback_data": "menu:folder"},
            {"text": "🗂 Pick existing", "callback_data": "menu:folders"},
        ],
        [
            {"text": "Clear selection", "callback_data": "menu:unsetfolder"},
        ],
        [
            {"text": "📋 Copy /setfolder", "copy_text": {"text": "/setfolder "}},
            {"text": "📋 Copy /usefolder", "copy_text": {"text": "/usefolder "}},
        ],
        [
            {
                "text": "📋 Copy /renamefolder",
                "copy_text": {"text": "/renamefolder "},
            },
        ],
        ADVANCED_SUBMENU_FOOTER,
    ]
}

SORTING_MENU = {
    "inline_keyboard": [
        [
            {"text": "Sort new files", "callback_data": "menu:sort_current"},
            {"text": "Sort latest", "callback_data": "menu:sort_latest"},
        ],
        [
            {"text": "Rename sorted files", "callback_data": "menu:resort_current"},
            {
                "text": "Fix episode metadata",
                "callback_data": "menu:fix_metadata_current",
            },
        ],
        [
            {"text": "Sorter status", "callback_data": "menu:sort_status"},
            {
                "text": "📋 Copy /sort_folder",
                "copy_text": {"text": "/sort_folder "},
            },
        ],
        ADVANCED_SUBMENU_FOOTER,
    ]
}

UNDO_MENU = {
    "inline_keyboard": [
        [
            {"text": "Sort history", "callback_data": "menu:sort_history"},
            {"text": "Recover current", "callback_data": "menu:recover_current"},
        ],
        [
            {"text": "One revision back", "callback_data": "menu:sort_back"},
            {"text": "One revision forward", "callback_data": "menu:sort_forward"},
        ],
        [
            {"text": "Undo latest batch", "callback_data": "menu:undo_sort_last"},
        ],
        [
            {
                "text": "📋 Copy /undo_sort_batch",
                "copy_text": {"text": "/undo_sort_batch "},
            },
        ],
        ADVANCED_SUBMENU_FOOTER,
    ]
}

JELLYFIN_MENU = {
    "inline_keyboard": [
        [
            {"text": "🔄 Scan library", "callback_data": "menu:jellyfin_scan"},
            {"text": "🟢 Connection status", "callback_data": "menu:jellyfin_status"},
        ],
        SUBMENU_FOOTER,
    ]
}

SERIES_MENU = {
    "inline_keyboard": [
        [
            {"text": "Choose series library", "callback_data": "menu:series_mode"},
        ],
        [
            {"text": "📁 Current folder", "callback_data": "menu:folder"},
            {"text": "🗂 Pick existing", "callback_data": "menu:folders"},
        ],
        [
            {"text": "Sort new files", "callback_data": "menu:sort_current"},
            {"text": "Current series", "callback_data": "menu:episodes"},
        ],
        [
            {"text": "All series", "callback_data": "menu:library_episodes"},
        ],
        ADVANCED_SUBMENU_FOOTER,
    ]
}

MOVIE_MENU = {
    "inline_keyboard": [
        [
            {"text": "Choose movie library", "callback_data": "menu:movie_mode"},
            {"text": "Series mode", "callback_data": "menu:series_mode"},
        ],
        [
            {"text": "Latest movie job", "callback_data": "menu:movie_current"},
            {"text": "Retry import", "callback_data": "menu:movie_import"},
        ],
        [
            {"text": "Cancel unprocessed movie", "callback_data": "menu:movie_cancel"},
        ],
        [
            {"text": "Undo latest movie", "callback_data": "menu:movie_undo_last"},
        ],
        [
            {
                "text": "Copy /movie_import",
                "copy_text": {"text": "/movie_import "},
            },
            {
                "text": "Copy /movie_undo_batch",
                "copy_text": {"text": "/movie_undo_batch "},
            },
        ],
        ADVANCED_SUBMENU_FOOTER,
    ]
}

IMDB_MENU = {
    "inline_keyboard": [
        [
            {
                "text": "📋 Copy /imdb_search",
                "copy_text": {"text": "/imdb_search "},
            },
        ],
        [
            {
                "text": "📋 Copy /imdb_fix_current",
                "copy_text": {"text": "/imdb_fix_current"},
            },
        ],
        ADVANCED_SUBMENU_FOOTER,
    ]
}

EPISODE_MENU = {
    "inline_keyboard": [
        [
            {"text": "Current series", "callback_data": "menu:episodes"},
            {"text": "All series", "callback_data": "menu:library_episodes"},
        ],
        [
            {
                "text": "📋 Copy /episodes NAME",
                "copy_text": {"text": "/episodes "},
            },
        ],
        SUBMENU_FOOTER,
    ]
}

BOT_MENU = {
    "inline_keyboard": [
        [
            {"text": "📊 Status", "callback_data": "menu:status"},
            {"text": "🆔 Chat ID", "callback_data": "menu:chatid"},
        ],
        [
            {"text": "📖 How to use", "callback_data": "menu:guide"},
            {"text": "❓ Command list", "callback_data": "menu:help"},
        ],
        [
            {"text": "🌐 Language", "callback_data": "menu:language"},
        ],
        SUBMENU_FOOTER,
    ]
}

ADVANCED_MENU = {
    "inline_keyboard": [
        [
            {"text": "📁 Folders", "callback_data": "nav:folders"},
            {"text": "🧹 Sorting", "callback_data": "nav:sorting"},
        ],
        [
            {"text": "↩️ Undo & Recovery", "callback_data": "nav:undo"},
            {"text": "🔎 Title Search", "callback_data": "nav:imdb"},
        ],
        [
            {"text": "📺 Series tools", "callback_data": "nav:series"},
            {"text": "🎬 Movie tools", "callback_data": "nav:movies"},
        ],
        SUBMENU_FOOTER,
    ]
}

CATEGORY_SUBMENUS = {
    "nav:downloads": ("Download commands:", DOWNLOAD_MENU),
    "nav:folders": ("Folder commands:", FOLDER_MENU),
    "nav:sorting": ("Sorting commands:", SORTING_MENU),
    "nav:undo": ("Undo and recovery commands:", UNDO_MENU),
    "nav:series": ("TV-series workflow:", SERIES_MENU),
    "nav:movies": ("Independent movie workflow:", MOVIE_MENU),
    "nav:jellyfin": ("Jellyfin commands:", JELLYFIN_MENU),
    "nav:imdb": ("Title-provider search commands:", IMDB_MENU),
    "nav:episodes": ("Episode inventory commands:", EPISODE_MENU),
    "nav:bot": ("Bot information and help:", BOT_MENU),
    "nav:advanced": ("Advanced commands:", ADVANCED_MENU),
}

REPLY_CATEGORY_ACTIONS = {
    "📥 Downloads": "nav:downloads",
    "📁 Folders": "nav:folders",
    "🧹 Sorting": "nav:sorting",
    "↩️ Undo & Recovery": "nav:undo",
    "🎬 Jellyfin": "nav:jellyfin",
    "🔎 Title Search": "nav:imdb",
    "📺 Episodes": "nav:episodes",
    "⚙️ Bot": "nav:bot",
    "🗄 Choose Library": "menu:libraries",
    "🧰 Advanced": "nav:advanced",
    "Series": "nav:series",
    "Movies": "nav:movies",
}

# Telegram immediately sends highlighted slash commands when tapped. In
# channels, switch_inline_query_current_chat (the only input-prefill button) is
# unsupported, so copy_text is the safe editable-template alternative.
HELP_COMMAND_TEMPLATES = {
    "inline_keyboard": [
        [
            {
                "text": "📋 Copy /setfolder",
                "copy_text": {"text": "/setfolder "},
            },
            {
                "text": "📋 Copy /renamefolder",
                "copy_text": {"text": "/renamefolder "},
            },
        ],
        [
            {
                "text": "📋 Copy /usefolder",
                "copy_text": {"text": "/usefolder "},
            }
        ],
        [
            {
                "text": "📋 Copy /remove",
                "copy_text": {"text": "/remove "},
            },
            {
                "text": "📋 Copy /resolve",
                "copy_text": {"text": "/resolve "},
            },
        ],
        [
            {
                "text": "📋 Copy /sort_folder",
                "copy_text": {"text": "/sort_folder "},
            },
            {
                "text": "📋 Copy /undo_sort_batch",
                "copy_text": {"text": "/undo_sort_batch "},
            },
        ],
        [
            {
                "text": "📋 Copy /episodes",
                "copy_text": {"text": "/episodes "},
            }
        ],
        [
            {
                "text": "📋 Copy /imdb_search",
                "copy_text": {"text": "/imdb_search "},
            },
            {
                "text": "📋 Copy /imdb_fix_current",
                "copy_text": {"text": "/imdb_fix_current"},
            },
        ],
        [
            {
                "text": "Copy /movie_import",
                "copy_text": {"text": "/movie_import "},
            },
            {
                "text": "Copy /movie_undo_batch",
                "copy_text": {"text": "/movie_undo_batch "},
            },
        ],
        [
            {
                "text": "🎛 Open main menu",
                "callback_data": "menu:open",
            }
        ],
    ]
}


class TelegramAPI:
    def __init__(self, config: Config, session: aiohttp.ClientSession):
        self.config = config
        self.session = session

    async def call(
        self,
        method: str,
        *,
        _request_timeout: aiohttp.ClientTimeout | None = None,
        **params: Any,
    ) -> Any:
        url = f"{self.config.api_root}/{method}"
        request_options: dict[str, Any] = {"data": params}
        if _request_timeout is not None:
            request_options["timeout"] = _request_timeout
        async with self.session.post(url, **request_options) as response:
            try:
                payload = await response.json()
            except Exception as exc:
                text = await response.text()
                raise RuntimeError(f"Invalid Local Bot API response: {text[:300]}") from exc
        if not payload.get("ok"):
            raise RuntimeError(payload.get("description", f"Bot API error: {method}"))
        return payload.get("result")

    async def send(
        self,
        chat_id: int,
        text: str,
        reply_markup: dict | None = None,
        *,
        message_thread_id: int | None = None,
        direct_messages_topic_id: int | None = None,
    ) -> Any:
        params: dict[str, str] = {
            "chat_id": str(chat_id),
            "text": text[:TELEGRAM_TEXT_LIMIT],
            "parse_mode": "HTML",
        }
        if message_thread_id is not None:
            params["message_thread_id"] = str(int(message_thread_id))
        if direct_messages_topic_id is not None:
            params["direct_messages_topic_id"] = str(
                int(direct_messages_topic_id)
            )
        if reply_markup is not None:
            params["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
        return await self.call("sendMessage", **params)

    async def edit(self, chat_id: int, message_id: int, text: str) -> Any:
        return await self.call(
            "editMessageText",
            chat_id=str(chat_id),
            message_id=str(message_id),
            text=text[:TELEGRAM_TEXT_LIMIT],
            parse_mode="HTML",
        )

    async def delete(self, chat_id: int, message_id: int) -> Any:
        return await self.call(
            "deleteMessage",
            chat_id=str(chat_id),
            message_id=str(message_id),
        )


class BotApp:
    def __init__(self, config: Config):
        self.config = config
        self.store = StateStore(config.data_path / "state.db")
        self.queue = QueueManager(self.store)
        self.session: aiohttp.ClientSession | None = None
        self.api: TelegramAPI | None = None
        self.downloader: DownloadManager | None = None
        self.jellyfin: JellyfinBridge | None = None
        self.ai_identifier: N8nMediaIdentifier | None = None
        self.sorter = SorterBridge(config, self.store)
        self.movie_sorter = MovieSorterBridge(config, self.store)
        self.catalog = EpisodeCatalog(config.allowed_video_extensions)
        self.imdb = ImdbFuzzySearchBridge(config)
        self.anilist = AniListSearchBridge(config)
        self.imdb_choices: dict[str, dict] = {}
        self.movie_choices: dict[str, dict] = {}
        self.movie_manual_pending: dict[int, int] = {}
        self.series_manual_pending: dict[int, int] = {}
        self.series_manual_groups: dict[int, list[dict]] = {}
        self.series_identification_batches: dict[
            tuple[int, int, str], dict[str, Any]
        ] = {}
        self.movie_identification_batches: dict[
            tuple[int, int, str], dict[str, Any]
        ] = {}
        self.background_tasks: set[asyncio.Task] = set()
        self.task_chat_ids: dict[asyncio.Task, int] = {}
        self.chat_types: dict[int, str] = {}

    @staticmethod
    def _provider_label(library: MediaLibrary) -> str:
        return "AniList" if library.metadata_provider == "anilist" else "IMDb"

    async def _search_metadata(
        self,
        library: MediaLibrary,
        query: str,
        *,
        media_type: str,
    ) -> tuple[list[dict], str, str]:
        """Search only the provider assigned to the selected library."""
        if library.metadata_provider == "anilist":
            results, source = await self.anilist.search(
                query, media_type=media_type
            )
            return results, source, "AniList"
        results, source = await self.imdb.search(query, media_type=media_type)
        return results, source, "IMDb"

    @staticmethod
    def _result_provider(result: dict, library: MediaLibrary) -> tuple[str, str]:
        provider = str(
            result.get("provider") or library.metadata_provider or "imdb"
        ).strip().casefold()
        if provider == "anilist":
            provider_id = str(
                result.get("provider_id") or result.get("anilist_id") or ""
            ).strip()
        else:
            provider = "imdb"
            provider_id = str(
                result.get("provider_id") or result.get("imdb_id") or ""
            ).strip()
        return provider, provider_id

    def track_task(
        self, awaitable: Any, name: str, chat_id: int | None = None
    ) -> asyncio.Task:
        """Start a background task and keep it visible until it finishes."""
        task = asyncio.create_task(awaitable, name=name)
        self.background_tasks.add(task)
        if chat_id is not None:
            self.task_chat_ids[task] = int(chat_id)

        def _done_callback(done_task: asyncio.Task) -> None:
            self.background_tasks.discard(done_task)
            self.task_chat_ids.pop(done_task, None)
            try:
                done_task.result()
            except asyncio.CancelledError:
                LOG.info("Background task cancelled: %s", done_task.get_name())
            except Exception:
                LOG.exception("Background task failed: %s", done_task.get_name())

        task.add_done_callback(_done_callback)
        return task

    async def shutdown(self) -> None:
        """Cancel tracked background tasks before closing the state database."""
        if not self.background_tasks:
            return
        for task in list(self.background_tasks):
            task.cancel()
        await asyncio.gather(*self.background_tasks, return_exceptions=True)
        self.background_tasks.clear()
        self.task_chat_ids.clear()

    async def run(self) -> None:
        timeout = aiohttp.ClientTimeout(total=None, connect=15, sock_read=60)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            self.session = session
            self.api = TelegramAPI(self.config, session)
            self.downloader = DownloadManager(
                self.config, self.queue, self.api.call, session
            )
            self.jellyfin = JellyfinBridge(self.config, self.store, session)
            self.ai_identifier = N8nMediaIdentifier(self.config, session)
            me = await self.api.call("getMe")
            LOG.info("Bot connected as @%s", me.get("username", "unknown"))
            try:
                await self.api.call(
                    "setMyCommands",
                    commands=json.dumps(BOT_COMMANDS, ensure_ascii=False),
                )
                LOG.info("Telegram command menu registered.")
            except Exception:
                # A menu failure must not stop queueing and downloads.
                LOG.exception("Could not register Telegram command menu")
            if not self.config.allowed_chat_ids:
                LOG.warning("allowed_chat_ids is empty; every chat can use the bot.")
            await self.poll()

    async def poll(self) -> None:
        assert self.api
        offset = int(self.store.get_setting("update_offset", "0") or 0)
        while True:
            try:
                updates = await self.api.call(
                    "getUpdates",
                    offset=str(offset),
                    timeout="30",
                    allowed_updates='["message","channel_post","callback_query"]',
                )
                offset = await self._process_update_batch(updates, offset)
            except asyncio.CancelledError:
                raise
            except Exception:
                LOG.exception("Polling error")
                await asyncio.sleep(3)

    async def _process_update_batch(self, updates: list[dict], offset: int) -> int:
        """Process each update independently so one bad update cannot block the rest."""
        for update in updates:
            try:
                update_id = int(update["update_id"])
            except (KeyError, TypeError, ValueError):
                LOG.error("Ignored a Telegram update without a valid update_id.")
                continue
            try:
                await self.handle_update(update)
            except asyncio.CancelledError:
                # Do not acknowledge an update interrupted by bot shutdown.
                raise
            except Exception:
                LOG.exception(
                    "Update %s failed and was skipped so polling can continue.",
                    update_id,
                )
            offset = max(offset, update_id + 1)
            try:
                self.store.set_setting("update_offset", str(offset))
            except Exception:
                # The in-memory offset still protects this running process.
                # A database failure is logged because a restart may replay it.
                LOG.exception("Could not persist Telegram update offset %s.", offset)
        return offset

    def allowed(self, chat_id: int) -> bool:
        return not self.config.allowed_chat_ids or chat_id in self.config.allowed_chat_ids

    async def handle_update(self, update: dict) -> None:
        callback = update.get("callback_query")
        message = (
            (callback.get("message") or {})
            if callback
            else (update.get("message") or update.get("channel_post") or {})
        )
        thread_id = message.get("message_thread_id")
        direct_topic = message.get("direct_messages_topic") or {}
        direct_topic_id = direct_topic.get("topic_id")
        thread_token = CURRENT_MESSAGE_THREAD_ID.set(
            int(thread_id) if thread_id is not None else None
        )
        direct_token = CURRENT_DIRECT_MESSAGES_TOPIC_ID.set(
            int(direct_topic_id) if direct_topic_id is not None else None
        )
        try:
            await self._handle_update_in_context(update, callback, message)
        finally:
            CURRENT_MESSAGE_THREAD_ID.reset(thread_token)
            CURRENT_DIRECT_MESSAGES_TOPIC_ID.reset(direct_token)

    async def _handle_update_in_context(
        self, update: dict, callback: dict | None, message: dict
    ) -> None:
        if callback:
            await self.handle_callback(callback)
            return
        if not message:
            return
        chat_id = int(message["chat"]["id"])
        self.chat_types[chat_id] = str(message["chat"].get("type", ""))
        if not self.allowed(chat_id):
            LOG.warning("Ignored unauthorized chat_id=%s", chat_id)
            return
        text = str(message.get("text", "")).strip()
        if text.startswith("/"):
            await self.handle_command(chat_id, text)
        elif reply_category_action(text, REPLY_CATEGORY_ACTIONS) or text in {
            "⚡ Quick Menu",
            "⚡ منوی سریع",
        }:
            await self.handle_reply_category(chat_id, text)
        elif text and self._chat_setting(chat_id, REMOVE_PROMPT_SETTING) == "1":
            await self._remove_review_item(chat_id, text)
        elif text and chat_id in self.series_manual_pending:
            pending_id = self.series_manual_pending.pop(chat_id)
            self.track_task(
                self._run_manual_series_identification(chat_id, pending_id, text),
                f"series-manual-identification:{chat_id}:{pending_id}",
                chat_id,
            )
        elif text and chat_id in self.movie_manual_pending:
            pending_id = self.movie_manual_pending.pop(chat_id)
            self.track_task(
                self._run_movie_search(
                    chat_id, pending_id, text, manual_query=True
                ),
                f"movie-imdb-search:{chat_id}:{pending_id}",
                chat_id,
            )
        else:
            await self.handle_media(chat_id, message)

    async def send(
        self,
        chat_id: int,
        text: str,
        reply_markup: dict | None = None,
        *,
        force_language: str | None = None,
    ) -> Any:
        assert self.api
        language = language_code(force_language or self._language(chat_id))
        text = translate_text(text, language)
        text = _telegram_html(text)
        reply_markup = translate_markup(reply_markup, language)
        try:
            return await self.api.send(
                chat_id,
                text,
                reply_markup,
                message_thread_id=CURRENT_MESSAGE_THREAD_ID.get(),
                direct_messages_topic_id=CURRENT_DIRECT_MESSAGES_TOPIC_ID.get(),
            )
        except Exception:
            LOG.exception("Could not send Telegram message")
            return None

    async def edit_message(self, chat_id: int, message_id: int, text: str) -> bool:
        """Edit one status message instead of posting repeated updates."""
        assert self.api
        text = translate_text(text, self._language(chat_id))
        text = _telegram_html(text)
        try:
            await self.api.edit(chat_id, message_id, text)
            return True
        except Exception:
            LOG.exception("Could not edit Telegram message %s", message_id)
            return False

    async def delete_message(self, chat_id: int, message_id: int) -> bool:
        assert self.api
        try:
            await self.api.delete(chat_id, message_id)
            return True
        except Exception:
            LOG.exception("Could not delete Telegram message %s", message_id)
            return False

    @staticmethod
    def _sent_message_id(result: Any) -> int | None:
        if not isinstance(result, dict):
            return None
        value = result.get("message_id")
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _language(self, chat_id: int) -> str:
        return language_code(
            self.store.get_setting(f"language:{chat_id}", "en")
        )

    def _chat_setting(self, chat_id: int, name: str, default: str = "") -> str:
        value = self.store.get_chat_setting(chat_id, name, default)
        if (
            name == "current_folder"
            and not self.store.has_chat_setting(chat_id, name)
            and self.config.default_target_folder
        ):
            return sanitize_folder_name(self.config.default_target_folder)
        return value

    def _set_chat_setting(self, chat_id: int, name: str, value: str) -> None:
        self.store.set_chat_setting(chat_id, name, value)

    def _download_review_state(self, chat_id: int) -> tuple[dict[int, int], int]:
        """Load stable per-batch IDs without exposing database queue IDs."""
        raw = self._chat_setting(chat_id, DOWNLOAD_REVIEW_SETTING)
        if not raw:
            return {}, 1
        try:
            values = json.loads(raw)
            # Migrate the short-lived list format used by the previous build.
            if isinstance(values, list):
                mapping = {
                    index: int(pending_id)
                    for index, pending_id in enumerate(values, start=1)
                    if int(pending_id) > 0
                }
                return mapping, len(mapping) + 1
            if not isinstance(values, dict):
                return {}, 1
            raw_items = values.get("items")
            if not isinstance(raw_items, dict):
                return {}, 1
            mapping: dict[int, int] = {}
            used_pending_ids: set[int] = set()
            for display_id, pending_id in raw_items.items():
                display_number = int(display_id)
                actual_id = int(pending_id)
                if (
                    display_number > 0
                    and actual_id > 0
                    and actual_id not in used_pending_ids
                ):
                    mapping[display_number] = actual_id
                    used_pending_ids.add(actual_id)
            next_id = max(
                int(values.get("next_id") or 1),
                max(mapping, default=0) + 1,
                1,
            )
            return mapping, next_id
        except (TypeError, ValueError, json.JSONDecodeError):
            LOG.warning("Ignored invalid download review mapping for chat %s.", chat_id)
            return {}, 1

    def _save_download_review_state(
        self, chat_id: int, mapping: dict[int, int], next_id: int
    ) -> None:
        self._set_chat_setting(
            chat_id,
            DOWNLOAD_REVIEW_SETTING,
            json.dumps(
                {
                    "next_id": max(int(next_id), 1),
                    "items": {
                        str(display_id): int(pending_id)
                        for display_id, pending_id in sorted(mapping.items())
                    },
                }
            ),
        )

    def _assign_download_batch_ids(
        self, chat_id: int, items: list[dict]
    ) -> list[tuple[int, dict]]:
        """Keep each movie/episode ID stable until this batch is confirmed."""
        mapping, next_id = self._download_review_state(chat_id)
        current_pending_ids = {int(item["pending_id"]) for item in items}
        mapping = {
            display_id: pending_id
            for display_id, pending_id in mapping.items()
            if pending_id in current_pending_ids
        }
        display_by_pending = {
            pending_id: display_id for display_id, pending_id in mapping.items()
        }
        for item in items:
            pending_id = int(item["pending_id"])
            if pending_id not in display_by_pending:
                display_by_pending[pending_id] = next_id
                mapping[next_id] = pending_id
                next_id += 1
        self._save_download_review_state(chat_id, mapping, next_id)
        return [
            (display_by_pending[int(item["pending_id"])], item)
            for item in items
        ]

    def _download_review_ids(self, chat_id: int) -> list[int]:
        """Return internal IDs ordered by their temporary per-batch IDs."""
        mapping, _ = self._download_review_state(chat_id)
        return [mapping[display_id] for display_id in sorted(mapping)]

    def _download_batch_id_for_pending(
        self, chat_id: int, pending_id: int
    ) -> int | None:
        mapping, _ = self._download_review_state(chat_id)
        return next(
            (
                display_id
                for display_id, actual_id in mapping.items()
                if actual_id == int(pending_id)
            ),
            None,
        )

    def _clear_download_review(self, chat_id: int) -> None:
        self._set_chat_setting(chat_id, DOWNLOAD_REVIEW_SETTING, "")
        self._set_chat_setting(chat_id, REMOVE_PROMPT_SETTING, "")

    def _selected_library(
        self, chat_id: int, media_kind: str | None = None
    ) -> MediaLibrary:
        key = self._chat_setting(chat_id, "current_library_key")
        try:
            return self.config.library(key or None, media_kind)
        except ValueError:
            # A removed/renamed key from an older deployment must not make the
            # bot unusable. Fall back to a configured library of the requested type.
            return self.config.library(None, media_kind)

    def _library_picker_markup(self, media_kind: str | None = None) -> dict:
        libraries = (
            self.config.libraries_for(media_kind)
            if media_kind
            else self.config.media_libraries
        )
        rows = [
            [{
                "text": (
                    ("📺 " if library.media_kind == "series" else "🎬 ")
                    + library.name
                ),
                "callback_data": f"library:{library.key}",
            }]
            for library in libraries
        ]
        rows.append([{"text": "⬅️ Main menu", "callback_data": "nav:categories"}])
        return {"inline_keyboard": rows}

    async def _require_library_kind(
        self, chat_id: int, media_kind: str
    ) -> MediaLibrary | None:
        current = self._selected_library(chat_id)
        if current.media_kind == media_kind:
            return current
        await self.send(
            chat_id,
            f"Choose a {media_kind} library before using this command:",
            self._library_picker_markup(media_kind),
        )
        return None

    async def _select_library(self, chat_id: int, library: MediaLibrary) -> None:
        previous = self._chat_setting(chat_id, "current_library_key")
        self._set_chat_setting(chat_id, "current_library_key", library.key)
        self.store.set_setting(f"media_mode:{chat_id}", library.media_kind)
        self.movie_manual_pending.pop(chat_id, None)
        if previous != library.key:
            # A folder name is meaningful only inside its own library root.
            self._set_chat_setting(chat_id, "current_folder", "")
            self._set_chat_setting(chat_id, "download_confirmation", "")
            self._clear_download_review(chat_id)
        await self.send(
            chat_id,
            f"Library selected: {_important(library.name)}\n"
            f"Mode: {library.media_kind}\n"
            f"Path: {_important(library.path)}\n\n"
            + (
                "Choose or create a series folder before sending episodes."
                if library.media_kind == "series"
                else "Send movie files; each queued movie will remember this library."
            ),
            SERIES_MENU if library.media_kind == "series" else MOVIE_MENU,
        )

    async def handle_callback(self, query: dict) -> None:
        assert self.api
        message = query.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        try:
            await self.api.call("answerCallbackQuery", callback_query_id=query["id"])
        except Exception:
            LOG.exception("Could not answer callback query")
        if chat_id is None or not self.allowed(int(chat_id)):
            return
        self.chat_types[int(chat_id)] = str(chat.get("type", ""))
        action = str(query.get("data", ""))
        handlers = {
            "menu:libraries": self.cmd_libraries,
            "menu:folder": self.cmd_folder,
            "menu:folders": self.cmd_folders,
            "menu:unsetfolder": self.cmd_unsetfolder,
            "menu:queue": self.cmd_queue,
            "menu:remove": self.cmd_remove,
            "menu:clearqueue": self.cmd_clearqueue,
            "menu:download": self.cmd_download,
            "menu:confirm": self.cmd_confirm,
            "menu:status": self.cmd_status,
            "menu:chatid": self.cmd_chatid,
            "menu:cancel": self.cmd_cancel,
            "menu:sort_current": self.cmd_sort_current,
            "menu:sort_latest": self.cmd_sort_latest,
            "menu:sort_status": self.cmd_sort_status,
            "menu:resort_current": self.cmd_resort_current,
            "menu:sort_history": self.cmd_sort_history,
            "menu:sort_back": self.cmd_sort_back,
            "menu:sort_forward": self.cmd_sort_forward,
            "menu:recover_current": self.cmd_recover_current,
            "menu:fix_metadata_current": self.cmd_fix_metadata_current,
            "menu:undo_sort_last": self.cmd_undo_sort_last,
            "menu:jellyfin_scan": self.cmd_jellyfin_scan,
            "menu:jellyfin_status": self.cmd_jellyfin_status,
            "menu:episodes": self.cmd_episodes,
            "menu:library_episodes": self.cmd_library_episodes,
            "menu:movie_mode": self.cmd_movie_mode,
            "menu:series_mode": self.cmd_series_mode,
            "menu:movie_current": self.cmd_movie_current,
            "menu:movie_cancel": self.cmd_movie_cancel,
            "menu:movie_import": self.cmd_movie_import,
            "menu:movie_undo_last": self.cmd_movie_undo_last,
            "menu:language": self.cmd_language,
            "menu:open": self.cmd_quick_menu,
            "menu:guide": self.cmd_guide,
            "menu:help": self.cmd_help,
        }
        if action in {"language:en", "language:fa"}:
            selected = action.partition(":")[2]
            self.store.set_setting(f"language:{int(chat_id)}", selected)
            confirmation = (
                "Language changed to Persian."
                if selected == "fa"
                else "Language changed to English."
            )
            await self.send(int(chat_id), confirmation)
            await self.cmd_menu(int(chat_id), "")
            return
        if action.startswith("library:"):
            key = action.partition(":")[2]
            try:
                library = self.config.library(key)
            except ValueError as exc:
                await self.send(int(chat_id), str(exc))
                return
            await self._select_library(int(chat_id), library)
            return
        if action == "guide:en":
            self.store.set_setting(f"language:{int(chat_id)}", "en")
            await self.send(
                int(chat_id),
                GUIDE_EN,
                GUIDE_LANGUAGE_MENU,
                force_language="en",
            )
            return
        if action == "guide:fa":
            self.store.set_setting(f"language:{int(chat_id)}", "fa")
            await self.send(
                int(chat_id),
                GUIDE_FA,
                GUIDE_LANGUAGE_MENU,
                force_language="fa",
            )
            return
        if action == "nav:categories":
            await self.send(
                int(chat_id),
                "Choose a command category:",
                CATEGORY_MENU,
            )
            return
        submenu = CATEGORY_SUBMENUS.get(action)
        if submenu:
            title, markup = submenu
            await self.send(int(chat_id), title, markup)
            return
        if action == "menu:folder_help":
            await self.send(
                int(chat_id),
                "To set a folder:\n/setfolder My Anime\n\n"
                "To rename the current folder:\n/renamefolder Correct Anime Name\n\n"
                "The buttons below copy editable command templates. Paste one, then add the name.",
                HELP_COMMAND_TEMPLATES,
            )
            return
        if action == "menu:undo_batch_help":
            await self.send(
                int(chat_id),
                "To undo a specific sorter batch:\n"
                "/undo_sort_batch BATCH_ID\n\n"
                "Example:\n/undo_sort_batch 20260628-024900-a1b2c3d4",
                CHANNEL_MENU,
            )
            return
        if action == "menu:imdb_help":
            await self.send(
                int(chat_id),
                "To find the official name and create a Jellyfin folder:\n/imdb_search dr ston\n\n"
                "To search and safely rename the current folder:\n/imdb_fix_current\n\n"
                "You can also provide a different search phrase:\n"
                "/imdb_fix_current dr ston",
                HELP_COMMAND_TEMPLATES,
            )
            return
        if action.startswith("libraryconflict:"):
            try:
                _, decision, pending_text = action.split(":", 2)
                pending_id = int(pending_text)
            except (ValueError, TypeError):
                return
            item = self.store.get_item(pending_id, chat_id=int(chat_id))
            if not item or item.get("status") != "waiting_overwrite":
                await self.send(
                    int(chat_id), "This replacement decision is no longer active."
                )
                return
            if decision == "cancel":
                self.store.update_item(
                    pending_id,
                    status="awaiting_identification",
                    target_folder=None,
                    overwrite_policy=None,
                    error="Download cancelled at library identity conflict.",
                )
                markup = (
                    self._movie_identification_markup(pending_id)
                    if item.get("media_kind") == "movie"
                    else self._series_identification_markup(pending_id)
                )
                await self.send(
                    int(chat_id),
                    "Download cancelled for this file. It remains undownloaded "
                    "so you can correct its identity or remove it from the queue.",
                    markup,
                )
                return
            if decision != "replace":
                return
            queued_conflict = (
                self._movie_queue_conflict_item(
                    int(chat_id), pending_id, item, str(item.get("library_key") or "")
                )
                if item.get("media_kind") == "movie"
                else self._series_queue_conflict_item(item)
            )
            if queued_conflict is not None and queued_conflict.get("status") in {
                "queued", "failed", "waiting_overwrite"
            }:
                self.queue.set_status(
                    int(queued_conflict["pending_id"]),
                    "skipped",
                    f"Superseded by approved replacement #{pending_id}.",
                )
            self.queue.set_status(
                pending_id,
                "queued",
                None,
                overwrite_policy="replace_library",
            )
            await self.send(
                int(chat_id),
                "Replacement approved. The old library media will be backed up "
                "for rollback before the new file is installed.\n\nNext: /download",
            )
            return
        if action.startswith("movieidentify:"):
            _, method, pending_text = action.split(":", 2)
            try:
                pending_id = int(pending_text)
            except ValueError:
                return
            item = self._movie_item_for_chat(pending_id, int(chat_id))
            if not item or item.get("status") != "awaiting_identification":
                await self.send(
                    int(chat_id),
                    "This movie choice is no longer active. Send the movie again if needed.",
                )
                return
            if method == "manual":
                self.movie_manual_pending[int(chat_id)] = pending_id
                await self.send(
                    int(chat_id),
                    "Send the movie title, preferably with its year.\n"
                    "Example: Interstellar 2014",
                )
                return
            if method != "filename":
                return
            query_text = movie_query_from_filename(item["original_filename"])
            if len(query_text) < 2:
                self.movie_manual_pending[int(chat_id)] = pending_id
                await self.send(
                    int(chat_id),
                    "The filename did not contain a useful movie title. Send the "
                    "title manually, preferably with its year.",
                )
                return
            self.track_task(
                self._run_movie_search(
                    int(chat_id), pending_id, query_text, manual_query=False
                ),
                f"movie-imdb-search:{chat_id}:{pending_id}",
                int(chat_id),
            )
            return
        if action.startswith("moviemanual:"):
            try:
                pending_id = int(action.partition(":")[2])
            except ValueError:
                return
            item = self._movie_item_for_chat(pending_id, int(chat_id))
            if not item or item.get("status") != "awaiting_identification":
                await self.send(int(chat_id), "This movie queue item was not found.")
                return
            self.movie_manual_pending[int(chat_id)] = pending_id
            await self.send(
                int(chat_id),
                "Send a different movie title, preferably with its year.",
            )
            return
        if action.startswith("moviepick:"):
            token = action.partition(":")[2]
            choice = self.movie_choices.get(token)
            if (
                not choice
                or time.time() - choice["created_at"] > 600
                or not (
                    item := self._movie_item_for_chat(
                        choice["pending_id"], int(chat_id)
                    )
                )
                or item.get("status") != "awaiting_identification"
            ):
                await self.send(
                    int(chat_id), "This movie result expired. Start the search again."
                )
                return
            await self._offer_movie_confirmation(int(chat_id), token, choice)
            return
        if action.startswith("movieconfirm:"):
            token = action.partition(":")[2]
            choice = self.movie_choices.pop(token, None)
            if not choice or time.time() - choice["created_at"] > 600:
                await self.send(int(chat_id), "This movie confirmation expired.")
                return
            await self._confirm_movie_choice(int(chat_id), choice)
            return
        if action.startswith("moviecancel:"):
            try:
                pending_id = int(action.partition(":")[2])
            except ValueError:
                return
            item = self._movie_item_for_chat(pending_id, int(chat_id))
            removed = bool(
                item
                and item.get("status") == "awaiting_identification"
                and self.queue.remove(pending_id, chat_id=int(chat_id))
            )
            if self.movie_manual_pending.get(int(chat_id)) == pending_id:
                self.movie_manual_pending.pop(int(chat_id), None)
            await self.send(
                int(chat_id),
                "Movie queue item cancelled." if removed else "Movie could not be cancelled.",
            )
            return
        if action.startswith("seriesidentify:"):
            _, method, pending_text = action.split(":", 2)
            try:
                pending_id = int(pending_text)
            except ValueError:
                return
            item = self._series_item_for_chat(pending_id, int(chat_id))
            if not item or item.get("status") != "awaiting_identification":
                await self.send(
                    int(chat_id),
                    "This episode is no longer waiting for identification.",
                )
                return
            if method == "manual":
                self.series_manual_pending[int(chat_id)] = pending_id
                await self.send(
                    int(chat_id),
                    "Send the series details in this format:\n"
                    "Series Title | Season | Episode\n"
                    "Example: Dr. Stone | 4 | 25",
                )
                return
            if method != "current":
                return
            current_library = self._selected_library(int(chat_id), "series")
            current_folder = self._chat_setting(int(chat_id), "current_folder")
            if (
                not current_folder
                or str(item.get("library_key") or "") != current_library.key
            ):
                await self.send(
                    int(chat_id),
                    "No compatible current series folder is selected. Open "
                    "Advanced → Folders, select one, then press this button again.",
                    self._series_identification_markup(pending_id),
                )
                return
            self.series_manual_pending.pop(int(chat_id), None)
            detected = detect_episode(str(item.get("original_filename") or ""))
            updates: dict[str, Any] = {
                "target_folder": current_folder,
                "status": "queued",
                "download_filename": None,
                "overwrite_policy": None,
                "error": None,
            }
            if detected:
                updates["series_season"], updates["series_episode"] = detected
            candidate = {**item, **updates}
            existing = self._series_library_conflict_path(candidate)
            queued_conflict = self._series_queue_conflict_item(candidate)
            self.store.update_item(pending_id, **updates)
            updated = self.store.get_item(pending_id, chat_id=int(chat_id))
            if updated and (existing is not None or queued_conflict is not None):
                await self._hold_for_library_conflict(
                    int(chat_id),
                    updated,
                    existing=existing,
                    queued=queued_conflict,
                )
                return
            await self.send(
                int(chat_id),
                "This episode will use the current folder without AI:\n"
                f"{_important(current_folder)}\n\n"
                "Use /download to review the destination.",
            )
            return
        if action.startswith("seriescancel:"):
            try:
                pending_id = int(action.partition(":")[2])
            except ValueError:
                return
            item = self._series_item_for_chat(pending_id, int(chat_id))
            removed = bool(
                item
                and item.get("status") == "awaiting_identification"
                and self.queue.remove(pending_id, chat_id=int(chat_id))
            )
            if self.series_manual_pending.get(int(chat_id)) == pending_id:
                self.series_manual_pending.pop(int(chat_id), None)
            await self.send(
                int(chat_id),
                "Episode queue item cancelled."
                if removed
                else "Episode could not be cancelled.",
            )
            return
        if action.startswith("folders:"):
            try:
                page = max(0, int(action.partition(":")[2]))
            except ValueError:
                page = 0
            await self._send_folder_picker(int(chat_id), page)
            return
        if action.startswith("pickfolder:"):
            token = action.partition(":")[2]
            matches = [
                folder for folder in self._existing_series_folders(int(chat_id))
                if self._folder_token(folder.name) == token
            ]
            if len(matches) != 1:
                await self.send(
                    int(chat_id),
                    "This folder choice is no longer valid. Send /folders again.",
                )
                return
            await self._select_existing_folder(int(chat_id), matches[0])
            return
        if action.startswith("imdbpick:"):
            token = action.partition(":")[2]
            choice = self.imdb_choices.get(token)
            if (
                not choice
                or int(choice.get("chat_id", 0)) != int(chat_id)
                or time.time() - choice["created_at"] > 600
            ):
                await self.send(
                    int(chat_id),
                    "This title-search result expired. Run /imdb_search again.",
                )
                return
            await self._offer_folder_confirmation(int(chat_id), token, choice)
            return
        if action.startswith("folderconfirm:"):
            token = action.partition(":")[2]
            choice = self.imdb_choices.get(token)
            if (
                not choice
                or int(choice.get("chat_id", 0)) != int(chat_id)
                or time.time() - choice["created_at"] > 600
            ):
                await self.send(int(chat_id), "This confirmation expired. Please try again.")
                return
            self.imdb_choices.pop(token, None)
            if choice.get("mode") == "queue":
                await self._confirm_series_queue_choice(int(chat_id), choice)
                return
            choice_library_key = str(choice.get("library_key") or "")
            if (
                choice_library_key
                and self._selected_library(int(chat_id), "series").key
                != choice_library_key
            ):
                await self.send(
                    int(chat_id),
                    "The selected library changed after this title search. "
                    "Nothing was changed. Start the folder search again.",
                )
                return
            if choice["mode"] == "rename":
                source_folder = str(choice.get("source_folder", ""))
                current_folder = self._chat_setting(int(chat_id), "current_folder")
                library_key = str(choice.get("library_key") or "")
                current_library = self._selected_library(int(chat_id), "series")
                if not source_folder or current_folder != source_folder:
                    await self.send(
                        int(chat_id),
                        "The selected folder changed after this title search. "
                        "Nothing was renamed. Run /imdb_fix_current again.",
                    )
                    return
                if library_key and current_library.key != library_key:
                    await self.send(
                        int(chat_id),
                        "The selected library changed after this title search. "
                        "Nothing was renamed. Run /imdb_fix_current again.",
                    )
                    return
                if not self.config.target_path(
                    source_folder, library_key or current_library.key
                ).is_dir():
                    await self.send(
                        int(chat_id),
                        "The folder used for this title search no longer exists. "
                        "Nothing was renamed.",
                    )
                    return
                await self.cmd_renamefolder(int(chat_id), choice["folder_name"])
            else:
                await self._commit_folder(int(chat_id), choice["folder_name"])
            return
        if action.startswith("foldercancel:"):
            token = action.partition(":")[2]
            choice = self.imdb_choices.get(token)
            if choice and int(choice.get("chat_id", 0)) == int(chat_id):
                self.imdb_choices.pop(token, None)
            if choice and choice.get("mode") == "queue":
                entries = choice.get("queue_entries")
                if not isinstance(entries, list) or not entries:
                    entries = [choice]
                pending_id = int(entries[0].get("pending_id") or 0)
                self.series_manual_pending[int(chat_id)] = pending_id
                self.series_manual_groups[int(chat_id)] = entries
                await self.send(
                    int(chat_id),
                    "Send the correct series title. The detected season and "
                    "episode numbers will be kept for all matching files.",
                )
            else:
                await self.send(int(chat_id), "Folder change cancelled.", CHANNEL_MENU)
            return
        handler = handlers.get(action)
        if handler:
            await handler(int(chat_id), "")

    async def handle_reply_category(self, chat_id: int, text: str) -> None:
        """Open an inline submenu selected from the persistent reply keyboard."""
        if text in {"⚡ Quick Menu", "⚡ منوی سریع"}:
            await self.cmd_quick_menu(chat_id, "")
            return
        action = reply_category_action(text, REPLY_CATEGORY_ACTIONS)
        if action == "menu:libraries":
            await self.cmd_libraries(chat_id, "")
            return
        submenu = CATEGORY_SUBMENUS.get(action or "")
        if submenu:
            title, markup = submenu
            await self.send(chat_id, title, markup)

    async def handle_media(self, chat_id: int, message: dict) -> None:
        media = message.get("video") or message.get("document")
        if not media:
            return
        filename = media.get("file_name")
        mime = str(media.get("mime_type", "")).lower()
        if not filename and message.get("video"):
            extension = mimetypes.guess_extension(mime) or ".mp4"
            filename = f"telegram_video_{media.get('file_unique_id', media['file_id'])}{extension}"
        extension = Path(filename or "").suffix.lower()
        if extension not in self.config.allowed_video_extensions and not mime.startswith("video/"):
            await self.send(chat_id, "This video file is not supported and was not added to the queue.")
            return
        if extension not in self.config.allowed_video_extensions:
            await self.send(chat_id, "This file extension is not allowed in allowed_video_extensions.")
            return
        try:
            filename = validate_original_filename(filename)
        except ValueError as exc:
            await self.send(chat_id, f"The file was not added to the queue: {exc}")
            return
        caption = str(message.get("caption") or "").strip()
        if self._media_mode(chat_id) == "movie":
            await self._queue_movie_for_identification(
                chat_id, message, media, filename, caption
            )
            return
        library = self._selected_library(chat_id, "series")
        if self.config.n8n_agent_enabled:
            await self._queue_series_for_identification(
                chat_id, message, media, filename, caption, library
            )
            return
        pending_id = self.queue.add(
            message_id=int(message["message_id"]),
            chat_id=chat_id,
            file_id=media["file_id"],
            file_unique_id=media["file_unique_id"],
            original_filename=filename,
            file_size=media.get("file_size"),
            received_at=datetime.now(timezone.utc).isoformat(),
            target_folder=self._chat_setting(chat_id, "current_folder"),
            library_key=library.key,
            media_kind="series",
        )
        if pending_id is None:
            await self.send(chat_id, "This video is already in the queue.")
        else:
            target_folder = self._chat_setting(chat_id, "current_folder")
            item_number = self._queue_display_number(
                chat_id, pending_id, target_folder, library.key
            )
            notice = self._episode_arrival_notice(
                chat_id, filename, target_folder, pending_id, library.key
            )
            await self.send(
                chat_id,
                f"Video added to the queue. Item {item_number} for this folder."
                + (f"\n{notice}" if notice else ""),
            )

    def _media_mode(self, chat_id: int) -> str:
        key = self._chat_setting(chat_id, "current_library_key")
        if key:
            try:
                return self.config.library(key).media_kind
            except ValueError:
                pass
        mode = self.store.get_setting(f"media_mode:{chat_id}", "series")
        return "movie" if mode == "movie" else "series"

    async def _queue_movie_for_identification(
        self,
        chat_id: int,
        message: dict,
        media: dict,
        filename: str,
        caption: str = "",
    ) -> None:
        if not self.config.movies_configured:
            await self.send(
                chat_id,
                "Movie mode is not configured yet. Set jellyfin_movie_library_path "
                "and movie_staging_path in config.json, then restart the bot.",
            )
            return
        library = self._selected_library(chat_id, "movie")
        pending_id = self.queue.add(
            message_id=int(message["message_id"]),
            chat_id=chat_id,
            file_id=media["file_id"],
            file_unique_id=media["file_unique_id"],
            original_filename=filename,
            file_size=media.get("file_size"),
            received_at=datetime.now(timezone.utc).isoformat(),
            target_folder=None,
            library_key=library.key,
            media_kind="movie",
            status="awaiting_identification",
        )
        if pending_id is None:
            await self.send(chat_id, "This movie is already registered in the queue.")
            return
        if self.config.n8n_agent_enabled:
            sender = message.get("from") or {}
            sender_id = int(sender.get("id") or chat_id)
            key = (chat_id, sender_id, library.key)
            batch = self.movie_identification_batches.setdefault(
                key, {"items": [], "task": None}
            )
            batch["items"].append((pending_id, caption))
            previous = batch.get("task")
            if isinstance(previous, asyncio.Task) and not previous.done():
                previous.cancel()
            batch["task"] = self.track_task(
                self._flush_movie_identification_batch(key),
                f"movie-identification-batch:{chat_id}:{sender_id}:{library.key}",
                chat_id,
            )
        else:
            await self.send(
                chat_id,
                f"Movie received but not downloaded yet.\n"
                f"Filename: {_important(filename)}\n\n"
                "How should I identify it?",
                self._movie_identification_markup(pending_id),
            )

    async def _flush_movie_identification_batch(
        self, key: tuple[int, int, str]
    ) -> None:
        """Identify a burst of movies while keeping Telegram output compact."""
        await asyncio.sleep(MOVIE_BATCH_WINDOW_SECONDS)
        batch = self.movie_identification_batches.pop(key, None)
        if not batch:
            return
        chat_id = key[0]
        items = list(batch.get("items") or [])
        if not items:
            return
        status_result = await self.send(
            chat_id, f"Identifying {len(items)} movie(s)…"
        )
        status_message_id = self._sent_message_id(status_result)

        # Free AI endpoints are commonly rate-limited, so identify sequentially.
        for pending_id, caption in items:
            await self._run_ai_movie_identification(
                chat_id, int(pending_id), str(caption), quiet=True
            )

        ready_items: list[dict] = []
        needs_attention = 0
        for pending_id, _ in items:
            item = self.store.get_item(int(pending_id), chat_id=chat_id)
            if item and item.get("status") == "queued":
                ready_items.append(item)
            else:
                needs_attention += 1
        ready = len(ready_items)
        if ready and not needs_attention:
            final_text = f"✅ {ready} movie(s) ready."
        else:
            final_text = (
                f"Checked {len(items)} movie(s): {ready} ready, "
                f"{needs_attention} need attention."
            )
        for item in ready_items[:12]:
            label = item.get("target_folder") or item["original_filename"]
            final_text += f"\n• {_important(label)}"
        if len(ready_items) > 12:
            final_text += f"\n• +{len(ready_items) - 12} more movie(s)"
        if ready:
            final_text += "\n\nNext: /download"
        elif needs_attention:
            final_text += "\n\nResolve the movie choices above before downloading."
        if status_message_id is not None:
            if await self.edit_message(chat_id, status_message_id, final_text):
                return
        await self.send(chat_id, final_text)

    @staticmethod
    def _movie_identification_markup(pending_id: int) -> dict:
        return {
            "inline_keyboard": [
                [{
                    "text": "Search using filename",
                    "callback_data": f"movieidentify:filename:{pending_id}",
                }],
                [{
                    "text": "Enter name manually",
                    "callback_data": f"movieidentify:manual:{pending_id}",
                }],
                [{
                    "text": "Cancel movie",
                    "callback_data": f"moviecancel:{pending_id}",
                }],
            ]
        }

    @staticmethod
    def _series_identification_markup(pending_id: int) -> dict:
        return {
            "inline_keyboard": [
                [{
                    "text": "Enter series details manually",
                    "callback_data": f"seriesidentify:manual:{pending_id}",
                }],
                [{
                    "text": "Use current folder without AI",
                    "callback_data": f"seriesidentify:current:{pending_id}",
                }],
                [{
                    "text": "Cancel episode",
                    "callback_data": f"seriescancel:{pending_id}",
                }],
            ]
        }

    def _ai_identifier(self) -> N8nMediaIdentifier:
        if self.ai_identifier is None or not self.ai_identifier.configured:
            raise RuntimeError(
                "AI identification is enabled but the n8n webhook client is not ready."
            )
        return self.ai_identifier

    async def _run_ai_movie_identification(
        self,
        chat_id: int,
        pending_id: int,
        caption: str = "",
        *,
        quiet: bool = False,
    ) -> None:
        item = self._movie_item_for_chat(pending_id, chat_id)
        if not item or item.get("status") != "awaiting_identification":
            return
        try:
            result = await self._ai_identifier().identify(
                chat_id=chat_id,
                media_kind="movie",
                library_key=str(item.get("library_key") or ""),
                filename=str(item["original_filename"]),
                caption=caption,
            )
        except Exception as exc:
            LOG.warning("n8n movie identification failed for #%s: %s", pending_id, exc)
            await self.send(
                chat_id,
                f"AI identification is unavailable for this movie: {exc}\n\n"
                "Use filename search or enter the title manually.",
                self._movie_identification_markup(pending_id),
            )
            return

        if result.needs_user_input or not result.title_query:
            self.movie_manual_pending[chat_id] = pending_id
            await self.send(
                chat_id,
                "AI needs more information for this movie.\n"
                f"{result.question or 'Send the full movie title and year.'}\n\n"
                "Reply with the full movie title, preferably followed by its year.",
            )
            return

        query = result.title_query
        if result.year is not None:
            query = f"{query} {result.year}"
        # Keep the AI suggestion attached to this exact source file while IMDb
        # choices are shown. This makes a title/year mismatch visible instead
        # of letting two unrelated uploads look like the same movie job.
        self.store.update_item(
            pending_id,
            movie_title=result.title_query,
            movie_year=result.year,
        )
        if not quiet:
            await self.send(
                chat_id,
                "AI filename suggestion:\n"
                f"Title query: {_important(result.title_query)}\n"
                f"Year: {result.year or 'unknown'}\n"
                f"Confidence: {result.confidence:.0%}\n\n"
                "Checking the existing IMDb title tool now...",
            )
        await self._run_movie_search(
            chat_id,
            pending_id,
            query,
            manual_query=False,
            ai_identity=result,
            quiet=quiet,
        )

    async def _queue_series_for_identification(
        self,
        chat_id: int,
        message: dict,
        media: dict,
        filename: str,
        caption: str,
        library: MediaLibrary,
    ) -> None:
        pending_id = self.queue.add(
            message_id=int(message["message_id"]),
            chat_id=chat_id,
            file_id=media["file_id"],
            file_unique_id=media["file_unique_id"],
            original_filename=filename,
            file_size=media.get("file_size"),
            received_at=datetime.now(timezone.utc).isoformat(),
            target_folder=None,
            library_key=library.key,
            media_kind="series",
            status="awaiting_identification",
        )
        if pending_id is None:
            await self.send(chat_id, "This video is already registered in the queue.")
            return
        sender = message.get("from") or {}
        sender_id = int(sender.get("id") or chat_id)
        key = (chat_id, sender_id, library.key)
        batch = self.series_identification_batches.setdefault(
            key, {"items": [], "task": None}
        )
        batch["items"].append((pending_id, caption))
        previous = batch.get("task")
        if isinstance(previous, asyncio.Task) and not previous.done():
            previous.cancel()
        batch["task"] = self.track_task(
            self._flush_series_identification_batch(key),
            f"series-identification-batch:{chat_id}:{sender_id}:{library.key}",
            chat_id,
        )

    async def _flush_series_identification_batch(
        self, key: tuple[int, int, str]
    ) -> None:
        """Identify a burst of episodes with one compact Telegram status."""
        await asyncio.sleep(SERIES_BATCH_WINDOW_SECONDS)
        batch = self.series_identification_batches.pop(key, None)
        if not batch:
            return
        chat_id = key[0]
        items = list(batch.get("items") or [])
        if not items:
            return
        status_result = await self.send(
            chat_id, f"Identifying {len(items)} episode(s)…"
        )
        status_message_id = self._sent_message_id(status_result)

        # Free AI endpoints are commonly rate-limited. Sequential requests keep
        # a multi-episode Telegram upload reliable while still batching its UI.
        for pending_id, caption in items:
            await self._run_ai_series_identification(
                chat_id, int(pending_id), str(caption)
            )

        ready_items: list[dict] = []
        needs_attention = 0
        for pending_id, _ in items:
            item = self.store.get_item(int(pending_id), chat_id=chat_id)
            if item and item.get("status") == "queued":
                ready_items.append(item)
            else:
                needs_attention += 1
        ready = len(ready_items)
        if ready and not needs_attention:
            final_text = f"✅ {ready} episode(s) ready."
        else:
            final_text = (
                f"Checked {len(items)} episode(s): {ready} ready, "
                f"{needs_attention} need attention."
            )
        episode_lines = self._compact_ready_episode_lines(ready_items)
        if episode_lines:
            final_text += "\n" + "\n".join(episode_lines)
        if ready and not needs_attention:
            final_text += "\n\nNext: /download"
        elif needs_attention:
            final_text += "\n\nResolve the items above before downloading."
        if status_message_id is not None:
            if await self.edit_message(chat_id, status_message_id, final_text):
                return
        await self.send(chat_id, final_text)

    @staticmethod
    def _compact_ready_episode_lines(items: list[dict]) -> list[str]:
        """Summarize useful identity details without returning to message spam."""
        groups: dict[str, dict[str, Any]] = {}
        for item in items:
            folder_name = str(item.get("target_folder") or "").strip()
            if not folder_name:
                continue
            try:
                season = int(item.get("series_season") or 1)
                episode = int(item.get("series_episode") or 0)
            except (TypeError, ValueError):
                continue
            if season < 1 or episode < 1:
                continue
            group = groups.setdefault(
                folder_name,
                {
                    "title": str(item.get("series_title") or "").strip()
                    or _series_file_title(folder_name),
                    "episodes": [],
                },
            )
            marker = (season, episode)
            if marker not in group["episodes"]:
                group["episodes"].append(marker)

        lines: list[str] = []
        grouped = list(groups.values())
        for group in grouped[:8]:
            episodes = sorted(group["episodes"])
            labels = [f"S{season:02d}E{episode:02d}" for season, episode in episodes[:12]]
            if len(episodes) > 12:
                labels.append(f"+{len(episodes) - 12} more")
            lines.append(
                "• " + _important(f"{group['title']}: {', '.join(labels)}")
            )
        if len(grouped) > 8:
            lines.append(f"• +{len(grouped) - 8} more series")
        return lines

    def _series_item_for_chat(
        self, pending_id: int, chat_id: int
    ) -> dict | None:
        item = self.store.get_item(pending_id, chat_id=chat_id)
        if (
            not item
            or item.get("media_kind") != "series"
            or int(item.get("chat_id") or 0) != chat_id
        ):
            return None
        return item

    async def _run_ai_series_identification(
        self, chat_id: int, pending_id: int, caption: str = ""
    ) -> None:
        item = self._series_item_for_chat(pending_id, chat_id)
        if not item or item.get("status") != "awaiting_identification":
            return
        try:
            result = await self._ai_identifier().identify(
                chat_id=chat_id,
                media_kind="series",
                library_key=str(item.get("library_key") or ""),
                filename=str(item["original_filename"]),
                caption=caption,
            )
        except Exception as exc:
            LOG.warning("n8n series identification failed for #%s: %s", pending_id, exc)
            await self.send(
                chat_id,
                f"AI identification is unavailable for this episode: {exc}\n\n"
                "Use manual series details or the already selected current folder.",
                self._series_identification_markup(pending_id),
            )
            return

        if (
            result.needs_user_input
            or not result.title_query
            or result.episode is None
        ):
            await self.send(
                chat_id,
                "Could not identify this episode: "
                f"{_important(item['original_filename'])}\n"
                "Choose manual details or use the current folder.",
                self._series_identification_markup(pending_id),
            )
            return

        await self._continue_series_identification(chat_id, pending_id, result)

    async def _continue_series_identification(
        self,
        chat_id: int,
        pending_id: int,
        result: MediaIdentification,
    ) -> None:
        item = self._series_item_for_chat(pending_id, chat_id)
        if not item or item.get("status") != "awaiting_identification":
            return
        query = str(result.title_query or "").strip()
        if result.year is not None:
            query = f"{query} {result.year}"
        await self._run_imdb_search(
            chat_id,
            query,
            "queue",
            pending_id=pending_id,
            identity=result,
        )

    async def _confirm_series_queue_choice(
        self, chat_id: int, choice: dict, *, notify: bool = True
    ) -> None:
        library_key = str(choice.get("library_key") or "")
        entries = choice.get("queue_entries")
        if not isinstance(entries, list) or not entries:
            entries = [choice]
        has_waiting_item = any(
            (
                item := self._series_item_for_chat(
                    int(entry.get("pending_id") or 0), chat_id
                )
            )
            and item.get("status") == "awaiting_identification"
            and str(item.get("library_key") or "") == library_key
            for entry in entries
        )
        if not has_waiting_item:
            if notify:
                await self.send(
                    chat_id,
                    "No waiting episodes could use this series folder.",
                )
            return
        try:
            folder_name = sanitize_folder_name(str(choice["folder_name"]))
            folder = self.config.target_path(folder_name, library_key)
            folder.mkdir(parents=True, exist_ok=True)
        except (OSError, ValueError) as exc:
            await self.send(chat_id, f"Could not prepare the series destination: {exc}")
            return

        queued = 0
        conflicts = 0
        ready_items: list[dict] = []
        for entry in entries:
            pending_id = int(entry.get("pending_id") or 0)
            item = self._series_item_for_chat(pending_id, chat_id)
            if not item or item.get("status") != "awaiting_identification":
                continue
            if str(item.get("library_key") or "") != library_key:
                LOG.warning(
                    "Episode #%s changed library while IMDb identification was running.",
                    pending_id,
                )
                continue
            season = int(entry.get("series_season") or 1)
            episode = int(entry.get("series_episode") or 0)
            if episode < 1:
                continue
            candidate = {
                **item,
                "target_folder": folder_name,
                "library_key": library_key,
                "series_season": season,
                "series_episode": episode,
                "download_filename": None,
            }
            existing = self._series_library_conflict_path(candidate)
            queued_conflict = self._series_queue_conflict_item(candidate)
            self.store.update_item(
                pending_id,
                target_folder=folder_name,
                series_title=str(entry.get("series_title") or "") or None,
                series_year=entry.get("series_year"),
                series_season=season,
                series_episode=episode,
                download_filename=None,
                imdb_id=str(entry.get("imdb_id") or choice.get("imdb_id") or "")
                or None,
                metadata_provider=str(
                    entry.get("metadata_provider")
                    or choice.get("metadata_provider")
                    or ""
                )
                or None,
                metadata_provider_id=str(
                    entry.get("metadata_provider_id")
                    or choice.get("metadata_provider_id")
                    or ""
                )
                or None,
                status=(
                    "waiting_overwrite"
                    if existing is not None or queued_conflict is not None
                    else "queued"
                ),
                overwrite_policy=None,
                error=None,
            )
            updated = self.store.get_item(pending_id, chat_id=chat_id)
            if updated and (existing is not None or queued_conflict is not None):
                conflicts += 1
                await self._hold_for_library_conflict(
                    chat_id,
                    updated,
                    existing=existing,
                    queued=queued_conflict,
                )
                continue
            queued += 1
            if updated:
                ready_items.append(updated)

        if not queued:
            if notify and not conflicts:
                await self.send(
                    chat_id,
                    "No waiting episodes could use this series folder.",
                )
            return
        self.series_manual_pending.pop(chat_id, None)
        if self._chat_setting(chat_id, "current_library_key") == library_key:
            self._set_chat_setting(chat_id, "current_folder", folder_name)
        if notify:
            episode_lines = self._compact_ready_episode_lines(ready_items)
            details = "\n" + "\n".join(episode_lines) if episode_lines else ""
            await self.send(
                chat_id,
                f"✅ {queued} episode(s) ready."
                f"{details}\n\nNext: /download",
            )

    async def _run_manual_series_identification(
        self, chat_id: int, pending_id: int, text: str
    ) -> None:
        item = self._series_item_for_chat(pending_id, chat_id)
        if not item or item.get("status") != "awaiting_identification":
            await self.send(chat_id, "This episode is no longer waiting for identification.")
            return
        grouped_entries = self.series_manual_groups.pop(chat_id, [])
        parts = [part.strip() for part in text.split("|")]
        title = ""
        season: int | None = None
        episode: int | None = None
        if grouped_entries and len(parts) == 1 and parts[0]:
            title = parts[0]
            first = grouped_entries[0]
            season = int(first.get("series_season") or 1)
            episode = int(first.get("series_episode") or 0)
        if len(parts) == 3 and parts[0]:
            try:
                season = int(parts[1])
                episode = int(parts[2])
            except ValueError:
                season = episode = None
            title = parts[0]
        if not title or not season or not episode or season < 1 or episode < 1:
            if grouped_entries:
                self.series_manual_groups[chat_id] = grouped_entries
                self.series_manual_pending[chat_id] = pending_id
            await self.send(
                chat_id,
                "I could not read those series details. Use exactly:\n"
                "Series Title | Season | Episode\nExample: Dr. Stone | 4 | 25",
                self._series_identification_markup(pending_id),
            )
            return
        if grouped_entries:
            for entry in grouped_entries:
                grouped_pending_id = int(entry.get("pending_id") or 0)
                grouped_item = self._series_item_for_chat(
                    grouped_pending_id, chat_id
                )
                if (
                    not grouped_item
                    or grouped_item.get("status") != "awaiting_identification"
                ):
                    continue
                result = MediaIdentification(
                    title_query=title[:300],
                    season=int(entry.get("series_season") or 1),
                    episode=int(entry.get("series_episode") or 0),
                    year=None,
                    confidence=1.0,
                    needs_user_input=False,
                    question=None,
                )
                await self._continue_series_identification(
                    chat_id, grouped_pending_id, result
                )
            return
        result = MediaIdentification(
            title_query=title[:300],
            season=season,
            episode=episode,
            year=None,
            confidence=1.0,
            needs_user_input=False,
            question=None,
        )
        await self._continue_series_identification(chat_id, pending_id, result)

    def _movie_item_for_chat(self, pending_id: int, chat_id: int) -> dict | None:
        item = self.store.get_item(pending_id, chat_id=chat_id)
        if (
            not item
            or item.get("media_kind") != "movie"
            or int(item.get("chat_id") or 0) != chat_id
        ):
            return None
        return item

    @staticmethod
    def _movie_identity_key(value: dict) -> str:
        provider = str(value.get("metadata_provider") or "").strip().casefold()
        provider_id = str(value.get("metadata_provider_id") or "").strip().casefold()
        if provider and provider_id:
            return f"{provider}:{provider_id}"
        imdb_id = str(value.get("imdb_id") or "").strip().casefold()
        if imdb_id:
            return f"imdb:{imdb_id}"
        folder_name = _normalized_title(
            str(value.get("folder_name") or value.get("target_folder") or "")
        )
        return f"folder:{folder_name}" if folder_name else ""

    @staticmethod
    def _automatic_movie_result(
        identity: MediaIdentification,
        results: list[dict],
        filename_year: int | None = None,
    ) -> dict | None:
        """Accept only an exact, high-confidence top result without a click."""
        if (
            not results
            or identity.confidence < 0.85
            or identity.year is None
        ):
            return None
        result = results[0]
        if _normalized_title(str(result.get("title") or "")) != _normalized_title(
            str(identity.title_query or "")
        ):
            return None
        try:
            score = float(result.get("score") or 0)
        except (TypeError, ValueError):
            return None
        if score < 90:
            return None
        try:
            if int(result.get("year")) != int(identity.year):
                return None
        except (TypeError, ValueError):
            return None
        if filename_year is not None and int(identity.year) != filename_year:
            return None
        return result

    @staticmethod
    def _movie_choice_from_result(
        pending_id: int,
        result: dict,
        source: str,
        library: MediaLibrary,
    ) -> dict:
        provider, provider_id = BotApp._result_provider(result, library)
        return {
            "pending_id": pending_id,
            "title": str(result["title"]),
            "year": result.get("year"),
            "imdb_id": str(result.get("imdb_id") or ""),
            "metadata_provider": provider,
            "metadata_provider_id": provider_id,
            "folder_name": str(result["folder_name"]),
            "source": source,
            "created_at": time.time(),
        }

    def _movie_library_conflict_path(
        self,
        library_key: str,
        folder_name: str,
        imdb_id: str = "",
        metadata_provider: str = "",
        metadata_provider_id: str = "",
    ) -> Path | None:
        """Return an existing final video without changing the library."""
        try:
            library = self.config.library(library_key or None, "movie")
            expected = self.config.movie_target_path(folder_name, library.key)
        except ValueError:
            return None
        candidates = [expected]
        wanted_id = str(imdb_id or "").strip().casefold()
        wanted_provider = str(metadata_provider or "").strip().casefold()
        wanted_provider_id = str(metadata_provider_id or "").strip().casefold()
        if wanted_id or (wanted_provider and wanted_provider_id):
            try:
                for folder in library.path.iterdir():
                    if not folder.is_dir() or folder == expected:
                        continue
                    match = IMDB_FOLDER_ID_RE.search(folder.name)
                    anilist_match = ANILIST_FOLDER_ID_RE.search(folder.name)
                    imdb_match = bool(
                        wanted_id
                        and match
                        and match.group(1).casefold() == wanted_id
                    )
                    provider_match = bool(
                        wanted_provider == "anilist"
                        and wanted_provider_id
                        and anilist_match
                        and anilist_match.group(1).casefold() == wanted_provider_id
                    )
                    if imdb_match or provider_match:
                        candidates.append(folder)
            except OSError:
                pass
        for folder in candidates:
            try:
                videos = sorted(
                    item.name
                    for item in folder.iterdir()
                    if item.is_file()
                    and item.suffix.lower() in self.config.allowed_video_extensions
                )
            except OSError:
                continue
            if videos:
                return folder / videos[0]
        return None

    def _movie_library_conflict(
        self,
        library_key: str,
        folder_name: str,
        imdb_id: str = "",
        metadata_provider: str = "",
        metadata_provider_id: str = "",
    ) -> str | None:
        existing = self._movie_library_conflict_path(
            library_key,
            folder_name,
            imdb_id,
            metadata_provider,
            metadata_provider_id,
        )
        return (
            f"already in the library as {existing.name}"
            if existing is not None
            else None
        )

    def _movie_queue_conflict_item(
        self, chat_id: int, pending_id: int, choice: dict, library_key: str
    ) -> dict | None:
        wanted = self._movie_identity_key(choice)
        if not wanted:
            return None
        active = self.store.list_items(
            ("queued", "failed", "downloading", "completed", "movie_import_failed"),
            chat_id=chat_id,
        )
        for item in active:
            if int(item.get("pending_id") or 0) == pending_id:
                continue
            if item.get("media_kind") != "movie":
                continue
            if str(item.get("library_key") or "") != library_key:
                continue
            if self._movie_identity_key(item) == wanted:
                return item
        return None

    def _movie_queue_conflict(
        self, chat_id: int, pending_id: int, choice: dict, library_key: str
    ) -> str | None:
        item = self._movie_queue_conflict_item(
            chat_id, pending_id, choice, library_key
        )
        return (
            "the same movie is already pending in this chat"
            if item is not None
            else None
        )

    @staticmethod
    def _library_conflict_markup(pending_id: int) -> dict:
        return {
            "inline_keyboard": [
                [{
                    "text": "Replace existing",
                    "callback_data": f"libraryconflict:replace:{pending_id}",
                }],
                [{
                    "text": "Cancel download",
                    "callback_data": f"libraryconflict:cancel:{pending_id}",
                }],
            ]
        }

    def _series_episode_identity(self, item: dict) -> tuple[int, int] | None:
        try:
            season = int(item.get("series_season") or 0)
            episode = int(item.get("series_episode") or 0)
        except (TypeError, ValueError):
            season = episode = 0
        if season > 0 and episode > 0:
            return season, episode
        return detect_episode(
            str(item.get("download_filename") or item.get("original_filename") or "")
        )

    def _series_library_conflict_path(self, item: dict) -> Path | None:
        detected = self._series_episode_identity(item)
        folder_name = str(item.get("target_folder") or "").strip()
        if not detected or not folder_name:
            return None
        try:
            folder = self.config.target_path(
                folder_name, str(item.get("library_key") or "") or None
            )
        except ValueError:
            return None
        existing = self.catalog.contains(folder, *detected)
        return existing.path if existing else None

    def _series_queue_conflict_item(self, item: dict) -> dict | None:
        detected = self._series_episode_identity(item)
        if not detected:
            return None
        pending_id = int(item.get("pending_id") or 0)
        chat_id = int(item.get("chat_id") or 0)
        library_key = str(item.get("library_key") or "")
        folder_name = str(item.get("target_folder") or "")
        for other in self.store.list_items(
            ("queued", "failed", "downloading", "completed"), chat_id=chat_id
        ):
            if int(other.get("pending_id") or 0) == pending_id:
                continue
            if other.get("media_kind", "series") != "series":
                continue
            if str(other.get("library_key") or "") != library_key:
                continue
            if str(other.get("target_folder") or "") != folder_name:
                continue
            if self._series_episode_identity(other) == detected:
                return other
        return None

    async def _hold_for_library_conflict(
        self,
        chat_id: int,
        item: dict,
        *,
        existing: Path | None = None,
        queued: dict | None = None,
    ) -> None:
        pending_id = int(item["pending_id"])
        incoming = str(item.get("original_filename") or "unknown")
        final_name = self._final_saved_filename(item)
        if existing is not None:
            conflict_line = f"Existing Jellyfin file: {_important(existing.name)}"
            error = f"library conflict: {existing}"
        elif queued is not None:
            conflict_line = (
                "Already queued: "
                f"{_important(queued.get('original_filename') or 'unknown')}"
            )
            error = f"queue conflict with internal job {queued['pending_id']}"
        else:
            conflict_line = "Another file already uses this destination."
            error = "library destination conflict"
        self.store.update_item(
            pending_id,
            status="waiting_overwrite",
            overwrite_policy=None,
            error=error,
        )
        await self.send(
            chat_id,
            "⚠️ This destination is already in use.\n"
            f"Incoming: {_important(incoming)}\n"
            f"Would be saved as: {_important(final_name)}\n"
            f"{conflict_line}\n\n"
            "Replace archives the old media for rollback. If this is the wrong "
            "title, cancel and identify the file again.",
            self._library_conflict_markup(pending_id),
        )

    async def _confirm_movie_choice(
        self, chat_id: int, choice: dict, *, notify: bool = True
    ) -> bool:
        item = self._movie_item_for_chat(int(choice["pending_id"]), chat_id)
        if not item or item.get("status") != "awaiting_identification":
            if notify:
                await self.send(chat_id, "This movie is no longer waiting for a name.")
            return False
        try:
            folder_name = sanitize_folder_name(str(choice["folder_name"]))
            library = self.config.library(
                str(item.get("library_key") or ""), "movie"
            )
        except ValueError as exc:
            await self.send(chat_id, f"Could not prepare the movie destination: {exc}")
            return False
        self.store.update_item(
            int(item["pending_id"]),
            target_folder=folder_name,
            movie_title=choice["title"],
            movie_year=choice.get("year"),
            imdb_id=choice.get("imdb_id") or None,
            metadata_provider=choice.get("metadata_provider") or None,
            metadata_provider_id=choice.get("metadata_provider_id") or None,
            status="awaiting_identification",
            overwrite_policy=None,
            error=None,
        )
        updated = self.store.get_item(int(item["pending_id"]), chat_id=chat_id)
        assert updated is not None
        existing = self._movie_library_conflict_path(
            library.key,
            folder_name,
            str(choice.get("imdb_id") or ""),
            str(choice.get("metadata_provider") or ""),
            str(choice.get("metadata_provider_id") or ""),
        )
        if existing is not None and existing.parent.name != folder_name:
            folder_name = existing.parent.name
            self.store.update_item(
                int(item["pending_id"]), target_folder=folder_name
            )
            updated["target_folder"] = folder_name
        queued = self._movie_queue_conflict_item(
            chat_id, int(item["pending_id"]), updated, library.key
        )
        self.movie_choices = {
            token: saved
            for token, saved in self.movie_choices.items()
            if int(saved.get("pending_id") or 0) != int(item["pending_id"])
        }
        if existing is not None or queued is not None:
            await self._hold_for_library_conflict(
                chat_id, updated, existing=existing, queued=queued
            )
            return False
        self.store.update_item(
            int(item["pending_id"]), status="queued", error=None
        )
        if notify:
            await self.send(
                chat_id,
                f"✅ Movie ready: {_important(folder_name)}\n\nNext: /download",
                MOVIE_MENU,
            )
        return True

    @staticmethod
    def _manual_movie_identity(query: str) -> tuple[str, int | None, str]:
        value = re.sub(r"\s+", " ", query).strip()
        if not value:
            raise ValueError("Enter a movie title, optionally followed by its year.")
        explicit_year = re.fullmatch(
            r"(.+?)\s*[\[(]((?:18|19|20)\d{2})[\])]\s*", value
        )
        trailing_year = re.fullmatch(
            r"(.+?)\s+((?:18|19|20)\d{2})\s*", value
        )
        if explicit_year:
            title_text = explicit_year.group(1).strip()
            year = int(explicit_year.group(2))
        elif (
            trailing_year
            and int(trailing_year.group(2)) <= datetime.now().year + 5
        ):
            title_text = trailing_year.group(1).strip()
            year = int(trailing_year.group(2))
        else:
            title_text = value
            year = None
        # A title can legitimately end in a four-digit number (for example,
        # "Blade Runner 2049" or "1917"). Treat it as a year only when it is
        # plausible, unless parentheses/brackets explicitly mark it as a year.
        if year is not None and year < 1878:
            raise ValueError("The movie year is outside the supported range.")
        title = sanitize_folder_name(title_text)
        folder_name = title + (f" ({year})" if year is not None else "")
        return title, year, folder_name

    async def _run_movie_search(
        self,
        chat_id: int,
        pending_id: int,
        query: str,
        *,
        manual_query: bool,
        ai_identity: MediaIdentification | None = None,
        quiet: bool = False,
    ) -> None:
        item = self._movie_item_for_chat(pending_id, chat_id)
        if not item or item.get("status") != "awaiting_identification":
            await self.send(chat_id, "This movie is no longer waiting for identification.")
            return
        try:
            library = self.config.library(
                str(item.get("library_key") or ""), "movie"
            )
            provider_label = self._provider_label(library)
            if not quiet:
                await self.send(
                    chat_id,
                    f"Searching {provider_label} movies for: {_important(query)}",
                )
            results, source, provider_label = await self._search_metadata(
                library, query, media_type="movie"
            )
        except Exception as exc:
            provider_label = locals().get("provider_label", "metadata")
            LOG.warning("Optional %s movie search failed: %s", provider_label, exc)
            if manual_query:
                await self._offer_manual_movie_fallback(
                    chat_id,
                    pending_id,
                    query,
                    f"{provider_label} search is unavailable: {exc}",
                )
            else:
                self.movie_manual_pending[chat_id] = pending_id
                await self.send(
                    chat_id,
                    f"{provider_label} filename search is unavailable: {exc}\n\n"
                    "Send the movie title manually. The bot will let you use that "
                    f"exact name if {provider_label} remains unavailable.",
                )
            return
        if not results:
            if manual_query:
                await self._offer_manual_movie_fallback(
                    chat_id,
                    pending_id,
                    query,
                    f"{provider_label} did not return a movie result.",
                )
            else:
                self.movie_manual_pending[chat_id] = pending_id
                await self.send(
                    chat_id,
                    f"{provider_label} did not recognize the filename. Send the movie title "
                    "manually, preferably with its year.",
                )
            return

        if ai_identity is not None:
            automatic = self._automatic_movie_result(
                ai_identity,
                results,
                _release_year_from_filename(str(item["original_filename"])),
            )
            if automatic is not None:
                choice = self._movie_choice_from_result(
                    pending_id, automatic, source, library
                )
                await self._confirm_movie_choice(
                    chat_id, choice, notify=False
                )
                return

        now = time.time()
        self.movie_choices = {
            key: value for key, value in self.movie_choices.items()
            if now - value["created_at"] <= 600
        }
        rows = []
        for result in results:
            token = uuid.uuid4().hex[:16]
            self.movie_choices[token] = self._movie_choice_from_result(
                pending_id, result, source, library
            )
            rows.append([{
                "text": (
                    f"{str(result['title'])[:32]} ({result.get('year') or '?'}) "
                    f"· {result.get('score', '?')}%"
                ),
                "callback_data": f"moviepick:{token}",
            }])
        rows.append([{
            "text": "Search with a different name",
            "callback_data": f"moviemanual:{pending_id}",
        }])
        rows.append([{
            "text": "Cancel movie",
            "callback_data": f"moviecancel:{pending_id}",
        }])
        await self.send(
            chat_id,
            "Choose the correct movie result for this file:\n"
            f"Incoming: {_important(item['original_filename'])}\n"
            + (
                "Detected: "
                + _important(
                    f"{item.get('movie_title')} "
                    f"({item.get('movie_year') or '?'})"
                )
                + "\n"
                if item.get("movie_title")
                else ""
            )
            + f"Source: {provider_label} {source}",
            {"inline_keyboard": rows},
        )

    async def _offer_manual_movie_fallback(
        self, chat_id: int, pending_id: int, query: str, reason: str
    ) -> None:
        item = self._movie_item_for_chat(pending_id, chat_id)
        library = self.config.library(
            str((item or {}).get("library_key") or ""), "movie"
        )
        provider_label = self._provider_label(library)
        try:
            title, year, folder_name = self._manual_movie_identity(query)
        except ValueError as exc:
            self.movie_manual_pending[chat_id] = pending_id
            await self.send(
                chat_id,
                f"{reason}\nThe manual name is not valid: {exc}\n"
                "Send another title.",
            )
            return
        token = uuid.uuid4().hex[:16]
        choice = {
            "pending_id": pending_id,
            "title": title,
            "year": year,
            "imdb_id": "",
            "metadata_provider": library.metadata_provider,
            "metadata_provider_id": "",
            "folder_name": folder_name,
            "source": f"Manual name ({provider_label} unavailable)",
            "created_at": time.time(),
        }
        self.movie_choices[token] = choice
        await self.send(chat_id, reason)
        await self._offer_movie_confirmation(chat_id, token, choice)

    async def _offer_movie_confirmation(
        self, chat_id: int, token: str, choice: dict
    ) -> None:
        extension = ""
        item = self._movie_item_for_chat(choice["pending_id"], chat_id)
        incoming = "unknown"
        detected = ""
        mismatch = ""
        if item:
            extension = Path(item["original_filename"]).suffix
            incoming = str(item["original_filename"])
            if item.get("movie_title"):
                detected = (
                    "\nDetected: "
                    + _important(
                        f"{item['movie_title']} "
                        f"({item.get('movie_year') or '?'})"
                    )
                )
            filename_year = _release_year_from_filename(incoming)
            selected_year = choice.get("year")
            if (
                filename_year is not None
                and selected_year is not None
                and int(selected_year) != filename_year
            ):
                mismatch += (
                    f"\n⚠️ The incoming filename contains year {filename_year}, "
                    f"but the selected movie is {selected_year}."
                )
        if item and item.get("movie_title"):
            same_title = _normalized_title(str(item["movie_title"])) == _normalized_title(
                str(choice["title"])
            )
            same_year = not item.get("movie_year") or (
                int(item["movie_year"]) == int(choice.get("year") or 0)
            )
            if not (same_title and same_year):
                mismatch += "\n⚠️ The selected metadata identity differs from the detected title/year."
        provider = str(choice.get("metadata_provider") or "imdb").casefold()
        provider_label = "AniList" if provider == "anilist" else "IMDb"
        provider_id = str(
            choice.get("metadata_provider_id") or choice.get("imdb_id") or ""
        )
        await self.send(
            chat_id,
            "Confirm this movie identity:\n"
            f"Incoming: {_important(incoming)}"
            f"{detected}{mismatch}\n\n"
            f"Title: {_important(choice['title'])}\n"
            f"Year: {choice.get('year') or 'not specified'}\n"
            f"{provider_label}: {provider_id or 'not available'}\n\n"
            f"Folder: {_important(choice['folder_name'])}\n"
            f"File: {_important(str(choice['folder_name']) + extension)}",
            {
                "inline_keyboard": [
                    [{
                        "text": "Confirm movie",
                        "callback_data": f"movieconfirm:{token}",
                    }],
                    [{
                        "text": "Search manually",
                        "callback_data": f"moviemanual:{choice['pending_id']}",
                    }],
                    [{
                        "text": "Cancel",
                        "callback_data": f"moviecancel:{choice['pending_id']}",
                    }],
                ]
            },
        )

    async def cmd_movie_mode(self, chat_id: int, _: str) -> None:
        if not self.config.movies_configured:
            await self.send(
                chat_id,
                "Movie mode is disabled. Configure jellyfin_movie_library_path "
                "and movie_staging_path in config.json, then restart the bot.",
            )
            return
        libraries = self.config.libraries_for("movie")
        if len(libraries) == 1:
            await self._select_library(chat_id, libraries[0])
            return
        await self.send(
            chat_id,
            "Choose which movie library should receive new movies:",
            self._library_picker_markup("movie"),
        )

    async def cmd_series_mode(self, chat_id: int, _: str) -> None:
        libraries = self.config.libraries_for("series")
        if len(libraries) == 1:
            await self._select_library(chat_id, libraries[0])
            return
        await self.send(
            chat_id,
            "Choose which series library should receive new episodes:",
            self._library_picker_markup("series"),
        )

    async def cmd_libraries(self, chat_id: int, _: str) -> None:
        current = self._selected_library(chat_id)
        await self.send(
            chat_id,
            f"Current library: {_important(current.name)}\n"
            f"Path: {_important(current.path)}\n\n"
            "Choose a destination. Selecting one also changes Series/Movie mode:",
            self._library_picker_markup(),
        )

    async def cmd_use_library(self, chat_id: int, argument: str) -> None:
        query = argument.strip().casefold()
        if not query:
            await self.cmd_libraries(chat_id, "")
            return
        matches = [
            library
            for library in self.config.media_libraries
            if query in {library.key.casefold(), library.name.casefold()}
        ]
        if len(matches) != 1:
            await self.send(
                chat_id,
                "Unknown library. Use /libraries and choose a button.",
                self._library_picker_markup(),
            )
            return
        await self._select_library(chat_id, matches[0])

    async def cmd_movie_current(self, chat_id: int, _: str) -> None:
        latest = self.store.latest_movie_item(chat_id=chat_id)
        mode = self._media_mode(chat_id)
        if not latest:
            await self.send(chat_id, f"Current mode: {mode}\nNo movie job exists yet.")
            return
        markup = (
            self._movie_identification_markup(int(latest["pending_id"]))
            if latest.get("status") == "awaiting_identification"
            else MOVIE_MENU
        )
        batch_id = self._download_batch_id_for_pending(
            chat_id, int(latest["pending_id"])
        )
        batch_line = (
            f"Current download-batch ID: #{batch_id}\n"
            if batch_id is not None
            else ""
        )
        await self.send(
            chat_id,
            f"Current mode: {mode}\n"
            f"{batch_line}"
            f"Status: {latest['status']}\n"
            f"Original file: {_important(latest['original_filename'])}\n"
            "Movie: "
            + _important(
                latest.get("target_folder") or "waiting for identification"
            ),
            markup,
        )

    async def cmd_movie_cancel(self, chat_id: int, _: str) -> None:
        latest = self.store.latest_movie_item(
            ("awaiting_identification", "queued", "failed", "waiting_overwrite"),
            chat_id=chat_id,
        )
        if not latest:
            await self.send(chat_id, "There is no removable current movie job.")
            return
        pending_id = int(latest["pending_id"])
        removed = self.queue.remove(pending_id, chat_id=chat_id)
        if self.movie_manual_pending.get(chat_id) == pending_id:
            self.movie_manual_pending.pop(chat_id, None)
        await self.send(
            chat_id,
            "Current movie job cancelled." if removed else "The movie job could not be cancelled.",
        )

    def _queue_display_number(
        self,
        chat_id: int,
        pending_id: int,
        target_folder: str,
        library_key: str = "",
    ) -> int:
        """Return a friendly per-folder number while keeping pending_id stable."""
        same_folder = [
            item for item in self.queue.pending(chat_id)
            if (item.get("target_folder") or "") == (target_folder or "")
            and (
                not library_key
                or (
                    item.get("library_key")
                    or self.config.default_series_library_key
                ) == library_key
            )
        ]
        for index, item in enumerate(same_folder, start=1):
            if int(item["pending_id"]) == pending_id:
                return index
        return len(same_folder) + 1

    def _episode_arrival_notice(
        self,
        chat_id: int,
        filename: str,
        target_folder: str,
        pending_id: int,
        library_key: str,
    ) -> str:
        detected = detect_episode(filename)
        if not detected or not target_folder:
            return ""
        season, episode = detected
        existing = self.catalog.contains(
            self.config.target_path(target_folder, library_key), season, episode
        )
        if existing:
            return (
                f"⚠️ S{season:02d}E{episode:02d} already exists in the library:\n"
                f"{existing.path.name}"
            )
        for queued in self.queue.pending(chat_id):
            if queued["pending_id"] == pending_id:
                continue
            if queued.get("target_folder") != target_folder:
                continue
            if (
                queued.get("library_key")
                or self.config.default_series_library_key
            ) != library_key:
                continue
            if detect_episode(queued["original_filename"]) == detected:
                return (
                    f"⚠️ S{season:02d}E{episode:02d} is already queued."
                )
        return f"🆕 New episode detected: S{season:02d}E{episode:02d}"

    async def handle_command(self, chat_id: int, text: str) -> None:
        command, _, argument = text.partition(" ")
        command = command.split("@", 1)[0].lower()
        argument = argument.strip()
        handlers = {
            "/start": self.cmd_start, "/help": self.cmd_help, "/menu": self.cmd_menu,
            "/guide": self.cmd_guide,
            "/language": self.cmd_language,
            "/chatid": self.cmd_chatid,
            "/libraries": self.cmd_libraries,
            "/use_library": self.cmd_use_library,
            "/setfolder": self.cmd_setfolder, "/folder": self.cmd_folder,
            "/folders": self.cmd_folders, "/usefolder": self.cmd_usefolder,
            "/renamefolder": self.cmd_renamefolder,
            "/unsetfolder": self.cmd_unsetfolder, "/queue": self.cmd_queue,
            "/clearqueue": self.cmd_clearqueue, "/remove": self.cmd_remove,
            "/download": self.cmd_download, "/confirm_download": self.cmd_confirm,
            "/status": self.cmd_status, "/cancel": self.cmd_cancel,
            "/resolve": self.cmd_resolve, "/sort_current": self.cmd_sort_current,
            "/sort_latest": self.cmd_sort_latest, "/sort_folder": self.cmd_sort_folder,
            "/sort_status": self.cmd_sort_status,
            "/resort_current": self.cmd_resort_current,
            "/sort_history": self.cmd_sort_history,
            "/sort_back": self.cmd_sort_back,
            "/sort_forward": self.cmd_sort_forward,
            "/recover_current": self.cmd_recover_current,
            "/fix_metadata_current": self.cmd_fix_metadata_current,
            "/undo_sort_last": self.cmd_undo_sort_last,
            "/undo_sort_batch": self.cmd_undo_sort_batch,
            "/jellyfin_scan": self.cmd_jellyfin_scan,
            "/jellyfin_status": self.cmd_jellyfin_status,
            "/episodes": self.cmd_episodes,
            "/library_episodes": self.cmd_library_episodes,
            "/imdb_search": self.cmd_imdb_search,
            "/imdb_fix_current": self.cmd_imdb_fix_current,
            "/movie_mode": self.cmd_movie_mode,
            "/series_mode": self.cmd_series_mode,
            "/movie_current": self.cmd_movie_current,
            "/movie_cancel": self.cmd_movie_cancel,
            "/movie_import": self.cmd_movie_import,
            "/movie_undo_last": self.cmd_movie_undo_last,
            "/movie_undo_batch": self.cmd_movie_undo_batch,
        }
        handler = handlers.get(command)
        if not handler:
            await self.send(chat_id, "Unknown command. Send /help.")
            return
        await handler(chat_id, argument)

    async def cmd_start(self, chat_id: int, _: str) -> None:
        if not self.store.get_setting(f"language:{chat_id}"):
            await self.cmd_language(chat_id, "")
            return
        if self.chat_types.get(chat_id) != "channel":
            await self.send(
                chat_id,
                "Category keyboard enabled. Choose a category below, or keep "
                "using slash commands.",
                PERSISTENT_CATEGORY_KEYBOARD,
            )
        await self.cmd_help(chat_id, "")

    async def cmd_help(self, chat_id: int, _: str) -> None:
        persian = self._language(chat_id) == "fa"
        help_text = HELP_FA if persian else HELP
        suffix = (
            "\n\nدکمه‌های زیر الگوی قابل‌ویرایش دستورها را کپی می‌کنند. "
            "پس از لمس دکمه، دستور را جای‌گذاری و مقدار لازم را اضافه کنید."
            if persian
            else "\n\nThe buttons below copy editable command templates. "
            "After tapping a button, paste the command and add the value."
        )
        await self.send(
            chat_id,
            help_text + suffix,
            HELP_COMMAND_TEMPLATES,
        )

    async def cmd_language(self, chat_id: int, _: str) -> None:
        await self.send(
            chat_id,
            "Choose your language:\nزبان خود را انتخاب کنید:",
            LANGUAGE_MENU,
            force_language="en",
        )

    async def cmd_guide(self, chat_id: int, _: str) -> None:
        await self.send(
            chat_id,
            "Choose the guide language:\nزبان راهنما را انتخاب کنید:",
            GUIDE_LANGUAGE_MENU,
        )

    async def cmd_menu(self, chat_id: int, _: str) -> None:
        if not self.store.get_setting(f"language:{chat_id}"):
            await self.cmd_language(chat_id, "")
            return
        if self.chat_types.get(chat_id) != "channel":
            await self.send(
                chat_id,
                "Persistent category keyboard enabled below the message box.",
                PERSISTENT_CATEGORY_KEYBOARD,
            )
        await self.cmd_quick_menu(chat_id, "")

    async def cmd_quick_menu(self, chat_id: int, _: str) -> None:
        await self.send(
            chat_id,
            "Main control menu:",
            CHANNEL_MENU,
        )

    async def cmd_chatid(self, chat_id: int, _: str) -> None:
        await self.send(chat_id, f"chat_id for this chat:\n{chat_id}")

    async def cmd_setfolder(self, chat_id: int, argument: str) -> None:
        if await self._require_library_kind(chat_id, "series") is None:
            return
        if not argument.strip():
            await self.send(chat_id, "Correct format:\n/setfolder dr ston")
            return
        self.track_task(
            self._run_imdb_search(chat_id, argument, "use"),
            f"imdb-search:{chat_id}",
            chat_id,
        )

    async def _commit_folder(self, chat_id: int, folder_name: str) -> None:
        try:
            folder = sanitize_folder_name(folder_name)
            library = self._selected_library(chat_id, "series")
            path = self.config.target_path(folder, library.key)
            self._set_chat_setting(chat_id, "current_folder", folder)
            await self.send(
                chat_id,
                f"Target folder set after confirmation:\n{_important(path)}",
                CHANNEL_MENU,
            )
        except ValueError as exc:
            await self.send(chat_id, str(exc))

    @staticmethod
    def _folder_token(name: str) -> str:
        return hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]

    def _existing_series_folders(self, chat_id: int = 0) -> list[Path]:
        folders: list[Path] = []
        library = self._selected_library(chat_id, "series")
        for folder in library.path.iterdir():
            if not folder.is_dir() or folder.name.startswith("_"):
                continue
            try:
                # Reuse the path-containment guard; directory junctions that
                # escape the configured library are deliberately excluded.
                safe = self.config.target_path(folder.name, library.key)
            except ValueError:
                continue
            if safe == folder.resolve():
                folders.append(folder)
        return sorted(folders, key=lambda path: path.name.casefold())

    def _folder_picker_markup(
        self, page: int, page_size: int = 12, chat_id: int = 0
    ) -> tuple[dict, int, int]:
        folders = self._existing_series_folders(chat_id)
        pages = max(1, (len(folders) + page_size - 1) // page_size)
        page = min(max(0, page), pages - 1)
        selected = folders[page * page_size:(page + 1) * page_size]
        rows = [
            [{
                "text": f"📁 {folder.name}",
                "callback_data": f"pickfolder:{self._folder_token(folder.name)}",
            }]
            for folder in selected
        ]
        navigation = []
        if page > 0:
            navigation.append(
                {"text": "⬅️ Previous", "callback_data": f"folders:{page - 1}"}
            )
        if page + 1 < pages:
            navigation.append(
                {"text": "Next ➡️", "callback_data": f"folders:{page + 1}"}
            )
        if navigation:
            rows.append(navigation)
        rows.append([{"text": "🎛 Main menu", "callback_data": "menu:open"}])
        return {"inline_keyboard": rows}, page, pages

    async def _send_folder_picker(self, chat_id: int, page: int = 0) -> None:
        markup, page, pages = self._folder_picker_markup(page, chat_id=chat_id)
        if len(markup["inline_keyboard"]) == 1:
            await self.send(
                chat_id,
                "No series folders were found inside the Jellyfin library.",
                CHANNEL_MENU,
            )
            return
        await self.send(
            chat_id,
            f"Choose an existing folder (page {page + 1}/{pages}):",
            markup,
        )

    async def _select_existing_folder(self, chat_id: int, folder: Path) -> None:
        self._set_chat_setting(chat_id, "current_folder", folder.name)
        await self.send(
            chat_id,
            "Existing folder selected as the target for new episodes:\n"
            f"{_important(folder)}\n\n"
            "New files added to the queue after this will go to this folder.",
            CHANNEL_MENU,
        )

    async def cmd_folders(self, chat_id: int, _: str) -> None:
        if await self._require_library_kind(chat_id, "series") is None:
            return
        await self._send_folder_picker(chat_id)

    async def cmd_usefolder(self, chat_id: int, argument: str) -> None:
        if await self._require_library_kind(chat_id, "series") is None:
            return
        try:
            name = sanitize_folder_name(argument)
            library = self._selected_library(chat_id, "series")
            folder = self.config.target_path(name, library.key)
        except ValueError as exc:
            await self.send(chat_id, str(exc))
            return
        if not folder.is_dir():
            await self.send(
                chat_id,
                f"This folder does not exist:\n{folder}\n"
                "Send /folders to see existing folders.",
            )
            return
        await self._select_existing_folder(chat_id, folder)

    async def cmd_folder(self, chat_id: int, _: str) -> None:
        folder = self._chat_setting(chat_id, "current_folder")
        if not folder:
            await self.send(chat_id, "No target folder is set. Use /setfolder NAME")
        else:
            library = self._selected_library(chat_id, "series")
            await self.send(
                chat_id,
                f"Current library: {_important(library.name)}\n"
                "Current folder:\n"
                f"{_important(self.config.target_path(folder, library.key))}",
            )

    async def cmd_renamefolder(self, chat_id: int, argument: str) -> None:
        assert self.downloader
        if await self._require_library_kind(chat_id, "series") is None:
            return
        old_name = self._chat_setting(chat_id, "current_folder")
        if not old_name:
            await self.send(chat_id, "No current folder is set. Use /setfolder first.")
            return
        if self.downloader.running or self.sorter.active:
            await self.send(chat_id, "You cannot rename the folder while a download or sort is running.")
            return
        try:
            library = self._selected_library(chat_id, "series")
            new_name = sanitize_folder_name(argument)
            old_path = self.config.target_path(old_name, library.key)
            new_path = self.config.target_path(new_name, library.key)
        except ValueError as exc:
            await self.send(chat_id, str(exc))
            return
        if new_name == old_name:
            await self.send(chat_id, "The new name is the same as the current name.")
            return
        if new_path.exists():
            await self.send(
                chat_id,
                f"Rename was not done because the destination folder already exists:\n{new_path}",
            )
            return
        try:
            if old_path.exists():
                await self.send(
                    chat_id,
                    "Safely renaming the folder and updating rollback paths...",
                )
                ok, output = await self.sorter.rename_folder(
                    old_path,
                    new_name,
                    chat_id=chat_id,
                    library_key=library.key,
                )
                if not ok:
                    await self.send(
                        chat_id,
                        "Rename failed and the bot state was not changed.\n" + output[-2500:],
                    )
                    return
            changed = self.store.rename_target_folder(
                old_name,
                new_name,
                old_path,
                new_path,
                library.key,
                include_legacy=(
                    library.key == self.config.default_series_library_key
                ),
            )
            self._set_chat_setting(chat_id, "current_folder", new_name)
            include_legacy = library.key == self.config.default_series_library_key
            self.store.replace_chat_setting_value_in_library(
                "current_folder",
                old_name,
                new_name,
                library.key,
                include_legacy=include_legacy,
            )
            self.store.replace_chat_setting_value_in_library(
                "latest_downloaded_folder",
                old_name,
                new_name,
                library.key,
                include_legacy=include_legacy,
                library_setting_name="latest_downloaded_library_key",
            )
            old_prefix = str(old_path)
            self.store.replace_chat_setting_prefix_in_library(
                "latest_downloaded_file",
                old_prefix,
                str(new_path),
                library.key,
                include_legacy=include_legacy,
                library_setting_name="latest_downloaded_library_key",
            )
            await self.send(
                chat_id,
                f"Folder renamed:\n{_important(old_path)}\n"
                f"→ {_important(new_path)}\n"
                f"Updated {changed} queued target(s) and rollback paths too.",
            )
        except Exception as exc:
            LOG.exception("Folder rename failed")
            await self.send(chat_id, f"Folder rename failed: {exc}")

    async def cmd_unsetfolder(self, chat_id: int, _: str) -> None:
        self._set_chat_setting(chat_id, "current_folder", "")
        await self.send(chat_id, "Target folder cleared.")

    async def cmd_queue(self, chat_id: int, _: str) -> None:
        items = self.queue.pending(chat_id)
        if not items:
            await self.send(chat_id, "The queue is empty.")
            return
        lines = [f"Queue ({len(items)} file(s)):"]
        review_mapping, _ = self._download_review_state(chat_id)
        display_by_pending = {
            pending_id: display_id
            for display_id, pending_id in review_mapping.items()
        }
        per_folder_counts: dict[tuple[str, str], int] = {}
        for item in items[:30]:
            kind = item.get("media_kind", "series")
            try:
                library = self.config.library(
                    str(item.get("library_key") or "") or None, kind
                )
                library_label = library.name
            except ValueError:
                library_label = "Unknown library"
            folder_label = item["target_folder"] or (
                "(waiting for movie identification)" if kind == "movie" else "(no folder)"
            )
            count_key = (library_label, folder_label)
            per_folder_counts[count_key] = per_folder_counts.get(count_key, 0) + 1
            batch_id = display_by_pending.get(int(item["pending_id"]))
            batch_label = (
                f"(Download ID #{batch_id}) " if batch_id is not None else ""
            )
            lines.append(
                f"{kind.title()} · {_important(library_label)} · "
                f"{_important(folder_label)} "
                f"item {per_folder_counts[count_key]} "
                f"{batch_label}[{item['status']}] "
                f"{_important(item['original_filename'])} — "
                f"{format_size(item['file_size'])} "
            )
        if len(items) > 30:
            lines.append(f"... and {len(items)-30} more file(s)")
        await self.send(chat_id, "\n".join(lines))

    async def cmd_clearqueue(self, chat_id: int, _: str) -> None:
        count = self.queue.clear(chat_id)
        self._set_chat_setting(chat_id, "download_confirmation", "")
        self._clear_download_review(chat_id)
        await self.send(chat_id, f"Removed {count} item(s) from the queue.")

    async def cmd_remove(self, chat_id: int, argument: str) -> None:
        if argument.strip():
            await self._remove_review_item(chat_id, argument)
            return
        review_mapping, _ = self._download_review_state(chat_id)
        if not review_mapping:
            await self.send(
                chat_id,
                "Open /download first. It will show temporary numbers starting "
                "from 1 for the files in that review.",
                DOWNLOAD_MENU,
            )
            return
        available = ", ".join(
            f"#{display_id}" for display_id in sorted(review_mapping)
        )
        self._set_chat_setting(chat_id, REMOVE_PROMPT_SETTING, "1")
        await self.send(
            chat_id,
            "Send the ID shown beside the movie or episode in the latest "
            f"/download list. Available: {available}.\n"
            "Send /cancel to stop without removing anything.",
        )

    async def _remove_review_item(self, chat_id: int, value: str) -> None:
        review_mapping, next_id = self._download_review_state(chat_id)
        if not review_mapping:
            self._set_chat_setting(chat_id, REMOVE_PROMPT_SETTING, "")
            await self.send(
                chat_id,
                "That download review is no longer active. Open /download again.",
            )
            return
        try:
            review_number = int(value.strip().lstrip("#"))
        except ValueError:
            await self.send(
                chat_id,
                "Send only a number from the latest /download list, or /cancel.",
            )
            return
        if review_number not in review_mapping:
            available = ", ".join(
                f"#{display_id}" for display_id in sorted(review_mapping)
            )
            await self.send(
                chat_id,
                f"That number is not in the latest /download list. "
                f"Available IDs: {available}. Send one of them, or /cancel.",
            )
            return
        pending_id = review_mapping[review_number]
        item = self.store.get_item(pending_id, chat_id=chat_id)
        filename = (
            self._final_saved_filename(item)
            if item is not None
            else f"item {review_number}"
        )
        removed = self.queue.remove(pending_id, chat_id=chat_id)
        self._set_chat_setting(chat_id, "download_confirmation", "")
        review_mapping.pop(review_number, None)
        if review_mapping:
            self._save_download_review_state(chat_id, review_mapping, next_id)
            self._set_chat_setting(chat_id, REMOVE_PROMPT_SETTING, "")
        else:
            # An emptied batch has no identity to preserve; the next starts at 1.
            self._clear_download_review(chat_id)
        await self.send(
            chat_id,
            (
                f"Removed #{review_number}: {_important(filename)}\n\n"
                "Open /download again to review the updated list."
                if removed
                else "That item is no longer removable. Open /download again."
            ),
        )

    @staticmethod
    def _final_saved_filename(item: dict) -> str:
        """Preview the library filename, not Telegram's release filename."""
        original = str(
            item.get("download_filename") or item.get("original_filename") or ""
        )
        suffix = Path(original).suffix.lower()
        if item.get("media_kind", "series") == "movie":
            folder_name = str(item.get("target_folder") or "").strip()
            return f"{folder_name}{suffix}" if folder_name else original
        episode = item.get("series_episode")
        if episode and item.get("target_folder"):
            season = int(item.get("series_season") or 1)
            title = _series_file_title(str(item["target_folder"]))
            return f"{title} - S{season:02d}E{int(episode):02d}{suffix}"
        return original

    def _prepare_download_items(
        self, chat_id: int, pending_ids: set[int] | None = None
    ) -> list[dict]:
        current = self._chat_setting(chat_id, "current_folder")
        current_library = self._selected_library(chat_id)
        items = [
            item
            for item in self.queue.downloadable(chat_id)
            if pending_ids is None or int(item["pending_id"]) in pending_ids
        ]
        prepared = []
        for item in items:
            if (
                item.get("media_kind", "series") == "series"
                and not item.get("target_folder")
                and current
                and (
                    item.get("library_key")
                    or self.config.default_series_library_key
                ) == current_library.key
            ):
                self.store.update_item(
                    item["pending_id"],
                    target_folder=current,
                    library_key=item.get("library_key")
                    or self.config.default_series_library_key,
                )
                item["target_folder"] = current
                item["library_key"] = (
                    item.get("library_key")
                    or self.config.default_series_library_key
                )
            prepared.append(item)
        return prepared

    def _movie_download_preflight(
        self, items: list[dict]
    ) -> tuple[list[dict], list[tuple[dict, Path | None, dict | None]]]:
        """Recheck final movie/episode destinations immediately before transfer."""
        safe: list[dict] = []
        rejected: list[tuple[dict, Path | None, dict | None]] = []
        seen_items: dict[tuple[str, str], dict] = {}
        for item in items:
            approved = item.get("overwrite_policy") == "replace_library"
            media_kind = str(item.get("media_kind") or "series")
            library_key = str(item.get("library_key") or "")
            if media_kind == "movie":
                identity_key = self._movie_identity_key(item)
                existing = self._movie_library_conflict_path(
                    library_key,
                    str(item.get("target_folder") or ""),
                    str(item.get("imdb_id") or ""),
                    str(item.get("metadata_provider") or ""),
                    str(item.get("metadata_provider_id") or ""),
                )
            else:
                detected = self._series_episode_identity(item)
                identity_key = (
                    f"{str(item.get('target_folder') or '').strip().casefold()}:"
                    f"S{detected[0]:03d}E{detected[1]:04d}"
                    if detected
                    else ""
                )
                existing = self._series_library_conflict_path(item)
            batch_key = (library_key, identity_key)
            queued = seen_items.get(batch_key) if identity_key else None
            if approved:
                safe.append(item)
                if identity_key:
                    seen_items[batch_key] = item
                continue
            if existing is not None or queued is not None:
                rejected.append((item, existing, queued))
                continue
            if identity_key:
                seen_items[batch_key] = item
            safe.append(item)
        return safe, rejected

    async def _prepare_safe_download_items(
        self, chat_id: int, pending_ids: set[int] | None = None
    ) -> list[dict]:
        items, rejected = self._movie_download_preflight(
            self._prepare_download_items(chat_id, pending_ids)
        )
        for item, existing, queued in rejected:
            await self._hold_for_library_conflict(
                chat_id, item, existing=existing, queued=queued
            )
        return items

    async def cmd_download(self, chat_id: int, _: str) -> None:
        assert self.downloader
        if self.downloader.running:
            message = (
                "A download is already running for this chat."
                if self.downloader.running_chat_id == chat_id
                else "The downloader is busy with another chat. Your queue was not changed."
            )
            await self.send(chat_id, message)
            return
        self._set_chat_setting(chat_id, "download_confirmation", "")
        self._set_chat_setting(chat_id, REMOVE_PROMPT_SETTING, "")
        items = await self._prepare_safe_download_items(chat_id)
        if not items:
            self._clear_download_review(chat_id)
            await self.send(chat_id, "There are no ready files in the queue.")
            return
        missing = [str(x["pending_id"]) for x in items if not x.get("target_folder")]
        if missing:
            await self.send(
                chat_id, "These files do not have a target folder: " + ", ".join(missing)
                + "\nSend /setfolder NAME first."
            )
            return
        total = sum(int(x.get("file_size") or 0) for x in items)
        header = f"Ready to save {len(items)} file(s) — {format_size(total)}:"
        numbered_items = self._assign_download_batch_ids(chat_id, items)
        lines = [
            f"• #{review_number} "
            f"{_important(self._final_saved_filename(item))}"
            for review_number, item in numbered_items
        ]
        review_messages: list[str] = []
        current = header
        for line in lines:
            if len(current) + len(line) + 1 > 3400:
                review_messages.append(current)
                current = "Download list continued:\n" + line
            else:
                current += "\n" + line
        footer = (
            "\n\nMistake in the list? Press Remove one item or send /remove, "
            "then reply with its number."
        )
        if len(current) + len(footer) > 3900:
            review_messages.append(current)
            current = "Download list continued:" + footer
        else:
            current += footer
        review_messages.append(current)
        if self.config.confirm_before_download:
            self._set_chat_setting(chat_id, "download_confirmation", "1")
            for message in review_messages[:-1]:
                await self.send(chat_id, message)
            await self.send(
                chat_id,
                review_messages[-1]
                + "\n\nSend /confirm_download to start, or /cancel.",
            )
        else:
            self._clear_download_review(chat_id)
            self.track_task(
                self._run_downloads_and_movie_imports(chat_id, items),
                f"download:{chat_id}",
                chat_id,
            )

    async def cmd_confirm(self, chat_id: int, _: str) -> None:
        assert self.downloader
        if self._chat_setting(chat_id, "download_confirmation") != "1":
            await self.send(chat_id, "There is no unconfirmed download request for this chat.")
            return
        if self.downloader.running:
            await self.send(
                chat_id,
                "The downloader is busy. Your confirmation is still saved; try again shortly.",
            )
            return
        self._set_chat_setting(chat_id, "download_confirmation", "")
        review_ids = self._download_review_ids(chat_id)
        pending_ids = set(review_ids) if review_ids else None
        items = await self._prepare_safe_download_items(chat_id, pending_ids)
        if not items:
            self._clear_download_review(chat_id)
            await self.send(chat_id, "There are no ready files to download.")
            return
        self._clear_download_review(chat_id)
        self.track_task(
            self._run_downloads_and_movie_imports(chat_id, items),
            f"download:{chat_id}",
            chat_id,
        )

    async def _run_downloads_and_movie_imports(
        self, chat_id: int, items: list[dict]
    ) -> None:
        assert self.downloader
        async def notify_download(text: str) -> None:
            if text.startswith("Download completed:"):
                LOG.info("Chat %s: %s", chat_id, text)
                return
            await self.send(chat_id, text)

        await self.downloader.run(items, notify_download)
        imported = 0
        imported_movies: list[str] = []
        failed_movies: list[tuple[int, str, str]] = []
        for original in items:
            if original.get("media_kind") != "movie":
                continue
            current = self.store.get_item(
                int(original["pending_id"]), chat_id=chat_id
            )
            if not current or current.get("status") != "completed":
                continue
            label = str(
                current.get("target_folder") or current.get("original_filename")
            )
            if await self._import_movie_item(chat_id, current, notify=False):
                imported += 1
                imported_movies.append(label)
            else:
                failed = self.store.get_item(
                    int(original["pending_id"]), chat_id=chat_id
                ) or current
                error = str(failed.get("error") or "import failed")
                if "already contains a video" in error.casefold():
                    error = "already exists in the library; staged file was kept"
                failed_movies.append(
                    (int(original["pending_id"]), label, error.splitlines()[0][:180])
                )
        if imported_movies or failed_movies:
            if failed_movies:
                summary = (
                    f"Movie import finished: {len(imported_movies)} imported, "
                    f"{len(failed_movies)} need attention."
                )
            else:
                summary = f"✅ {len(imported_movies)} movie(s) imported."
            for label in imported_movies[:10]:
                summary += f"\n• ✅ {_important(label)}"
            for pending_id, label, error in failed_movies[:10]:
                summary += (
                    f"\n• ⚠️ Recovery job #{pending_id} "
                    f"{_important(label)} — {error}"
                )
            if failed_movies:
                summary += (
                    "\n\nStaged failures remain safe. Use /movie_import ID "
                    "after fixing them."
                )
            await self.send(chat_id, summary)
        series_targets: dict[
            tuple[str, str], dict[str, Any]
        ] = {}
        for original in items:
            if (
                original.get("media_kind") != "series"
                or not original.get("series_episode")
            ):
                continue
            current = self.store.get_item(
                int(original["pending_id"]), chat_id=chat_id
            )
            if not current or current.get("status") != "completed":
                continue
            target_key = (
                str(current.get("library_key") or ""),
                str(current.get("target_folder") or ""),
            )
            target = series_targets.setdefault(
                target_key,
                {"replacements": set(), "episode_overrides": {}},
            )
            detected = self._series_episode_identity(current)
            downloaded_path = Path(str(current.get("downloaded_path") or ""))
            if detected and downloaded_path.name:
                target["episode_overrides"][downloaded_path.name] = detected
            if current.get("overwrite_policy") == "replace_library":
                if detected:
                    target["replacements"].add(detected)
        series_sorted = False
        for (library_key, folder_name), target in sorted(
            series_targets.items()
        ):
            if folder_name and await self._run_sorter(
                chat_id,
                folder_name,
                library_key,
                quiet_success=True,
                replace_episodes=target["replacements"],
                episode_overrides=target["episode_overrides"],
                preview_first=True,
            ):
                series_sorted = True
        if (
            (
                (imported and self.config.scan_after_movie_import)
                or (
                    series_sorted
                    and self.config.scan_after_ai_series_sort
                )
            )
            and self.jellyfin
            and self.jellyfin.configured
        ):
            await self._run_jellyfin_scan(chat_id)

    async def _import_movie_item(
        self, chat_id: int, item: dict, *, notify: bool = True
    ) -> bool:
        pending_id = int(item["pending_id"])
        try:
            if notify:
                await self.send(
                    chat_id,
                    f"Checking movie recovery job #{pending_id}...",
                )
            preview = await self.movie_sorter.import_movie(item, dry_run=True)
            if notify:
                await self.send(
                    chat_id,
                    "Movie import plan verified. Importing without overwriting:\n"
                    f"{_important(preview['destination'])}",
                )
            result = await self.movie_sorter.import_movie(item, dry_run=False)
            video = next(
                (
                    entry["destination"]
                    for entry in result.get("files", [])
                    if entry.get("file_type") == "video"
                    and entry.get("operation") != "replace-existing"
                ),
                "",
            )
            self.store.update_item(
                pending_id,
                status="imported",
                error=None,
                downloaded_path=video or item.get("downloaded_path"),
                movie_batch_id=result.get("batch_id"),
            )
            self._set_chat_setting(
                chat_id, "latest_imported_movie_id", str(pending_id)
            )
            self._set_chat_setting(
                chat_id, "latest_movie_batch_id", str(result.get("batch_id", ""))
            )
            staging_file = Path(str(item.get("downloaded_path") or ""))
            try:
                staging_file.parent.rmdir()
            except OSError:
                pass
            if notify:
                await self.send(
                    chat_id,
                    "Movie imported successfully.\n"
                    f"Destination: {_important(result['destination'])}\n"
                    f"Batch ID: {result['batch_id']}\n\n"
                    "This movie job is closed; send another movie while remaining in movie mode.",
                )
            return True
        except Exception as exc:
            LOG.exception("Movie import failed for queue ID %s", pending_id)
            self.store.update_item(
                pending_id, status="movie_import_failed", error=str(exc)
            )
            if notify:
                await self.send(
                    chat_id,
                    "Movie download is safe in staging, but import failed for "
                    f"recovery job #{pending_id}:\n{exc}\n\n"
                    f"Fix the problem and use /movie_import {pending_id} to retry.",
                )
            return False

    async def _run_movie_import_command(self, chat_id: int, item: dict) -> None:
        if self.downloader and self.downloader.running:
            await self.send(chat_id, "Wait for the current download to finish first.")
            return
        imported = await self._import_movie_item(chat_id, item)
        if (
            imported
            and self.config.scan_after_movie_import
            and self.jellyfin
            and self.jellyfin.configured
        ):
            await self._run_jellyfin_scan(chat_id)

    async def cmd_movie_import(self, chat_id: int, argument: str) -> None:
        if not self.config.movies_configured:
            await self.send(chat_id, "Movie mode is not configured.")
            return
        item: dict | None
        if argument.strip():
            try:
                pending_id = int(argument)
            except ValueError:
                await self.send(chat_id, "Correct format: /movie_import 12")
                return
            item = self._movie_item_for_chat(pending_id, chat_id)
        else:
            item = self.store.latest_movie_item(
                ("completed", "movie_import_failed"), chat_id=chat_id
            )
        if not item or item.get("status") not in {"completed", "movie_import_failed"}:
            await self.send(chat_id, "No downloaded movie is waiting for import.")
            return
        source = Path(str(item.get("downloaded_path") or ""))
        if not source.is_file():
            await self.send(chat_id, f"The staged movie file is missing:\n{source}")
            return
        self.track_task(
            self._run_movie_import_command(chat_id, item),
            f"movie-import:{chat_id}:{item['pending_id']}",
            chat_id,
        )

    async def cmd_status(self, chat_id: int, _: str) -> None:
        all_items = self.store.list_items(chat_id=chat_id)
        counts: dict[str, int] = {}
        for item in all_items:
            counts[item["status"]] = counts.get(item["status"], 0) + 1
        part_count = 0
        for item in all_items:
            try:
                filename = validate_original_filename(
                    item["original_filename"]
                    if item.get("media_kind", "series") == "series"
                    else item.get("download_filename") or item["original_filename"]
                )
                if item.get("media_kind") == "movie":
                    part = self.config.movie_staging_job_path(
                        int(item["pending_id"])
                    ) / f"{filename}.part"
                elif item.get("target_folder"):
                    part = self.config.target_path(
                        item["target_folder"],
                        str(item.get("library_key") or "") or None,
                    ) / f"{filename}.part"
                else:
                    continue
                part_count += int(part.is_file())
            except (AssertionError, TypeError, ValueError):
                continue
        text = "\n".join(f"{key}: {value}" for key, value in sorted(counts.items()))
        active = sum(
            1 for owner in self.task_chat_ids.values() if owner == chat_id
        )
        selected_library = self._selected_library(chat_id)
        ai_status = "enabled" if self.config.n8n_agent_enabled else "disabled"
        await self.send(
            chat_id,
            f"Current library: {_important(selected_library.name)}\n"
            f"Mode: {selected_library.media_kind}\n"
            + (text or "No files have been registered yet.")
            + f"\nIncomplete .part files: {part_count}"
            + f"\nTracked background tasks: {active}"
            + f"\nAI identification: {ai_status}",
        )

    async def cmd_cancel(self, chat_id: int, _: str) -> None:
        if self._chat_setting(chat_id, REMOVE_PROMPT_SETTING) == "1":
            self._set_chat_setting(chat_id, REMOVE_PROMPT_SETTING, "")
            await self.send(
                chat_id,
                "Remove operation cancelled. The download review was not changed.",
            )
            return
        had_confirmation = (
            self._chat_setting(chat_id, "download_confirmation") == "1"
        )
        self._set_chat_setting(chat_id, "download_confirmation", "")
        cancelled = bool(self.downloader and self.downloader.cancel(chat_id))
        await self.send(
            chat_id,
            "Cancel request registered."
            if cancelled or had_confirmation
            else "There is no active operation.",
        )

    async def cmd_resolve(self, chat_id: int, argument: str) -> None:
        parts = argument.split()
        if len(parts) != 2 or parts[1] not in {"skip", "overwrite", "save_with_suffix"}:
            await self.send(chat_id, "Format: /resolve ID skip|overwrite|save_with_suffix")
            return
        try:
            pending_id = int(parts[0])
        except ValueError:
            await self.send(chat_id, "The ID must be a number.")
            return
        item = self.store.get_item(pending_id, chat_id=chat_id)
        if not item or item["status"] != "waiting_overwrite":
            await self.send(chat_id, "This file is not waiting for an overwrite decision.")
            return
        library_conflict = str(item.get("error") or "").startswith(
            ("library conflict:", "queue conflict:", "library destination conflict")
        )
        if library_conflict and parts[1] == "save_with_suffix":
            await self.send(
                chat_id,
                "A Jellyfin identity conflict cannot use save_with_suffix. "
                "Choose Replace existing or Cancel download from the conflict message.",
            )
            return
        if parts[1] == "skip":
            self.queue.set_status(pending_id, "skipped", "Skipped by user decision.")
        else:
            self.queue.set_status(
                pending_id,
                "queued",
                None,
                overwrite_policy=(
                    "replace_library" if library_conflict else parts[1]
                ),
            )
        await self.send(chat_id, "Decision saved. Send /download to continue.")

    async def _run_sorter(
        self,
        chat_id: int,
        folder_name: str,
        library_key: str | None = None,
        *,
        quiet_success: bool = False,
        replace_episodes: set[tuple[int, int]] | None = None,
        episode_overrides: dict[str, tuple[int, int]] | None = None,
        preview_first: bool = False,
    ) -> bool:
        try:
            library = self.config.library(
                library_key or self._selected_library(chat_id, "series").key,
                "series",
            )
            folder = self.config.target_path(folder_name, library.key)
            if not folder.is_dir():
                await self.send(chat_id, f"Folder not found:\n{folder}")
                return False
            if not quiet_success:
                await self.send(chat_id, f"Sorting: {_important(folder_name)}")
            if preview_first:
                preview_ok, preview_output = await self.sorter.run(
                    folder,
                    dry_run=True,
                    chat_id=chat_id,
                    library_key=library.key,
                    replace_episodes=replace_episodes,
                    episode_overrides=episode_overrides,
                )
                if not preview_ok:
                    LOG.error("Sorter dry-run failed for %s:\n%s", folder, preview_output)
                    await self.send(
                        chat_id,
                        "Sorting preview failed. No downloaded filenames were changed. "
                        "Use /sort_status for the detailed output.",
                    )
                    return False
            ok, output = await self.sorter.run(
                folder,
                chat_id=chat_id,
                library_key=library.key,
                replace_episodes=replace_episodes,
                episode_overrides=episode_overrides,
            )
            if ok:
                if not quiet_success:
                    await self.send(chat_id, "Sorting completed.")
            else:
                LOG.error("Sorter failed for %s:\n%s", folder, output)
                await self.send(
                    chat_id,
                    "Sorting failed. Use /sort_status for the detailed output.",
                )
            return ok
        except Exception as exc:
            LOG.exception("Sorter error")
            await self.send(chat_id, f"Sorter error: {exc}")
            return False

    async def cmd_sort_current(self, chat_id: int, _: str) -> None:
        if await self._require_library_kind(chat_id, "series") is None:
            return
        folder = self._chat_setting(chat_id, "current_folder")
        if not folder:
            await self.send(chat_id, "No current folder is selected.")
            return
        self.track_task(
            self._run_sorter(chat_id, folder), f"sort-current:{chat_id}", chat_id
        )

    async def _run_series_sort_action(
        self, chat_id: int, action: str, label: str
    ) -> None:
        if await self._require_library_kind(chat_id, "series") is None:
            return
        folder_name = self._chat_setting(chat_id, "current_folder")
        if not folder_name:
            await self.send(chat_id, "No current folder is selected.")
            return
        if self.downloader and self.downloader.running:
            await self.send(chat_id, "Wait for the current download to finish first.")
            return
        library = self._selected_library(chat_id, "series")
        folder = self.config.target_path(folder_name, library.key)
        if not folder.is_dir():
            await self.send(chat_id, f"Folder not found:\n{folder}")
            return
        try:
            await self.send(chat_id, f"{label}:\n{_important(folder)}")
            ok, output = await self.sorter.series_action(
                action, folder, chat_id=chat_id, library_key=library.key
            )
            await self.send(
                chat_id,
                ("Completed.\n" if ok else "Could not complete the action.\n")
                + output[-3000:],
            )
        except Exception as exc:
            LOG.exception("Series sort action failed")
            await self.send(chat_id, f"Sorter error: {exc}")

    async def cmd_resort_current(self, chat_id: int, _: str) -> None:
        self.track_task(
            self._run_series_sort_action(chat_id, "resort-existing", "Renaming existing sorted episodes"),
            f"resort-current:{chat_id}",
            chat_id,
        )

    async def cmd_sort_history(self, chat_id: int, _: str) -> None:
        self.track_task(
            self._run_series_sort_action(chat_id, "sort-history", "Reading sort history"),
            f"sort-history:{chat_id}",
            chat_id,
        )

    async def cmd_sort_back(self, chat_id: int, _: str) -> None:
        self.track_task(
            self._run_series_sort_action(chat_id, "sort-back", "Moving one revision back"),
            f"sort-back:{chat_id}",
            chat_id,
        )

    async def cmd_sort_forward(self, chat_id: int, _: str) -> None:
        self.track_task(
            self._run_series_sort_action(chat_id, "sort-forward", "Moving one revision forward"),
            f"sort-forward:{chat_id}",
            chat_id,
        )

    async def cmd_recover_current(self, chat_id: int, _: str) -> None:
        self.track_task(
            self._run_series_sort_action(
                chat_id,
                "recover-folder",
                "Checking the current folder for incomplete operations",
            ),
            f"recover-current:{chat_id}",
            chat_id,
        )

    async def cmd_fix_metadata_current(self, chat_id: int, _: str) -> None:
        self.track_task(
            self._run_series_sort_action(
                chat_id,
                "fix-metadata",
                "Renaming episode metadata in the current folder",
            ),
            f"fix-metadata-current:{chat_id}",
            chat_id,
        )

    async def cmd_sort_latest(self, chat_id: int, _: str) -> None:
        folder = self._chat_setting(chat_id, "latest_downloaded_folder")
        if not folder:
            await self.send(chat_id, "No completed download has been recorded yet.")
            return
        library_key = self._chat_setting(chat_id, "latest_downloaded_library_key")
        self.track_task(
            self._run_sorter(chat_id, folder, library_key or None),
            f"sort-latest:{chat_id}",
            chat_id,
        )

    async def cmd_sort_folder(self, chat_id: int, argument: str) -> None:
        if await self._require_library_kind(chat_id, "series") is None:
            return
        try:
            folder = sanitize_folder_name(argument)
        except ValueError as exc:
            await self.send(chat_id, str(exc))
            return
        self.track_task(
            self._run_sorter(chat_id, folder), f"sort-folder:{chat_id}", chat_id
        )

    async def cmd_sort_status(self, chat_id: int, _: str) -> None:
        run = self.store.latest_series_sorter_run(chat_id)
        if not run:
            await self.send(chat_id, "The sorter has not run yet.")
        else:
            await self.send(
                chat_id,
                f"Latest run #{run['id']}\nStatus: {run['status']}\n"
                f"Folder: {_important(run['folder'])}\n"
                f"Time: {run['started_at']}",
            )

    async def _run_sort_undo(
        self, chat_id: int, batch_id: str | None = None
    ) -> None:
        if self.downloader and self.downloader.running:
            await self.send(
                chat_id,
                "Files cannot be restored while a download is running.",
            )
            return
        selected_library = self._selected_library(chat_id, "series")
        batch_id = batch_id or self.store.latest_sorter_batch(chat_id)
        if not batch_id:
            await self.send(chat_id, "This chat has no recorded sort batch to undo.")
            return
        if not self.store.sorter_batch_belongs_to_chat(batch_id, chat_id):
            await self.send(chat_id, "That sort batch does not belong to this chat.")
            return
        try:
            library_key = self.store.sorter_batch_library(batch_id, chat_id)
            library = self.config.library(
                library_key or selected_library.key, "series"
            )
            await self.send(chat_id, f"Sort undo started: Batch {batch_id}")
            ok, output = await self.sorter.undo_batch(
                batch_id, chat_id=chat_id, library_key=library.key
            )
            self.store.mark_sorter_batch_status(
                batch_id, chat_id, "undone" if ok else "undo_partial"
            )
            await self.send(
                chat_id,
                ("Undo completed successfully.\n" if ok else "Undo was incomplete or had errors.\n")
                + output[-3000:],
            )
        except Exception as exc:
            LOG.exception("Sort undo error")
            await self.send(chat_id, f"Sort undo error: {exc}")

    async def cmd_undo_sort_last(self, chat_id: int, _: str) -> None:
        self.track_task(
            self._run_sort_undo(chat_id), f"undo-sort-last:{chat_id}", chat_id
        )

    async def cmd_undo_sort_batch(self, chat_id: int, argument: str) -> None:
        batch_id = argument.strip()
        if not batch_id:
            await self.send(
                chat_id,
                "Correct format:\n/undo_sort_batch 20260628-024900-a1b2c3d4",
            )
            return
        self.track_task(
            self._run_sort_undo(chat_id, batch_id),
            f"undo-sort-batch:{chat_id}",
            chat_id,
        )

    async def _run_movie_undo(
        self, chat_id: int, batch_id: str | None = None
    ) -> None:
        if self.downloader and self.downloader.running:
            await self.send(chat_id, "Movies cannot be restored while a download is running.")
            return
        batch_id = batch_id or self.store.latest_movie_batch(chat_id)
        if not batch_id:
            await self.send(chat_id, "This chat has no imported movie batch to undo.")
            return
        if not self.store.movie_batch_belongs_to_chat(batch_id, chat_id):
            await self.send(chat_id, "That movie batch does not belong to this chat.")
            return
        try:
            library_key = self.store.movie_batch_library(batch_id, chat_id)
            library = self.config.library(
                library_key or self._selected_library(chat_id, "movie").key,
                "movie",
            )
            await self.send(
                chat_id,
                f"Movie undo started: {batch_id}",
            )
            result = await self.movie_sorter.undo_batch(
                batch_id, chat_id=chat_id, library_key=library.key
            )
            actual_batch = str(result.get("batch_id") or batch_id or "")
            skipped = int(result.get("skipped", 0) or 0)
            if actual_batch:
                self.store.mark_movie_batch_status(
                    actual_batch,
                    "movie_undone" if bool(result.get("ok")) else "movie_undo_partial",
                    chat_id=chat_id,
                )
                if result.get("ok"):
                    self._set_chat_setting(chat_id, "latest_movie_batch_id", "")
            outcome = (
                "Movie undo completed.\n"
                if result.get("ok")
                else "Movie undo was incomplete; conflicting files were skipped.\n"
            )
            await self.send(
                chat_id,
                outcome + f"Batch ID: {actual_batch or 'unknown'}\n"
                f"Restored: {result.get('restored', 0)}\n"
                f"Skipped: {skipped}",
                MOVIE_MENU,
            )
            if (
                self.config.scan_after_movie_import
                and self.jellyfin
                and self.jellyfin.configured
            ):
                await self._run_jellyfin_scan(chat_id)
        except Exception as exc:
            LOG.exception("Movie undo failed")
            await self.send(chat_id, f"Movie undo failed: {exc}")

    async def cmd_movie_undo_last(self, chat_id: int, _: str) -> None:
        self.track_task(
            self._run_movie_undo(chat_id), f"movie-undo-last:{chat_id}", chat_id
        )

    async def cmd_movie_undo_batch(self, chat_id: int, argument: str) -> None:
        batch_id = argument.strip()
        if not batch_id:
            await self.send(chat_id, "Correct format: /movie_undo_batch BATCH_ID")
            return
        self.track_task(
            self._run_movie_undo(chat_id, batch_id),
            f"movie-undo-batch:{chat_id}",
            chat_id,
        )

    async def _run_jellyfin_scan(self, chat_id: int) -> None:
        if not self.jellyfin:
            await self.send(chat_id, "Jellyfin connection is not ready yet.")
            return

        status_result = await self.send(chat_id, "Jellyfin scan started…")
        status_message_id = self._sent_message_id(status_result)
        last_status = "Jellyfin scan started…"

        async def set_status(text: str) -> None:
            nonlocal last_status
            if status_message_id is not None and text != last_status:
                if await self.edit_message(chat_id, status_message_id, text):
                    last_status = text

        async def report_scan_update(update: dict) -> None:
            phase = update.get("phase")
            progress = update.get("progress")
            progress_text = (
                f" ({float(progress):.0f}%)"
                if isinstance(progress, (int, float))
                else ""
            )
            if phase == "accepted":
                await set_status("Jellyfin scan is running…")
            elif phase == "already-running":
                await set_status("Jellyfin scan is already running…")
            elif phase == "running":
                await set_status(f"Jellyfin scan is running{progress_text}…")
            elif phase == "progress":
                await set_status(f"Jellyfin scan is running ({float(progress):.0f}%)…")

        try:
            result = await self.jellyfin.scan_library_and_wait(
                report_scan_update
            )
            status = str(result.get("status", "unknown"))
            completed_at = result.get("completed_at") or "unknown"
            if status.casefold() == "completed":
                if status_message_id is not None:
                    await set_status("✅ Jellyfin is ready.")
                else:
                    await self.send(chat_id, "✅ Jellyfin is ready.")
            else:
                message = (
                    "⚠️ Jellyfin stopped scanning, but it did not report a "
                    "successful completion.\n"
                    f"Result: {status}\n"
                    f"Stopped at: {completed_at}\n"
                    "Check the Jellyfin dashboard or server logs."
                )
                if status_message_id is not None:
                    await set_status(message)
                else:
                    await self.send(chat_id, message)
        except TimeoutError as exc:
            LOG.warning("Jellyfin scan monitoring timed out: %s", exc)
            message = (
                "Jellyfin accepted the scan, but the bot stopped waiting before "
                "Jellyfin reported completion.\n"
                f"{exc}\n"
                "The scan was not cancelled. Use /jellyfin_status to check it."
            )
            if status_message_id is not None:
                await set_status(message)
            else:
                await self.send(chat_id, message)
        except Exception as exc:
            LOG.exception("Jellyfin scan request failed")
            message = f"Jellyfin scan error: {exc}"
            if status_message_id is not None:
                await set_status(message)
            else:
                await self.send(chat_id, message)

    async def cmd_jellyfin_scan(self, chat_id: int, _: str) -> None:
        self.track_task(
            self._run_jellyfin_scan(chat_id), f"jellyfin-scan:{chat_id}", chat_id
        )

    async def cmd_jellyfin_status(self, chat_id: int, _: str) -> None:
        if not self.jellyfin:
            await self.send(chat_id, "Jellyfin connection is not ready yet.")
            return
        try:
            info = await self.jellyfin.server_status()
            try:
                scan_task = await self.jellyfin.library_scan_status()
                scan_state = str(scan_task.get("State", "unknown"))
                progress = scan_task.get("CurrentProgressPercentage")
                progress_text = (
                    f"{float(progress):.0f}%"
                    if isinstance(progress, (int, float))
                    else "not reported"
                )
                execution = scan_task.get("LastExecutionResult") or {}
                last_result = (
                    str(execution.get("Status", "not recorded"))
                    if isinstance(execution, dict)
                    else "not recorded"
                )
                live_scan = (
                    f"Live scan state: {scan_state}\n"
                    f"Live progress: {progress_text}\n"
                    f"Last Jellyfin task result: {last_result}\n"
                )
            except Exception as scan_exc:
                live_scan = f"Live scan state unavailable: {scan_exc}\n"
            await self.send(
                chat_id,
                "Jellyfin connection is working.\n"
                f"Server: {info.get('ServerName', 'unknown')}\n"
                f"Version: {info.get('Version', 'unknown')}\n"
                f"{live_scan}"
                f"{self.jellyfin.last_scan_summary()}",
            )
        except Exception as exc:
            LOG.exception("Jellyfin status failed")
            await self.send(
                chat_id,
                f"Jellyfin connection failed: {exc}\n"
                f"{self.jellyfin.last_scan_summary()}",
            )

    def _safe_series_folders(self, library: MediaLibrary) -> list[Path]:
        folders: list[Path] = []
        try:
            candidates = list(library.path.iterdir())
        except OSError:
            return folders
        for folder in candidates:
            if not folder.is_dir() or folder.name.startswith("_"):
                continue
            try:
                safe = self.config.target_path(folder.name, library.key)
            except ValueError:
                continue
            if safe == folder.resolve():
                folders.append(folder)
        return folders

    def _existing_series_result(
        self, library: MediaLibrary, results: list[dict]
    ) -> tuple[dict, str] | None:
        """Match only the selected provider's best result to one folder."""
        folders = self._safe_series_folders(library)
        if not folders:
            return None

        by_imdb: dict[str, list[Path]] = {}
        by_anilist: dict[str, list[Path]] = {}
        by_name: dict[str, list[Path]] = {}
        for folder in folders:
            by_name.setdefault(folder.name.casefold(), []).append(folder)
            match = IMDB_FOLDER_ID_RE.search(folder.name)
            if match:
                by_imdb.setdefault(match.group(1).casefold(), []).append(folder)
            anilist_match = ANILIST_FOLDER_ID_RE.search(folder.name)
            if anilist_match:
                by_anilist.setdefault(
                    anilist_match.group(1).casefold(), []
                ).append(folder)

        # Provider IDs are authoritative, even if the folder was renamed later.
        # Lower-ranked search results never trigger automatic routing.
        for result in results[:1]:
            imdb_id = str(result.get("imdb_id") or "").casefold()
            matches = by_imdb.get(imdb_id, [])
            if len(matches) == 1:
                return result, matches[0].name
            anilist_id = str(
                result.get("provider_id") or result.get("anilist_id") or ""
            ).casefold()
            matches = by_anilist.get(anilist_id, [])
            if len(matches) == 1:
                return result, matches[0].name

        # The exact Jellyfin folder format is the next safest match.
        for result in results[:1]:
            expected = str(result.get("folder_name") or "").casefold()
            matches = by_name.get(expected, [])
            if len(matches) == 1:
                return result, matches[0].name

        # Older/manual folders may not have an ID. Only accept a normalized
        # title when it identifies exactly one folder; ambiguity still asks.
        for result in results[:1]:
            title_key = _normalized_title(str(result.get("title") or ""))
            if not title_key:
                continue
            matches = [
                folder for folder in folders
                if _normalized_title(folder.name) == title_key
            ]
            year = result.get("year")
            if year is not None and len(matches) > 1:
                year_text = f"({int(year)})"
                matches = [folder for folder in matches if year_text in folder.name]
            if len(matches) == 1:
                return result, matches[0].name
        return None

    @staticmethod
    def _series_queue_entry(
        pending_id: int, identity: MediaIdentification, result: dict
    ) -> dict:
        provider = str(result.get("provider") or "imdb").strip().casefold()
        provider_id = str(
            result.get("provider_id")
            or result.get("anilist_id")
            or result.get("imdb_id")
            or ""
        ).strip()
        return {
            "pending_id": pending_id,
            "series_title": str(result.get("title") or identity.title_query or ""),
            "series_year": result.get("year") or identity.year,
            "series_season": identity.season or 1,
            "series_episode": identity.episode,
            "imdb_id": str(result.get("imdb_id") or "") or None,
            "metadata_provider": provider,
            "metadata_provider_id": provider_id or None,
        }

    def _series_queue_choice(
        self,
        chat_id: int,
        library: MediaLibrary,
        pending_id: int,
        identity: MediaIdentification,
        result: dict,
        source: str,
        *,
        folder_name: str | None = None,
    ) -> dict:
        entry = self._series_queue_entry(pending_id, identity, result)
        return {
            "chat_id": chat_id,
            "folder_name": folder_name or result["folder_name"],
            "mode": "queue",
            "created_at": time.time(),
            "source": source,
            "source_folder": "",
            "library_key": library.key,
            **entry,
            "queue_entries": [entry],
        }

    def _merge_series_queue_choice(self, choice: dict) -> tuple[str, bool]:
        """Merge matching new-series episodes into one confirmation prompt."""
        now = time.time()
        self.imdb_choices = {
            key: value for key, value in self.imdb_choices.items()
            if now - value["created_at"] <= 600
        }
        for token, existing in self.imdb_choices.items():
            if (
                existing.get("mode") == "queue"
                and int(existing.get("chat_id") or 0) == int(choice["chat_id"])
                and str(existing.get("library_key") or "")
                == str(choice.get("library_key") or "")
                and str(existing.get("folder_name") or "").casefold()
                == str(choice.get("folder_name") or "").casefold()
            ):
                existing.setdefault("queue_entries", []).extend(
                    choice.get("queue_entries") or []
                )
                return token, False
        token = uuid.uuid4().hex[:16]
        self.imdb_choices[token] = choice
        return token, True

    async def _route_series_ai_fallback(
        self,
        chat_id: int,
        library: MediaLibrary,
        pending_id: int,
        identity: MediaIdentification,
        source: str,
    ) -> None:
        """Use the AI title conservatively when metadata has no usable result."""
        fallback_name = str(identity.title_query or "").strip()
        if identity.year is not None:
            fallback_name = f"{fallback_name} ({identity.year})"
        try:
            fallback_name = sanitize_folder_name(fallback_name)
        except ValueError:
            await self.send(
                chat_id,
                "Series not found for this episode. Choose manual "
                "series details.",
                self._series_identification_markup(pending_id),
            )
            return
        fallback_result = {
            "title": identity.title_query,
            "year": identity.year,
            "folder_name": fallback_name,
            "imdb_id": "",
            "provider": library.metadata_provider,
            "provider_id": "",
        }
        existing = self._existing_series_result(library, [fallback_result])
        if existing is not None:
            result, folder_name = existing
            choice = self._series_queue_choice(
                chat_id,
                library,
                pending_id,
                identity,
                result,
                source,
                folder_name=folder_name,
            )
            await self._confirm_series_queue_choice(
                chat_id, choice, notify=False
            )
            return
        choice = self._series_queue_choice(
            chat_id,
            library,
            pending_id,
            identity,
            fallback_result,
            source,
        )
        token, created = self._merge_series_queue_choice(choice)
        if created:
            await self._offer_folder_confirmation(
                chat_id, token, self.imdb_choices[token]
            )

    async def _run_imdb_search(
        self,
        chat_id: int,
        query: str,
        mode: str,
        *,
        pending_id: int | None = None,
        identity: MediaIdentification | None = None,
    ) -> None:
        queue_item: dict | None = None
        if mode == "queue":
            if pending_id is None or identity is None:
                raise ValueError("Queue metadata search requires an episode identity.")
            queue_item = self._series_item_for_chat(pending_id, chat_id)
            if not queue_item or queue_item.get("status") != "awaiting_identification":
                return
            library = self.config.library(
                str(queue_item.get("library_key") or ""), "series"
            )
        else:
            if await self._require_library_kind(chat_id, "series") is None:
                return
            library = self._selected_library(chat_id, "series")
        if not query.strip():
            command = "/imdb_fix_current" if mode == "rename" else "/imdb_search"
            await self.send(chat_id, f"Correct format:\n{command} dr ston")
            return
        source_folder = (
            self._chat_setting(chat_id, "current_folder") if mode == "rename" else ""
        )
        try:
            provider_label = self._provider_label(library)
            if mode != "queue":
                await self.send(
                    chat_id,
                    f"Searching {provider_label} for: {_important(query)}",
                )
            results, source, provider_label = await self._search_metadata(
                library, query, media_type="series"
            )
            if not results:
                if (
                    mode == "queue"
                    and pending_id is not None
                    and identity is not None
                ):
                    await self._route_series_ai_fallback(
                        chat_id,
                        library,
                        pending_id,
                        identity,
                        f"AI title ({provider_label} returned no results)",
                    )
                    return
                await self._offer_manual_folder_fallback(
                    chat_id,
                    query,
                    mode,
                    f"{provider_label} did not return any results.",
                    source_folder,
                    library.key,
                    pending_id,
                    identity,
                )
                return
            if mode == "queue":
                assert pending_id is not None and identity is not None
                existing = self._existing_series_result(library, results)
                if existing is not None:
                    result, folder_name = existing
                    choice = self._series_queue_choice(
                        chat_id,
                        library,
                        pending_id,
                        identity,
                        result,
                        source,
                        folder_name=folder_name,
                    )
                    await self._confirm_series_queue_choice(
                        chat_id, choice, notify=False
                    )
                    return

                # No known folder: offer the best IMDb result once. Additional
                # episodes resolving to the same series join this confirmation.
                choice = self._series_queue_choice(
                    chat_id,
                    library,
                    pending_id,
                    identity,
                    results[0],
                    source,
                )
                token, created = self._merge_series_queue_choice(choice)
                if created:
                    await self._offer_folder_confirmation(
                        chat_id, token, self.imdb_choices[token]
                    )
                return
            now = time.time()
            self.imdb_choices = {
                key: value for key, value in self.imdb_choices.items()
                if now - value["created_at"] <= 600
            }
            rows = []
            for result in results:
                token = uuid.uuid4().hex[:16]
                self.imdb_choices[token] = {
                    "chat_id": chat_id,
                    "folder_name": result["folder_name"],
                    "mode": mode,
                    "created_at": now,
                    "source": source,
                    "source_folder": source_folder,
                    "library_key": library.key,
                    "pending_id": pending_id,
                    "series_title": identity.title_query if identity else None,
                    "series_year": identity.year if identity else None,
                    "series_season": identity.season if identity else None,
                    "series_episode": identity.episode if identity else None,
                    "imdb_id": result.get("imdb_id"),
                    "metadata_provider": str(
                        result.get("provider") or library.metadata_provider
                    ),
                    "metadata_provider_id": str(
                        result.get("provider_id")
                        or result.get("anilist_id")
                        or result.get("imdb_id")
                        or ""
                    ),
                }
                title = str(result["title"])
                year = result.get("year") or "?"
                score_value = result.get("score", "?")
                rows.append(
                    [{
                        "text": f"{title[:34]} ({year}) · {score_value}%",
                        "callback_data": f"imdbpick:{token}",
                    }]
                )
            rows.append([{"text": "🎛 Main menu", "callback_data": "menu:open"}])
            action_text = (
                "Choose the correct result to rename the current folder:"
                if mode == "rename"
                else "Choose the correct result for the Jellyfin destination:"
            )
            await self.send(
                chat_id,
                f"{action_text}\nSource: {provider_label} {source}\n"
                + (
                    "Final folder format: AniList Title (Year)"
                    if library.metadata_provider == "anilist"
                    else "Final folder format: Title (Year) [imdbid-ID]"
                ),
                {"inline_keyboard": rows},
            )
        except Exception as exc:
            provider_label = locals().get(
                "provider_label", self._provider_label(library)
            )
            LOG.warning("Optional %s search failed: %s", provider_label, exc)
            if mode == "queue" and pending_id is not None and identity is not None:
                await self._route_series_ai_fallback(
                    chat_id,
                    library,
                    pending_id,
                    identity,
                    "AI title fallback",
                )
                return
            await self._offer_manual_folder_fallback(
                chat_id,
                query,
                mode,
                f"Optional {provider_label} search is not available: {exc}",
                source_folder,
                library.key,
                pending_id,
                identity,
            )

    async def _offer_folder_confirmation(
        self, chat_id: int, token: str, choice: dict
    ) -> None:
        if choice.get("mode") == "queue":
            await self.send(
                chat_id,
                f"New series: {_important(choice['folder_name'])}\n"
                "Is this name correct for the matching queued episodes?",
                {
                    "inline_keyboard": [[
                        {
                            "text": "✅ Confirm",
                            "callback_data": f"folderconfirm:{token}",
                        },
                        {
                            "text": "❌ Cancel",
                            "callback_data": f"foldercancel:{token}",
                        },
                    ]]
                },
            )
            return
        source = choice.get("source", "Metadata search")
        action = (
            "Rename current folder"
            if choice["mode"] == "rename"
            else "Queue episode in this destination"
            if choice["mode"] == "queue"
            else "Set destination"
        )
        await self.send(
            chat_id,
            f"Suggested folder name:\n{_important(choice['folder_name'])}\n\n"
            f"Source: {source}\nAction: {action}\nDo you confirm?",
            {
                "inline_keyboard": [[
                    {
                        "text": "✅ Confirm",
                        "callback_data": f"folderconfirm:{token}",
                    },
                    {
                        "text": "❌ Cancel",
                        "callback_data": f"foldercancel:{token}",
                    },
                ]]
            },
        )

    async def _offer_manual_folder_fallback(
        self,
        chat_id: int,
        entered_name: str,
        mode: str,
        reason: str,
        source_folder: str = "",
        library_key: str = "",
        pending_id: int | None = None,
        identity: MediaIdentification | None = None,
    ) -> None:
        try:
            manual_name = sanitize_folder_name(entered_name)
        except ValueError as exc:
            await self.send(chat_id, f"{reason}\nThe manual name is not valid either: {exc}")
            return
        token = uuid.uuid4().hex[:16]
        selected_library = self.config.library(
            library_key or self._selected_library(chat_id, "series").key,
            "series",
        )
        provider_label = self._provider_label(selected_library)
        choice = {
            "chat_id": chat_id,
            "folder_name": manual_name,
            "mode": mode,
            "created_at": time.time(),
            "source": f"Manual fallback ({provider_label} unavailable)",
            "source_folder": source_folder,
            "library_key": library_key
            or self._selected_library(chat_id, "series").key,
            "pending_id": pending_id,
            "series_title": identity.title_query if identity else None,
            "series_year": identity.year if identity else None,
            "series_season": identity.season if identity else None,
            "series_episode": identity.episode if identity else None,
            "metadata_provider": selected_library.metadata_provider,
            "metadata_provider_id": "",
        }
        self.imdb_choices[token] = choice
        await self.send(
            chat_id,
            f"{reason}\n\nYour entered name will be offered as the fallback.",
        )
        await self._offer_folder_confirmation(chat_id, token, choice)

    async def cmd_imdb_search(self, chat_id: int, argument: str) -> None:
        self.track_task(
            self._run_imdb_search(chat_id, argument, "use"),
            f"imdb-search:{chat_id}",
            chat_id,
        )

    async def cmd_imdb_fix_current(self, chat_id: int, argument: str) -> None:
        query = argument.strip() or self._chat_setting(chat_id, "current_folder")
        if not query:
            await self.send(
                chat_id,
                "No current folder is selected. Use /folders or /setfolder first.",
            )
            return
        self.track_task(
            self._run_imdb_search(chat_id, query, "rename"),
            f"imdb-fix-current:{chat_id}",
            chat_id,
        )

    async def cmd_episodes(self, chat_id: int, argument: str) -> None:
        if await self._require_library_kind(chat_id, "series") is None:
            return
        folder_name = argument.strip() or self._chat_setting(chat_id, "current_folder")
        if not folder_name:
            await self.send(
                chat_id,
                "No folder was specified.\nUse /episodes Anime Name\nor select one first with /setfolder.",
            )
            return
        try:
            folder_name = sanitize_folder_name(folder_name)
            library = self._selected_library(chat_id, "series")
            folder = self.config.target_path(folder_name, library.key)
        except ValueError as exc:
            await self.send(chat_id, str(exc))
            return
        if not folder.is_dir():
            await self.send(chat_id, f"Folder not found:\n{folder}")
            return
        entries = await asyncio.to_thread(self.catalog.scan_series, folder)
        await self.send(
            chat_id, format_series_inventory(_important(folder_name), entries)
        )

    def _library_episode_summary(self) -> str:
        lines = ["📚 Jellyfin series-library episode summary"]
        series_count = 0
        for library in self.config.libraries_for("series"):
            library_lines: list[str] = []
            for folder in sorted(
                (p for p in library.path.iterdir() if p.is_dir()),
                key=lambda p: p.name.casefold(),
            ):
                grouped = self.catalog.grouped(self.catalog.scan_series(folder))
                if not grouped:
                    continue
                series_count += 1
                seasons = ", ".join(
                    f"S{season:02d}: {len(episodes)} eps (latest E{max(episodes):02d})"
                    for season, episodes in sorted(grouped.items())
                )
                library_lines.append(f"• {_important(folder.name)} — {seasons}")
                if len(lines) + len(library_lines) >= 60:
                    library_lines.append(
                        "... result shortened; select a library and use /episodes NAME"
                    )
                    break
            if library_lines:
                lines.append(f"\n[{_important(library.name)}]")
                lines.extend(library_lines)
            if len(lines) >= 60:
                break
        if not series_count:
            return "No recognizable episodes were found in the library."
        return "\n".join(lines)

    async def cmd_library_episodes(self, chat_id: int, _: str) -> None:
        await self.send(chat_id, "Scanning library files...")
        summary = await asyncio.to_thread(self._library_episode_summary)
        await self.send(chat_id, summary)


async def async_main() -> None:
    config = load_config()
    setup_logging(config.logs_path)
    app = BotApp(config)
    try:
        await app.run()
    finally:
        await app.shutdown()
        app.store.close()


def main() -> int:
    try:
        asyncio.run(async_main())
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
