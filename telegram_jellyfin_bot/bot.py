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
/chatid - Show this chat ID
/help - Show this help"""

BOT_COMMANDS = [
    {"command": "menu", "description": "Show the button menu"},
    {"command": "setfolder", "description": "Set the target folder"},
    {"command": "folders", "description": "Pick from existing folders"},
    {"command": "usefolder", "description": "Use an existing folder"},
    {"command": "renamefolder", "description": "Rename the current folder"},
    {"command": "folder", "description": "Show the current folder"},
    {"command": "unsetfolder", "description": "Clear the current folder"},
    {"command": "queue", "description": "Show the download queue"},
    {"command": "remove", "description": "Remove one queued file"},
    {"command": "clearqueue", "description": "Clear the queue"},
    {"command": "download", "description": "Prepare downloads"},
    {"command": "confirm_download", "description": "Confirm and start download"},
    {"command": "status", "description": "Show status"},
    {"command": "cancel", "description": "Cancel the current operation"},
    {"command": "resolve", "description": "Resolve an existing-file conflict"},
    {"command": "sort_current", "description": "Sort current folder"},
    {"command": "resort_current", "description": "Rename existing sorted episodes"},
    {"command": "sort_history", "description": "Show numbered sort history"},
    {"command": "sort_back", "description": "Undo one sort revision"},
    {"command": "sort_forward", "description": "Redo one sort revision"},
    {"command": "sort_latest", "description": "Sort latest download"},
    {"command": "sort_folder", "description": "Sort a specific folder"},
    {"command": "sort_status", "description": "Show sorter status"},
    {"command": "undo_sort_last", "description": "Undo latest sorter batch"},
    {"command": "undo_sort_batch", "description": "Undo a specific sorter batch"},
    {"command": "jellyfin_scan", "description": "Start Jellyfin library scan"},
    {"command": "jellyfin_status", "description": "Check Jellyfin connection"},
    {"command": "episodes", "description": "Show episodes for one series"},
    {"command": "library_episodes", "description": "Show all series summary"},
    {"command": "imdb_search", "description": "Fuzzy-search IMDb title"},
    {"command": "imdb_fix_current", "description": "Fix current folder with IMDb"},
    {"command": "chatid", "description": "Show this chat ID"},
    {"command": "help", "description": "Show help"},
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
            {"text": "✏️ Set/rename folder", "callback_data": "menu:folder_help"},
            {"text": "❓ Help", "callback_data": "menu:help"},
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
                "copy_text": {"text": "/imdb_fix_current"},
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
        self.catalog = EpisodeCatalog(config.allowed_video_extensions)
        self.imdb = ImdbFuzzySearchBridge(config)
        self.imdb_choices: dict[str, dict] = {}
        self.background_tasks: set[asyncio.Task] = set()
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
            "menu:resort_current": self.cmd_resort_current,
            "menu:sort_history": self.cmd_sort_history,
            "menu:sort_back": self.cmd_sort_back,
            "menu:sort_forward": self.cmd_sort_forward,
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
            "/resort_current": self.cmd_resort_current,
            "/sort_history": self.cmd_sort_history,
            "/sort_back": self.cmd_sort_back,
            "/sort_forward": self.cmd_sort_forward,
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
            await self.send(chat_id, "Unknown command. Send /help.")
            return
        await handler(chat_id, argument)

    async def cmd_help(self, chat_id: int, _: str) -> None:
        await self.send(
            chat_id,
            HELP + "\n\nThe buttons below copy editable command templates. "
            "After tapping a button, paste the command and add the value.",
            HELP_COMMAND_TEMPLATES,
        )

    async def cmd_menu(self, chat_id: int, _: str) -> None:
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
            folder_label = item["target_folder"] or "(no folder)"
            per_folder_counts[folder_label] = per_folder_counts.get(folder_label, 0) + 1
            lines.append(
                f"{folder_label} item {per_folder_counts[folder_label]} "
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
            if not item.get("target_folder") and current:
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
        destinations = sorted({str(self.config.target_path(x["target_folder"])) for x in items})
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
                self.downloader.run(items, lambda text: self.send(chat_id, text)),
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
            self.downloader.run(items, lambda text: self.send(chat_id, text)),
            f"download:{chat_id}",
        )

    async def cmd_status(self, chat_id: int, _: str) -> None:
        all_items = self.store.list_items()
        counts: dict[str, int] = {}
        for item in all_items:
            counts[item["status"]] = counts.get(item["status"], 0) + 1
        part_count = sum(1 for _ in self.config.jellyfin_library_path.rglob("*.part"))
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

    async def _run_jellyfin_scan(self, chat_id: int) -> None:
        if not self.jellyfin:
            await self.send(chat_id, "Jellyfin connection is not ready yet.")
            return
        try:
            await self.send(chat_id, "Sending Jellyfin library scan request...")
            requested_at = await self.jellyfin.scan_library()
            await self.send(
                chat_id,
                "Jellyfin accepted the scan request.\n"
                f"Requested at: {requested_at}\n"
                "Note: the scan continues in the Jellyfin background.",
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
            await self.send(
                chat_id,
                "Jellyfin connection is working.\n"
                f"Server: {info.get('ServerName', 'unknown')}\n"
                f"Version: {info.get('Version', 'unknown')}\n"
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
            results, source = await self.imdb.search(query)
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
