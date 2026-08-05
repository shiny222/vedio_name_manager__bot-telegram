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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from telegram_jellyfin_bot.config import Config, load_config
    from telegram_jellyfin_bot.downloader import DownloadManager
    from telegram_jellyfin_bot.episode_catalog import (
        EpisodeCatalog, detect_episode, format_series_inventory
    )
    from telegram_jellyfin_bot.jellyfin_bridge import JellyfinBridge
    from telegram_jellyfin_bot.imdb_bridge import (
        ImdbFuzzySearchBridge, movie_query_from_filename
    )
    from telegram_jellyfin_bot.movie_sorter_bridge import MovieSorterBridge
    from telegram_jellyfin_bot.queue_manager import QueueManager
    from telegram_jellyfin_bot.sorter_bridge import SorterBridge
    from telegram_jellyfin_bot.state_store import StateStore
    from telegram_jellyfin_bot.utils import (
        format_size, sanitize_folder_name, setup_logging, validate_original_filename
    )
else:
    from .config import Config, load_config
    from .downloader import DownloadManager
    from .episode_catalog import EpisodeCatalog, detect_episode, format_series_inventory
    from .jellyfin_bridge import JellyfinBridge
    from .imdb_bridge import ImdbFuzzySearchBridge, movie_query_from_filename
    from .movie_sorter_bridge import MovieSorterBridge
    from .queue_manager import QueueManager
    from .sorter_bridge import SorterBridge
    from .state_store import StateStore
    from .utils import format_size, sanitize_folder_name, setup_logging, validate_original_filename

LOG = logging.getLogger(__name__)
HELP = """Commands:
/menu - Show the button menu
/setfolder NAME - Set the target folder
/folders - Pick from existing folders
/usefolder NAME - Use an existing folder by name
/renamefolder NAME - Rename the current folder safely
/folder - Show the current folder
/unsetfolder - Clear the current folder
/queue - Show the download queue
/remove ID - Remove one item from the queue
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
/imdb_search NAME - Fuzzy-search the correct IMDb title
/imdb_fix_current [NAME] - Rename the current folder using IMDb search
/movie_mode - Send new files as independent movie jobs
/series_mode - Return to TV-series episode mode
/movie_current - Show the latest movie job
/movie_cancel - Cancel the current unprocessed movie
/movie_import [ID] - Retry a downloaded movie import
/movie_undo_last - Undo the latest movie import batch
/movie_undo_batch ID - Undo a specific movie import batch
/chatid - Show this chat ID
/guide - Show the English/Persian usage guide
/help - Show this help"""

GUIDE_EN = """How to use the Telegram Jellyfin Bot

1. Start the bot on the computer
• Run run_local_bot_api.bat and leave it open.
• Run run.bat and leave it open.

2. Open the controls
• Send /menu.
• Private chats and groups get the persistent category keyboard.
• In a channel, press Command categories in the inline menu.

3. Select the series folder before sending videos
• Existing series: /folders, then press its folder.
• New series: /setfolder SERIES NAME
• Confirm the IMDb suggestion, or choose the manual name.
• Check the selection with /folder.

Important: the current folder is shared by the authorized chats. A queued video
keeps the folder that was selected when that video was received.

4. Send episodes
Send supported video files to the authorized bot chat or channel. The bot adds
them to the queue; it does not download immediately.

5. Download
• /queue — review queued files.
• /download — prepare the download.
• /confirm_download — start it.
• If a file exists, use /resolve ID skip, overwrite, or save_with_suffix.

6. Organize the current series
• /sort_current — sort only new loose files.
• /resort_current — rename previously sorted episodes after correcting the
  series folder name.
• /fix_metadata_current — manually align episode NFO/artwork names.
• /sort_history — review revisions before undoing anything.

7. Update Jellyfin
Use /jellyfin_scan after downloading and sorting. The bot monitors Jellyfin and
sends another message when the scan completes and the latest library state is
ready. Use /jellyfin_status to check live progress or diagnose an HTTP error.

8. Check owned episodes
• /episodes — current series.
• /episodes NAME — another series.
• /library_episodes — the whole library.

9. Undo and recovery
• /sort_back — undo the latest applied revision in the current series.
• /sort_forward — reapply an undone revision.
• /undo_sort_batch ID — undo a known technical batch.
• /recover_current — manually reconcile an operation interrupted by a crash or
  power loss. It checks only the current series folder.

10. Import an independent movie
• /movie_mode — new videos become movie jobs, not episodes.
• Send one movie, choose filename or manual search, select the IMDb result, and
  confirm the exact name.
• /download then /confirm_download — download to staging and safely import it.
• /movie_current — show status; /movie_import ID — retry a staged movie.
• /movie_undo_last — restore the latest import to staging.
• /series_mode — return to episode mode.

Safety reminders
• Check /folder before sending or sorting.
• The organizer never intentionally overwrites an existing destination.
• Do not delete .rename_history.json files while you need rollback.
• Use /renamefolder or /imdb_fix_current instead of renaming a sorted series
  directly in Windows Explorer."""

GUIDE_FA = """راهنمای استفاده از ربات تلگرام Jellyfin

۱. اجرای ربات روی کامپیوتر
• ابتدا run_local_bot_api.bat را اجرا کنید و پنجره آن را باز نگه دارید.
• سپس run.bat را اجرا کنید و آن را نیز باز نگه دارید.

۲. باز کردن کنترل‌ها
• دستور /menu را ارسال کنید.
• در گفت‌وگوی خصوصی و گروه، صفحه‌کلید دائمی دسته‌بندی‌ها نمایش داده می‌شود.
• در کانال، دکمه Command categories را در منوی شیشه‌ای انتخاب کنید.

۳. قبل از فرستادن ویدیو، پوشه سریال را انتخاب کنید
• سریال موجود: دستور /folders را بفرستید و پوشه را انتخاب کنید.
• سریال جدید: /setfolder SERIES NAME
• پیشنهاد IMDb را تأیید کنید یا نام دستی را انتخاب کنید.
• با دستور /folder پوشه انتخاب‌شده را بررسی کنید.

مهم: پوشه فعلی بین چت‌های مجاز مشترک است. هر ویدیوی واردشده، پوشه‌ای را که
در زمان دریافت آن انتخاب شده بود در صف خود نگه می‌دارد.

۴. فرستادن قسمت‌ها
فایل‌های ویدیویی پشتیبانی‌شده را در چت یا کانال مجاز بفرستید. ربات آن‌ها را
وارد صف می‌کند و دانلود به‌صورت خودکار شروع نمی‌شود.

۵. دانلود
• /queue — مشاهده فایل‌های صف.
• /download — آماده‌سازی دانلود.
• /confirm_download — شروع دانلود.
• اگر فایل از قبل وجود داشت، از /resolve ID همراه با skip یا overwrite یا
  save_with_suffix استفاده کنید.

۶. مرتب‌سازی سریال فعلی
• /sort_current — فقط فایل‌های جدید و مرتب‌نشده را مرتب می‌کند.
• /resort_current — پس از اصلاح نام پوشه سریال، نام قسمت‌های قبلی را اصلاح
  می‌کند.
• /fix_metadata_current — نام NFO و تصویرهای مربوط به قسمت‌ها را به‌صورت دستی
  هماهنگ می‌کند.
• /sort_history — قبل از بازگردانی، تاریخچه نسخه‌ها را نشان می‌دهد.

۷. به‌روزرسانی Jellyfin
بعد از دانلود و مرتب‌سازی از /jellyfin_scan استفاده کنید. ربات وضعیت Jellyfin
را بررسی می‌کند و پس از پایان اسکن و آماده شدن آخرین وضعیت کتابخانه پیام
دیگری می‌فرستد. برای مشاهده پیشرفت زنده یا بررسی خطای HTTP، دستور
/jellyfin_status را اجرا کنید.

۸. بررسی قسمت‌های موجود
• /episodes — قسمت‌های سریال فعلی.
• /episodes NAME — قسمت‌های یک سریال دیگر.
• /library_episodes — خلاصه تمام کتابخانه.

۹. بازگردانی و بازیابی
• /sort_back — آخرین نسخه اعمال‌شده در سریال فعلی را برمی‌گرداند.
• /sort_forward — نسخه بازگردانده‌شده را دوباره اعمال می‌کند.
• /undo_sort_batch ID — یک Batch ID مشخص را برمی‌گرداند.
• /recover_current — عملیات ناقص پس از خاموشی یا توقف ناگهانی را به‌صورت دستی
  بررسی می‌کند و فقط پوشه سریال فعلی را می‌گردد.

۱۰. وارد کردن فیلم مستقل
• با /movie_mode ویدیوهای جدید به‌عنوان فیلم دریافت می‌شوند، نه قسمت سریال.
• یک فیلم بفرستید، جستجو با نام فایل یا نام دستی را انتخاب کنید، نتیجه IMDb را
  انتخاب و نام نهایی را تأیید کنید.
• با /download و سپس /confirm_download فیلم ابتدا در staging دانلود و بعد
  به‌صورت امن وارد کتابخانه می‌شود.
• /movie_current وضعیت را نشان می‌دهد و /movie_import ID انتقال را تکرار می‌کند.
• /movie_undo_last آخرین انتقال را به staging برمی‌گرداند.
• برای بازگشت به حالت قسمت‌های سریال از /series_mode استفاده کنید.

نکات ایمنی
• پیش از ارسال یا مرتب‌سازی همیشه /folder را بررسی کنید.
• ابزار مرتب‌سازی عمداً هیچ فایل مقصدی را بازنویسی نمی‌کند.
• تا زمانی که به بازگردانی نیاز دارید فایل‌های .rename_history.json را حذف
  نکنید.
• برای تغییر نام سریال مرتب‌شده از /renamefolder یا /imdb_fix_current استفاده
  کنید و نام پوشه را مستقیماً در Windows Explorer عوض نکنید."""

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
    # Folder selection and naming
    {"command": "folder", "description": "Folders: Show the current folder"},
    {"command": "folders", "description": "Folders: Pick an existing folder"},
    {"command": "setfolder", "description": "Folders: Set or create a target folder"},
    {"command": "usefolder", "description": "Folders: Use an existing folder by name"},
    {"command": "renamefolder", "description": "Folders: Rename the current folder"},
    {"command": "unsetfolder", "description": "Folders: Clear the current folder"},
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
    {"command": "imdb_search", "description": "IMDb: Search for an official title"},
    {"command": "imdb_fix_current", "description": "IMDb: Fix the current folder name"},
]

CHANNEL_MENU = {
    "inline_keyboard": [
        [
            {"text": "Rename sorted files", "callback_data": "menu:resort_current"},
            {"text": "Sort history", "callback_data": "menu:sort_history"},
        ],
        [
            {"text": "Sort back", "callback_data": "menu:sort_back"},
            {"text": "Sort forward", "callback_data": "menu:sort_forward"},
        ],
        [
            {"text": "Recover current folder", "callback_data": "menu:recover_current"},
            {"text": "Fix episode metadata", "callback_data": "menu:fix_metadata_current"},
        ],
        [
            {"text": "📁 Current folder", "callback_data": "menu:folder"},
            {"text": "📋 Queue", "callback_data": "menu:queue"},
        ],
        [
            {"text": "🗂 Pick existing folder", "callback_data": "menu:folders"},
        ],
        [
            {"text": "⬇️ Download", "callback_data": "menu:download"},
            {"text": "✅ Confirm download", "callback_data": "menu:confirm"},
        ],
        [
            {"text": "📊 Status", "callback_data": "menu:status"},
            {"text": "⛔ Cancel", "callback_data": "menu:cancel"},
        ],
        [
            {"text": "🧹 Sort current", "callback_data": "menu:sort_current"},
            {"text": "🧹 Sort latest", "callback_data": "menu:sort_latest"},
        ],
        [
            {"text": "↩️ Undo latest sort", "callback_data": "menu:undo_sort_last"},
            {"text": "🔢 Undo by batch ID", "callback_data": "menu:undo_batch_help"},
        ],
        [
            {"text": "🔄 Scan Jellyfin", "callback_data": "menu:jellyfin_scan"},
            {"text": "🟢 Jellyfin Status", "callback_data": "menu:jellyfin_status"},
        ],
        [
            {"text": "🎞 Episodes", "callback_data": "menu:episodes"},
            {"text": "📚 All series", "callback_data": "menu:library_episodes"},
        ],
        [
            {"text": "🔎 IMDb title search", "callback_data": "menu:imdb_help"},
        ],
        [
            {"text": "Movies", "callback_data": "nav:movies"},
            {"text": "Movie mode", "callback_data": "menu:movie_mode"},
        ],
        [
            {"text": "✏️ Set/rename folder", "callback_data": "menu:folder_help"},
            {"text": "❓ Help", "callback_data": "menu:help"},
        ],
        [
            {"text": "📖 How to use (English / فارسی)", "callback_data": "menu:guide"},
        ],
        [
            {"text": "🗂 Command categories", "callback_data": "nav:categories"},
        ],
    ]
}

# This persistent keyboard appears beside the message input in private chats,
# groups, and supergroups. Telegram does not support reply keyboards in channels,
# so CHANNEL_MENU links to the same categories with an inline button.
PERSISTENT_CATEGORY_KEYBOARD = {
    "keyboard": [
        [
            {"text": "📥 Downloads"},
            {"text": "📁 Folders"},
        ],
        [
            {"text": "🧹 Sorting"},
            {"text": "↩️ Undo & Recovery"},
        ],
        [
            {"text": "🎬 Jellyfin"},
            {"text": "🔎 IMDb"},
        ],
        [
            {"text": "📺 Episodes"},
            {"text": "⚙️ Bot"},
        ],
        [
            {"text": "Movies"},
        ],
        [
            {"text": "⚡ Quick Menu"},
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
            {"text": "📁 Folders", "callback_data": "nav:folders"},
        ],
        [
            {"text": "🧹 Sorting", "callback_data": "nav:sorting"},
            {"text": "↩️ Undo & Recovery", "callback_data": "nav:undo"},
        ],
        [
            {"text": "🎬 Jellyfin", "callback_data": "nav:jellyfin"},
            {"text": "🔎 IMDb", "callback_data": "nav:imdb"},
        ],
        [
            {"text": "📺 Episodes", "callback_data": "nav:episodes"},
            {"text": "⚙️ Bot", "callback_data": "nav:bot"},
        ],
        [
            {"text": "Movies", "callback_data": "nav:movies"},
        ],
        [
            {"text": "⚡ Quick menu", "callback_data": "menu:open"},
        ],
    ]
}

SUBMENU_FOOTER = [
    {"text": "⬅️ Categories", "callback_data": "nav:categories"},
    {"text": "⚡ Quick menu", "callback_data": "menu:open"},
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
            {"text": "📋 Copy /remove", "copy_text": {"text": "/remove "}},
            {"text": "📋 Copy /resolve", "copy_text": {"text": "/resolve "}},
        ],
        SUBMENU_FOOTER,
    ]
}

FOLDER_MENU = {
    "inline_keyboard": [
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
        SUBMENU_FOOTER,
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
        SUBMENU_FOOTER,
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
        SUBMENU_FOOTER,
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

MOVIE_MENU = {
    "inline_keyboard": [
        [
            {"text": "Enter movie mode", "callback_data": "menu:movie_mode"},
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
        SUBMENU_FOOTER,
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
        SUBMENU_FOOTER,
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
            {"text": "⚡ Quick menu", "callback_data": "menu:open"},
        ],
        [
            {"text": "⬅️ Categories", "callback_data": "nav:categories"},
        ],
    ]
}

CATEGORY_SUBMENUS = {
    "nav:downloads": ("Download commands:", DOWNLOAD_MENU),
    "nav:folders": ("Folder commands:", FOLDER_MENU),
    "nav:sorting": ("Sorting commands:", SORTING_MENU),
    "nav:undo": ("Undo and recovery commands:", UNDO_MENU),
    "nav:movies": ("Independent movie workflow:", MOVIE_MENU),
    "nav:jellyfin": ("Jellyfin commands:", JELLYFIN_MENU),
    "nav:imdb": ("IMDb fuzzy-search commands:", IMDB_MENU),
    "nav:episodes": ("Episode inventory commands:", EPISODE_MENU),
    "nav:bot": ("Bot information and help:", BOT_MENU),
}

REPLY_CATEGORY_ACTIONS = {
    "📥 Downloads": "nav:downloads",
    "📁 Folders": "nav:folders",
    "🧹 Sorting": "nav:sorting",
    "↩️ Undo & Recovery": "nav:undo",
    "🎬 Jellyfin": "nav:jellyfin",
    "🔎 IMDb": "nav:imdb",
    "📺 Episodes": "nav:episodes",
    "⚙️ Bot": "nav:bot",
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
        self, chat_id: int, text: str, reply_markup: dict | None = None
    ) -> None:
        params: dict[str, str] = {"chat_id": str(chat_id), "text": text[:4000]}
        if reply_markup is not None:
            params["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
        await self.call("sendMessage", **params)


class BotApp:
    def __init__(self, config: Config):
        self.config = config
        self.store = StateStore(config.data_path / "state.db")
        self.queue = QueueManager(self.store)
        self.session: aiohttp.ClientSession | None = None
        self.api: TelegramAPI | None = None
        self.downloader: DownloadManager | None = None
        self.jellyfin: JellyfinBridge | None = None
        self.sorter = SorterBridge(config, self.store)
        self.movie_sorter = MovieSorterBridge(config, self.store)
        self.catalog = EpisodeCatalog(config.allowed_video_extensions)
        self.imdb = ImdbFuzzySearchBridge(config)
        self.imdb_choices: dict[str, dict] = {}
        self.movie_choices: dict[str, dict] = {}
        self.movie_manual_pending: dict[int, int] = {}
        self.background_tasks: set[asyncio.Task] = set()
        self.chat_types: dict[int, str] = {}
        if not self.store.get_setting("current_folder") and config.default_target_folder:
            self.store.set_setting("current_folder", sanitize_folder_name(config.default_target_folder))

    def track_task(self, awaitable: Any, name: str) -> asyncio.Task:
        """Start a background task and keep it visible until it finishes."""
        task = asyncio.create_task(awaitable, name=name)
        self.background_tasks.add(task)

        def _done_callback(done_task: asyncio.Task) -> None:
            self.background_tasks.discard(done_task)
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

    async def run(self) -> None:
        timeout = aiohttp.ClientTimeout(total=None, connect=15, sock_read=60)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            self.session = session
            self.api = TelegramAPI(self.config, session)
            self.downloader = DownloadManager(
                self.config, self.queue, self.api.call, session
            )
            self.jellyfin = JellyfinBridge(self.config, self.store, session)
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
        if update.get("callback_query"):
            await self.handle_callback(update["callback_query"])
            return
        message = update.get("message") or update.get("channel_post")
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
        elif text in REPLY_CATEGORY_ACTIONS or text == "⚡ Quick Menu":
            await self.handle_reply_category(chat_id, text)
        elif text and chat_id in self.movie_manual_pending:
            pending_id = self.movie_manual_pending.pop(chat_id)
            self.track_task(
                self._run_movie_search(
                    chat_id, pending_id, text, manual_query=True
                ),
                f"movie-imdb-search:{chat_id}:{pending_id}",
            )
        else:
            await self.handle_media(chat_id, message)

    async def send(
        self, chat_id: int, text: str, reply_markup: dict | None = None
    ) -> None:
        assert self.api
        try:
            await self.api.send(chat_id, text, reply_markup)
        except Exception:
            LOG.exception("Could not send Telegram message")

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
            "menu:folder": self.cmd_folder,
            "menu:folders": self.cmd_folders,
            "menu:unsetfolder": self.cmd_unsetfolder,
            "menu:queue": self.cmd_queue,
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
            "menu:open": self.cmd_quick_menu,
            "menu:guide": self.cmd_guide,
            "menu:help": self.cmd_help,
        }
        if action == "guide:en":
            await self.send(int(chat_id), GUIDE_EN, GUIDE_LANGUAGE_MENU)
            return
        if action == "guide:fa":
            await self.send(int(chat_id), GUIDE_FA, GUIDE_LANGUAGE_MENU)
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
            item = self._movie_item_for_chat(choice["pending_id"], int(chat_id))
            if not item or item.get("status") != "awaiting_identification":
                await self.send(int(chat_id), "This movie is no longer waiting for a name.")
                return
            self.store.update_item(
                int(item["pending_id"]),
                target_folder=choice["folder_name"],
                movie_title=choice["title"],
                movie_year=choice.get("year"),
                imdb_id=choice.get("imdb_id") or None,
                status="queued",
                error=None,
            )
            await self.send(
                int(chat_id),
                "Movie identity confirmed.\n"
                f"Folder: {choice['folder_name']}\n\n"
                "Use /download to review the destination, then /confirm_download.",
                MOVIE_MENU,
            )
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
                and self.queue.remove(pending_id)
            )
            if self.movie_manual_pending.get(int(chat_id)) == pending_id:
                self.movie_manual_pending.pop(int(chat_id), None)
            await self.send(
                int(chat_id),
                "Movie queue item cancelled." if removed else "Movie could not be cancelled.",
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
                folder for folder in self._existing_series_folders()
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
            if not choice or time.time() - choice["created_at"] > 600:
                await self.send(
                    int(chat_id),
                    "This IMDb result expired. Run /imdb_search again.",
                )
                return
            await self._offer_folder_confirmation(int(chat_id), token, choice)
            return
        if action.startswith("folderconfirm:"):
            token = action.partition(":")[2]
            choice = self.imdb_choices.pop(token, None)
            if not choice or time.time() - choice["created_at"] > 600:
                await self.send(int(chat_id), "This confirmation expired. Please try again.")
                return
            if choice["mode"] == "rename":
                source_folder = str(choice.get("source_folder", ""))
                current_folder = self.store.get_setting("current_folder")
                if not source_folder or current_folder != source_folder:
                    await self.send(
                        int(chat_id),
                        "The selected folder changed after this IMDb search. "
                        "Nothing was renamed. Run /imdb_fix_current again.",
                    )
                    return
                if not self.config.target_path(source_folder).is_dir():
                    await self.send(
                        int(chat_id),
                        "The folder used for this IMDb search no longer exists. "
                        "Nothing was renamed.",
                    )
                    return
                await self.cmd_renamefolder(int(chat_id), choice["folder_name"])
            else:
                await self._commit_folder(int(chat_id), choice["folder_name"])
            return
        if action.startswith("foldercancel:"):
            token = action.partition(":")[2]
            self.imdb_choices.pop(token, None)
            await self.send(int(chat_id), "Folder change cancelled.", CHANNEL_MENU)
            return
        handler = handlers.get(action)
        if handler:
            await handler(int(chat_id), "")

    async def handle_reply_category(self, chat_id: int, text: str) -> None:
        """Open an inline submenu selected from the persistent reply keyboard."""
        if text == "⚡ Quick Menu":
            await self.cmd_quick_menu(chat_id, "")
            return
        action = REPLY_CATEGORY_ACTIONS.get(text)
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
        if self._media_mode(chat_id) == "movie":
            await self._queue_movie_for_identification(chat_id, message, media, filename)
            return
        pending_id = self.queue.add(
            message_id=int(message["message_id"]),
            chat_id=chat_id,
            file_id=media["file_id"],
            file_unique_id=media["file_unique_id"],
            original_filename=filename,
            file_size=media.get("file_size"),
            received_at=datetime.now(timezone.utc).isoformat(),
            target_folder=self.store.get_setting("current_folder"),
            media_kind="series",
        )
        if pending_id is None:
            await self.send(chat_id, "This video is already in the queue.")
        else:
            target_folder = self.store.get_setting("current_folder")
            item_number = self._queue_display_number(pending_id, target_folder)
            notice = self._episode_arrival_notice(
                filename, target_folder, pending_id
            )
            await self.send(
                chat_id,
                f"Video added to the queue. Item {item_number} for this folder."
                f"\nQueue ID for commands: #{pending_id}"
                + (f"\n{notice}" if notice else ""),
            )

    def _media_mode(self, chat_id: int) -> str:
        mode = self.store.get_setting(f"media_mode:{chat_id}", "series")
        return "movie" if mode == "movie" else "series"

    async def _queue_movie_for_identification(
        self, chat_id: int, message: dict, media: dict, filename: str
    ) -> None:
        if not self.config.movies_configured:
            await self.send(
                chat_id,
                "Movie mode is not configured yet. Set jellyfin_movie_library_path "
                "and movie_staging_path in config.json, then restart the bot.",
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
            target_folder=None,
            media_kind="movie",
            status="awaiting_identification",
        )
        if pending_id is None:
            await self.send(chat_id, "This movie is already registered in the queue.")
            return
        await self.send(
            chat_id,
            f"Movie received but not downloaded yet.\n"
            f"Queue ID: #{pending_id}\nFilename: {filename}\n\n"
            "How should I identify it?",
            self._movie_identification_markup(pending_id),
        )

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

    def _movie_item_for_chat(self, pending_id: int, chat_id: int) -> dict | None:
        item = self.store.get_item(pending_id)
        if (
            not item
            or item.get("media_kind") != "movie"
            or int(item.get("chat_id") or 0) != chat_id
        ):
            return None
        return item

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
    ) -> None:
        item = self._movie_item_for_chat(pending_id, chat_id)
        if not item or item.get("status") != "awaiting_identification":
            await self.send(chat_id, "This movie is no longer waiting for identification.")
            return
        try:
            await self.send(chat_id, f"Searching IMDb movies for: {query}")
            results, source = await self.imdb.search(
                query, media_type="movie"
            )
        except Exception as exc:
            LOG.warning("Optional IMDb movie search failed: %s", exc)
            if manual_query:
                await self._offer_manual_movie_fallback(
                    chat_id, pending_id, query, f"IMDb search is unavailable: {exc}"
                )
            else:
                self.movie_manual_pending[chat_id] = pending_id
                await self.send(
                    chat_id,
                    f"IMDb filename search is unavailable: {exc}\n\n"
                    "Send the movie title manually. The bot will let you use that "
                    "exact name if IMDb remains unavailable.",
                )
            return
        if not results:
            if manual_query:
                await self._offer_manual_movie_fallback(
                    chat_id, pending_id, query, "IMDb did not return a movie result."
                )
            else:
                self.movie_manual_pending[chat_id] = pending_id
                await self.send(
                    chat_id,
                    "IMDb did not recognize the filename. Send the movie title "
                    "manually, preferably with its year.",
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
            self.movie_choices[token] = {
                "pending_id": pending_id,
                "title": str(result["title"]),
                "year": result.get("year"),
                "imdb_id": str(result.get("imdb_id") or ""),
                "folder_name": str(result["folder_name"]),
                "source": source,
                "created_at": now,
            }
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
            f"Choose the correct movie result.\nSource: {source}",
            {"inline_keyboard": rows},
        )

    async def _offer_manual_movie_fallback(
        self, chat_id: int, pending_id: int, query: str, reason: str
    ) -> None:
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
            "folder_name": folder_name,
            "source": "Manual name (IMDb unavailable)",
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
        if item:
            extension = Path(item["original_filename"]).suffix
        await self.send(
            chat_id,
            "Confirm this movie identity:\n"
            f"Title: {choice['title']}\n"
            f"Year: {choice.get('year') or 'not specified'}\n"
            f"IMDb: {choice.get('imdb_id') or 'not available'}\n\n"
            f"Folder: {choice['folder_name']}\n"
            f"File: {choice['folder_name']}{extension}",
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
        self.store.set_setting(f"media_mode:{chat_id}", "movie")
        await self.send(
            chat_id,
            "Movie mode enabled. Send one movie video. The bot will ask whether "
            "to search using its filename or a manually entered title.\n\n"
            f"Movie library: {self.config.jellyfin_movie_library_path}",
            MOVIE_MENU,
        )

    async def cmd_series_mode(self, chat_id: int, _: str) -> None:
        self.store.set_setting(f"media_mode:{chat_id}", "series")
        self.movie_manual_pending.pop(chat_id, None)
        await self.send(
            chat_id,
            "Series mode enabled. New video files will use the currently selected "
            "series folder.",
            CHANNEL_MENU,
        )

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
        await self.send(
            chat_id,
            f"Current mode: {mode}\n"
            f"Latest movie queue ID: #{latest['pending_id']}\n"
            f"Status: {latest['status']}\n"
            f"Original file: {latest['original_filename']}\n"
            f"Movie: {latest.get('target_folder') or 'waiting for identification'}",
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
        removed = self.queue.remove(pending_id)
        if self.movie_manual_pending.get(chat_id) == pending_id:
            self.movie_manual_pending.pop(chat_id, None)
        await self.send(
            chat_id,
            "Current movie job cancelled." if removed else "The movie job could not be cancelled.",
        )

    def _queue_display_number(self, pending_id: int, target_folder: str) -> int:
        """Return a friendly per-folder number while keeping pending_id stable."""
        same_folder = [
            item for item in self.queue.pending()
            if (item.get("target_folder") or "") == (target_folder or "")
        ]
        for index, item in enumerate(same_folder, start=1):
            if int(item["pending_id"]) == pending_id:
                return index
        return len(same_folder) + 1

    def _episode_arrival_notice(
        self, filename: str, target_folder: str, pending_id: int
    ) -> str:
        detected = detect_episode(filename)
        if not detected or not target_folder:
            return ""
        season, episode = detected
        existing = self.catalog.contains(
            self.config.target_path(target_folder), season, episode
        )
        if existing:
            return (
                f"⚠️ S{season:02d}E{episode:02d} already exists in the library:\n"
                f"{existing.path.name}"
            )
        for queued in self.queue.pending():
            if queued["pending_id"] == pending_id:
                continue
            if queued.get("target_folder") != target_folder:
                continue
            if detect_episode(queued["original_filename"]) == detected:
                return (
                    f"⚠️ S{season:02d}E{episode:02d} is already queued "
                    f"(Queue ID #{queued['pending_id']})."
                )
        return f"🆕 New episode detected: S{season:02d}E{episode:02d}"

    async def handle_command(self, chat_id: int, text: str) -> None:
        command, _, argument = text.partition(" ")
        command = command.split("@", 1)[0].lower()
        argument = argument.strip()
        handlers = {
            "/start": self.cmd_start, "/help": self.cmd_help, "/menu": self.cmd_menu,
            "/guide": self.cmd_guide,
            "/chatid": self.cmd_chatid,
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
        if self.chat_types.get(chat_id) != "channel":
            await self.send(
                chat_id,
                "Category keyboard enabled. Choose a category below, or keep "
                "using slash commands.",
                PERSISTENT_CATEGORY_KEYBOARD,
            )
        await self.cmd_help(chat_id, "")

    async def cmd_help(self, chat_id: int, _: str) -> None:
        await self.send(
            chat_id,
            HELP + "\n\nThe buttons below copy editable command templates. "
            "After tapping a button, paste the command and add the value.",
            HELP_COMMAND_TEMPLATES,
        )

    async def cmd_guide(self, chat_id: int, _: str) -> None:
        await self.send(
            chat_id,
            "Choose the guide language:\nزبان راهنما را انتخاب کنید:",
            GUIDE_LANGUAGE_MENU,
        )

    async def cmd_menu(self, chat_id: int, _: str) -> None:
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
            "Download and sorting control menu:",
            CHANNEL_MENU,
        )

    async def cmd_chatid(self, chat_id: int, _: str) -> None:
        await self.send(chat_id, f"chat_id for this chat:\n{chat_id}")

    async def cmd_setfolder(self, chat_id: int, argument: str) -> None:
        if not argument.strip():
            await self.send(chat_id, "Correct format:\n/setfolder dr ston")
            return
        self.track_task(
            self._run_imdb_search(chat_id, argument, "use"),
            f"imdb-search:{chat_id}",
        )

    async def _commit_folder(self, chat_id: int, folder_name: str) -> None:
        try:
            folder = sanitize_folder_name(folder_name)
            path = self.config.target_path(folder)
            self.store.set_setting("current_folder", folder)
            await self.send(
                chat_id,
                f"Target folder set after confirmation:\n{path}",
                CHANNEL_MENU,
            )
        except ValueError as exc:
            await self.send(chat_id, str(exc))

    @staticmethod
    def _folder_token(name: str) -> str:
        return hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]

    def _existing_series_folders(self) -> list[Path]:
        folders: list[Path] = []
        for folder in self.config.jellyfin_library_path.iterdir():
            if not folder.is_dir() or folder.name.startswith("_"):
                continue
            try:
                # Reuse the path-containment guard; directory junctions that
                # escape the configured library are deliberately excluded.
                safe = self.config.target_path(folder.name)
            except ValueError:
                continue
            if safe == folder.resolve():
                folders.append(folder)
        return sorted(folders, key=lambda path: path.name.casefold())

    def _folder_picker_markup(self, page: int, page_size: int = 12) -> tuple[dict, int, int]:
        folders = self._existing_series_folders()
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
        markup, page, pages = self._folder_picker_markup(page)
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
        self.store.set_setting("current_folder", folder.name)
        await self.send(
            chat_id,
            "Existing folder selected as the target for new episodes:\n"
            f"{folder}\n\nNew files added to the queue after this will go to this folder.",
            CHANNEL_MENU,
        )

    async def cmd_folders(self, chat_id: int, _: str) -> None:
        await self._send_folder_picker(chat_id)

    async def cmd_usefolder(self, chat_id: int, argument: str) -> None:
        try:
            name = sanitize_folder_name(argument)
            folder = self.config.target_path(name)
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
        folder = self.store.get_setting("current_folder")
        if not folder:
            await self.send(chat_id, "No target folder is set. Use /setfolder NAME")
        else:
            await self.send(chat_id, f"Current folder:\n{self.config.target_path(folder)}")

    async def cmd_renamefolder(self, chat_id: int, argument: str) -> None:
        assert self.downloader
        old_name = self.store.get_setting("current_folder")
        if not old_name:
            await self.send(chat_id, "No current folder is set. Use /setfolder first.")
            return
        if self.downloader.running or self.sorter.active:
            await self.send(chat_id, "You cannot rename the folder while a download or sort is running.")
            return
        try:
            new_name = sanitize_folder_name(argument)
            old_path = self.config.target_path(old_name)
            new_path = self.config.target_path(new_name)
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
                ok, output = await self.sorter.rename_folder(old_path, new_name)
                if not ok:
                    await self.send(
                        chat_id,
                        "Rename failed and the bot state was not changed.\n" + output[-2500:],
                    )
                    return
            changed = self.store.rename_target_folder(
                old_name, new_name, old_path, new_path
            )
            self.store.set_setting("current_folder", new_name)
            if self.store.get_setting("latest_downloaded_folder") == old_name:
                self.store.set_setting("latest_downloaded_folder", new_name)
            latest_file = self.store.get_setting("latest_downloaded_file")
            old_prefix = str(old_path)
            if latest_file.startswith(old_prefix):
                self.store.set_setting(
                    "latest_downloaded_file",
                    str(new_path) + latest_file[len(old_prefix):],
                )
            await self.send(
                chat_id,
                f"Folder renamed:\n{old_path}\n→ {new_path}\n"
                f"Updated {changed} queued target(s) and rollback paths too.",
            )
        except Exception as exc:
            LOG.exception("Folder rename failed")
            await self.send(chat_id, f"Folder rename failed: {exc}")

    async def cmd_unsetfolder(self, chat_id: int, _: str) -> None:
        self.store.set_setting("current_folder", "")
        await self.send(chat_id, "Target folder cleared.")

    async def cmd_queue(self, chat_id: int, _: str) -> None:
        items = self.queue.pending()
        if not items:
            await self.send(chat_id, "The queue is empty.")
            return
        lines = [f"Queue ({len(items)} file(s)):"]
        per_folder_counts: dict[str, int] = {}
        for item in items[:30]:
            kind = item.get("media_kind", "series")
            folder_label = item["target_folder"] or (
                "(waiting for movie identification)" if kind == "movie" else "(no folder)"
            )
            per_folder_counts[folder_label] = per_folder_counts.get(folder_label, 0) + 1
            lines.append(
                f"{kind.title()} · {folder_label} item {per_folder_counts[folder_label]} "
                f"(Queue ID #{item['pending_id']}) [{item['status']}] "
                f"{item['original_filename']} — {format_size(item['file_size'])} "
            )
        if len(items) > 30:
            lines.append(f"... and {len(items)-30} more file(s)")
        await self.send(chat_id, "\n".join(lines))

    async def cmd_clearqueue(self, chat_id: int, _: str) -> None:
        count = self.queue.clear()
        await self.send(chat_id, f"Removed {count} item(s) from the queue.")

    async def cmd_remove(self, chat_id: int, argument: str) -> None:
        try:
            pending_id = int(argument)
        except ValueError:
            await self.send(chat_id, "Correct format: /remove 12")
            return
        await self.send(
            chat_id,
            "Removed from the queue." if self.queue.remove(pending_id) else "No removable item was found.",
        )

    def _prepare_download_items(self) -> list[dict]:
        current = self.store.get_setting("current_folder")
        items = self.queue.downloadable()
        prepared = []
        for item in items:
            if (
                item.get("media_kind", "series") == "series"
                and not item.get("target_folder")
                and current
            ):
                self.store.update_item(item["pending_id"], target_folder=current)
                item["target_folder"] = current
            prepared.append(item)
        return prepared

    async def cmd_download(self, chat_id: int, _: str) -> None:
        assert self.downloader
        if self.downloader.running:
            await self.send(chat_id, "A download is already running.")
            return
        items = self._prepare_download_items()
        if not items:
            await self.send(chat_id, "There are no ready files in the queue.")
            return
        missing = [str(x["pending_id"]) for x in items if not x.get("target_folder")]
        if missing:
            await self.send(
                chat_id, "These files do not have a target folder: " + ", ".join(missing)
                + "\nSend /setfolder NAME first."
            )
            return
        destinations = sorted({
            str(
                self.config.movie_target_path(x["target_folder"])
                if x.get("media_kind") == "movie"
                else self.config.target_path(x["target_folder"])
            )
            for x in items
        })
        total = sum(int(x.get("file_size") or 0) for x in items)
        names = "\n".join(f"• {x['original_filename']}" for x in items[:10])
        summary = (
            "Final download destination:\n" + "\n".join(destinations)
            + f"\n\nCount: {len(items)}\nApprox size: {format_size(total)}\n{names}"
        )
        if len(items) > 10:
            summary += f"\n... and {len(items)-10} more file(s)"
        if self.config.confirm_before_download:
            self.store.set_setting("download_confirmation_chat", str(chat_id))
            await self.send(chat_id, summary + "\n\nSend /confirm_download to start, or /cancel.")
        else:
            self.track_task(
                self._run_downloads_and_movie_imports(chat_id, items),
                f"download:{chat_id}",
            )

    async def cmd_confirm(self, chat_id: int, _: str) -> None:
        assert self.downloader
        if self.store.get_setting("download_confirmation_chat") != str(chat_id):
            await self.send(chat_id, "There is no unconfirmed download request for this chat.")
            return
        self.store.set_setting("download_confirmation_chat", "")
        items = self._prepare_download_items()
        if not items:
            await self.send(chat_id, "There are no ready files to download.")
            return
        self.track_task(
            self._run_downloads_and_movie_imports(chat_id, items),
            f"download:{chat_id}",
        )

    async def _run_downloads_and_movie_imports(
        self, chat_id: int, items: list[dict]
    ) -> None:
        assert self.downloader
        await self.downloader.run(items, lambda text: self.send(chat_id, text))
        imported = 0
        for original in items:
            if original.get("media_kind") != "movie":
                continue
            current = self.store.get_item(int(original["pending_id"]))
            if not current or current.get("status") != "completed":
                continue
            if await self._import_movie_item(chat_id, current):
                imported += 1
        if (
            imported
            and self.config.scan_after_movie_import
            and self.jellyfin
            and self.jellyfin.configured
        ):
            await self._run_jellyfin_scan(chat_id)

    async def _import_movie_item(self, chat_id: int, item: dict) -> bool:
        pending_id = int(item["pending_id"])
        try:
            await self.send(
                chat_id,
                f"Checking movie import plan for queue ID #{pending_id}...",
            )
            preview = await self.movie_sorter.import_movie(item, dry_run=True)
            await self.send(
                chat_id,
                "Movie import plan verified. Importing without overwriting:\n"
                f"{preview['destination']}",
            )
            result = await self.movie_sorter.import_movie(item, dry_run=False)
            video = next(
                (
                    entry["destination"]
                    for entry in result.get("files", [])
                    if entry.get("file_type") == "video"
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
            self.store.set_setting("latest_imported_movie_id", str(pending_id))
            self.store.set_setting("latest_movie_batch_id", str(result.get("batch_id", "")))
            staging_file = Path(str(item.get("downloaded_path") or ""))
            try:
                staging_file.parent.rmdir()
            except OSError:
                pass
            await self.send(
                chat_id,
                "Movie imported successfully.\n"
                f"Destination: {result['destination']}\n"
                f"Batch ID: {result['batch_id']}\n\n"
                "This movie job is closed; send another movie while remaining in movie mode.",
                MOVIE_MENU,
            )
            return True
        except Exception as exc:
            LOG.exception("Movie import failed for queue ID %s", pending_id)
            self.store.update_item(
                pending_id, status="movie_import_failed", error=str(exc)
            )
            await self.send(
                chat_id,
                f"Movie download is safe in staging, but import failed for queue "
                f"ID #{pending_id}:\n{exc}\n\n"
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
        )

    async def cmd_status(self, chat_id: int, _: str) -> None:
        all_items = self.store.list_items()
        counts: dict[str, int] = {}
        for item in all_items:
            counts[item["status"]] = counts.get(item["status"], 0) + 1
        part_count = sum(1 for _ in self.config.jellyfin_library_path.rglob("*.part"))
        if self.config.movie_staging_path is not None:
            part_count += sum(
                1 for _ in self.config.movie_staging_path.rglob("*.part")
            )
        text = "\n".join(f"{key}: {value}" for key, value in sorted(counts.items()))
        active = len(self.background_tasks)
        await self.send(
            chat_id,
            (text or "No files have been registered yet.")
            + f"\nIncomplete .part files: {part_count}"
            + f"\nTracked background tasks: {active}",
        )

    async def cmd_cancel(self, chat_id: int, _: str) -> None:
        self.store.set_setting("download_confirmation_chat", "")
        cancelled = bool(self.downloader and self.downloader.cancel())
        await self.send(chat_id, "Cancel request registered." if cancelled else "There is no active operation.")

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
        item = self.store.get_item(pending_id)
        if not item or item["status"] != "waiting_overwrite":
            await self.send(chat_id, "This file is not waiting for an overwrite decision.")
            return
        if parts[1] == "skip":
            self.queue.set_status(pending_id, "skipped", "Skipped by user decision.")
        else:
            self.queue.set_status(
                pending_id, "queued", None, overwrite_policy=parts[1]
            )
        await self.send(chat_id, "Decision saved. Send /download to continue.")

    async def _run_sorter(self, chat_id: int, folder_name: str) -> None:
        try:
            folder = self.config.target_path(folder_name)
            if not folder.is_dir():
                await self.send(chat_id, f"Folder not found:\n{folder}")
                return
            await self.send(chat_id, f"Sorting started:\n{folder}")
            ok, output = await self.sorter.run(folder)
            await self.send(
                chat_id,
                ("Sorting completed successfully.\n" if ok else "Sorting finished with errors.\n") + output[-3000:],
            )
        except Exception as exc:
            LOG.exception("Sorter error")
            await self.send(chat_id, f"Sorter error: {exc}")

    async def cmd_sort_current(self, chat_id: int, _: str) -> None:
        folder = self.store.get_setting("current_folder")
        if not folder:
            await self.send(chat_id, "No current folder is selected.")
            return
        self.track_task(self._run_sorter(chat_id, folder), f"sort-current:{chat_id}")

    async def _run_series_sort_action(
        self, chat_id: int, action: str, label: str
    ) -> None:
        folder_name = self.store.get_setting("current_folder")
        if not folder_name:
            await self.send(chat_id, "No current folder is selected.")
            return
        if self.downloader and self.downloader.running:
            await self.send(chat_id, "Wait for the current download to finish first.")
            return
        folder = self.config.target_path(folder_name)
        if not folder.is_dir():
            await self.send(chat_id, f"Folder not found:\n{folder}")
            return
        try:
            await self.send(chat_id, f"{label}:\n{folder}")
            ok, output = await self.sorter.series_action(action, folder)
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
        )

    async def cmd_sort_history(self, chat_id: int, _: str) -> None:
        self.track_task(
            self._run_series_sort_action(chat_id, "sort-history", "Reading sort history"),
            f"sort-history:{chat_id}",
        )

    async def cmd_sort_back(self, chat_id: int, _: str) -> None:
        self.track_task(
            self._run_series_sort_action(chat_id, "sort-back", "Moving one revision back"),
            f"sort-back:{chat_id}",
        )

    async def cmd_sort_forward(self, chat_id: int, _: str) -> None:
        self.track_task(
            self._run_series_sort_action(chat_id, "sort-forward", "Moving one revision forward"),
            f"sort-forward:{chat_id}",
        )

    async def cmd_recover_current(self, chat_id: int, _: str) -> None:
        self.track_task(
            self._run_series_sort_action(
                chat_id,
                "recover-folder",
                "Checking the current folder for incomplete operations",
            ),
            f"recover-current:{chat_id}",
        )

    async def cmd_fix_metadata_current(self, chat_id: int, _: str) -> None:
        self.track_task(
            self._run_series_sort_action(
                chat_id,
                "fix-metadata",
                "Renaming episode metadata in the current folder",
            ),
            f"fix-metadata-current:{chat_id}",
        )

    async def cmd_sort_latest(self, chat_id: int, _: str) -> None:
        folder = self.store.get_setting("latest_downloaded_folder")
        if not folder:
            await self.send(chat_id, "No completed download has been recorded yet.")
            return
        self.track_task(self._run_sorter(chat_id, folder), f"sort-latest:{chat_id}")

    async def cmd_sort_folder(self, chat_id: int, argument: str) -> None:
        try:
            folder = sanitize_folder_name(argument)
        except ValueError as exc:
            await self.send(chat_id, str(exc))
            return
        self.track_task(self._run_sorter(chat_id, folder), f"sort-folder:{chat_id}")

    async def cmd_sort_status(self, chat_id: int, _: str) -> None:
        run = self.store.latest_sorter_run()
        if not run:
            await self.send(chat_id, "The sorter has not run yet.")
        else:
            await self.send(
                chat_id,
                f"Latest run #{run['id']}\nStatus: {run['status']}\n"
                f"Folder: {run['folder']}\nTime: {run['started_at']}",
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
        try:
            label = f"Batch {batch_id}" if batch_id else "latest batch"
            await self.send(chat_id, f"Sort undo started: {label}")
            if batch_id:
                ok, output = await self.sorter.undo_batch(batch_id)
            else:
                ok, output = await self.sorter.undo_last()
            await self.send(
                chat_id,
                ("Undo completed successfully.\n" if ok else "Undo was incomplete or had errors.\n")
                + output[-3000:],
            )
        except Exception as exc:
            LOG.exception("Sort undo error")
            await self.send(chat_id, f"Sort undo error: {exc}")

    async def cmd_undo_sort_last(self, chat_id: int, _: str) -> None:
        self.track_task(self._run_sort_undo(chat_id), f"undo-sort-last:{chat_id}")

    async def cmd_undo_sort_batch(self, chat_id: int, argument: str) -> None:
        batch_id = argument.strip()
        if not batch_id:
            await self.send(
                chat_id,
                "Correct format:\n/undo_sort_batch 20260628-024900-a1b2c3d4",
            )
            return
        self.track_task(self._run_sort_undo(chat_id, batch_id), f"undo-sort-batch:{chat_id}")

    async def _run_movie_undo(
        self, chat_id: int, batch_id: str | None = None
    ) -> None:
        if self.downloader and self.downloader.running:
            await self.send(chat_id, "Movies cannot be restored while a download is running.")
            return
        try:
            await self.send(
                chat_id,
                f"Movie undo started: {batch_id or 'latest movie batch'}",
            )
            result = (
                await self.movie_sorter.undo_batch(batch_id)
                if batch_id
                else await self.movie_sorter.undo_last()
            )
            actual_batch = str(result.get("batch_id") or batch_id or "")
            skipped = int(result.get("skipped", 0) or 0)
            if actual_batch:
                self.store.mark_movie_batch_status(
                    actual_batch,
                    "movie_undone" if bool(result.get("ok")) else "movie_undo_partial",
                )
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
            self._run_movie_undo(chat_id), f"movie-undo-last:{chat_id}"
        )

    async def cmd_movie_undo_batch(self, chat_id: int, argument: str) -> None:
        batch_id = argument.strip()
        if not batch_id:
            await self.send(chat_id, "Correct format: /movie_undo_batch BATCH_ID")
            return
        self.track_task(
            self._run_movie_undo(chat_id, batch_id),
            f"movie-undo-batch:{chat_id}",
        )

    async def _run_jellyfin_scan(self, chat_id: int) -> None:
        if not self.jellyfin:
            await self.send(chat_id, "Jellyfin connection is not ready yet.")
            return

        async def report_scan_update(update: dict) -> None:
            phase = update.get("phase")
            progress = update.get("progress")
            progress_text = (
                f" ({float(progress):.0f}%)"
                if isinstance(progress, (int, float))
                else ""
            )
            if phase == "accepted":
                await self.send(
                    chat_id,
                    "Jellyfin accepted the scan request. I will notify you "
                    "when the library scan finishes.",
                )
            elif phase == "already-running":
                await self.send(
                    chat_id,
                    "A Jellyfin library scan is already running. I will monitor "
                    "it and notify you when it finishes.",
                )
            elif phase == "running":
                await self.send(
                    chat_id,
                    f"Jellyfin library scan is running{progress_text}.",
                )
            elif phase == "progress":
                await self.send(
                    chat_id,
                    f"Jellyfin scan progress: {float(progress):.0f}%",
                )

        try:
            await self.send(chat_id, "Sending Jellyfin library scan request...")
            result = await self.jellyfin.scan_library_and_wait(
                report_scan_update
            )
            status = str(result.get("status", "unknown"))
            completed_at = result.get("completed_at") or "unknown"
            if status.casefold() == "completed":
                await self.send(
                    chat_id,
                    "✅ Jellyfin library scan completed.\n"
                    "The latest library state is ready.\n"
                    f"Completed at: {completed_at}",
                )
            else:
                await self.send(
                    chat_id,
                    "⚠️ Jellyfin stopped scanning, but it did not report a "
                    "successful completion.\n"
                    f"Result: {status}\n"
                    f"Stopped at: {completed_at}\n"
                    "Check the Jellyfin dashboard or server logs.",
                )
        except TimeoutError as exc:
            LOG.warning("Jellyfin scan monitoring timed out: %s", exc)
            await self.send(
                chat_id,
                "Jellyfin accepted the scan, but the bot stopped waiting before "
                "Jellyfin reported completion.\n"
                f"{exc}\n"
                "The scan was not cancelled. Use /jellyfin_status to check it.",
            )
        except Exception as exc:
            LOG.exception("Jellyfin scan request failed")
            await self.send(chat_id, f"Jellyfin scan error: {exc}")

    async def cmd_jellyfin_scan(self, chat_id: int, _: str) -> None:
        self.track_task(self._run_jellyfin_scan(chat_id), f"jellyfin-scan:{chat_id}")

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

    async def _run_imdb_search(
        self, chat_id: int, query: str, mode: str
    ) -> None:
        if not query.strip():
            command = "/imdb_fix_current" if mode == "rename" else "/imdb_search"
            await self.send(chat_id, f"Correct format:\n{command} dr ston")
            return
        source_folder = (
            self.store.get_setting("current_folder") if mode == "rename" else ""
        )
        try:
            await self.send(chat_id, f"Searching IMDb for: {query}")
            results, source = await self.imdb.search(query, media_type="series")
            if not results:
                await self._offer_manual_folder_fallback(
                    chat_id,
                    query,
                    mode,
                    "IMDb did not return any results.",
                    source_folder,
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
                    "folder_name": result["folder_name"],
                    "mode": mode,
                    "created_at": now,
                    "source": source,
                    "source_folder": source_folder,
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
                f"{action_text}\nSource: {source}\n"
                "Final folder format: Title (Year) [imdbid-ID]",
                {"inline_keyboard": rows},
            )
        except Exception as exc:
            LOG.warning("Optional IMDb fuzzy search failed: %s", exc)
            await self._offer_manual_folder_fallback(
                chat_id,
                query,
                mode,
                f"Optional IMDb search is not available: {exc}",
                source_folder,
            )

    async def _offer_folder_confirmation(
        self, chat_id: int, token: str, choice: dict
    ) -> None:
        source = choice.get("source", "IMDb fuzzy search")
        action = "Rename current folder" if choice["mode"] == "rename" else "Set destination"
        await self.send(
            chat_id,
            f"Suggested folder name:\n{choice['folder_name']}\n\n"
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
    ) -> None:
        try:
            manual_name = sanitize_folder_name(entered_name)
        except ValueError as exc:
            await self.send(chat_id, f"{reason}\nThe manual name is not valid either: {exc}")
            return
        token = uuid.uuid4().hex[:16]
        choice = {
            "folder_name": manual_name,
            "mode": mode,
            "created_at": time.time(),
            "source": "Manual fallback (IMDb unavailable)",
            "source_folder": source_folder,
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
        )

    async def cmd_imdb_fix_current(self, chat_id: int, argument: str) -> None:
        query = argument.strip() or self.store.get_setting("current_folder")
        if not query:
            await self.send(
                chat_id,
                "No current folder is selected. Use /folders or /setfolder first.",
            )
            return
        self.track_task(
            self._run_imdb_search(chat_id, query, "rename"),
            f"imdb-fix-current:{chat_id}",
        )

    async def cmd_episodes(self, chat_id: int, argument: str) -> None:
        folder_name = argument.strip() or self.store.get_setting("current_folder")
        if not folder_name:
            await self.send(
                chat_id,
                "No folder was specified.\nUse /episodes Anime Name\nor select one first with /setfolder.",
            )
            return
        try:
            folder_name = sanitize_folder_name(folder_name)
            folder = self.config.target_path(folder_name)
        except ValueError as exc:
            await self.send(chat_id, str(exc))
            return
        if not folder.is_dir():
            await self.send(chat_id, f"Folder not found:\n{folder}")
            return
        entries = await asyncio.to_thread(self.catalog.scan_series, folder)
        await self.send(chat_id, format_series_inventory(folder_name, entries))

    def _library_episode_summary(self) -> str:
        lines = ["📚 Jellyfin library episode summary"]
        series_count = 0
        for folder in sorted(
            (p for p in self.config.jellyfin_library_path.iterdir() if p.is_dir()),
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
            lines.append(f"• {folder.name} — {seasons}")
            if len(lines) >= 60:
                lines.append("... result shortened; use /episodes NAME for details")
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
