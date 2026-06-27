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
        "allowed_chat_ids": [-100123],
        "allowed_video_extensions": [".mkv", ".mp4"],
        "max_parallel_downloads": 1,
        "default_target_folder": "",
        "confirm_before_download": True,
        "keep_original_filenames": True,
        "ask_before_overwrite": True
    }


class ConfigAndPathTests(unittest.TestCase):
    def test_read_config(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "config.json"
            path.write_text(json.dumps(config_data(root)), encoding="utf-8")
            cfg = load_config(path, create_from_example=False)
            self.assertEqual(cfg.local_bot_api_host, "127.0.0.1")
            self.assertEqual(cfg.allowed_chat_ids, {-100123})

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
                ok, output = await bridge.run(folder, dry_run=True)
                self.assertTrue(ok)
                self.assertIn("dry sorter", output)
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
