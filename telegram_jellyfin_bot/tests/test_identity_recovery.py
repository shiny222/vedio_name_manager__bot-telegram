from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import AsyncMock

from telegram_jellyfin_bot.bot import BotApp
from telegram_jellyfin_bot.config import load_config
from telegram_jellyfin_bot.n8n_bridge import MediaIdentification
from telegram_jellyfin_bot.tests.test_core import config_data


class IdentityRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        path = self.root / "config.json"
        path.write_text(json.dumps(config_data(self.root)), encoding="utf-8")
        self.cfg = load_config(path, create_from_example=False)
        self.app = BotApp(self.cfg)
        self.addCleanup(self.app.store.close)
        self.app.send = AsyncMock()
        self.chat = 987654321
        self.result = dict(title="Example", year=2025, imdb_id="tt2222222",
                           score=99, folder_name="Example (2025) [imdbid-tt2222222]")
        self.identity = MediaIdentification("Example", 1, 1, 2025, 0.96, False, None)

    def queue(self, kind="series"):
        return self.app.queue.add(
            message_id=1, chat_id=self.chat, file_id="f", file_unique_id="u",
            original_filename="Example.2025.S01E01.mkv", file_size=4,
            target_folder=None, library_key="series" if kind == "series" else "movies",
            media_kind=kind, status="awaiting_identification",
        )

    async def route(self):
        pending = self.queue()
        self.app.imdb = AsyncMock()
        self.app.imdb.search.return_value = ([self.result], "test")
        await self.app._continue_series_identification(self.chat, pending, self.identity)
        return self.app.store.get_item(pending)

    async def test_remake_does_not_select_old_folder_automatically(self):
        old = self.cfg.target_path("Example (1990) [imdbid-tt1111111]", "series")
        old.mkdir(parents=True)
        (old / "Example S01E01.mkv").write_bytes(b"old")
        item = await self.route()
        self.assertEqual(item["status"], "awaiting_identification")
        self.assertIsNone(item["target_folder"])
        self.assertFalse(self.cfg.target_path(self.result["folder_name"], "series").exists())
        self.assertEqual((old / "Example S01E01.mkv").read_bytes(), b"old")
        self.assertEqual(len(self.app.imdb_choices), 2)

    async def test_all_three_values_match_reuses_folder(self):
        self.cfg.target_path(self.result["folder_name"], "series").mkdir(parents=True)
        item = await self.route()
        self.assertEqual(item["status"], "queued")
        self.assertEqual(item["target_folder"], self.result["folder_name"])
        self.assertFalse(self.app.imdb_choices)

    async def test_missing_id_requires_confirmation(self):
        self.cfg.target_path("Example (2025)", "series").mkdir(parents=True)
        item = await self.route()
        self.assertEqual(item["status"], "awaiting_identification")
        self.assertEqual(len(self.app.imdb_choices), 2)

    async def test_same_id_different_name_requires_confirmation(self):
        self.cfg.target_path("Other (2025) [imdbid-tt2222222]", "series").mkdir(parents=True)
        item = await self.route()
        self.assertEqual(item["status"], "awaiting_identification")

    async def test_unrelated_title_in_same_year_does_not_block_creation(self):
        self.cfg.target_path("Other (2025) [imdbid-tt1111111]", "series").mkdir(parents=True)
        item = await self.route()
        self.assertEqual(item["status"], "queued")
        self.assertTrue(self.cfg.target_path(self.result["folder_name"], "series").is_dir())

    async def test_low_imdb_score_requires_confirmation_even_with_existing_id(self):
        self.result["score"] = 40
        self.cfg.target_path(self.result["folder_name"], "series").mkdir(parents=True)
        item = await self.route()
        self.assertEqual(item["status"], "awaiting_identification")

    async def test_ai_missing_year_or_low_confidence_never_calls_imdb(self):
        pending = self.queue()
        self.app.ai_identifier = AsyncMock()
        self.app.imdb = AsyncMock()
        for year, confidence in ((None, 0.99), (2025, 0.4)):
            with self.subTest(year=year, confidence=confidence):
                self.app.ai_identifier.identify.return_value = MediaIdentification(
                    "Example", 1, 1, year, confidence, False, None)
                await self.app._run_ai_series_identification(self.chat, pending)
                self.app.imdb.search.assert_not_called()
                self.assertEqual(self.app.store.get_item(pending)["status"], "awaiting_identification")
                self.assertEqual(list(self.cfg.library("series", "series").path.iterdir()), [])

    async def test_manual_name_creates_without_id_and_without_imdb(self):
        pending = self.queue()
        self.app.imdb = AsyncMock()
        await self.app._run_manual_series_identification(self.chat, pending, "Example (2025) | 1 | 1")
        self.app.imdb.search.assert_not_called()
        item = self.app.store.get_item(pending)
        self.assertEqual(item["status"], "queued")
        self.assertEqual(item["target_folder"], "Example (2025)")
        self.assertIsNone(item["imdb_id"])

    async def test_imdb_outage_creates_without_id(self):
        pending = self.queue()
        self.app.imdb = AsyncMock()
        self.app.imdb.search.side_effect = RuntimeError("offline")
        await self.app._continue_series_identification(self.chat, pending, self.identity)
        item = self.app.store.get_item(pending)
        self.assertEqual(item["status"], "queued")
        self.assertEqual(item["target_folder"], "Example (2025)")

    async def test_movie_legacy_folder_detected_by_bot(self):
        legacy = self.cfg.movie_target_path("Example (2025)", "movies")
        legacy.mkdir(parents=True)
        video = legacy / "Example (2025).mkv"
        video.write_bytes(b"film")
        self.assertEqual(self.app._movie_library_conflict_path(
            "movies", self.result["folder_name"], "tt2222222").resolve(), video.resolve())

    async def test_movie_undo_updates_path_and_forward_command_imports_again(self):
        pending = self.queue("movie")
        source = self.cfg.movie_staging_job_path(pending) / "original.release.mkv"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"film")
        self.app.store.update_item(pending, status="completed", downloaded_path=str(source),
                                   movie_title="Example", movie_year=2025, imdb_id="tt2222222",
                                   target_folder=self.result["folder_name"])
        self.assertTrue(await self.app._import_movie_item(self.chat, self.app.store.get_item(pending)))
        item = self.app.store.get_item(pending)
        await self.app._run_movie_undo(self.chat, item["movie_batch_id"])
        item = self.app.store.get_item(pending)
        self.assertEqual(item["status"], "movie_undone")
        self.assertEqual(Path(item["downloaded_path"]), source)
        tasks = []
        self.app.track_task = lambda coro, *args: tasks.append(coro)
        await self.app.cmd_movie_import(self.chat, str(pending))
        self.assertEqual(len(tasks), 1)
        await tasks[0]
        self.assertEqual(self.app.store.get_item(pending)["status"], "imported")
        self.assertFalse(source.exists())

    async def test_movie_recovery_syncs_imported_path_after_lost_response(self):
        pending = self.queue("movie")
        source = self.cfg.movie_staging_job_path(pending) / "original.release.mkv"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"film")
        self.app.store.update_item(pending, status="completed", downloaded_path=str(source),
                                   movie_source_path=str(source), movie_title="Example", movie_year=2025,
                                   imdb_id="tt2222222", target_folder=self.result["folder_name"])
        await self.app.movie_sorter.import_movie(self.app.store.get_item(pending))
        await self.app._recover_movie_item(self.chat, self.app.store.get_item(pending))
        item = self.app.store.get_item(pending)
        self.assertEqual(item["status"], "imported")
        self.assertTrue(Path(item["downloaded_path"]).is_file())
        self.app.store.update_item(pending, movie_source_path=None)
        await self.app._recover_movie_item(self.chat, self.app.store.get_item(pending))
        self.assertEqual(Path(self.app.store.get_item(pending)["movie_source_path"]), source)
