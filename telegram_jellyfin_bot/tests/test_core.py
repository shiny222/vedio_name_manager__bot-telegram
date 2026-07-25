from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from telegram_jellyfin_bot.config import load_config
from telegram_jellyfin_bot.bot import (
    BOT_COMMANDS,
    BotApp,
    CHANNEL_MENU,
    GUIDE_EN,
    GUIDE_FA,
    GUIDE_LANGUAGE_MENU,
    PERSISTENT_CATEGORY_KEYBOARD,
    SORTING_MENU,
)
from telegram_jellyfin_bot.downloader import DownloadManager
from telegram_jellyfin_bot.episode_catalog import (
    EpisodeCatalog, compact_numbers, detect_episode, format_series_inventory
)
from telegram_jellyfin_bot.jellyfin_bridge import JellyfinBridge
from telegram_jellyfin_bot.queue_manager import QueueManager
from telegram_jellyfin_bot.sorter_bridge import SorterBridge
from telegram_jellyfin_bot.state_store import StateStore
from telegram_jellyfin_bot.utils import safe_child, sanitize_folder_name


def config_data(root: Path) -> dict:
    return {
        "bot_token": "123:test-token",
        "telegram_api_id": 123,
        "telegram_api_hash": "hash",
        "telegram_bot_api_exe_path": str(root / "telegram-bot-api.exe"),
        "local_bot_api_host": "127.0.0.1",
        "local_bot_api_port": 8081,
        "local_bot_api_base_url": "http://127.0.0.1:8081/bot",
        "local_bot_api_base_file_url": "http://127.0.0.1:8081/file/bot",
        "jellyfin_library_path": str(root / "library"),
        "data_path": str(root / "data"),
        "logs_path": str(root / "logs"),
        "sorter_command": [sys.executable, "-c", "print('dry sorter')", "{folder}", "{mode}"],
        "allowed_chat_ids": [-100123, 987654321],
        "allowed_video_extensions": [".mkv", ".mp4"],
        "max_parallel_downloads": 1,
        "default_target_folder": "",
        "confirm_before_download": True,
        "ask_before_overwrite": True,
        "jellyfin_server_url": "http://127.0.0.1:8096",
        "jellyfin_api_key": "test-api-key",
        "jellyfin_scan_poll_interval_seconds": 1,
        "jellyfin_scan_monitor_timeout_seconds": 60,
    }


class ConfigAndPathTests(unittest.TestCase):
    def test_read_config(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "config.json"
            path.write_text(json.dumps(config_data(root)), encoding="utf-8")
            cfg = load_config(path, create_from_example=False)
            self.assertEqual(cfg.local_bot_api_host, "127.0.0.1")
            self.assertEqual(cfg.allowed_chat_ids, {-100123, 987654321})

    def test_sanitize_folder(self):
        self.assertEqual(sanitize_folder_name("My Course"), "My Course")
        self.assertEqual(sanitize_folder_name("Bad:Name"), "Bad_Name")
        for bad in ("../outside", r"C:\Windows", "..", ""):
            with self.assertRaises(ValueError):
                sanitize_folder_name(bad)

    def test_safe_path_stays_in_library(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "library"
            base.mkdir()
            self.assertEqual(safe_child(base, "Anime").parent, base.resolve())
            with self.assertRaises(ValueError):
                safe_child(base, r"..\outside")

    def test_existing_folder_picker(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "config.json"
            path.write_text(json.dumps(config_data(root)), encoding="utf-8")
            cfg = load_config(path, create_from_example=False)
            (cfg.jellyfin_library_path / "Dr. Stone").mkdir()
            (cfg.jellyfin_library_path / "One Piece").mkdir()
            app = BotApp(cfg)
            try:
                folders = app._existing_series_folders()
                self.assertEqual(
                    [item.name for item in folders], ["Dr. Stone", "One Piece"]
                )
                markup, page, pages = app._folder_picker_markup(0)
                labels = [
                    button["text"]
                    for row in markup["inline_keyboard"]
                    for button in row
                ]
                self.assertTrue(any("Dr. Stone" in label for label in labels))
                self.assertEqual((page, pages), (0, 1))
            finally:
                app.store.close()

    def test_queue_display_number_resets_per_folder(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "config.json"
            path.write_text(json.dumps(config_data(root)), encoding="utf-8")
            cfg = load_config(path, create_from_example=False)
            app = BotApp(cfg)
            try:
                first = app.queue.add(
                    message_id=1, chat_id=-1, file_id="f1", file_unique_id="u1",
                    original_filename="a1.mkv", file_size=10,
                    target_folder="Anime A",
                )
                second = app.queue.add(
                    message_id=2, chat_id=-1, file_id="f2", file_unique_id="u2",
                    original_filename="a2.mkv", file_size=10,
                    target_folder="Anime A",
                )
                third = app.queue.add(
                    message_id=3, chat_id=-1, file_id="f3", file_unique_id="u3",
                    original_filename="b1.mkv", file_size=10,
                    target_folder="Anime B",
                )
                self.assertEqual(app._queue_display_number(first, "Anime A"), 1)
                self.assertEqual(app._queue_display_number(second, "Anime A"), 2)
                self.assertEqual(app._queue_display_number(third, "Anime B"), 1)
            finally:
                app.store.close()


class QueueTests(unittest.TestCase):
    def test_queue_persists(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "state.db"
            store = StateStore(db)
            queue = QueueManager(store)
            pending_id = queue.add(
                message_id=1, chat_id=-1, file_id="f", file_unique_id="u",
                original_filename="episode.mkv", file_size=10,
                target_folder="Anime",
            )
            self.assertIsInstance(pending_id, int)
            store.close()
            reopened = StateStore(db)
            self.assertEqual(reopened.get_item(pending_id)["original_filename"], "episode.mkv")
            reopened.close()

    def test_rename_target_folder_updates_queue(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = StateStore(root / "state.db")
            queue = QueueManager(store)
            pending_id = queue.add(
                message_id=2, chat_id=-1, file_id="f2", file_unique_id="u2",
                original_filename="episode2.mkv", file_size=20,
                target_folder="Wrong Name",
            )
            old_path = root / "library" / "Wrong Name"
            new_path = root / "library" / "Correct Name"
            changed = store.rename_target_folder(
                "Wrong Name", "Correct Name", old_path, new_path
            )
            self.assertEqual(changed, 1)
            self.assertEqual(store.get_item(pending_id)["target_folder"], "Correct Name")
            store.close()


class MenuNavigationTests(unittest.TestCase):
    def test_native_command_menu_is_grouped_by_related_function(self):
        labels = [
            item["description"].partition(":")[0]
            for item in BOT_COMMANDS
        ]
        grouped_labels = []
        for label in labels:
            if not grouped_labels or grouped_labels[-1] != label:
                grouped_labels.append(label)
        self.assertEqual(
            grouped_labels,
            [
                "General",
                "Folders",
                "Downloads",
                "Sorting",
                "History",
                "Jellyfin",
                "Episodes",
                "IMDb",
            ],
        )
        commands = [item["command"] for item in BOT_COMMANDS]
        self.assertEqual(len(commands), 36)
        self.assertEqual(len(commands), len(set(commands)))

    def test_bilingual_guide_fits_telegram_and_language_callback_opens_it(self):
        self.assertLessEqual(len(GUIDE_EN), 4000)
        self.assertLessEqual(len(GUIDE_FA), 4000)
        self.assertIn("How to use", GUIDE_EN)
        self.assertIn("راهنمای استفاده", GUIDE_FA)

        async def exercise():
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                path = root / "config.json"
                path.write_text(json.dumps(config_data(root)), encoding="utf-8")
                cfg = load_config(path, create_from_example=False)
                app = BotApp(cfg)
                sent = []

                class FakeApi:
                    async def call(self, method, **params):
                        return True

                async def fake_send(chat_id, text, reply_markup=None):
                    sent.append((chat_id, text, reply_markup))

                app.api = FakeApi()
                app.send = fake_send
                try:
                    await app.cmd_guide(987654321, "")
                    self.assertIs(sent[-1][2], GUIDE_LANGUAGE_MENU)
                    await app.handle_callback(
                        {
                            "id": "guide-callback",
                            "data": "guide:fa",
                            "message": {
                                "chat": {
                                    "id": 987654321,
                                    "type": "private",
                                }
                            },
                        }
                    )
                    self.assertEqual(sent[-1][1], GUIDE_FA)
                    self.assertIs(sent[-1][2], GUIDE_LANGUAGE_MENU)
                finally:
                    app.store.close()

        asyncio.run(exercise())

    def test_private_menu_installs_persistent_keyboard_and_keeps_quick_menu(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                path = root / "config.json"
                path.write_text(json.dumps(config_data(root)), encoding="utf-8")
                cfg = load_config(path, create_from_example=False)
                app = BotApp(cfg)
                sent = []

                async def fake_send(chat_id, text, reply_markup=None):
                    sent.append((chat_id, text, reply_markup))

                app.send = fake_send
                app.chat_types[987654321] = "private"
                try:
                    await app.cmd_menu(987654321, "")
                    self.assertEqual(len(sent), 2)
                    self.assertIs(sent[0][2], PERSISTENT_CATEGORY_KEYBOARD)
                    self.assertIs(sent[1][2], CHANNEL_MENU)
                finally:
                    app.store.close()
        asyncio.run(exercise())

    def test_channel_menu_keeps_inline_keyboard_without_reply_keyboard(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                path = root / "config.json"
                path.write_text(json.dumps(config_data(root)), encoding="utf-8")
                cfg = load_config(path, create_from_example=False)
                app = BotApp(cfg)
                sent = []

                async def fake_send(chat_id, text, reply_markup=None):
                    sent.append((chat_id, text, reply_markup))

                app.send = fake_send
                app.chat_types[-100123] = "channel"
                try:
                    await app.cmd_menu(-100123, "")
                    self.assertEqual(len(sent), 1)
                    self.assertIs(sent[0][2], CHANNEL_MENU)
                    self.assertTrue(
                        any(
                            button.get("callback_data") == "nav:categories"
                            for row in CHANNEL_MENU["inline_keyboard"]
                            for button in row
                        )
                    )
                finally:
                    app.store.close()
        asyncio.run(exercise())

    def test_reply_keyboard_category_opens_inline_submenu(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                path = root / "config.json"
                path.write_text(json.dumps(config_data(root)), encoding="utf-8")
                cfg = load_config(path, create_from_example=False)
                app = BotApp(cfg)
                sent = []

                async def fake_send(chat_id, text, reply_markup=None):
                    sent.append((chat_id, text, reply_markup))

                app.send = fake_send
                try:
                    await app.handle_update(
                        {
                            "message": {
                                "message_id": 1,
                                "chat": {
                                    "id": 987654321,
                                    "type": "private",
                                },
                                "text": "🧹 Sorting",
                            }
                        }
                    )
                    self.assertEqual(len(sent), 1)
                    self.assertIs(sent[0][2], SORTING_MENU)
                    self.assertEqual(app.queue.pending(), [])
                finally:
                    app.store.close()
        asyncio.run(exercise())


class DownloadSafetyTests(unittest.TestCase):
    def test_size_mismatch_keeps_partial_file_and_does_not_publish_it(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                path = root / "config.json"
                path.write_text(json.dumps(config_data(root)), encoding="utf-8")
                cfg = load_config(path, create_from_example=False)
                store = StateStore(cfg.data_path / "state.db")
                queue = QueueManager(store)
                pending_id = queue.add(
                    message_id=2,
                    chat_id=-1,
                    file_id="file-id",
                    file_unique_id="unique-id",
                    original_filename="episode.mkv",
                    file_size=4,
                    target_folder="Show",
                )
                local_source = root / "telegram-source.mkv"
                local_source.write_bytes(b"new")

                async def fake_api_call(method, **params):
                    return {"file_path": str(local_source)}

                async def notify(text):
                    return None

                manager = DownloadManager(cfg, queue, fake_api_call, None)
                try:
                    await manager._download_one(
                        store.get_item(pending_id),
                        notify,
                    )
                    destination = cfg.target_path("Show") / "episode.mkv"
                    self.assertFalse(destination.exists())
                    self.assertEqual(
                        destination.with_name("episode.mkv.part").read_bytes(),
                        b"new",
                    )
                    item = store.get_item(pending_id)
                    self.assertEqual(item["status"], "failed")
                    self.assertIn("size mismatch", item["error"].lower())
                finally:
                    store.close()
        asyncio.run(exercise())

    def test_failed_atomic_overwrite_preserves_existing_file(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                path = root / "config.json"
                path.write_text(json.dumps(config_data(root)), encoding="utf-8")
                cfg = load_config(path, create_from_example=False)
                store = StateStore(cfg.data_path / "state.db")
                queue = QueueManager(store)
                pending_id = queue.add(
                    message_id=3,
                    chat_id=-1,
                    file_id="file-id",
                    file_unique_id="unique-id",
                    original_filename="episode.mkv",
                    file_size=3,
                    target_folder="Show",
                )
                queue.set_status(
                    pending_id,
                    "queued",
                    None,
                    overwrite_policy="overwrite",
                )
                destination = cfg.target_path("Show") / "episode.mkv"
                destination.parent.mkdir()
                destination.write_bytes(b"old")
                local_source = root / "telegram-source.mkv"
                local_source.write_bytes(b"new")

                async def fake_api_call(method, **params):
                    return {"file_path": str(local_source)}

                async def notify(text):
                    return None

                manager = DownloadManager(
                    cfg,
                    queue,
                    fake_api_call,
                    None,
                )
                try:
                    with patch.object(
                        Path,
                        "replace",
                        side_effect=OSError("simulated replace failure"),
                    ):
                        await manager._download_one(
                            store.get_item(pending_id),
                            notify,
                        )
                    self.assertEqual(destination.read_bytes(), b"old")
                    self.assertEqual(store.get_item(pending_id)["status"], "failed")
                finally:
                    store.close()
        asyncio.run(exercise())


class EpisodeCatalogTests(unittest.TestCase):
    def test_detects_common_arrival_names(self):
        cases = {
            "Show - S04E25.mkv": (4, 25),
            "[AWHT] Dr. Stone S4 - 25 [480p].mkv": (4, 25),
            "Anime - 026 [1080p].mkv": (1, 26),
            "فصل ۲ قسمت ۱۲.mkv": (2, 12),
        }
        for filename, expected in cases.items():
            with self.subTest(filename=filename):
                self.assertEqual(detect_episode(filename), expected)

    def test_inventory_and_missing_episodes(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td) / "Anime"
            season = folder / "Season 01"
            unsorted = folder / "_Unsorted"
            season.mkdir(parents=True)
            unsorted.mkdir()
            for episode in (1, 2, 4):
                (season / f"Anime - S01E{episode:02d}.mkv").write_bytes(b"x")
            (unsorted / "Anime - S01E03.mkv").write_bytes(b"x")
            catalog = EpisodeCatalog({".mkv"})
            entries = catalog.scan_series(folder)
            text = format_series_inventory("Anime", entries)
            self.assertEqual(len(entries), 3)
            self.assertIn("01-02, 04", text)
            self.assertIn("Missing: 03", text)
            self.assertEqual(compact_numbers({1, 2, 3, 5}), "01-03, 05")


class FolderSuggestionTests(unittest.TestCase):
    def test_search_proposes_before_commit_and_manual_fallback(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                path = root / "config.json"
                path.write_text(json.dumps(config_data(root)), encoding="utf-8")
                cfg = load_config(path, create_from_example=False)
                app = BotApp(cfg)
                sent = []

                async def fake_send(chat_id, text, reply_markup=None):
                    sent.append((text, reply_markup))

                async def fake_search(query, limit=8):
                    return ([{
                        "imdb_id": "tt9679542",
                        "title": "Dr. Stone",
                        "year": 2019,
                        "type": "TV series",
                        "score": 96.0,
                        "folder_name": "Dr. Stone (2019) [imdbid-tt9679542]",
                    }], "online")

                app.send = fake_send
                app.imdb.search = fake_search
                try:
                    await app._run_imdb_search(1, "dr ston", "use")
                    self.assertEqual(app.store.get_setting("current_folder"), "")
                    choice = next(iter(app.imdb_choices.values()))
                    self.assertEqual(
                        choice["folder_name"],
                        "Dr. Stone (2019) [imdbid-tt9679542]",
                    )
                    await app._commit_folder(1, choice["folder_name"])
                    self.assertEqual(
                        app.store.get_setting("current_folder"),
                        "Dr. Stone (2019) [imdbid-tt9679542]",
                    )
                    await app._offer_manual_folder_fallback(
                        1, "My Typed Name", "use", "offline"
                    )
                    self.assertTrue(
                        any(
                            item["folder_name"] == "My Typed Name"
                            and item["source"].startswith("Manual fallback")
                            for item in app.imdb_choices.values()
                        )
                    )
                finally:
                    app.store.close()
        asyncio.run(exercise())

    def test_imdb_confirmation_refuses_to_rename_a_different_current_folder(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                path = root / "config.json"
                path.write_text(json.dumps(config_data(root)), encoding="utf-8")
                cfg = load_config(path, create_from_example=False)
                app = BotApp(cfg)
                sent = []
                rename_calls = []

                class FakeApi:
                    async def call(self, method, **params):
                        return True

                async def fake_send(chat_id, text, reply_markup=None):
                    sent.append(text)

                async def fake_rename(chat_id, name):
                    rename_calls.append((chat_id, name))

                app.api = FakeApi()
                app.send = fake_send
                app.cmd_renamefolder = fake_rename
                first = cfg.jellyfin_library_path / "First Show"
                first.mkdir()
                app.store.set_setting("current_folder", "First Show")
                token = "rename-test"
                app.imdb_choices[token] = {
                    "folder_name": "Official Show (2026) [imdbid-tt1234567]",
                    "mode": "rename",
                    "created_at": 9999999999,
                    "source": "online",
                    "source_folder": "First Show",
                }
                app.store.set_setting("current_folder", "Second Show")
                try:
                    await app.handle_callback(
                        {
                            "id": "callback-id",
                            "data": f"folderconfirm:{token}",
                            "message": {"chat": {"id": 987654321}},
                        }
                    )
                    self.assertEqual(rename_calls, [])
                    self.assertTrue(
                        any("changed after this IMDb search" in text for text in sent)
                    )
                finally:
                    app.store.close()
        asyncio.run(exercise())

    def test_fix_current_uses_current_folder_as_default_query(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                path = root / "config.json"
                path.write_text(json.dumps(config_data(root)), encoding="utf-8")
                cfg = load_config(path, create_from_example=False)
                app = BotApp(cfg)
                calls = []

                async def fake_search(chat_id, query, mode):
                    calls.append((chat_id, query, mode))

                app._run_imdb_search = fake_search
                app.store.set_setting("current_folder", "NIPPON SAGOKu")
                try:
                    await app.cmd_imdb_fix_current(123, "")
                    await asyncio.sleep(0)
                    self.assertEqual(
                        calls, [(123, "NIPPON SAGOKu", "rename")]
                    )
                finally:
                    app.store.close()
        asyncio.run(exercise())


class SorterTests(unittest.TestCase):
    def test_sorter_bridge_dry_run(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                path = root / "config.json"
                path.write_text(json.dumps(config_data(root)), encoding="utf-8")
                cfg = load_config(path, create_from_example=False)
                folder = cfg.jellyfin_library_path / "Anime"
                folder.mkdir()
                store = StateStore(cfg.data_path / "state.db")
                bridge = SorterBridge(cfg, store)
                command = bridge.build_command(folder, dry_run=True)
                self.assertIn("dry-run", command)
                self.assertIn(str(folder.resolve()), command)
                self.assertTrue(Path(command[0]).is_absolute())
                undo = bridge.build_undo_command("20260628-024900-a1b2c3d4")
                self.assertIn("undo-batch", undo)
                self.assertIn("20260628-024900-a1b2c3d4", undo)
                self.assertIn(str(cfg.jellyfin_library_path), undo)
                with self.assertRaises(ValueError):
                    bridge.build_undo_command("bad id & unsafe")
                rename = bridge.build_rename_command(folder, "Correct Name")
                self.assertIn("rename-folder", rename)
                self.assertIn("Correct Name", rename)
                recover = bridge.build_series_action_command(
                    "recover-folder", folder
                )
                self.assertIn("recover-folder", recover)
                metadata = bridge.build_series_action_command(
                    "fix-metadata", folder
                )
                self.assertIn("fix-metadata", metadata)
                ok, output = await bridge.run(folder, dry_run=True)
                self.assertTrue(ok)
                self.assertIn("dry sorter", output)
                store.close()
        asyncio.run(exercise())

    def test_cancelled_sorter_process_is_recorded(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                path = root / "config.json"
                path.write_text(json.dumps(config_data(root)), encoding="utf-8")
                cfg = load_config(path, create_from_example=False)
                store = StateStore(cfg.data_path / "state.db")
                bridge = SorterBridge(cfg, store)
                task = asyncio.create_task(
                    bridge._execute(
                        root,
                        [sys.executable, "-c", "import time; time.sleep(30)"],
                    )
                )
                await asyncio.sleep(0.2)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertEqual(store.latest_sorter_run()["status"], "cancelled")
                self.assertFalse(bridge.active)
                store.close()
        asyncio.run(exercise())


class PollingRecoveryTests(unittest.TestCase):
    def test_one_failed_update_does_not_block_the_next(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                path = root / "config.json"
                path.write_text(json.dumps(config_data(root)), encoding="utf-8")
                cfg = load_config(path, create_from_example=False)
                app = BotApp(cfg)
                handled = []

                async def fake_handle(update):
                    handled.append(update["update_id"])
                    if update["update_id"] == 10:
                        raise RuntimeError("bad update")

                app.handle_update = fake_handle
                try:
                    offset = await app._process_update_batch(
                        [{"update_id": 10}, {"update_id": 11}],
                        0,
                    )
                    self.assertEqual(handled, [10, 11])
                    self.assertEqual(offset, 12)
                    self.assertEqual(app.store.get_setting("update_offset"), "12")
                finally:
                    app.store.close()
        asyncio.run(exercise())


class _FakeResponse:
    def __init__(self, status=204, data=None):
        self.status = status
        self.data = {} if data is None else data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def text(self):
        return ""

    async def json(self, content_type=None):
        return self.data


class _FakeJellyfinSession:
    def __init__(self, scheduled_tasks=None):
        self.posts = []
        self.gets = []
        self.scheduled_tasks = list(scheduled_tasks or [])
        self.last_scheduled_tasks = []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return _FakeResponse(204)

    def get(self, url, **kwargs):
        self.gets.append((url, kwargs))
        if url.endswith("/ScheduledTasks"):
            if self.scheduled_tasks:
                self.last_scheduled_tasks = self.scheduled_tasks.pop(0)
            return _FakeResponse(200, self.last_scheduled_tasks)
        return _FakeResponse(200, {"ServerName": "Test", "Version": "10.x"})


class JellyfinBridgeTests(unittest.TestCase):
    @staticmethod
    def scan_task(
        state,
        *,
        status="Completed",
        started="2026-07-25T12:00:00Z",
        ended="2026-07-25T12:00:05Z",
        progress=None,
    ):
        return [{
            "Name": "Scan Media Library",
            "Key": "RefreshLibrary",
            "State": state,
            "CurrentProgressPercentage": progress,
            "LastExecutionResult": {
                "StartTimeUtc": started,
                "EndTimeUtc": ended,
                "Status": status,
            },
        }]

    def test_scan_and_status(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                path = root / "config.json"
                path.write_text(json.dumps(config_data(root)), encoding="utf-8")
                cfg = load_config(path, create_from_example=False)
                store = StateStore(cfg.data_path / "state.db")
                session = _FakeJellyfinSession()
                bridge = JellyfinBridge(cfg, store, session)
                await bridge.scan_library()
                info = await bridge.server_status()
                self.assertEqual(info["ServerName"], "Test")
                self.assertTrue(session.posts[0][0].endswith("/Library/Refresh"))
                self.assertTrue(session.gets[0][0].endswith("/System/Info"))
                self.assertEqual(
                    session.posts[0][1]["headers"]["X-Emby-Token"],
                    "test-api-key",
                )
                self.assertIn("accepted", bridge.last_scan_summary())
                store.close()
        asyncio.run(exercise())

    def test_scan_monitor_waits_until_jellyfin_reports_completion(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                path = root / "config.json"
                path.write_text(json.dumps(config_data(root)), encoding="utf-8")
                cfg = load_config(path, create_from_example=False)
                store = StateStore(cfg.data_path / "state.db")
                old = self.scan_task(
                    "Idle",
                    started="2026-07-24T10:00:00Z",
                    ended="2026-07-24T10:00:05Z",
                )
                running = self.scan_task(
                    "Running",
                    started="2026-07-25T12:00:00Z",
                    ended="",
                    progress=42,
                )
                completed = self.scan_task(
                    "Idle",
                    started="2026-07-25T12:00:00Z",
                    ended="2026-07-25T12:00:05Z",
                    progress=None,
                )
                session = _FakeJellyfinSession([old, running, completed])
                bridge = JellyfinBridge(cfg, store, session)
                updates = []

                async def on_update(update):
                    updates.append(update)

                try:
                    result = await bridge.scan_library_and_wait(
                        on_update,
                        poll_interval_seconds=0,
                        timeout_seconds=1,
                    )
                    self.assertEqual(result["status"], "Completed")
                    self.assertEqual(
                        store.get_setting("latest_jellyfin_scan_result"),
                        "completed",
                    )
                    self.assertIn(
                        "accepted",
                        [update["phase"] for update in updates],
                    )
                    self.assertIn(
                        "running",
                        [update["phase"] for update in updates],
                    )
                    self.assertFalse(bridge.active)
                    self.assertTrue(
                        all(
                            call[1]["params"] == {"IsEnabled": "true"}
                            for call in session.gets
                        )
                    )
                finally:
                    store.close()
        asyncio.run(exercise())

    def test_bot_announces_when_jellyfin_scan_is_ready(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                path = root / "config.json"
                path.write_text(json.dumps(config_data(root)), encoding="utf-8")
                cfg = load_config(path, create_from_example=False)
                app = BotApp(cfg)
                sent = []

                class FakeJellyfin:
                    async def scan_library_and_wait(self, on_update):
                        await on_update(
                            {
                                "phase": "accepted",
                                "requested_at": "2026-07-25T12:00:00Z",
                            }
                        )
                        await on_update({"phase": "running", "progress": 50.0})
                        return {
                            "status": "Completed",
                            "completed_at": "2026-07-25T12:00:05Z",
                        }

                async def fake_send(chat_id, text, reply_markup=None):
                    sent.append(text)

                app.jellyfin = FakeJellyfin()
                app.send = fake_send
                try:
                    await app._run_jellyfin_scan(987654321)
                    self.assertTrue(
                        any("accepted the scan" in text for text in sent)
                    )
                    self.assertTrue(
                        any(
                            "library scan completed" in text
                            and "ready" in text
                            for text in sent
                        )
                    )
                finally:
                    app.store.close()
        asyncio.run(exercise())


@unittest.skipUnless(os.environ.get("RUN_LOCAL_API_TEST") == "1", "Local API not requested")
class LocalAPIIntegrationTest(unittest.TestCase):
    def test_get_me(self):
        from telegram_jellyfin_bot.config import PROJECT_DIR
        cfg = load_config(PROJECT_DIR / "config.json", create_from_example=False)
        with urllib.request.urlopen(f"{cfg.api_root}/getMe", timeout=5) as response:
            payload = json.load(response)
        self.assertTrue(payload["ok"])


if __name__ == "__main__":
    unittest.main()
