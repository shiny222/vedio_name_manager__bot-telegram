from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from telegram_jellyfin_bot.config import Config, load_config
    from telegram_jellyfin_bot.downloader import DownloadManager
    from telegram_jellyfin_bot.queue_manager import QueueManager
    from telegram_jellyfin_bot.sorter_bridge import SorterBridge
    from telegram_jellyfin_bot.state_store import StateStore
    from telegram_jellyfin_bot.utils import (
        format_size, sanitize_folder_name, setup_logging, validate_original_filename
    )
else:
    from .config import Config, load_config
    from .downloader import DownloadManager
    from .queue_manager import QueueManager
    from .sorter_bridge import SorterBridge
    from .state_store import StateStore
    from .utils import format_size, sanitize_folder_name, setup_logging, validate_original_filename

LOG = logging.getLogger(__name__)
HELP = """دستورها:
/menu - نمایش منوی دکمه‌ای
/setfolder NAME - تنظیم فولدر مقصد
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
/chatid - نمایش شناسه چت
/help - راهنما"""

BOT_COMMANDS = [
    {"command": "menu", "description": "نمایش منوی دکمه‌ای"},
    {"command": "setfolder", "description": "تنظیم فولدر مقصد"},
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
        self.sorter = SorterBridge(config, self.store)
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
            "menu:queue": self.cmd_queue,
            "menu:download": self.cmd_download,
            "menu:confirm": self.cmd_confirm,
            "menu:status": self.cmd_status,
            "menu:cancel": self.cmd_cancel,
            "menu:sort_current": self.cmd_sort_current,
            "menu:sort_latest": self.cmd_sort_latest,
            "menu:undo_sort_last": self.cmd_undo_sort_last,
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
            await self.send(chat_id, f"ویدیو به صف اضافه شد. شناسه: #{pending_id}")

    async def handle_command(self, chat_id: int, text: str) -> None:
        command, _, argument = text.partition(" ")
        command = command.split("@", 1)[0].lower()
        argument = argument.strip()
        handlers = {
            "/start": self.cmd_help, "/help": self.cmd_help, "/menu": self.cmd_menu,
            "/chatid": self.cmd_chatid,
            "/setfolder": self.cmd_setfolder, "/folder": self.cmd_folder,
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
        try:
            folder = sanitize_folder_name(argument)
            path = self.config.target_path(folder)
            self.store.set_setting("current_folder", folder)
            await self.send(chat_id, f"فولدر مقصد تنظیم شد:\n{path}")
        except ValueError as exc:
            await self.send(chat_id, str(exc))

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
