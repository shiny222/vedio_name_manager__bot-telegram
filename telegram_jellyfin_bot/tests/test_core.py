from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from telegram_jellyfin_bot.config import load_config
from telegram_jellyfin_bot.bot import (
    ADVANCED_MENU,
    BOT_COMMANDS,
    BotApp,
    CHANNEL_MENU,
    GUIDE_EN,
    GUIDE_FA,
    GUIDE_LANGUAGE_MENU,
    MOVIE_MENU,
    PERSISTENT_CATEGORY_KEYBOARD,
    SERIES_MENU,
    SORTING_MENU,
    TelegramAPI,
    _important,
    _telegram_html,
)
from telegram_jellyfin_bot.downloader import DownloadManager
from telegram_jellyfin_bot.episode_catalog import (
    EpisodeCatalog, compact_numbers, detect_episode, format_series_inventory
)
from telegram_jellyfin_bot.jellyfin_bridge import JellyfinBridge
from telegram_jellyfin_bot.imdb_bridge import movie_query_from_filename
from telegram_jellyfin_bot.n8n_bridge import MediaIdentification
from telegram_jellyfin_bot.queue_manager import QueueManager
from telegram_jellyfin_bot.sorter_bridge import SorterBridge
from telegram_jellyfin_bot.state_store import StateStore
from telegram_jellyfin_bot.utils import safe_child, sanitize_folder_name


def config_data(root: Path) -> dict:
    project_root = Path(__file__).resolve().parents[2]
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
        "jellyfin_movie_library_path": str(root / "movies"),
        "movie_staging_path": str(root / "movie-staging"),
        "data_path": str(root / "data"),
        "logs_path": str(root / "logs"),
        "sorter_command": [sys.executable, "-c", "print('dry sorter')", "{folder}", "{mode}"],
        "movie_sorter_command": [
            sys.executable,
            str(project_root / "movie_organizer" / "movie_organizer.py"),
        ],
        "movie_sorter_timeout_seconds": 30,
        "scan_after_movie_import": False,
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
            self.assertEqual(cfg.telegram_download_read_timeout_seconds, 1800)

    def test_sanitize_folder(self):
        self.assertEqual(sanitize_folder_name("My Course"), "My Course")
        self.assertEqual(sanitize_folder_name("Bad:Name"), "Bad_Name")
        for bad in ("../outside", r"C:\Windows", "..", ""):
            with self.assertRaises(ValueError):
                sanitize_folder_name(bad)

    def test_movie_mode_is_optional_but_its_roots_must_be_separate(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = config_data(root)
            data.pop("jellyfin_movie_library_path")
            data.pop("movie_staging_path")
            path = root / "disabled.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            self.assertFalse(load_config(path, create_from_example=False).movies_configured)

            data = config_data(root)
            data["jellyfin_movie_library_path"] = str(root / "library" / "movies")
            path = root / "nested.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "separate, non-nested"):
                load_config(path, create_from_example=False)

    def test_safe_path_stays_in_library(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "library"
            base.mkdir()
            self.assertEqual(safe_child(base, "Anime").parent, base.resolve())
            with self.assertRaises(ValueError):
                safe_child(base, r"..\outside")

    def test_queue_item_library_key_freezes_its_download_destination(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = config_data(root)
            data["media_libraries"] = [
                {
                    "key": "animation_series",
                    "name": "Animation Series",
                    "media_kind": "series",
                    "path": str(root / "animation-series"),
                },
                {
                    "key": "video_series",
                    "name": "Video Series",
                    "media_kind": "series",
                    "path": str(root / "video-series"),
                },
                {
                    "key": "video_movies",
                    "name": "Video Movies",
                    "media_kind": "movie",
                    "path": str(root / "video-movies"),
                },
            ]
            data["default_library_key"] = "animation_series"
            path = root / "config.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            cfg = load_config(path, create_from_example=False)
            store = StateStore(root / "queue.db")
            queue = QueueManager(store)
            try:
                manager = DownloadManager(cfg, queue, None, None)  # type: ignore[arg-type]
                item = {
                    "pending_id": 1,
                    "target_folder": "Example Show",
                    "library_key": "video_series",
                    "media_kind": "series",
                    "original_filename": "episode.mkv",
                }
                destination, _ = manager._destination(item)  # type: ignore[misc]
                self.assertEqual(
                    destination,
                    (root / "video-series" / "Example Show" / "episode.mkv").resolve(),
                )

                # A later chat selection does not mutate the already queued item.
                store.set_chat_setting(5, "current_library_key", "animation_series")
                destination_again, _ = manager._destination(item)  # type: ignore[misc]
                self.assertEqual(destination_again, destination)
            finally:
                store.close()

    def test_large_file_timeout_override_is_not_sent_as_bot_api_data(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                path = root / "config.json"
                path.write_text(json.dumps(config_data(root)), encoding="utf-8")
                cfg = load_config(path, create_from_example=False)

                class FakeResponse:
                    async def __aenter__(self):
                        return self

                    async def __aexit__(self, *_):
                        return False

                    async def json(self):
                        return {"ok": True, "result": {"file_path": "movie.mp4"}}

                class FakeSession:
                    def __init__(self):
                        self.calls = []

                    def post(self, url, **kwargs):
                        self.calls.append(kwargs)
                        return FakeResponse()

                session = FakeSession()
                api = TelegramAPI(cfg, session)
                await api.call("getMe")
                large_timeout = object()
                await api.call(
                    "getFile",
                    file_id="movie-id",
                    _request_timeout=large_timeout,
                )
                self.assertNotIn("timeout", session.calls[0])
                self.assertIs(session.calls[1]["timeout"], large_timeout)
                self.assertNotIn("_request_timeout", session.calls[1]["data"])

        asyncio.run(exercise())

    def test_send_passes_existing_topic_identifiers_to_telegram(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                path = root / "config.json"
                path.write_text(json.dumps(config_data(root)), encoding="utf-8")
                cfg = load_config(path, create_from_example=False)

                class FakeResponse:
                    async def __aenter__(self):
                        return self

                    async def __aexit__(self, *_):
                        return False

                    async def json(self):
                        return {"ok": True, "result": True}

                class FakeSession:
                    def __init__(self):
                        self.calls = []

                    def post(self, url, **kwargs):
                        self.calls.append(kwargs)
                        return FakeResponse()

                session = FakeSession()
                api = TelegramAPI(cfg, session)
                await api.send(
                    -100123,
                    "Category menu",
                    message_thread_id=42,
                    direct_messages_topic_id=7001,
                )
                data = session.calls[0]["data"]
                self.assertEqual(data["message_thread_id"], "42")
                self.assertEqual(data["direct_messages_topic_id"], "7001")
                self.assertEqual(data["parse_mode"], "HTML")

        asyncio.run(exercise())

    def test_important_values_are_bold_and_all_html_is_escaped(self):
        rendered = _telegram_html(
            f"Library: {_important('Movies & <Special>')}\nordinary <text>"
        )
        self.assertEqual(
            rendered,
            "Library: <b>Movies &amp; &lt;Special&gt;</b>\n"
            "ordinary &lt;text&gt;",
        )

    def test_bot_send_renders_important_values_after_localization(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                path = root / "config.json"
                path.write_text(json.dumps(config_data(root)), encoding="utf-8")
                cfg = load_config(path, create_from_example=False)
                app = BotApp(cfg)

                class FakeApi:
                    def __init__(self):
                        self.sent = []

                    async def send(self, chat_id, text, reply_markup=None, **kwargs):
                        self.sent.append(text)

                api = FakeApi()
                app.api = api
                try:
                    await app.send(
                        987654321,
                        f"Current library: {_important('Movies & <Special>')}",
                    )
                    self.assertEqual(
                        api.sent[-1],
                        "Current library: <b>Movies &amp; &lt;Special&gt;</b>",
                    )
                finally:
                    app.store.close()

        asyncio.run(exercise())

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
                self.assertEqual(app._queue_display_number(-1, first, "Anime A"), 1)
                self.assertEqual(app._queue_display_number(-1, second, "Anime A"), 2)
                self.assertEqual(app._queue_display_number(-1, third, "Anime B"), 1)
            finally:
                app.store.close()


class QueueTests(unittest.TestCase):
    def test_library_scoped_rename_does_not_change_another_library_chat(self):
        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "state.db")
            try:
                for chat_id, library_key in ((10, "animation_series"), (20, "video_series")):
                    store.set_chat_setting(chat_id, "current_library_key", library_key)
                    store.set_chat_setting(chat_id, "current_folder", "Same Name")
                changed = store.replace_chat_setting_value_in_library(
                    "current_folder",
                    "Same Name",
                    "Renamed Animation",
                    "animation_series",
                )
                self.assertEqual(changed, 1)
                self.assertEqual(
                    store.get_chat_setting(10, "current_folder"),
                    "Renamed Animation",
                )
                self.assertEqual(
                    store.get_chat_setting(20, "current_folder"), "Same Name"
                )
            finally:
                store.close()

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

    def test_clear_queue_preserves_a_downloaded_movie_waiting_for_import(self):
        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "state.db")
            queue = QueueManager(store)
            pending_id = queue.add(
                message_id=3,
                chat_id=-1,
                file_id="movie",
                file_unique_id="movie-unique",
                original_filename="movie.mkv",
                file_size=20,
                target_folder="Movie (2020)",
                media_kind="movie",
                status="movie_import_failed",
            )
            try:
                self.assertEqual(queue.clear(), 0)
                self.assertIsNotNone(store.get_item(pending_id))
            finally:
                store.close()

    def test_old_database_is_migrated_to_series_items(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "old-state.db"
            connection = sqlite3.connect(db)
            connection.executescript(
                """
                CREATE TABLE queue_items (
                    pending_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    file_id TEXT NOT NULL,
                    file_unique_id TEXT NOT NULL,
                    original_filename TEXT NOT NULL,
                    file_size INTEGER,
                    received_at TEXT NOT NULL,
                    target_folder TEXT,
                    status TEXT NOT NULL DEFAULT 'queued',
                    error TEXT,
                    downloaded_path TEXT,
                    overwrite_policy TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(chat_id, message_id, file_unique_id)
                );
                INSERT INTO queue_items(
                    message_id,chat_id,file_id,file_unique_id,original_filename,
                    received_at,target_folder,status,created_at,updated_at
                ) VALUES(1,2,'f','u','episode.mkv','now','Show','queued','now','now');
                """
            )
            connection.commit()
            connection.close()

            store = StateStore(db)
            try:
                item = store.get_item(1)
                self.assertEqual(item["media_kind"], "series")
                self.assertIn("movie_batch_id", item)
            finally:
                store.close()

    def test_chat_state_queue_and_batch_ownership_are_isolated(self):
        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "state.db")
            queue = QueueManager(store)
            try:
                store.set_chat_setting(10, "current_folder", "Chat Ten Show")
                store.set_chat_setting(20, "current_folder", "Chat Twenty Show")
                store.set_chat_setting(10, "download_confirmation", "1")
                first = queue.add(
                    message_id=1,
                    chat_id=10,
                    file_id="first",
                    file_unique_id="first-unique",
                    original_filename="first.mkv",
                    target_folder="Chat Ten Show",
                )
                second = queue.add(
                    message_id=1,
                    chat_id=20,
                    file_id="second",
                    file_unique_id="second-unique",
                    original_filename="second.mkv",
                    target_folder="Chat Twenty Show",
                )

                self.assertEqual(
                    store.get_chat_setting(10, "current_folder"), "Chat Ten Show"
                )
                self.assertEqual(
                    store.get_chat_setting(20, "current_folder"), "Chat Twenty Show"
                )
                self.assertEqual(
                    store.get_chat_setting(20, "download_confirmation"), ""
                )
                self.assertEqual([item["pending_id"] for item in queue.pending(10)], [first])
                self.assertEqual([item["pending_id"] for item in queue.pending(20)], [second])
                self.assertIsNone(store.get_item(first, chat_id=20))
                self.assertFalse(queue.remove(first, chat_id=20))
                self.assertEqual(queue.clear(chat_id=10), 1)
                self.assertIsNotNone(store.get_item(second, chat_id=20))

                run_id = store.create_sorter_run(
                    "Chat Twenty Show",
                    "[]",
                    chat_id=20,
                    operation_kind="series",
                )
                store.finish_sorter_run(
                    run_id, "completed", "ok", batch_id="chat-20-batch"
                )
                self.assertTrue(
                    store.sorter_batch_belongs_to_chat("chat-20-batch", 20)
                )
                self.assertFalse(
                    store.sorter_batch_belongs_to_chat("chat-20-batch", 10)
                )
                queue.add(
                    message_id=2,
                    chat_id=20,
                    file_id="movie",
                    file_unique_id="movie-unique",
                    original_filename="movie.mkv",
                    target_folder="Movie (2026)",
                    media_kind="movie",
                    movie_batch_id="movie-chat-20",
                    status="imported",
                )
                self.assertEqual(
                    store.latest_movie_batch(20), "movie-chat-20"
                )
                self.assertTrue(
                    store.movie_batch_belongs_to_chat("movie-chat-20", 20)
                )
                self.assertFalse(
                    store.movie_batch_belongs_to_chat("movie-chat-20", 10)
                )
            finally:
                store.close()

    def test_legacy_shared_folder_is_claimed_by_existing_queue_owner(self):
        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "state.db")
            queue = QueueManager(store)
            try:
                store.set_setting("current_folder", "Legacy Show")
                queue.add(
                    message_id=1,
                    chat_id=111,
                    file_id="owner",
                    file_unique_id="owner-unique",
                    original_filename="episode.mkv",
                    target_folder="Legacy Show",
                )
                self.assertEqual(
                    store.get_chat_setting(222, "current_folder"), ""
                )
                self.assertEqual(
                    store.get_chat_setting(111, "current_folder"), "Legacy Show"
                )
            finally:
                store.close()

    def test_legacy_sort_batch_is_migrated_to_existing_queue_owner(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "state.db"
            store = StateStore(db)
            queue = QueueManager(store)
            queue.add(
                message_id=1,
                chat_id=111,
                file_id="owner",
                file_unique_id="owner-unique",
                original_filename="episode.mkv",
                target_folder="Legacy Show",
            )
            run_id = store.create_sorter_run("Legacy Show", '["run"]')
            store.finish_sorter_run(
                run_id, "completed", "Batch ID: legacy-owned-batch"
            )
            store.close()

            reopened = StateStore(db)
            try:
                self.assertEqual(
                    reopened.latest_sorter_batch(111), "legacy-owned-batch"
                )
                self.assertTrue(
                    reopened.sorter_batch_belongs_to_chat(
                        "legacy-owned-batch", 111
                    )
                )
            finally:
                reopened.close()


class MovieWorkflowTests(unittest.TestCase):
    def test_filename_query_removes_release_tags(self):
        self.assertEqual(
            movie_query_from_filename(
                "[Group] Interstellar.2014.1080p.BluRay.x265-GROUP.mkv"
            ),
            "Interstellar 2014",
        )

    def test_numbered_movie_title_is_not_mistaken_for_a_future_year(self):
        self.assertEqual(
            BotApp._manual_movie_identity("Blade Runner 2049"),
            ("Blade Runner 2049", None, "Blade Runner 2049"),
        )
        self.assertEqual(
            BotApp._manual_movie_identity("1917 2019"),
            ("1917", 2019, "1917 (2019)"),
        )
        self.assertEqual(
            BotApp._manual_movie_identity("Future Film (2049)"),
            ("Future Film", 2049, "Future Film (2049)"),
        )

    def test_bot_movie_identification_confirmation(self):
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
                    sent.append((text, reply_markup))

                async def fake_search(query, limit=8, media_type="any"):
                    self.assertEqual(query, "Interstellar 2014")
                    self.assertEqual(media_type, "movie")
                    return ([{
                        "imdb_id": "tt0816692",
                        "title": "Interstellar",
                        "year": 2014,
                        "type": "movie",
                        "score": 99.0,
                        "folder_name": "Interstellar (2014) [imdbid-tt0816692]",
                    }], "online")

                app.api = FakeApi()
                app.send = fake_send
                app.imdb.search = fake_search
                try:
                    await app.cmd_movie_mode(987654321, "")
                    await app.handle_media(
                        987654321,
                        {
                            "message_id": 50,
                            "document": {
                                "file_id": "movie-file",
                                "file_unique_id": "movie-unique",
                                "file_name": "Interstellar.2014.1080p.BluRay.mkv",
                                "file_size": 123,
                                "mime_type": "video/x-matroska",
                            },
                        },
                    )
                    item = app.store.latest_movie_item(chat_id=987654321)
                    self.assertEqual(item["status"], "awaiting_identification")
                    await app.cmd_movie_current(987654321, "")
                    callbacks = {
                        button.get("callback_data")
                        for row in sent[-1][1]["inline_keyboard"]
                        for button in row
                    }
                    self.assertIn(
                        f"movieidentify:filename:{item['pending_id']}", callbacks
                    )
                    query = movie_query_from_filename(item["original_filename"])
                    await app._run_movie_search(
                        987654321, item["pending_id"], query, manual_query=False
                    )
                    token = next(iter(app.movie_choices))
                    await app.handle_callback({
                        "id": "movie-confirm",
                        "data": f"movieconfirm:{token}",
                        "message": {"chat": {"id": 987654321, "type": "private"}},
                    })
                    confirmed = app.store.get_item(item["pending_id"])
                    self.assertEqual(confirmed["status"], "queued")
                    self.assertEqual(confirmed["movie_title"], "Interstellar")
                    self.assertEqual(confirmed["movie_year"], 2014)
                    self.assertEqual(confirmed["imdb_id"], "tt0816692")
                    self.assertEqual(
                        confirmed["target_folder"],
                        "Interstellar (2014) [imdbid-tt0816692]",
                    )
                    self.assertIs(sent[-1][1], MOVIE_MENU)
                finally:
                    app.store.close()

        asyncio.run(exercise())

    def test_existing_movie_requires_replace_or_cancel_before_download(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                path = root / "config.json"
                path.write_text(json.dumps(config_data(root)), encoding="utf-8")
                cfg = load_config(path, create_from_example=False)
                app = BotApp(cfg)
                sent = []
                folder_name = "Interstellar (2014) [imdbid-tt0816692]"
                existing_folder_name = "Interstellar Old [imdbid-tt0816692]"
                destination = cfg.movie_target_path(existing_folder_name, "movies")
                destination.mkdir(parents=True)
                (destination / f"{existing_folder_name}.mkv").write_bytes(b"existing")

                async def fake_send(chat_id, text, reply_markup=None, **kwargs):
                    sent.append((text, reply_markup))

                app.send = fake_send
                class FakeApi:
                    async def call(self, *_args, **_kwargs):
                        return True

                app.api = FakeApi()
                try:
                    pending_id = app.queue.add(
                        message_id=52,
                        chat_id=987654321,
                        file_id="duplicate-movie",
                        file_unique_id="duplicate-movie-unique",
                        original_filename="Interstellar.2014.mp4",
                        file_size=100,
                        target_folder=None,
                        library_key="movies",
                        media_kind="movie",
                        status="awaiting_identification",
                    )
                    choice = {
                        "pending_id": pending_id,
                        "title": "Interstellar",
                        "year": 2014,
                        "imdb_id": "tt0816692",
                        "folder_name": folder_name,
                    }
                    self.assertFalse(
                        await app._confirm_movie_choice(
                            987654321, choice, notify=False
                        )
                    )
                    item = app.store.get_item(
                        pending_id, chat_id=987654321
                    )
                    self.assertEqual(item["status"], "waiting_overwrite")
                    self.assertIn("library conflict", item["error"])
                    conflict_message = next(
                        entry for entry in sent
                        if "Existing Jellyfin file" in entry[0]
                    )
                    self.assertIn("Interstellar.2014.mp4", conflict_message[0])
                    callbacks = {
                        button["callback_data"]
                        for row in conflict_message[1]["inline_keyboard"]
                        for button in row
                    }
                    self.assertEqual(
                        callbacks,
                        {
                            f"libraryconflict:replace:{pending_id}",
                            f"libraryconflict:cancel:{pending_id}",
                        },
                    )
                    await app.handle_callback({
                        "id": "replace-existing-movie",
                        "data": f"libraryconflict:replace:{pending_id}",
                        "message": {"chat": {"id": 987654321, "type": "private"}},
                    })
                    approved = app.store.get_item(pending_id, chat_id=987654321)
                    self.assertEqual(approved["status"], "queued")
                    self.assertEqual(
                        approved["overwrite_policy"], "replace_library"
                    )
                    self.assertEqual(
                        approved["target_folder"], existing_folder_name
                    )
                finally:
                    app.store.close()

        asyncio.run(exercise())

    def test_download_preflight_requests_replacement_for_older_duplicate(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                path = root / "config.json"
                path.write_text(json.dumps(config_data(root)), encoding="utf-8")
                cfg = load_config(path, create_from_example=False)
                app = BotApp(cfg)
                sent = []
                folder_name = "Ratatouille (2007) [imdbid-tt0382932]"
                destination = cfg.movie_target_path(folder_name, "movies")
                destination.mkdir(parents=True)
                (destination / f"{folder_name}.mkv").write_bytes(b"existing")

                async def fake_send(chat_id, text, reply_markup=None, **kwargs):
                    sent.append((text, reply_markup))

                app.send = fake_send
                try:
                    pending_id = app.queue.add(
                        message_id=53,
                        chat_id=987654321,
                        file_id="old-duplicate-movie",
                        file_unique_id="old-duplicate-movie-unique",
                        original_filename="Ratatouille.2007.mp4",
                        file_size=100,
                        target_folder=folder_name,
                        library_key="movies",
                        media_kind="movie",
                        movie_title="Ratatouille",
                        movie_year=2007,
                        imdb_id="tt0382932",
                        status="queued",
                    )
                    items = await app._prepare_safe_download_items(987654321)
                    self.assertEqual(items, [])
                    item = app.store.get_item(
                        pending_id, chat_id=987654321
                    )
                    self.assertEqual(item["status"], "waiting_overwrite")
                    self.assertTrue(
                        any("Existing Jellyfin file" in text for text, _ in sent)
                    )
                finally:
                    app.store.close()

        asyncio.run(exercise())

    def test_bot_imports_and_undoes_movie_through_independent_bridge(self):
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

                app.send = fake_send
                try:
                    pending_id = app.queue.add(
                        message_id=51,
                        chat_id=987654321,
                        file_id="downloaded-movie",
                        file_unique_id="downloaded-movie-unique",
                        original_filename="Interstellar.2014.mkv",
                        file_size=4,
                        target_folder="Interstellar (2014) [imdbid-tt0816692]",
                        media_kind="movie",
                        movie_title="Interstellar",
                        movie_year=2014,
                        imdb_id="tt0816692",
                        status="completed",
                    )
                    staging = cfg.movie_staging_job_path(pending_id)
                    staging.mkdir(parents=True)
                    source = staging / "Interstellar.2014.mkv"
                    subtitle = staging / "Interstellar.2014.fa.srt"
                    source.write_bytes(b"film")
                    subtitle.write_text("subtitle", encoding="utf-8")
                    app.store.update_item(pending_id, downloaded_path=str(source))

                    self.assertTrue(
                        await app._import_movie_item(
                            987654321, app.store.get_item(pending_id)
                        )
                    )
                    success = next(
                        entry for entry in sent
                        if entry[0].startswith("Movie imported successfully.")
                    )
                    self.assertIsNone(success[1])
                    imported = app.store.get_item(pending_id)
                    destination = Path(imported["downloaded_path"])
                    self.assertTrue(destination.is_file())
                    self.assertTrue(
                        destination.with_name(
                            "Interstellar (2014) [imdbid-tt0816692].fa.srt"
                        ).is_file()
                    )
                    self.assertTrue(
                        (destination.parent / ".rename_history.json").is_file()
                    )

                    duplicate_staging = cfg.movie_staging_job_path(pending_id + 100)
                    duplicate_staging.mkdir(parents=True)
                    duplicate_source = duplicate_staging / "duplicate.mkv"
                    duplicate_source.write_bytes(b"new-film")
                    duplicate_item = dict(imported)
                    duplicate_item["downloaded_path"] = str(duplicate_source)
                    with self.assertRaises(RuntimeError):
                        await app.movie_sorter.import_movie(
                            duplicate_item, dry_run=True
                        )
                    self.assertTrue(duplicate_source.is_file())
                    self.assertEqual(destination.read_bytes(), b"film")

                    duplicate_item["overwrite_policy"] = "replace_library"
                    preview = await app.movie_sorter.import_movie(
                        duplicate_item, dry_run=True
                    )
                    self.assertTrue(preview["replacement"])
                    replacement = await app.movie_sorter.import_movie(
                        duplicate_item, dry_run=False
                    )
                    self.assertEqual(destination.read_bytes(), b"new-film")
                    backup = (
                        destination.parent
                        / ".replacement_backups"
                        / replacement["batch_id"]
                        / destination.name
                    )
                    self.assertEqual(backup.read_bytes(), b"film")
                    undone = await app.movie_sorter.undo_batch(
                        replacement["batch_id"],
                        chat_id=987654321,
                        library_key="movies",
                    )
                    self.assertTrue(undone["ok"])
                    self.assertEqual(destination.read_bytes(), b"film")
                    self.assertEqual(duplicate_source.read_bytes(), b"new-film")

                    source.parent.mkdir(parents=True, exist_ok=True)
                    source.write_bytes(b"undo-conflict")
                    await app._run_movie_undo(
                        987654321, imported["movie_batch_id"]
                    )
                    self.assertTrue(destination.is_file())
                    self.assertTrue(subtitle.is_file())
                    self.assertEqual(
                        app.store.get_item(pending_id)["status"],
                        "movie_undo_partial",
                    )
                    self.assertTrue(
                        any("incomplete" in text for text, _ in sent)
                    )

                    source.unlink()
                    await app._run_movie_undo(
                        987654321, imported["movie_batch_id"]
                    )
                    self.assertEqual(source.read_bytes(), b"film")
                    self.assertEqual(
                        app.store.get_item(pending_id)["status"], "movie_undone"
                    )
                finally:
                    app.store.close()

        asyncio.run(exercise())

    def test_automatic_movie_import_uses_one_compact_summary(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                path = root / "config.json"
                path.write_text(json.dumps(config_data(root)), encoding="utf-8")
                cfg = load_config(path, create_from_example=False)
                app = BotApp(cfg)
                sent = []
                items = []
                for index, title in enumerate(("Interstellar", "Ratatouille"), 1):
                    pending_id = app.queue.add(
                        message_id=60 + index,
                        chat_id=987654321,
                        file_id=f"download-{index}",
                        file_unique_id=f"download-unique-{index}",
                        original_filename=f"{title}.mkv",
                        file_size=4,
                        target_folder=f"{title} (2000) [imdbid-tt000000{index}]",
                        library_key="movies",
                        media_kind="movie",
                        movie_title=title,
                        movie_year=2000,
                        imdb_id=f"tt000000{index}",
                        status="queued",
                    )
                    items.append(app.store.get_item(pending_id))

                class FakeDownloader:
                    async def run(self, batch, notify):
                        await notify("Download started.")
                        for item in batch:
                            app.store.update_item(
                                int(item["pending_id"]), status="completed"
                            )
                            await notify(
                                f"Download completed: {item['original_filename']}"
                            )
                        await notify(
                            "Downloads finished. 2 of 2 file(s) completed.\n"
                            "Completed movies will now be imported automatically."
                        )

                async def fake_import(chat_id, item, *, notify=True):
                    self.assertFalse(notify)
                    app.store.update_item(
                        int(item["pending_id"]), status="imported"
                    )
                    return True

                async def fake_send(chat_id, text, reply_markup=None, **kwargs):
                    sent.append(text)

                app.downloader = FakeDownloader()
                app._import_movie_item = fake_import
                app.send = fake_send
                try:
                    await app._run_downloads_and_movie_imports(
                        987654321, items
                    )
                    self.assertFalse(
                        any(text.startswith("Download completed:") for text in sent)
                    )
                    summaries = [
                        text for text in sent if "movie(s) imported" in text
                    ]
                    self.assertEqual(len(summaries), 1)
                    self.assertIn("2 movie(s) imported", summaries[0])
                    self.assertFalse(
                        any("Checking movie import plan" in text for text in sent)
                    )
                finally:
                    app.store.close()

        asyncio.run(exercise())


class AiIdentificationWorkflowTests(unittest.TestCase):
    def test_completed_ai_series_download_is_sorted_automatically(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                path = root / "config.json"
                path.write_text(json.dumps(config_data(root)), encoding="utf-8")
                cfg = load_config(path, create_from_example=False)
                app = BotApp(cfg)
                sorted_targets = []
                scan_requests = []

                pending_id = app.queue.add(
                    message_id=3,
                    chat_id=987654321,
                    file_id="series-file",
                    file_unique_id="series-unique",
                    original_filename="episode.mkv",
                    file_size=100,
                    target_folder="Dr. Stone (2019) [imdbid-tt9679542]",
                    library_key="series",
                    media_kind="series",
                    status="queued",
                )
                app.store.update_item(
                    pending_id,
                    series_season=4,
                    series_episode=25,
                    download_filename="Incoming - S04E25.mkv",
                )

                class FakeDownloader:
                    async def run(self, items, notify):
                        app.store.update_item(pending_id, status="completed")

                async def fake_sorter(
                    chat_id, folder_name, library_key=None, **kwargs
                ):
                    sorted_targets.append((chat_id, folder_name, library_key))
                    return True

                class FakeJellyfin:
                    configured = True

                async def fake_scan(chat_id):
                    scan_requests.append(chat_id)

                app.downloader = FakeDownloader()
                app._run_sorter = fake_sorter
                app.jellyfin = FakeJellyfin()
                app._run_jellyfin_scan = fake_scan
                try:
                    await app._run_downloads_and_movie_imports(
                        987654321, [app.store.get_item(pending_id)]
                    )
                    self.assertEqual(
                        sorted_targets,
                        [(
                            987654321,
                            "Dr. Stone (2019) [imdbid-tt9679542]",
                            "series",
                        )],
                    )
                    self.assertEqual(scan_requests, [987654321])
                finally:
                    app.store.close()

        asyncio.run(exercise())

    def test_ai_series_identity_becomes_a_confirmed_sorter_safe_queue_item(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                data = config_data(root)
                data.update({
                    "n8n_agent_enabled": True,
                    "n8n_agent_url": "http://n8n:5678/webhook/media-identify",
                    "n8n_agent_secret": "test-secret",
                })
                path = root / "config.json"
                path.write_text(json.dumps(data), encoding="utf-8")
                cfg = load_config(path, create_from_example=False)
                app = BotApp(cfg)
                sent = []

                class FakeIdentifier:
                    configured = True

                    async def identify(self, **kwargs):
                        self.kwargs = kwargs
                        return MediaIdentification(
                            title_query="Dr. Stone",
                            season=4,
                            episode=25,
                            year=2026,
                            confidence=0.96,
                            needs_user_input=False,
                            question=None,
                        )

                class FakeIMDb:
                    async def search(self, query, media_type="any"):
                        self.query = query
                        self.media_type = media_type
                        return ([{
                            "title": "Dr. Stone",
                            "year": 2019,
                            "score": 98,
                            "imdb_id": "tt9679542",
                            "folder_name": "Dr. Stone (2019) [imdbid-tt9679542]",
                        }], "test IMDb")

                async def fake_send(chat_id, text, reply_markup=None, **kwargs):
                    sent.append((text, reply_markup))

                app.ai_identifier = FakeIdentifier()
                app.imdb = FakeIMDb()
                app.send = fake_send
                try:
                    pending_id = app.queue.add(
                        message_id=1,
                        chat_id=987654321,
                        file_id="file-id",
                        file_unique_id="unique-id",
                        original_filename="[AWHT] Dr. Stone S4 - 25 [480p].mkv",
                        file_size=100,
                        received_at="2026-08-22T00:00:00+00:00",
                        target_folder=None,
                        library_key="series",
                        media_kind="series",
                        status="awaiting_identification",
                    )
                    await app._run_ai_series_identification(
                        987654321, pending_id, ""
                    )
                    self.assertEqual(len(app.imdb_choices), 1)
                    choice = next(iter(app.imdb_choices.values()))
                    await app._confirm_series_queue_choice(987654321, choice)

                    item = app.store.get_item(pending_id, chat_id=987654321)
                    self.assertEqual(item["status"], "queued")
                    self.assertEqual(item["series_season"], 4)
                    self.assertEqual(item["series_episode"], 25)
                    self.assertIsNone(item["download_filename"])
                    self.assertEqual(
                        item["original_filename"],
                        "[AWHT] Dr. Stone S4 - 25 [480p].mkv",
                    )
                    self.assertEqual(
                        item["target_folder"],
                        "Dr. Stone (2019) [imdbid-tt9679542]",
                    )
                    self.assertTrue(cfg.target_path(item["target_folder"]).is_dir())
                    ready_messages = [
                        text for text, _ in sent if "episode(s) ready" in text
                    ]
                    self.assertEqual(len(ready_messages), 1)
                    self.assertIn("Dr. Stone: S04E25", ready_messages[0])
                    self.assertIn("Next: /download", ready_messages[0])
                finally:
                    app.store.close()

        asyncio.run(exercise())

    def test_existing_series_episode_requires_replace_or_cancel(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                path = root / "config.json"
                path.write_text(json.dumps(config_data(root)), encoding="utf-8")
                cfg = load_config(path, create_from_example=False)
                app = BotApp(cfg)
                sent = []
                folder_name = "Dr. Stone (2019) [imdbid-tt9679542]"
                season = cfg.target_path(folder_name, "series") / "Season 04"
                season.mkdir(parents=True)
                existing = season / "Dr. Stone - S04E25.mkv"
                existing.write_bytes(b"old")

                async def fake_send(chat_id, text, reply_markup=None, **kwargs):
                    sent.append((text, reply_markup))

                class FakeApi:
                    async def call(self, *_args, **_kwargs):
                        return True

                app.send = fake_send
                app.api = FakeApi()
                try:
                    pending_id = app.queue.add(
                        message_id=2,
                        chat_id=987654321,
                        file_id="episode-file",
                        file_unique_id="episode-unique",
                        original_filename="Dr.Stone.S04E25.mp4",
                        file_size=100,
                        target_folder=None,
                        library_key="series",
                        media_kind="series",
                        status="awaiting_identification",
                    )
                    choice = {
                        "folder_name": folder_name,
                        "library_key": "series",
                        "imdb_id": "tt9679542",
                        "queue_entries": [{
                            "pending_id": pending_id,
                            "series_title": "Dr. Stone",
                            "series_year": 2019,
                            "series_season": 4,
                            "series_episode": 25,
                            "imdb_id": "tt9679542",
                        }],
                    }
                    await app._confirm_series_queue_choice(
                        987654321, choice, notify=False
                    )
                    waiting = app.store.get_item(pending_id, chat_id=987654321)
                    self.assertEqual(waiting["status"], "waiting_overwrite")
                    self.assertTrue(
                        any("Existing Jellyfin file" in text for text, _ in sent)
                    )

                    await app.handle_callback({
                        "id": "replace-existing-episode",
                        "data": f"libraryconflict:replace:{pending_id}",
                        "message": {"chat": {"id": 987654321, "type": "private"}},
                    })
                    approved = app.store.get_item(pending_id, chat_id=987654321)
                    self.assertEqual(approved["status"], "queued")
                    self.assertEqual(
                        approved["overwrite_policy"], "replace_library"
                    )
                    command = app.sorter.build_command(
                        cfg.target_path(folder_name, "series"),
                        library_key="series",
                        replace_episodes={(4, 25)},
                    )
                    self.assertEqual(command[-2:], ["--replace-episode", "S04E25"])
                finally:
                    app.store.close()

        asyncio.run(exercise())

    def test_ai_series_reuses_existing_imdb_folder_without_confirmation(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                data = config_data(root)
                data.update({
                    "n8n_agent_enabled": True,
                    "n8n_agent_url": "http://n8n:5678/webhook/media-identify",
                })
                path = root / "config.json"
                path.write_text(json.dumps(data), encoding="utf-8")
                cfg = load_config(path, create_from_example=False)
                app = BotApp(cfg)
                folder_name = "Dr. Stone (2019) [imdbid-tt9679542]"
                cfg.target_path(folder_name, "series").mkdir(parents=True)
                sent = []

                class FakeIdentifier:
                    configured = True

                    async def identify(self, **kwargs):
                        return MediaIdentification(
                            title_query="Dr. Stone",
                            season=4,
                            episode=25,
                            year=2019,
                            confidence=0.96,
                            needs_user_input=False,
                            question=None,
                        )

                class FakeIMDb:
                    async def search(self, query, media_type="any"):
                        return ([{
                            "title": "Dr. Stone",
                            "year": 2019,
                            "score": 98,
                            "imdb_id": "tt9679542",
                            "folder_name": folder_name,
                        }], "test IMDb")

                async def fake_send(chat_id, text, reply_markup=None, **kwargs):
                    sent.append(text)

                app.ai_identifier = FakeIdentifier()
                app.imdb = FakeIMDb()
                app.send = fake_send
                try:
                    pending_id = app.queue.add(
                        message_id=1,
                        chat_id=987654321,
                        file_id="file-id",
                        file_unique_id="existing-series-episode",
                        original_filename="Dr.Stone.S04E25.mkv",
                        file_size=100,
                        received_at="2026-08-22T00:00:00+00:00",
                        target_folder=None,
                        library_key="series",
                        media_kind="series",
                        status="awaiting_identification",
                    )
                    await app._run_ai_series_identification(
                        987654321, pending_id, ""
                    )
                    item = app.store.get_item(pending_id, chat_id=987654321)
                    self.assertEqual(item["status"], "queued")
                    self.assertEqual(item["target_folder"], folder_name)
                    self.assertEqual(app.imdb_choices, {})
                    self.assertEqual(sent, [])
                finally:
                    app.store.close()

        asyncio.run(exercise())

    def test_mixed_existing_series_are_routed_independently(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                path = root / "config.json"
                path.write_text(json.dumps(config_data(root)), encoding="utf-8")
                cfg = load_config(path, create_from_example=False)
                app = BotApp(cfg)
                destinations = {
                    "Witch Hat Atelier": (
                        "Witch Hat Atelier (2026) [imdbid-tt32550889]",
                        "tt32550889",
                    ),
                    "Solo Leveling": (
                        "Solo Leveling (2024) [imdbid-tt21209876]",
                        "tt21209876",
                    ),
                }
                for folder_name, _ in destinations.values():
                    cfg.target_path(folder_name, "series").mkdir(parents=True)

                class FakeIMDb:
                    async def search(self, query, media_type="any"):
                        title = next(name for name in destinations if name in query)
                        folder_name, imdb_id = destinations[title]
                        return ([{
                            "title": title,
                            "year": 2026 if title.startswith("Witch") else 2024,
                            "score": 99,
                            "imdb_id": imdb_id,
                            "folder_name": folder_name,
                        }], "test IMDb")

                app.imdb = FakeIMDb()
                try:
                    ids = []
                    for index, (title, episode) in enumerate(
                        (("Witch Hat Atelier", 1), ("Solo Leveling", 7)), 1
                    ):
                        pending_id = app.queue.add(
                            message_id=index,
                            chat_id=987654321,
                            file_id=f"file-{index}",
                            file_unique_id=f"mixed-{index}",
                            original_filename=f"episode-{index}.mkv",
                            file_size=100,
                            received_at="2026-08-22T00:00:00+00:00",
                            target_folder=None,
                            library_key="series",
                            media_kind="series",
                            status="awaiting_identification",
                        )
                        ids.append((pending_id, title))
                        await app._continue_series_identification(
                            987654321,
                            pending_id,
                            MediaIdentification(
                                title_query=title,
                                season=1,
                                episode=episode,
                                year=None,
                                confidence=0.95,
                                needs_user_input=False,
                                question=None,
                            ),
                        )
                    for pending_id, title in ids:
                        item = app.store.get_item(pending_id, chat_id=987654321)
                        self.assertEqual(item["status"], "queued")
                        self.assertEqual(
                            item["target_folder"], destinations[title][0]
                        )
                finally:
                    app.store.close()

        asyncio.run(exercise())

    def test_new_series_episodes_share_one_folder_confirmation(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                path = root / "config.json"
                path.write_text(json.dumps(config_data(root)), encoding="utf-8")
                cfg = load_config(path, create_from_example=False)
                app = BotApp(cfg)
                sent = []

                class FakeIMDb:
                    async def search(self, query, media_type="any"):
                        return ([{
                            "title": "Witch Hat Atelier",
                            "year": 2026,
                            "score": 99,
                            "imdb_id": "tt32550889",
                            "folder_name": (
                                "Witch Hat Atelier (2026) "
                                "[imdbid-tt32550889]"
                            ),
                        }], "test IMDb")

                async def fake_send(chat_id, text, reply_markup=None, **kwargs):
                    sent.append((text, reply_markup))

                app.imdb = FakeIMDb()
                app.send = fake_send
                try:
                    pending_ids = []
                    for episode in (1, 2, 3):
                        pending_id = app.queue.add(
                            message_id=episode,
                            chat_id=987654321,
                            file_id=f"file-{episode}",
                            file_unique_id=f"new-series-{episode}",
                            original_filename=f"episode-{episode}.mkv",
                            file_size=100,
                            received_at="2026-08-22T00:00:00+00:00",
                            target_folder=None,
                            library_key="series",
                            media_kind="series",
                            status="awaiting_identification",
                        )
                        pending_ids.append(pending_id)
                        await app._continue_series_identification(
                            987654321,
                            pending_id,
                            MediaIdentification(
                                title_query="Witch Hat Atelier",
                                season=1,
                                episode=episode,
                                year=2026,
                                confidence=0.95,
                                needs_user_input=False,
                                question=None,
                            ),
                        )
                    self.assertEqual(len(app.imdb_choices), 1)
                    choice = next(iter(app.imdb_choices.values()))
                    self.assertEqual(len(choice["queue_entries"]), 3)
                    self.assertEqual(
                        sum("New series:" in text for text, _ in sent), 1
                    )
                    await app._confirm_series_queue_choice(
                        987654321, choice
                    )
                    for pending_id in pending_ids:
                        item = app.store.get_item(
                            pending_id, chat_id=987654321
                        )
                        self.assertEqual(item["status"], "queued")
                finally:
                    app.store.close()

        asyncio.run(exercise())

    def test_episode_burst_uses_one_compact_identification_message(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                path = root / "config.json"
                path.write_text(json.dumps(config_data(root)), encoding="utf-8")
                cfg = load_config(path, create_from_example=False)
                app = BotApp(cfg)
                sent = []
                edited = []

                async def fake_send(chat_id, text, reply_markup=None, **kwargs):
                    sent.append(text)
                    return {"message_id": 99}

                async def fake_edit(chat_id, message_id, text):
                    edited.append((message_id, text))
                    return True

                async def fake_identify(chat_id, pending_id, caption=""):
                    app.store.update_item(
                        pending_id,
                        target_folder="Existing Series",
                        series_season=1,
                        series_episode=pending_id,
                        download_filename=f"Incoming - S01E{pending_id:02d}.mkv",
                        status="queued",
                    )

                app.send = fake_send
                app.edit_message = fake_edit
                app._run_ai_series_identification = fake_identify
                library = cfg.library("series", "series")
                try:
                    with patch(
                        "telegram_jellyfin_bot.bot.SERIES_BATCH_WINDOW_SECONDS",
                        0,
                    ):
                        for episode in (1, 2, 3):
                            await app._queue_series_for_identification(
                                987654321,
                                {
                                    "message_id": episode,
                                    "from": {"id": 44},
                                },
                                {
                                    "file_id": f"file-{episode}",
                                    "file_unique_id": f"burst-{episode}",
                                },
                                f"episode-{episode}.mkv",
                                "",
                                library,
                            )
                        await asyncio.sleep(0.05)
                    self.assertEqual(len(sent), 1)
                    self.assertIn("Identifying 3 episode(s)", sent[0])
                    self.assertEqual(len(edited), 1)
                    self.assertIn("3 episode(s) ready", edited[0][1])
                    self.assertIn(
                        "Existing Series: S01E01, S01E02, S01E03",
                        edited[0][1],
                    )
                    self.assertIn("Next: /download", edited[0][1])
                finally:
                    await app.shutdown()
                    app.store.close()

        asyncio.run(exercise())

    def test_download_review_shows_final_series_filename(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                path = root / "config.json"
                path.write_text(json.dumps(config_data(root)), encoding="utf-8")
                cfg = load_config(path, create_from_example=False)
                app = BotApp(cfg)
                sent = []

                class FakeDownloader:
                    running = False
                    running_chat_id = None

                async def fake_send(chat_id, text, reply_markup=None, **kwargs):
                    sent.append(text)

                app.downloader = FakeDownloader()
                app.send = fake_send
                try:
                    pending_id = app.queue.add(
                        message_id=1,
                        chat_id=987654321,
                        file_id="file-id",
                        file_unique_id="preview-final-name",
                        original_filename="[AWHT] Dr Stone S4 - 25 [480p].mkv",
                        file_size=1024,
                        received_at="2026-08-22T00:00:00+00:00",
                        target_folder="Dr. Stone (2019) [imdbid-tt9679542]",
                        library_key="series",
                        media_kind="series",
                        status="queued",
                    )
                    app.store.update_item(
                        pending_id,
                        series_season=4,
                        series_episode=25,
                        download_filename="Incoming - S04E25.mkv",
                    )
                    await app.cmd_download(987654321, "")
                    preview = sent[-1]
                    self.assertIn("#1", preview)
                    self.assertIn("Dr. Stone - S04E25.mkv", preview)
                    self.assertNotIn("[AWHT]", preview)
                    self.assertNotIn("Incoming", preview)
                    self.assertIn("send /remove", preview)
                finally:
                    app.store.close()

        asyncio.run(exercise())

    def test_download_review_id_removes_only_the_selected_item(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                path = root / "config.json"
                path.write_text(json.dumps(config_data(root)), encoding="utf-8")
                cfg = load_config(path, create_from_example=False)
                app = BotApp(cfg)
                sent = []

                class FakeDownloader:
                    running = False
                    running_chat_id = None

                async def fake_send(chat_id, text, reply_markup=None, **kwargs):
                    sent.append(text)

                app.downloader = FakeDownloader()
                app.send = fake_send
                try:
                    discarded = app.queue.add(
                        message_id=99,
                        chat_id=987654321,
                        file_id="discarded",
                        file_unique_id="discarded-before-review",
                        original_filename="discarded.mkv",
                        file_size=1,
                        received_at="2026-08-22T00:00:00+00:00",
                        target_folder="Example Show",
                        library_key="series",
                        media_kind="series",
                        status="queued",
                    )
                    self.assertTrue(
                        app.queue.remove(discarded, chat_id=987654321)
                    )
                    ids = []
                    for number in range(1, 16):
                        pending_id = app.queue.add(
                            message_id=number,
                            chat_id=987654321,
                            file_id=f"file-{number}",
                            file_unique_id=f"remove-preview-{number}",
                            original_filename=f"Show.S01E{number:02d}.mkv",
                            file_size=1024,
                            received_at="2026-08-22T00:00:00+00:00",
                            target_folder="Example Show",
                            library_key="series",
                            media_kind="series",
                            status="queued",
                            series_season=1,
                            series_episode=number,
                        )
                        ids.append(pending_id)

                    await app.cmd_download(987654321, "")
                    self.assertGreater(ids[0], 1)
                    self.assertIn("#1 ", sent[-1])
                    self.assertIn("#15 ", sent[-1])
                    self.assertEqual(
                        app._download_batch_id_for_pending(
                            987654321, ids[0]
                        ),
                        1,
                    )
                    await app.cmd_queue(987654321, "")
                    self.assertIn("Download ID #1", sent[-1])
                    self.assertNotIn("Queue ID", sent[-1])

                    await app.cmd_remove(987654321, "")
                    self.assertEqual(
                        app._chat_setting(987654321, "remove_review_pending"),
                        "1",
                    )
                    self.assertIn("/cancel", sent[-1])
                    await app._handle_update_in_context(
                        {},
                        None,
                        {"chat": {"id": 987654321}, "text": "1"},
                    )
                    await app.cmd_download(987654321, "")
                    updated_preview = sent[-1]
                    self.assertNotIn("Show - S01E01.mkv", updated_preview)
                    self.assertNotIn("#1 ", updated_preview)
                    self.assertIn("#2 ", updated_preview)
                    self.assertIn("Show - S01E02.mkv", updated_preview)
                    self.assertIn("#15 ", updated_preview)
                    self.assertIn("Show - S01E15.mkv", updated_preview)
                    self.assertEqual(
                        app._download_batch_id_for_pending(
                            987654321, ids[1]
                        ),
                        2,
                    )
                    remaining = app.queue.downloadable(987654321)
                    self.assertEqual(
                        [item["pending_id"] for item in remaining], ids[1:]
                    )

                    await app.cmd_remove(987654321, "")
                    await app.cmd_cancel(987654321, "")
                    self.assertEqual(
                        app._chat_setting(987654321, "remove_review_pending"),
                        "",
                    )
                    self.assertEqual(
                        app._chat_setting(987654321, "download_confirmation"),
                        "1",
                    )
                    self.assertIn("review was not changed", sent[-1])

                    def fake_track_task(coroutine, *args, **kwargs):
                        coroutine.close()
                        return None

                    app.track_task = fake_track_task
                    await app.cmd_confirm(987654321, "")
                    self.assertEqual(app._download_review_ids(987654321), [])
                    for pending_id in ids[1:]:
                        app.store.update_item(pending_id, status="completed")
                    new_id = app.queue.add(
                        message_id=100,
                        chat_id=987654321,
                        file_id="new-batch-file",
                        file_unique_id="new-batch-unique",
                        original_filename="Show.S01E16.mkv",
                        file_size=1024,
                        received_at="2026-08-22T00:00:00+00:00",
                        target_folder="Example Show",
                        library_key="series",
                        media_kind="series",
                        status="queued",
                        series_season=1,
                        series_episode=16,
                    )
                    self.assertGreater(new_id, 15)
                    await app.cmd_download(987654321, "")
                    self.assertIn("#1 ", sent[-1])
                    self.assertIn("Show - S01E16.mkv", sent[-1])
                    self.assertEqual(
                        app._download_batch_id_for_pending(
                            987654321, new_id
                        ),
                        1,
                    )
                finally:
                    app.store.close()

        asyncio.run(exercise())

    def test_ai_movie_exact_identity_is_queued_automatically(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                data = config_data(root)
                data.update({
                    "n8n_agent_enabled": True,
                    "n8n_agent_url": "http://n8n:5678/webhook/media-identify",
                })
                path = root / "config.json"
                path.write_text(json.dumps(data), encoding="utf-8")
                cfg = load_config(path, create_from_example=False)
                app = BotApp(cfg)

                class FakeIdentifier:
                    configured = True

                    async def identify(self, **kwargs):
                        return MediaIdentification(
                            title_query="The Last Whale Singer",
                            season=None,
                            episode=None,
                            year=2025,
                            confidence=0.93,
                            needs_user_input=False,
                            question=None,
                        )

                class FakeIMDb:
                    async def search(self, query, media_type="any"):
                        self.query = query
                        return ([{
                            "title": "The Last Whale Singer",
                            "year": 2025,
                            "score": 97,
                            "imdb_id": "tt13518550",
                            "folder_name": (
                                "The Last Whale Singer (2025) "
                                "[imdbid-tt13518550]"
                            ),
                        }], "test IMDb")

                async def fake_send(chat_id, text, reply_markup=None, **kwargs):
                    return None

                app.ai_identifier = FakeIdentifier()
                app.imdb = FakeIMDb()
                app.send = fake_send
                try:
                    pending_id = app.queue.add(
                        message_id=2,
                        chat_id=987654321,
                        file_id="movie-file",
                        file_unique_id="movie-unique",
                        original_filename="The.Last.Whale.Singer.1080p.mkv",
                        file_size=100,
                        received_at="2026-08-22T00:00:00+00:00",
                        target_folder=None,
                        library_key="movies",
                        media_kind="movie",
                        status="awaiting_identification",
                    )
                    await app._run_ai_movie_identification(
                        987654321, pending_id, ""
                    )
                    self.assertIn("The Last Whale Singer 2025", app.imdb.query)
                    item = app.store.get_item(
                        pending_id, chat_id=987654321
                    )
                    self.assertEqual(item["status"], "queued")
                    self.assertEqual(
                        item["target_folder"],
                        "The Last Whale Singer (2025) [imdbid-tt13518550]",
                    )
                    self.assertEqual(app.movie_choices, {})
                finally:
                    app.store.close()

        asyncio.run(exercise())

    def test_ai_movie_year_mismatch_still_requires_a_choice(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                data = config_data(root)
                data.update({
                    "n8n_agent_enabled": True,
                    "n8n_agent_url": "http://n8n:5678/webhook/media-identify",
                })
                path = root / "config.json"
                path.write_text(json.dumps(data), encoding="utf-8")
                cfg = load_config(path, create_from_example=False)
                app = BotApp(cfg)
                sent = []

                class FakeIdentifier:
                    configured = True

                    async def identify(self, **kwargs):
                        return MediaIdentification(
                            title_query="Kung Fu Panda",
                            season=None,
                            episode=None,
                            year=2011,
                            confidence=0.95,
                            needs_user_input=False,
                            question=None,
                        )

                class FakeIMDb:
                    async def search(self, query, media_type="any"):
                        return ([{
                            "title": "Kung Fu Panda",
                            "year": 2008,
                            "score": 99,
                            "imdb_id": "tt0441773",
                            "folder_name": (
                                "Kung Fu Panda (2008) [imdbid-tt0441773]"
                            ),
                        }], "test IMDb")

                async def fake_send(chat_id, text, reply_markup=None, **kwargs):
                    sent.append((text, reply_markup))

                app.ai_identifier = FakeIdentifier()
                app.imdb = FakeIMDb()
                app.send = fake_send
                try:
                    pending_id = app.queue.add(
                        message_id=3,
                        chat_id=987654321,
                        file_id="movie-file-mismatch",
                        file_unique_id="movie-unique-mismatch",
                        original_filename="Kung.Fu.Panda.2011.mkv",
                        file_size=100,
                        received_at="2026-08-23T00:00:00+00:00",
                        target_folder=None,
                        library_key="movies",
                        media_kind="movie",
                        status="awaiting_identification",
                    )
                    await app._run_ai_movie_identification(
                        987654321, pending_id, ""
                    )
                    item = app.store.get_item(
                        pending_id, chat_id=987654321
                    )
                    self.assertEqual(item["status"], "awaiting_identification")
                    self.assertEqual(len(app.movie_choices), 1)
                    self.assertTrue(
                        any("Choose the correct movie result" in text for text, _ in sent)
                    )
                finally:
                    app.store.close()

        asyncio.run(exercise())

    def test_filename_year_blocks_wrong_automatic_movie_identity(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                data = config_data(root)
                data.update({
                    "n8n_agent_enabled": True,
                    "n8n_agent_url": "http://n8n:5678/webhook/media-identify",
                })
                path = root / "config.json"
                path.write_text(json.dumps(data), encoding="utf-8")
                cfg = load_config(path, create_from_example=False)
                app = BotApp(cfg)
                sent = []

                class FakeIdentifier:
                    configured = True

                    async def identify(self, **kwargs):
                        return MediaIdentification(
                            title_query="Kung Fu Panda",
                            season=None,
                            episode=None,
                            year=2008,
                            confidence=0.99,
                            needs_user_input=False,
                            question=None,
                        )

                class FakeIMDb:
                    async def search(self, query, media_type="any"):
                        return ([{
                            "title": "Kung Fu Panda",
                            "year": 2008,
                            "score": 99,
                            "imdb_id": "tt0441773",
                            "folder_name": "Kung Fu Panda (2008) [imdbid-tt0441773]",
                        }], "test IMDb")

                async def fake_send(chat_id, text, reply_markup=None, **kwargs):
                    sent.append((text, reply_markup))

                app.ai_identifier = FakeIdentifier()
                app.imdb = FakeIMDb()
                app.send = fake_send
                try:
                    pending_id = app.queue.add(
                        message_id=31,
                        chat_id=987654321,
                        file_id="wrong-year-file",
                        file_unique_id="wrong-year-unique",
                        original_filename="Kung.Fu.Panda.2025.mkv",
                        file_size=100,
                        received_at="2026-08-23T00:00:00+00:00",
                        target_folder=None,
                        library_key="movies",
                        media_kind="movie",
                        status="awaiting_identification",
                    )
                    await app._run_ai_movie_identification(
                        987654321, pending_id, ""
                    )
                    item = app.store.get_item(pending_id, chat_id=987654321)
                    self.assertEqual(item["status"], "awaiting_identification")
                    self.assertEqual(len(app.movie_choices), 1)
                    self.assertTrue(
                        any(
                            "Kung.Fu.Panda.2025.mkv" in text
                            for text, _ in sent
                        )
                    )
                finally:
                    app.store.close()

        asyncio.run(exercise())

    def test_movie_burst_uses_one_compact_automatic_result(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                data = config_data(root)
                data.update({
                    "n8n_agent_enabled": True,
                    "n8n_agent_url": "http://n8n:5678/webhook/media-identify",
                })
                path = root / "config.json"
                path.write_text(json.dumps(data), encoding="utf-8")
                cfg = load_config(path, create_from_example=False)
                app = BotApp(cfg)
                sent = []
                edited = []
                identities = {
                    "Interstellar": (2014, "tt0816692"),
                    "Ratatouille": (2007, "tt0382932"),
                    "Kung Fu Panda 3": (2016, "tt2267968"),
                }

                class FakeIdentifier:
                    configured = True

                    async def identify(self, **kwargs):
                        title = Path(kwargs["filename"]).stem.replace("_", " ")
                        year, _ = identities[title]
                        return MediaIdentification(
                            title_query=title,
                            season=None,
                            episode=None,
                            year=year,
                            confidence=0.98,
                            needs_user_input=False,
                            question=None,
                        )

                class FakeIMDb:
                    async def search(self, query, media_type="any"):
                        title = next(title for title in identities if query.startswith(title))
                        year, imdb_id = identities[title]
                        return ([{
                            "title": title,
                            "year": year,
                            "score": 99,
                            "imdb_id": imdb_id,
                            "folder_name": f"{title} ({year}) [imdbid-{imdb_id}]",
                        }], "test IMDb")

                async def fake_send(chat_id, text, reply_markup=None, **kwargs):
                    sent.append(text)
                    return {"message_id": 88}

                async def fake_edit(chat_id, message_id, text):
                    edited.append((message_id, text))
                    return True

                app.ai_identifier = FakeIdentifier()
                app.imdb = FakeIMDb()
                app.send = fake_send
                app.edit_message = fake_edit
                try:
                    with patch(
                        "telegram_jellyfin_bot.bot.MOVIE_BATCH_WINDOW_SECONDS", 0
                    ):
                        for index, title in enumerate(identities, 1):
                            await app._queue_movie_for_identification(
                                987654321,
                                {
                                    "message_id": index,
                                    "from": {"id": 44},
                                },
                                {
                                    "file_id": f"file-{index}",
                                    "file_unique_id": f"movie-burst-{index}",
                                    "file_size": 100,
                                },
                                f"{title.replace(' ', '_')}.mkv",
                                "",
                            )
                        await asyncio.sleep(0.05)
                    self.assertEqual(len(sent), 1)
                    self.assertIn("Identifying 3 movie(s)", sent[0])
                    self.assertEqual(len(edited), 1)
                    self.assertIn("3 movie(s) ready", edited[0][1])
                    self.assertIn("Next: /download", edited[0][1])
                    queued = app.store.list_items(
                        ("queued",), chat_id=987654321
                    )
                    self.assertEqual(len(queued), 3)
                    self.assertEqual(app.movie_choices, {})
                finally:
                    await app.shutdown()
                    app.store.close()

        asyncio.run(exercise())


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
                "Movies",
                "Jellyfin",
                "Episodes",
                "IMDb",
            ],
        )
        commands = [item["command"] for item in BOT_COMMANDS]
        self.assertEqual(len(commands), 46)
        self.assertEqual(len(commands), len(set(commands)))

    def test_bilingual_guide_fits_telegram_and_language_callback_opens_it(self):
        self.assertLessEqual(len(GUIDE_EN), 4000)
        self.assertLessEqual(len(GUIDE_FA), 4000)
        self.assertIn("How to use", GUIDE_EN)
        self.assertIn("NORMAL WORKFLOW", GUIDE_EN)
        self.assertIn("ADVANCED WORKFLOW", GUIDE_EN)
        self.assertIn("If n8n is unavailable", GUIDE_EN)
        self.assertIn("راهنمای استفاده", GUIDE_FA)
        self.assertIn("روش عادی", GUIDE_FA)
        self.assertIn("روش پیشرفته", GUIDE_FA)

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

                async def fake_send(
                    chat_id, text, reply_markup=None, **kwargs
                ):
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

    def test_language_choice_persists_and_localizes_menus(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                path = root / "config.json"
                path.write_text(json.dumps(config_data(root)), encoding="utf-8")
                cfg = load_config(path, create_from_example=False)
                app = BotApp(cfg)

                class FakeApi:
                    def __init__(self):
                        self.sent = []

                    async def call(self, method, **params):
                        return True

                    async def send(
                        self, chat_id, text, reply_markup=None, **kwargs
                    ):
                        self.sent.append((text, reply_markup))

                api = FakeApi()
                app.api = api
                app.chat_types[987654321] = "private"
                try:
                    await app.cmd_menu(987654321, "")
                    self.assertIn("Choose your language", api.sent[-1][0])

                    await app.handle_callback({
                        "id": "language-fa",
                        "data": "language:fa",
                        "message": {
                            "chat": {"id": 987654321, "type": "private"}
                        },
                    })
                    self.assertEqual(
                        app.store.get_setting("language:987654321"), "fa"
                    )
                    self.assertTrue(
                        any(
                            "زبان ربات به فارسی تغییر کرد" in text
                            for text, _ in api.sent
                        )
                    )
                    all_button_texts = {
                        button["text"]
                        for _, markup in api.sent
                        if markup
                        for row in markup.get("keyboard", markup.get("inline_keyboard", []))
                        for button in row
                    }
                    self.assertIn("🗄 انتخاب کتابخانه", all_button_texts)
                    self.assertIn("🧰 پیشرفته", all_button_texts)

                    await app.handle_reply_category(
                        987654321, "🧹 مرتب‌سازی"
                    )
                    self.assertIn("دستورهای مرتب‌سازی", api.sent[-1][0])
                    submenu_buttons = {
                        button["text"]
                        for row in api.sent[-1][1]["inline_keyboard"]
                        for button in row
                    }
                    self.assertIn("مرتب‌سازی فایل‌های جدید", submenu_buttons)

                    await app.send(
                        987654321,
                        "Movie imported successfully.\nDestination: D:\\Movies\\Example",
                    )
                    self.assertIn("فیلم با موفقیت منتقل شد", api.sent[-1][0])
                    self.assertIn("مقصد:", api.sent[-1][0])
                    self.assertIn(r"D:\Movies\Example", api.sent[-1][0])

                    await app.send(
                        987654321,
                        "Downloads finished. 2 of 3 file(s) completed.\n"
                        "Queue (1 file(s)):\n"
                        "Movie · Example item 1 (Download ID #1) [queued] movie.mkv",
                    )
                    self.assertIn("2 از 3 فایل کامل شد", api.sent[-1][0])
                    self.assertIn("صف (1 فایل)", api.sent[-1][0])
                    self.assertIn("فیلم · Example مورد 1", api.sent[-1][0])
                    self.assertIn("شناسه دانلود #1", api.sent[-1][0])
                    self.assertIn("[در صف]", api.sent[-1][0])

                    await app.send(
                        987654321,
                        "✅ 3 movie(s) ready.\n"
                        "• Interstellar (2014)\n\nNext: /download",
                    )
                    self.assertIn("3 فیلم آماده است", api.sent[-1][0])
                    self.assertIn("مرحله بعد: /download", api.sent[-1][0])
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
                app.store.set_setting("language:987654321", "en")
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
                app.store.set_setting("language:-100123", "en")
                try:
                    await app.cmd_menu(-100123, "")
                    self.assertEqual(len(sent), 1)
                    self.assertIs(sent[0][2], CHANNEL_MENU)
                    callbacks = {
                        button.get("callback_data")
                        for row in CHANNEL_MENU["inline_keyboard"]
                        for button in row
                    }
                    self.assertIn("nav:advanced", callbacks)
                    self.assertIn("menu:libraries", callbacks)
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

    def test_reply_keyboard_choose_library_opens_picker(self):
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
                                "text": "🗄 Choose Library",
                            }
                        }
                    )
                    self.assertEqual(len(sent), 1)
                    self.assertIn("Current library:", sent[0][1])
                    callbacks = {
                        button.get("callback_data")
                        for row in sent[0][2]["inline_keyboard"]
                        for button in row
                    }
                    self.assertTrue(any(
                        callback and callback.startswith("library:")
                        for callback in callbacks
                    ))
                finally:
                    app.store.close()

        asyncio.run(exercise())

    def test_series_and_movie_tools_are_inside_advanced(self):
        labels = [
            button["text"]
            for row in PERSISTENT_CATEGORY_KEYBOARD["keyboard"]
            for button in row
        ]
        self.assertEqual(
            labels,
            [
                "📥 Downloads",
                "📺 Episodes",
                "🎬 Jellyfin",
                "⚙️ Bot",
                "🗄 Choose Library",
                "🧰 Advanced",
            ],
        )

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
                                "text": "🧰 Advanced",
                            }
                        }
                    )
                    self.assertEqual(len(sent), 1)
                    self.assertIs(sent[0][2], ADVANCED_MENU)
                    callbacks = {
                        button.get("callback_data")
                        for row in ADVANCED_MENU["inline_keyboard"]
                        for button in row
                    }
                    self.assertIn("nav:series", callbacks)
                    self.assertIn("nav:movies", callbacks)
                finally:
                    app.store.close()

        asyncio.run(exercise())

    def test_category_callback_replies_inside_its_existing_topic(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                path = root / "config.json"
                path.write_text(json.dumps(config_data(root)), encoding="utf-8")
                cfg = load_config(path, create_from_example=False)
                app = BotApp(cfg)

                class FakeApi:
                    def __init__(self):
                        self.sent = []

                    async def call(self, method, **params):
                        return True

                    async def send(
                        self, chat_id, text, reply_markup=None, **kwargs
                    ):
                        self.sent.append((chat_id, text, reply_markup, kwargs))

                api = FakeApi()
                app.api = api
                app.store.set_setting("language:-100123", "en")
                try:
                    await app.handle_update(
                        {
                            "callback_query": {
                                "id": "category-callback",
                                "data": "nav:sorting",
                                "message": {
                                    "message_id": 1,
                                    "message_thread_id": 42,
                                    "chat": {
                                        "id": -100123,
                                        "type": "supergroup",
                                    },
                                },
                            }
                        }
                    )
                    self.assertEqual(len(api.sent), 1)
                    self.assertIs(api.sent[0][2], SORTING_MENU)
                    self.assertEqual(api.sent[0][3]["message_thread_id"], 42)
                    self.assertIsNone(
                        api.sent[0][3]["direct_messages_topic_id"]
                    )
                finally:
                    app.store.close()

        asyncio.run(exercise())

    def test_concurrent_chats_keep_their_own_direct_message_topics(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                path = root / "config.json"
                data = config_data(root)
                data["allowed_chat_ids"] = []
                path.write_text(json.dumps(data), encoding="utf-8")
                cfg = load_config(path, create_from_example=False)
                app = BotApp(cfg)

                class FakeApi:
                    def __init__(self):
                        self.sent = []

                    async def call(self, method, **params):
                        return True

                    async def send(
                        self, chat_id, text, reply_markup=None, **kwargs
                    ):
                        # Force both updates to overlap. Context-local routing
                        # must keep every response attached to its source.
                        await asyncio.sleep(0)
                        self.sent.append((chat_id, kwargs))

                api = FakeApi()
                app.api = api
                for chat_id in (111, 222):
                    app.store.set_setting(f"language:{chat_id}", "en")

                def message(chat_id, topic_id):
                    return {
                        "message": {
                            "message_id": topic_id,
                            "chat": {"id": chat_id, "type": "private"},
                            "direct_messages_topic": {"topic_id": topic_id},
                            "text": "\U0001f9f9 Sorting",
                        }
                    }

                try:
                    await asyncio.gather(
                        app.handle_update(message(111, 7001)),
                        app.handle_update(message(222, 8002)),
                    )
                    routes = {
                        chat_id: params["direct_messages_topic_id"]
                        for chat_id, params in api.sent
                    }
                    self.assertEqual(routes, {111: 7001, 222: 8002})
                    self.assertTrue(
                        all(
                            params["message_thread_id"] is None
                            for _, params in api.sent
                        )
                    )
                finally:
                    app.store.close()

        asyncio.run(exercise())


class DownloadSafetyTests(unittest.TestCase):
    def test_one_chat_cannot_cancel_another_chats_download(self):
        manager = object.__new__(DownloadManager)
        manager.running = True
        manager.running_chat_id = 10
        manager.cancel_event = asyncio.Event()

        self.assertFalse(manager.cancel(20))
        self.assertFalse(manager.cancel_event.is_set())
        self.assertTrue(manager.cancel(10))
        self.assertTrue(manager.cancel_event.is_set())

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
                api_params = {}

                async def fake_api_call(method, **params):
                    api_params.update(params)
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
                    self.assertEqual(
                        api_params["_request_timeout"].sock_read, 1800
                    )
                finally:
                    store.close()
        asyncio.run(exercise())

    def test_http_movie_stream_uses_large_file_timeout(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                path = root / "config.json"
                data = config_data(root)
                data["telegram_download_read_timeout_seconds"] = 2400
                path.write_text(json.dumps(data), encoding="utf-8")
                cfg = load_config(path, create_from_example=False)
                store = StateStore(cfg.data_path / "state.db")
                queue = QueueManager(store)

                class FakeContent:
                    async def iter_chunked(self, size):
                        yield b"movie-data"

                class FakeResponse:
                    content = FakeContent()

                    async def __aenter__(self):
                        return self

                    async def __aexit__(self, *_):
                        return False

                    def raise_for_status(self):
                        return None

                class FakeSession:
                    def __init__(self):
                        self.timeout = None

                    def get(self, url, **kwargs):
                        self.timeout = kwargs.get("timeout")
                        return FakeResponse()

                session = FakeSession()
                manager = DownloadManager(cfg, queue, None, session)
                destination = root / "large-movie.part"
                try:
                    await manager._download_http("videos/movie.mp4", destination)
                    self.assertEqual(destination.read_bytes(), b"movie-data")
                    self.assertEqual(session.timeout.sock_read, 2400)
                    self.assertIsNone(session.timeout.total)
                finally:
                    store.close()

        asyncio.run(exercise())

    def test_failed_movie_batch_does_not_claim_it_will_import(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                path = root / "config.json"
                path.write_text(json.dumps(config_data(root)), encoding="utf-8")
                cfg = load_config(path, create_from_example=False)
                store = StateStore(cfg.data_path / "state.db")
                queue = QueueManager(store)
                pending_id = queue.add(
                    message_id=4,
                    chat_id=-1,
                    file_id="movie-id",
                    file_unique_id="movie-unique",
                    original_filename="movie.mkv",
                    file_size=100,
                    target_folder="Movie (2026)",
                    media_kind="movie",
                )
                manager = DownloadManager(cfg, queue, None, None)
                notices = []

                async def fail_download(item, notify):
                    queue.set_status(item["pending_id"], "failed", "simulated")

                async def notify(text):
                    notices.append(text)

                manager._download_one = fail_download
                try:
                    await manager.run(
                        [store.get_item(pending_id)], notify
                    )
                    final = notices[-1]
                    self.assertIn("0 of 1", final)
                    self.assertIn("No files were completed or imported", final)
                    self.assertNotIn("imported automatically", final)
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
    def test_anime_library_uses_the_same_imdb_search_as_other_libraries(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                data = config_data(root)
                data["media_libraries"] = [
                    {
                        "key": "anime_series",
                        "name": "Anime Series",
                        "media_kind": "series",
                        "path": str(root / "anime-series"),
                    },
                    {
                        "key": "anime_movies",
                        "name": "Anime Movies",
                        "media_kind": "movie",
                        "path": str(root / "anime-movies"),
                    },
                ]
                data["default_library_key"] = "anime_series"
                path = root / "config.json"
                path.write_text(json.dumps(data), encoding="utf-8")
                cfg = load_config(path, create_from_example=False)
                app = BotApp(cfg)

                async def fake_send(chat_id, text, reply_markup=None):
                    return None

                async def fake_imdb(query, limit=8, media_type="any"):
                    self.assertEqual(media_type, "series")
                    return ([{
                        "imdb_id": "tt14986406",
                        "title": "BLEACH: Thousand-Year Blood War",
                        "year": 2022,
                        "score": 98.0,
                        "folder_name": (
                            "BLEACH_ Thousand-Year Blood War (2022) "
                            "[imdbid-tt14986406]"
                        ),
                    }], "online")

                app.send = fake_send
                app.imdb.search = fake_imdb
                try:
                    await app._run_imdb_search(1, "bleach tybw", "use")
                    choice = next(iter(app.imdb_choices.values()))
                    self.assertEqual(choice["imdb_id"], "tt14986406")
                    self.assertEqual(
                        choice["folder_name"],
                        "BLEACH_ Thousand-Year Blood War (2022) "
                        "[imdbid-tt14986406]",
                    )
                finally:
                    app.store.close()

        asyncio.run(exercise())

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

                async def fake_search(query, limit=8, media_type="any"):
                    self.assertEqual(media_type, "series")
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
                    self.assertEqual(
                        app.store.get_chat_setting(1, "current_folder"), ""
                    )
                    choice = next(iter(app.imdb_choices.values()))
                    self.assertEqual(
                        choice["folder_name"],
                        "Dr. Stone (2019) [imdbid-tt9679542]",
                    )
                    await app._commit_folder(1, choice["folder_name"])
                    self.assertEqual(
                        app.store.get_chat_setting(1, "current_folder"),
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
                app.store.set_chat_setting(987654321, "current_folder", "First Show")
                token = "rename-test"
                app.imdb_choices[token] = {
                    "chat_id": 987654321,
                    "folder_name": "Official Show (2026) [imdbid-tt1234567]",
                    "mode": "rename",
                    "created_at": 9999999999,
                    "source": "online",
                    "source_folder": "First Show",
                }
                app.store.set_chat_setting(987654321, "current_folder", "Second Show")
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
                app.store.set_chat_setting(123, "current_folder", "NIPPON SAGOKu")
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

    def test_sort_batch_is_recorded_for_the_chat_that_started_it(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                path = root / "config.json"
                path.write_text(json.dumps(config_data(root)), encoding="utf-8")
                cfg = load_config(path, create_from_example=False)
                store = StateStore(cfg.data_path / "state.db")
                bridge = SorterBridge(cfg, store)
                try:
                    ok, _ = await bridge._execute(
                        root,
                        [
                            sys.executable,
                            "-c",
                            "print('Batch ID: owned-batch-123')",
                        ],
                        chat_id=777,
                        operation_kind="series",
                    )
                    self.assertTrue(ok)
                    self.assertEqual(
                        store.latest_sorter_batch(777), "owned-batch-123"
                    )
                    self.assertTrue(
                        store.sorter_batch_belongs_to_chat(
                            "owned-batch-123", 777
                        )
                    )
                    self.assertFalse(
                        store.sorter_batch_belongs_to_chat(
                            "owned-batch-123", 888
                        )
                    )
                finally:
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

    def test_scan_waits_for_existing_task_then_requests_a_fresh_refresh(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                path = root / "config.json"
                path.write_text(json.dumps(config_data(root)), encoding="utf-8")
                cfg = load_config(path, create_from_example=False)
                store = StateStore(cfg.data_path / "state.db")
                existing_running = self.scan_task(
                    "Running",
                    started="2026-08-23T10:00:00Z",
                    ended="",
                    progress=80,
                )
                existing_completed = self.scan_task(
                    "Idle",
                    started="2026-08-23T10:00:00Z",
                    ended="2026-08-23T10:00:10Z",
                )
                fresh_running = self.scan_task(
                    "Running",
                    started="2026-08-23T10:00:11Z",
                    ended="",
                    progress=25,
                )
                fresh_completed = self.scan_task(
                    "Idle",
                    started="2026-08-23T10:00:11Z",
                    ended="2026-08-23T10:00:20Z",
                )
                session = _FakeJellyfinSession([
                    existing_running,
                    existing_completed,
                    existing_completed,
                    fresh_running,
                    fresh_completed,
                ])
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
                    self.assertEqual(len(session.posts), 1)
                    self.assertTrue(
                        session.posts[0][0].endswith("/Library/Refresh")
                    )
                    phases = [update["phase"] for update in updates]
                    self.assertIn("already-running", phases)
                    self.assertIn("accepted", phases)
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
                    self.assertTrue(any("scan started" in text for text in sent))
                    self.assertTrue(
                        any("Jellyfin is ready" in text for text in sent)
                    )
                finally:
                    app.store.close()
        asyncio.run(exercise())

    def test_completed_jellyfin_scan_keeps_one_ready_status_message(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                path = root / "config.json"
                path.write_text(json.dumps(config_data(root)), encoding="utf-8")
                cfg = load_config(path, create_from_example=False)
                app = BotApp(cfg)

                class FakeJellyfin:
                    async def scan_library_and_wait(self, on_update):
                        await on_update({"phase": "accepted"})
                        await on_update({"phase": "progress", "progress": 75.0})
                        return {
                            "status": "Completed",
                            "completed_at": "2026-08-23T00:00:00Z",
                        }

                class FakeApi:
                    def __init__(self):
                        self.sent = []
                        self.edited = []
                        self.deleted = []

                    async def send(self, chat_id, text, reply_markup=None, **kwargs):
                        self.sent.append(text)
                        return {"message_id": 55}

                    async def edit(self, chat_id, message_id, text):
                        self.edited.append((message_id, text))
                        return True

                    async def delete(self, chat_id, message_id):
                        self.deleted.append(message_id)
                        return True

                api = FakeApi()
                app.api = api
                app.jellyfin = FakeJellyfin()
                try:
                    await app._run_jellyfin_scan(987654321)
                    self.assertEqual(len(api.sent), 1)
                    self.assertEqual(api.edited[-1], (55, "✅ Jellyfin is ready."))
                    self.assertEqual(api.deleted, [])
                finally:
                    await app.shutdown()
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
