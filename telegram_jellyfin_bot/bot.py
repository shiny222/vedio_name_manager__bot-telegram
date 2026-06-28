from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import mimetypes
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
    from telegram_jellyfin_bot.imdb_bridge import ImdbFuzzySearchBridge
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
    from .imdb_bridge import ImdbFuzzySearchBridge
    from .queue_manager import QueueManager
    from .sorter_bridge import SorterBridge
    from .state_store import StateStore
    from .utils import format_size, sanitize_folder_name, setup_logging, validate_original_filename

LOG = logging.getLogger(__name__)
HELP = """دستورها:
/menu - نمایش منوی دکمه‌ای
/setfolder NAME - تنظیم فولدر مقصد
/folders - انتخاب از فولدرهای موجود
/usefolder NAME - انتخاب فولدر موجود با نام
/renamefolder NAME - اصلاح نام فولدر فعلی
/folder - نمایش فولدر فعلی
/unsetfolder - پاک کردن فولدر فعلی
/queue - نمایش صف
/remove ID - حذف از صف
/clearqueue - پاک کردن صف
/download - بررسی و آماده‌سازی دانلود
/confirm_download - تایید و شروع
/status - وضعیت
/cancel - لغو دانلود
/resolve ID skip|overwrite|save_with_suffix - تصمیم فایل تکراری
/sort_current - مرتب‌سازی فولدر فعلی
/sort_latest - مرتب‌سازی آخرین فولدر دانلود
/sort_folder NAME - مرتب‌سازی یک فولدر
/sort_status - وضعیت sorter
/undo_sort_last - برگرداندن آخرین مرتب‌سازی
/undo_sort_batch ID - برگرداندن Batch مشخص
/jellyfin_scan - شروع Scan کتابخانه Jellyfin
/jellyfin_status - وضعیت اتصال Jellyfin
/episodes [NAME] - نمایش اپیزودهای یک سریال
/library_episodes - خلاصه تمام سریال‌ها
/imdb_search NAME - جستجوی نام درست در IMDb
/imdb_fix_current NAME - اصلاح فولدر فعلی با نتیجه IMDb
/chatid - نمایش شناسه چت
/help - راهنما"""

BOT_COMMANDS = [
    {"command": "menu", "description": "نمایش منوی دکمه‌ای"},
    {"command": "setfolder", "description": "تنظیم فولدر مقصد"},
    {"command": "folders", "description": "انتخاب از فولدرهای موجود"},
    {"command": "usefolder", "description": "انتخاب فولدر موجود با نام"},
    {"command": "renamefolder", "description": "اصلاح نام فولدر فعلی"},
    {"command": "folder", "description": "نمایش فولدر فعلی"},
    {"command": "unsetfolder", "description": "پاک کردن فولدر فعلی"},
    {"command": "queue", "description": "نمایش صف دانلود"},
    {"command": "remove", "description": "حذف یک فایل از صف"},
    {"command": "clearqueue", "description": "پاک کردن صف"},
    {"command": "download", "description": "آماده‌سازی دانلود"},
    {"command": "confirm_download", "description": "تأیید و شروع دانلود"},
    {"command": "status", "description": "نمایش وضعیت"},
    {"command": "cancel", "description": "لغو عملیات فعلی"},
    {"command": "sort_current", "description": "مرتب‌سازی فولدر فعلی"},
    {"command": "sort_latest", "description": "مرتب‌سازی آخرین دانلود"},
    {"command": "sort_folder", "description": "مرتب‌سازی فولدر مشخص"},
    {"command": "sort_status", "description": "وضعیت مرتب‌ساز"},
    {"command": "undo_sort_last", "description": "برگرداندن آخرین مرتب‌سازی"},
    {"command": "undo_sort_batch", "description": "برگرداندن Batch مشخص"},
    {"command": "jellyfin_scan", "description": "شروع Scan کتابخانه Jellyfin"},
    {"command": "jellyfin_status", "description": "وضعیت اتصال Jellyfin"},
    {"command": "episodes", "description": "نمایش اپیزودهای یک سریال"},
    {"command": "library_episodes", "description": "خلاصه اپیزودهای Library"},
    {"command": "imdb_search", "description": "جستجوی نام درست در IMDb"},
    {"command": "imdb_fix_current", "description": "اصلاح فولدر فعلی با IMDb"},
    {"command": "chatid", "description": "نمایش شناسه چت"},
    {"command": "help", "description": "نمایش راهنما"},
]

CHANNEL_MENU = {
    "inline_keyboard": [
        [
            {"text": "📁 فولدر فعلی", "callback_data": "menu:folder"},
            {"text": "📋 صف", "callback_data": "menu:queue"},
        ],
        [
            {"text": "🗂 انتخاب فولدر موجود", "callback_data": "menu:folders"},
        ],
        [
            {"text": "⬇️ دانلود", "callback_data": "menu:download"},
            {"text": "✅ تأیید دانلود", "callback_data": "menu:confirm"},
        ],
        [
            {"text": "📊 وضعیت", "callback_data": "menu:status"},
            {"text": "⛔ لغو", "callback_data": "menu:cancel"},
        ],
        [
            {"text": "🧹 مرتب‌سازی فعلی", "callback_data": "menu:sort_current"},
            {"text": "🧹 مرتب‌سازی آخرین", "callback_data": "menu:sort_latest"},
        ],
        [
            {"text": "↩️ Undo آخرین Sort", "callback_data": "menu:undo_sort_last"},
            {"text": "🔢 Undo با Batch ID", "callback_data": "menu:undo_batch_help"},
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
            {"text": "✏️ تنظیم/اصلاح فولدر", "callback_data": "menu:folder_help"},
            {"text": "❓ راهنما", "callback_data": "menu:help"},
        ],
    ]
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
                "copy_text": {"text": "/imdb_fix_current "},
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

    async def call(self, method: str, **params: Any) -> Any:
        url = f"{self.config.api_root}/{method}"
        async with self.session.post(url, data=params) as response:
            try:
                payload = await response.json()
            except Exception as exc:
                text = await response.text()
                raise RuntimeError(f"پاسخ نامعتبر Local Bot API: {text[:300]}") from exc
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
        self.catalog = EpisodeCatalog(config.allowed_video_extensions)
        self.imdb = ImdbFuzzySearchBridge(config)
        self.imdb_choices: dict[str, dict] = {}
        if not self.store.get_setting("current_folder") and config.default_target_folder:
            self.store.set_setting("current_folder", sanitize_folder_name(config.default_target_folder))

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
                for update in updates:
                    await self.handle_update(update)
                    offset = int(update["update_id"]) + 1
                    self.store.set_setting("update_offset", str(offset))
            except asyncio.CancelledError:
                raise
            except Exception:
                LOG.exception("Polling error")
                await asyncio.sleep(3)

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
        if not self.allowed(chat_id):
            LOG.warning("Ignored unauthorized chat_id=%s", chat_id)
            return
        text = str(message.get("text", "")).strip()
        if text.startswith("/"):
            await self.handle_command(chat_id, text)
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
        action = str(query.get("data", ""))
        handlers = {
            "menu:folder": self.cmd_folder,
            "menu:folders": self.cmd_folders,
            "menu:queue": self.cmd_queue,
            "menu:download": self.cmd_download,
            "menu:confirm": self.cmd_confirm,
            "menu:status": self.cmd_status,
            "menu:cancel": self.cmd_cancel,
            "menu:sort_current": self.cmd_sort_current,
            "menu:sort_latest": self.cmd_sort_latest,
            "menu:undo_sort_last": self.cmd_undo_sort_last,
            "menu:jellyfin_scan": self.cmd_jellyfin_scan,
            "menu:jellyfin_status": self.cmd_jellyfin_status,
            "menu:episodes": self.cmd_episodes,
            "menu:library_episodes": self.cmd_library_episodes,
            "menu:open": self.cmd_menu,
            "menu:help": self.cmd_help,
        }
        if action == "menu:folder_help":
            await self.send(
                int(chat_id),
                "برای تنظیم نام:\n/setfolder My Anime\n\n"
                "برای اصلاح نام فعلی:\n/renamefolder Correct Anime Name\n\n"
                "دکمه زیر فرمان را کپی می‌کند؛ سپس آن را Paste کن و نام را بنویس.",
                HELP_COMMAND_TEMPLATES,
            )
            return
        if action == "menu:undo_batch_help":
            await self.send(
                int(chat_id),
                "برای برگرداندن یک Batch مشخص:\n"
                "/undo_sort_batch BATCH_ID\n\n"
                "مثال:\n/undo_sort_batch 20260628-024900-a1b2c3d4",
                CHANNEL_MENU,
            )
            return
        if action == "menu:imdb_help":
            await self.send(
                int(chat_id),
                "برای پیدا کردن نام درست و ساخت مقصد:\n/imdb_search dr ston\n\n"
                "برای تغییر نام امن فولدر فعلی:\n/imdb_fix_current dr ston",
                HELP_COMMAND_TEMPLATES,
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
                    "این انتخاب دیگر معتبر نیست. دوباره /folders را بفرست.",
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
                    "این نتیجه منقضی شده است. دوباره /imdb_search را اجرا کن.",
                )
                return
            await self._offer_folder_confirmation(int(chat_id), token, choice)
            return
        if action.startswith("folderconfirm:"):
            token = action.partition(":")[2]
            choice = self.imdb_choices.pop(token, None)
            if not choice or time.time() - choice["created_at"] > 600:
                await self.send(int(chat_id), "این تأیید منقضی شده است؛ دوباره تلاش کن.")
                return
            if choice["mode"] == "rename":
                await self.cmd_renamefolder(int(chat_id), choice["folder_name"])
            else:
                await self._commit_folder(int(chat_id), choice["folder_name"])
            return
        if action.startswith("foldercancel:"):
            token = action.partition(":")[2]
            self.imdb_choices.pop(token, None)
            await self.send(int(chat_id), "تغییر فولدر لغو شد.", CHANNEL_MENU)
            return
        handler = handlers.get(action)
        if handler:
            await handler(int(chat_id), "")

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
            await self.send(chat_id, "این فایل ویدیویی پشتیبانی نمی‌شود و به صف اضافه نشد.")
            return
        if extension not in self.config.allowed_video_extensions:
            await self.send(chat_id, "پسوند فایل در allowed_video_extensions مجاز نیست.")
            return
        try:
            filename = validate_original_filename(filename)
        except ValueError as exc:
            await self.send(chat_id, f"فایل به صف اضافه نشد: {exc}")
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
        )
        if pending_id is None:
            await self.send(chat_id, "این ویدیو قبلاً در صف ثبت شده است.")
        else:
            notice = self._episode_arrival_notice(
                filename, self.store.get_setting("current_folder"), pending_id
            )
            await self.send(
                chat_id,
                f"ویدیو به صف اضافه شد. شناسه: #{pending_id}"
                + (f"\n{notice}" if notice else ""),
            )

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
                f"⚠️ S{season:02d}E{episode:02d} از قبل در Library وجود دارد:\n"
                f"{existing.path.name}"
            )
        for queued in self.queue.pending():
            if queued["pending_id"] == pending_id:
                continue
            if queued.get("target_folder") != target_folder:
                continue
            if detect_episode(queued["original_filename"]) == detected:
                return (
                    f"⚠️ S{season:02d}E{episode:02d} قبلاً در صف ثبت شده "
                    f"(#{queued['pending_id']})."
                )
        return f"🆕 اپیزود جدید تشخیص داده شد: S{season:02d}E{episode:02d}"

    async def handle_command(self, chat_id: int, text: str) -> None:
        command, _, argument = text.partition(" ")
        command = command.split("@", 1)[0].lower()
        argument = argument.strip()
        handlers = {
            "/start": self.cmd_help, "/help": self.cmd_help, "/menu": self.cmd_menu,
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
            "/undo_sort_last": self.cmd_undo_sort_last,
            "/undo_sort_batch": self.cmd_undo_sort_batch,
            "/jellyfin_scan": self.cmd_jellyfin_scan,
            "/jellyfin_status": self.cmd_jellyfin_status,
            "/episodes": self.cmd_episodes,
            "/library_episodes": self.cmd_library_episodes,
            "/imdb_search": self.cmd_imdb_search,
            "/imdb_fix_current": self.cmd_imdb_fix_current,
        }
        handler = handlers.get(command)
        if not handler:
            await self.send(chat_id, "دستور شناخته نشد. /help را بفرست.")
            return
        await handler(chat_id, argument)

    async def cmd_help(self, chat_id: int, _: str) -> None:
        await self.send(
            chat_id,
            HELP + "\n\nدکمه‌های زیر فرمان قابل‌ویرایش را کپی می‌کنند. "
            "بعد از زدن دکمه، فرمان را Paste کن و مقدارش را بنویس.",
            HELP_COMMAND_TEMPLATES,
        )

    async def cmd_menu(self, chat_id: int, _: str) -> None:
        await self.send(
            chat_id,
            "منوی مدیریت دانلود و مرتب‌سازی:",
            CHANNEL_MENU,
        )

    async def cmd_chatid(self, chat_id: int, _: str) -> None:
        await self.send(chat_id, f"chat_id این گفتگو:\n{chat_id}")

    async def cmd_setfolder(self, chat_id: int, argument: str) -> None:
        if not argument.strip():
            await self.send(chat_id, "فرمت درست:\n/setfolder dr ston")
            return
        asyncio.create_task(self._run_imdb_search(chat_id, argument, "use"))

    async def _commit_folder(self, chat_id: int, folder_name: str) -> None:
        try:
            folder = sanitize_folder_name(folder_name)
            path = self.config.target_path(folder)
            self.store.set_setting("current_folder", folder)
            await self.send(
                chat_id,
                f"فولدر مقصد پس از تأیید تنظیم شد:\n{path}",
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
                "هیچ فولدر سریالی داخل Jellyfin Library پیدا نشد.",
                CHANNEL_MENU,
            )
            return
        await self.send(
            chat_id,
            f"یک فولدر موجود را انتخاب کن (صفحه {page + 1}/{pages}):",
            markup,
        )

    async def _select_existing_folder(self, chat_id: int, folder: Path) -> None:
        self.store.set_setting("current_folder", folder.name)
        await self.send(
            chat_id,
            "فولدر موجود به‌عنوان مقصد قسمت‌های جدید انتخاب شد:\n"
            f"{folder}\n\nفایل‌های جدیدی که بعد از این وارد صف شوند به این مقصد می‌روند.",
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
                f"این فولدر موجود نیست:\n{folder}\n"
                "برای دیدن فولدرهای موجود /folders را بفرست.",
            )
            return
        await self._select_existing_folder(chat_id, folder)

    async def cmd_folder(self, chat_id: int, _: str) -> None:
        folder = self.store.get_setting("current_folder")
        if not folder:
            await self.send(chat_id, "فولدر مقصد تنظیم نشده است. /setfolder NAME")
        else:
            await self.send(chat_id, f"فولدر فعلی:\n{self.config.target_path(folder)}")

    async def cmd_renamefolder(self, chat_id: int, argument: str) -> None:
        assert self.downloader
        old_name = self.store.get_setting("current_folder")
        if not old_name:
            await self.send(chat_id, "فولدر فعلی تنظیم نشده است. ابتدا /setfolder را بزن.")
            return
        if self.downloader.running or self.sorter.active:
            await self.send(chat_id, "هنگام دانلود یا مرتب‌سازی نمی‌توان نام فولدر را تغییر داد.")
            return
        try:
            new_name = sanitize_folder_name(argument)
            old_path = self.config.target_path(old_name)
            new_path = self.config.target_path(new_name)
        except ValueError as exc:
            await self.send(chat_id, str(exc))
            return
        if new_name == old_name:
            await self.send(chat_id, "نام جدید با نام فعلی یکسان است.")
            return
        if new_path.exists():
            await self.send(
                chat_id,
                f"تغییر انجام نشد؛ فولدر مقصد از قبل وجود دارد:\n{new_path}",
            )
            return
        if old_path.exists() and any(old_path.rglob(".rename_history.json")):
            await self.send(
                chat_id,
                "تغییر انجام نشد؛ این فولدر تاریخچه مرتب‌سازی دارد و تغییر نام "
                "می‌تواند rollback را خراب کند.",
            )
            return
        try:
            if old_path.exists():
                old_path.rename(new_path)
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
                f"نام فولدر اصلاح شد:\n{old_path}\n→ {new_path}\n"
                f"مقصد {changed} مورد صف نیز به‌روزرسانی شد.",
            )
        except OSError as exc:
            LOG.exception("Folder rename failed")
            await self.send(chat_id, f"تغییر نام فولدر انجام نشد: {exc}")

    async def cmd_unsetfolder(self, chat_id: int, _: str) -> None:
        self.store.set_setting("current_folder", "")
        await self.send(chat_id, "فولدر مقصد پاک شد.")

    async def cmd_queue(self, chat_id: int, _: str) -> None:
        items = self.queue.pending()
        if not items:
            await self.send(chat_id, "صف خالی است.")
            return
        lines = [f"صف ({len(items)} فایل):"]
        for item in items[:30]:
            lines.append(
                f"#{item['pending_id']} [{item['status']}] "
                f"{item['original_filename']} — {format_size(item['file_size'])} "
                f"→ {item['target_folder'] or '(بدون فولدر)'}"
            )
        if len(items) > 30:
            lines.append(f"... و {len(items)-30} فایل دیگر")
        await self.send(chat_id, "\n".join(lines))

    async def cmd_clearqueue(self, chat_id: int, _: str) -> None:
        count = self.queue.clear()
        await self.send(chat_id, f"{count} مورد از صف پاک شد.")

    async def cmd_remove(self, chat_id: int, argument: str) -> None:
        try:
            pending_id = int(argument)
        except ValueError:
            await self.send(chat_id, "فرمت درست: /remove 12")
            return
        await self.send(
            chat_id,
            "از صف حذف شد." if self.queue.remove(pending_id) else "مورد قابل حذفی پیدا نشد.",
        )

    def _prepare_download_items(self) -> list[dict]:
        current = self.store.get_setting("current_folder")
        items = self.queue.downloadable()
        prepared = []
        for item in items:
            if not item.get("target_folder") and current:
                self.store.update_item(item["pending_id"], target_folder=current)
                item["target_folder"] = current
            prepared.append(item)
        return prepared

    async def cmd_download(self, chat_id: int, _: str) -> None:
        assert self.downloader
        if self.downloader.running:
            await self.send(chat_id, "دانلود در حال اجرا است.")
            return
        items = self._prepare_download_items()
        if not items:
            await self.send(chat_id, "فایل آماده‌ای در صف نیست.")
            return
        missing = [str(x["pending_id"]) for x in items if not x.get("target_folder")]
        if missing:
            await self.send(
                chat_id, "برای این فایل‌ها مقصد مشخص نیست: " + ", ".join(missing)
                + "\nابتدا /setfolder NAME را بفرست."
            )
            return
        destinations = sorted({str(self.config.target_path(x["target_folder"])) for x in items})
        total = sum(int(x.get("file_size") or 0) for x in items)
        names = "\n".join(f"• {x['original_filename']}" for x in items[:10])
        summary = (
            "مسیر نهایی دانلود:\n" + "\n".join(destinations)
            + f"\n\nتعداد: {len(items)}\nحجم تقریبی: {format_size(total)}\n{names}"
        )
        if len(items) > 10:
            summary += f"\n... و {len(items)-10} فایل دیگر"
        if self.config.confirm_before_download:
            self.store.set_setting("download_confirmation_chat", str(chat_id))
            await self.send(chat_id, summary + "\n\nبرای شروع /confirm_download را بفرست یا /cancel.")
        else:
            asyncio.create_task(self.downloader.run(items, lambda text: self.send(chat_id, text)))

    async def cmd_confirm(self, chat_id: int, _: str) -> None:
        assert self.downloader
        if self.store.get_setting("download_confirmation_chat") != str(chat_id):
            await self.send(chat_id, "درخواست دانلود تاییدنشده‌ای برای این چت وجود ندارد.")
            return
        self.store.set_setting("download_confirmation_chat", "")
        items = self._prepare_download_items()
        if not items:
            await self.send(chat_id, "فایل آماده‌ای برای دانلود نیست.")
            return
        asyncio.create_task(self.downloader.run(items, lambda text: self.send(chat_id, text)))

    async def cmd_status(self, chat_id: int, _: str) -> None:
        all_items = self.store.list_items()
        counts: dict[str, int] = {}
        for item in all_items:
            counts[item["status"]] = counts.get(item["status"], 0) + 1
        part_count = sum(1 for _ in self.config.jellyfin_library_path.rglob("*.part"))
        text = "\n".join(f"{key}: {value}" for key, value in sorted(counts.items()))
        await self.send(chat_id, (text or "هنوز فایلی ثبت نشده است.") + f"\nفایل ناقص .part: {part_count}")

    async def cmd_cancel(self, chat_id: int, _: str) -> None:
        self.store.set_setting("download_confirmation_chat", "")
        cancelled = bool(self.downloader and self.downloader.cancel())
        await self.send(chat_id, "درخواست لغو ثبت شد." if cancelled else "عملیات فعالی وجود ندارد.")

    async def cmd_resolve(self, chat_id: int, argument: str) -> None:
        parts = argument.split()
        if len(parts) != 2 or parts[1] not in {"skip", "overwrite", "save_with_suffix"}:
            await self.send(chat_id, "فرمت: /resolve ID skip|overwrite|save_with_suffix")
            return
        try:
            pending_id = int(parts[0])
        except ValueError:
            await self.send(chat_id, "شناسه باید عدد باشد.")
            return
        item = self.store.get_item(pending_id)
        if not item or item["status"] != "waiting_overwrite":
            await self.send(chat_id, "این فایل منتظر تصمیم overwrite نیست.")
            return
        if parts[1] == "skip":
            self.queue.set_status(pending_id, "skipped", "با تصمیم کاربر رد شد.")
        else:
            self.queue.set_status(
                pending_id, "queued", None, overwrite_policy=parts[1]
            )
        await self.send(chat_id, "تصمیم ذخیره شد. برای ادامه /download را بفرست.")

    async def _run_sorter(self, chat_id: int, folder_name: str) -> None:
        try:
            folder = self.config.target_path(folder_name)
            if not folder.is_dir():
                await self.send(chat_id, f"فولدر پیدا نشد:\n{folder}")
                return
            await self.send(chat_id, f"مرتب‌سازی شروع شد:\n{folder}")
            ok, output = await self.sorter.run(folder)
            await self.send(
                chat_id,
                ("مرتب‌سازی موفق بود.\n" if ok else "مرتب‌سازی خطا داشت.\n") + output[-3000:],
            )
        except Exception as exc:
            LOG.exception("Sorter error")
            await self.send(chat_id, f"خطای sorter: {exc}")

    async def cmd_sort_current(self, chat_id: int, _: str) -> None:
        folder = self.store.get_setting("current_folder")
        if not folder:
            await self.send(chat_id, "فولدر فعلی تنظیم نشده است.")
            return
        asyncio.create_task(self._run_sorter(chat_id, folder))

    async def cmd_sort_latest(self, chat_id: int, _: str) -> None:
        folder = self.store.get_setting("latest_downloaded_folder")
        if not folder:
            await self.send(chat_id, "هنوز دانلود کاملی ثبت نشده است.")
            return
        asyncio.create_task(self._run_sorter(chat_id, folder))

    async def cmd_sort_folder(self, chat_id: int, argument: str) -> None:
        try:
            folder = sanitize_folder_name(argument)
        except ValueError as exc:
            await self.send(chat_id, str(exc))
            return
        asyncio.create_task(self._run_sorter(chat_id, folder))

    async def cmd_sort_status(self, chat_id: int, _: str) -> None:
        run = self.store.latest_sorter_run()
        if not run:
            await self.send(chat_id, "هنوز sorter اجرا نشده است.")
        else:
            await self.send(
                chat_id,
                f"آخرین اجرا #{run['id']}\nوضعیت: {run['status']}\n"
                f"فولدر: {run['folder']}\nزمان: {run['started_at']}",
            )

    async def _run_sort_undo(
        self, chat_id: int, batch_id: str | None = None
    ) -> None:
        if self.downloader and self.downloader.running:
            await self.send(
                chat_id,
                "هنگام دانلود نمی‌توان فایل‌ها را به محل قبلی برگرداند.",
            )
            return
        try:
            label = f"Batch {batch_id}" if batch_id else "آخرین Batch"
            await self.send(chat_id, f"Undo مرتب‌سازی شروع شد: {label}")
            if batch_id:
                ok, output = await self.sorter.undo_batch(batch_id)
            else:
                ok, output = await self.sorter.undo_last()
            await self.send(
                chat_id,
                ("Undo موفق بود.\n" if ok else "Undo کامل نشد یا خطا داشت.\n")
                + output[-3000:],
            )
        except Exception as exc:
            LOG.exception("Sort undo error")
            await self.send(chat_id, f"خطای Undo مرتب‌سازی: {exc}")

    async def cmd_undo_sort_last(self, chat_id: int, _: str) -> None:
        asyncio.create_task(self._run_sort_undo(chat_id))

    async def cmd_undo_sort_batch(self, chat_id: int, argument: str) -> None:
        batch_id = argument.strip()
        if not batch_id:
            await self.send(
                chat_id,
                "فرمت درست:\n/undo_sort_batch 20260628-024900-a1b2c3d4",
            )
            return
        asyncio.create_task(self._run_sort_undo(chat_id, batch_id))

    async def _run_jellyfin_scan(self, chat_id: int) -> None:
        if not self.jellyfin:
            await self.send(chat_id, "اتصال Jellyfin هنوز آماده نیست.")
            return
        try:
            await self.send(chat_id, "درخواست Scan کتابخانه به Jellyfin ارسال شد...")
            requested_at = await self.jellyfin.scan_library()
            await self.send(
                chat_id,
                "Jellyfin درخواست Scan را پذیرفت.\n"
                f"زمان درخواست: {requested_at}\n"
                "توجه: Scan در پس‌زمینه Jellyfin ادامه پیدا می‌کند.",
            )
        except Exception as exc:
            LOG.exception("Jellyfin scan request failed")
            await self.send(chat_id, f"خطای Jellyfin Scan: {exc}")

    async def cmd_jellyfin_scan(self, chat_id: int, _: str) -> None:
        asyncio.create_task(self._run_jellyfin_scan(chat_id))

    async def cmd_jellyfin_status(self, chat_id: int, _: str) -> None:
        if not self.jellyfin:
            await self.send(chat_id, "اتصال Jellyfin هنوز آماده نیست.")
            return
        try:
            info = await self.jellyfin.server_status()
            await self.send(
                chat_id,
                "اتصال Jellyfin برقرار است.\n"
                f"Server: {info.get('ServerName', 'نامشخص')}\n"
                f"Version: {info.get('Version', 'نامشخص')}\n"
                f"{self.jellyfin.last_scan_summary()}",
            )
        except Exception as exc:
            LOG.exception("Jellyfin status failed")
            await self.send(
                chat_id,
                f"اتصال Jellyfin ناموفق بود: {exc}\n"
                f"{self.jellyfin.last_scan_summary()}",
            )

    async def _run_imdb_search(
        self, chat_id: int, query: str, mode: str
    ) -> None:
        if not query.strip():
            command = "/imdb_fix_current" if mode == "rename" else "/imdb_search"
            await self.send(chat_id, f"فرمت درست:\n{command} dr ston")
            return
        try:
            await self.send(chat_id, f"در حال جستجوی IMDb برای: {query}")
            results, source = await self.imdb.search(query)
            if not results:
                await self._offer_manual_folder_fallback(
                    chat_id, query, mode, "IMDb نتیجه‌ای پیدا نکرد."
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
                "نتیجه درست را برای تغییر نام فولدر فعلی انتخاب کن:"
                if mode == "rename"
                else "نتیجه درست را برای مقصد Jellyfin انتخاب کن:"
            )
            await self.send(
                chat_id,
                f"{action_text}\nSource: {source}\n"
                "فرمت نهایی: Title (Year) [imdbid-ID]",
                {"inline_keyboard": rows},
            )
        except Exception as exc:
            LOG.warning("Optional IMDb fuzzy search failed: %s", exc)
            await self._offer_manual_folder_fallback(
                chat_id,
                query,
                mode,
                f"جستجوی اختیاری IMDb در دسترس نیست: {exc}",
            )

    async def _offer_folder_confirmation(
        self, chat_id: int, token: str, choice: dict
    ) -> None:
        source = choice.get("source", "IMDb fuzzy search")
        action = "تغییر نام فولدر فعلی" if choice["mode"] == "rename" else "تنظیم مقصد"
        await self.send(
            chat_id,
            f"نام پیشنهادی:\n{choice['folder_name']}\n\n"
            f"Source: {source}\nAction: {action}\nآیا تأیید می‌کنی؟",
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
        self, chat_id: int, entered_name: str, mode: str, reason: str
    ) -> None:
        try:
            manual_name = sanitize_folder_name(entered_name)
        except ValueError as exc:
            await self.send(chat_id, f"{reason}\nنام دستی نیز معتبر نیست: {exc}")
            return
        token = uuid.uuid4().hex[:16]
        choice = {
            "folder_name": manual_name,
            "mode": mode,
            "created_at": time.time(),
            "source": "Manual fallback (IMDb unavailable)",
        }
        self.imdb_choices[token] = choice
        await self.send(
            chat_id,
            f"{reason}\n\nنام واردشده خودت به‌عنوان fallback پیشنهاد می‌شود.",
        )
        await self._offer_folder_confirmation(chat_id, token, choice)

    async def cmd_imdb_search(self, chat_id: int, argument: str) -> None:
        asyncio.create_task(self._run_imdb_search(chat_id, argument, "use"))

    async def cmd_imdb_fix_current(self, chat_id: int, argument: str) -> None:
        asyncio.create_task(self._run_imdb_search(chat_id, argument, "rename"))

    async def cmd_episodes(self, chat_id: int, argument: str) -> None:
        folder_name = argument.strip() or self.store.get_setting("current_folder")
        if not folder_name:
            await self.send(
                chat_id,
                "فولدر مشخص نیست.\n/episodes Anime Name\nیا ابتدا /setfolder را بزن.",
            )
            return
        try:
            folder_name = sanitize_folder_name(folder_name)
            folder = self.config.target_path(folder_name)
        except ValueError as exc:
            await self.send(chat_id, str(exc))
            return
        if not folder.is_dir():
            await self.send(chat_id, f"فولدر پیدا نشد:\n{folder}")
            return
        entries = await asyncio.to_thread(self.catalog.scan_series, folder)
        await self.send(chat_id, format_series_inventory(folder_name, entries))

    def _library_episode_summary(self) -> str:
        lines = ["📚 خلاصه اپیزودهای Jellyfin Library"]
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
                lines.append("... نتیجه کوتاه شد؛ برای جزئیات /episodes NAME")
                break
        if not series_count:
            return "هیچ اپیزود قابل‌شناسایی در Library پیدا نشد."
        return "\n".join(lines)

    async def cmd_library_episodes(self, chat_id: int, _: str) -> None:
        await self.send(chat_id, "در حال بررسی فایل‌های Library...")
        summary = await asyncio.to_thread(self._library_episode_summary)
        await self.send(chat_id, summary)


async def async_main() -> None:
    config = load_config()
    setup_logging(config.logs_path)
    app = BotApp(config)
    try:
        await app.run()
    finally:
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
