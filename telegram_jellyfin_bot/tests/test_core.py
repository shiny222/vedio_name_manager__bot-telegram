from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path

from telegram_jellyfin_bot.config import load_config
from telegram_jellyfin_bot.bot import BotApp
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
        "temp_download_path": str(root / "temp"),
        "data_path": str(root / "data"),
        "logs_path": str(root / "logs"),
        "sorter_command": [sys.executable, "-c", "print('dry sorter')", "{folder}", "{mode}"],
        "allowed_chat_ids": [-100123, 987654321],
        "allowed_video_extensions": [".mkv", ".mp4"],
        "max_parallel_downloads": 1,
        "default_target_folder": "",
        "confirm_before_download": True,
        "keep_original_filenames": True,
        "ask_before_overwrite": True,
        "jellyfin_server_url": "http://127.0.0.1:8096",
        "jellyfin_api_key": "test-api-key",
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
                ok, output = await bridge.run(folder, dry_run=True)
                self.assertTrue(ok)
                self.assertIn("dry sorter", output)
                store.close()
        asyncio.run(exercise())


class _FakeResponse:
    def __init__(self, status=204, data=None):
        self.status = status
        self.data = data or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def text(self):
        return ""

    async def json(self, content_type=None):
        return self.data


class _FakeJellyfinSession:
    def __init__(self):
        self.posts = []
        self.gets = []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return _FakeResponse(204)

    def get(self, url, **kwargs):
        self.gets.append((url, kwargs))
        return _FakeResponse(200, {"ServerName": "Test", "Version": "10.x"})


class JellyfinBridgeTests(unittest.TestCase):
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
