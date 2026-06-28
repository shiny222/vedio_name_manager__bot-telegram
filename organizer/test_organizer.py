from pathlib import Path
import json
import tempfile
import unittest

import organizer


class EpisodeDetectionTests(unittest.TestCase):
    def test_anime_season_dash_episode(self):
        result = organizer.detect_episode(
            Path("[AWHT] Dr. Stone S4 - 25 [480p].mkv")
        )
        self.assertEqual(result, (4, 25))

    def test_resolution_is_not_episode(self):
        result = organizer.explicit_episode_match("Dr. Stone S4 - 480p")
        self.assertIsNone(result)

    def test_supported_explicit_formats(self):
        cases = {
            "Show.S04E025.1080p.mkv": (4, 25),
            "Show 4x25.mkv": (4, 25),
            "Show Season 4 Episode 25.mkv": (4, 25),
            "Show.Season04E25.mkv": (4, 25),
            "Show S4 EP25.mkv": (4, 25),
            "Show Episode 25 - S4.mkv": (4, 25),
            "Show Season 4 - 25 [720p].mkv": (4, 25),
            "Show Episode 25.mkv": (1, 25),
            "Show EP25v2.mkv": (1, 25),
            "Show E25.mkv": (1, 25),
            "فصل ۴ قسمت ۲۵.mkv": (4, 25),
            "الموسم 4 الحلقة 25.mkv": (4, 25),
            "アニメ 第25話.mkv": (1, 25),
            "애니메이션 25화.mkv": (1, 25),
        }
        for filename, expected in cases.items():
            with self.subTest(filename=filename):
                self.assertEqual(
                    organizer.detect_episode(Path(filename)), expected
                )

    def test_quality_and_year_are_not_standalone_episodes(self):
        for filename in (
            "Show 1080p.mkv",
            "Show 2026 720p.mkv",
            "Show S2 - 1080p.mkv",
        ):
            with self.subTest(filename=filename):
                self.assertIsNone(organizer.detect_episode(Path(filename)))


class HistoryAwareRenameTests(unittest.TestCase):
    def test_rename_migrates_history_and_undo_still_works(self):
        with tempfile.TemporaryDirectory() as td:
            library = Path(td) / "Library"
            old = library / "Wrong Name"
            season = old / "Season 01"
            season.mkdir(parents=True)
            organized = season / "Wrong Name - S01E01.mkv"
            organized.write_bytes(b"episode")
            original = old / "downloaded_episode.mkv"
            history = season / organizer.HISTORY_NAME
            history.write_text(
                json.dumps(
                    [{
                        "timestamp": organizer.now_iso(),
                        "original_full_path": str(original.resolve()),
                        "new_full_path": str(organized.resolve()),
                        "original_filename": original.name,
                        "new_filename": organized.name,
                        "file_size": organized.stat().st_size,
                        "file_type": "video",
                        "status": "done",
                        "batch_id": "test-batch",
                    }],
                    indent=2,
                ),
                encoding="utf-8",
            )

            new, affected, migration_id = organizer.rename_series_folder(
                old, "Correct Name (2025) [imdbid-tt1234567]"
            )
            self.assertFalse(old.exists())
            self.assertTrue(new.is_dir())
            self.assertEqual(affected, 1)
            self.assertTrue(migration_id)
            migrated_history = new / "Season 01" / organizer.HISTORY_NAME
            records = json.loads(migrated_history.read_text(encoding="utf-8"))
            self.assertTrue(
                records[0]["original_full_path"].startswith(str(new.resolve()))
            )
            self.assertTrue(
                records[0]["new_full_path"].startswith(str(new.resolve()))
            )
            self.assertTrue(
                records[0]["recorded_original_full_path"].startswith(
                    str(old.resolve())
                )
            )
            folder_audit = json.loads(
                (new / organizer.FOLDER_HISTORY_NAME).read_text(encoding="utf-8")
            )
            self.assertEqual(folder_audit[-1]["migration_id"], migration_id)

            restored, skipped = organizer.undo_records([migrated_history])
            self.assertEqual((restored, skipped), (1, 0))
            self.assertTrue(new.joinpath("downloaded_episode.mkv").exists())

    def test_rename_never_merges_existing_folder(self):
        with tempfile.TemporaryDirectory() as td:
            library = Path(td)
            old = library / "Old"
            destination = library / "Existing"
            old.mkdir()
            destination.mkdir()
            with self.assertRaises(FileExistsError):
                organizer.rename_series_folder(old, "Existing")
            self.assertTrue(old.exists())


class SortRevisionTests(unittest.TestCase):
    def test_existing_resort_and_simple_back_forward(self):
        with tempfile.TemporaryDirectory() as td:
            series = Path(td) / "Correct Show [imdbid-tt123]"
            season = series / "Season 01"
            season.mkdir(parents=True)
            old_file = season / "Old Show - S01E01.mkv"
            old_file.write_bytes(b"episode")

            self.assertEqual(organizer.resort_existing(series), 0)
            renamed = season / "Correct Show - S01E01.mkv"
            self.assertTrue(renamed.exists())

            revisions = organizer.sync_sort_revisions(series)
            self.assertEqual(len(revisions), 1)
            self.assertEqual(revisions[0]["revision"], 1)
            self.assertEqual(revisions[0]["operation"], "resort-existing")

            self.assertEqual(
                organizer.change_sort_revision(series, "back"), 0
            )
            self.assertTrue(old_file.exists())
            self.assertFalse(renamed.exists())

            self.assertEqual(
                organizer.change_sort_revision(series, "forward"), 0
            )
            self.assertTrue(renamed.exists())
            self.assertFalse(old_file.exists())

    def test_normal_sort_does_not_rename_existing_episodes(self):
        with tempfile.TemporaryDirectory() as td:
            series = Path(td) / "New Folder Name"
            season = series / "Season 01"
            season.mkdir(parents=True)
            existing = season / "Old Folder Name - S01E01.mkv"
            existing.write_bytes(b"old")
            incoming = series / "episode 2.mkv"
            incoming.write_bytes(b"new")

            self.assertEqual(organizer.run_organizer(series), 0)
            self.assertTrue(existing.exists())
            self.assertTrue(
                season.joinpath("New Folder Name - S01E02.mkv").exists()
            )


if __name__ == "__main__":
    unittest.main()
