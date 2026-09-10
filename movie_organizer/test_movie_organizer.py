from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

import movie_organizer as movie


class MovieRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.staging = self.root / "staging"
        self.staging.mkdir()
        self.source = self.staging / "original.release.mkv"
        self.source.write_bytes(b"movie")
        self.library = self.root / "library"
        self.folder = self.library / "Film (2020) [imdbid-tt1234567]"

    def import_movie(self, **kwargs):
        return movie.import_movie(self.source, self.library, "Film", 2020, "tt1234567", **kwargs)

    def test_damaged_history_is_detected_before_move(self):
        self.folder.mkdir(parents=True)
        history = self.folder / movie.HISTORY_NAME
        history.write_text("broken", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.import_movie()
        self.assertEqual(self.source.read_bytes(), b"movie")
        self.assertEqual(history.read_text(), "broken")
        self.assertEqual(list(self.folder.glob("*.mkv")), [])

    def test_recovery_accepts_completed_replacement_undo(self):
        self.folder.mkdir(parents=True)
        old = self.folder / (self.folder.name + ".mkv")
        old.write_bytes(b"old movie")
        result = self.import_movie(replace_existing=True)
        self.assertTrue(movie.undo_batch(self.library, result["batch_id"])["ok"])
        recovered = movie.recover_folder(self.folder, self.staging, self.source)
        self.assertTrue(recovered["ok"])
        self.assertEqual(recovered["status"], "movie_undone")
        self.assertEqual(old.read_bytes(), b"old movie")

    def test_recovery_rolls_back_video_if_subtitle_was_not_reached(self):
        subtitle = self.source.with_suffix(".srt")
        subtitle.write_text("subtitle", encoding="utf-8")
        original_move = movie.move_and_record
        def stop_before_subtitle(plan, *args):
            if plan.file_type == "subtitle":
                raise KeyboardInterrupt
            return original_move(plan, *args)
        with patch.object(movie, "move_and_record", side_effect=stop_before_subtitle):
            with self.assertRaises(KeyboardInterrupt):
                self.import_movie()
        self.assertFalse(self.source.exists())
        recovered = movie.recover_folder(self.folder, self.staging, self.source)
        self.assertTrue(recovered["ok"])
        self.assertEqual(recovered["status"], "movie_undone")
        self.assertEqual(self.source.read_bytes(), b"movie")
        self.assertTrue(self.import_movie()["ok"])

    def test_legacy_folder_blocks_duplicate_and_replacement_is_reversible(self):
        legacy = self.library / "Film (2020)"
        legacy.mkdir(parents=True)
        old = legacy / "Film (2020).mkv"
        old.write_bytes(b"old movie")
        with self.assertRaises(FileExistsError):
            self.import_movie()
        self.assertFalse(self.folder.exists())
        result = self.import_movie(replace_existing=True)
        self.assertEqual(Path(result["destination"]), legacy)
        self.assertEqual(old.read_bytes(), b"movie")
        undone = movie.undo_batch(self.library, result["batch_id"])
        self.assertTrue(undone["ok"])
        self.assertEqual(old.read_bytes(), b"old movie")
        self.assertEqual(self.source.read_bytes(), b"movie")

    def test_different_imdb_id_is_a_different_movie(self):
        other = self.library / "Film (2020) [imdbid-tt7654321]"
        other.mkdir(parents=True)
        (other / "other.mkv").write_bytes(b"other")
        self.import_movie()
        self.assertTrue(self.folder.is_dir())
        self.assertEqual((other / "other.mkv").read_bytes(), b"other")

    def test_interrupted_move_recovers_history_and_undo_restores_original_name(self):
        with patch.object(movie, "save_history", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.import_movie()
        self.assertFalse(self.source.exists())
        recovered = movie.recover_folder(self.folder, self.staging, self.source)
        self.assertTrue(recovered["ok"])
        self.assertEqual(recovered["status"], "imported")
        self.assertTrue(movie.undo_batch(self.library, recovered["batch_id"])["ok"])
        self.assertEqual(self.source.read_bytes(), b"movie")
        self.assertTrue(self.import_movie()["ok"])

    def test_interrupted_undo_recovers_staging_path(self):
        result = self.import_movie()
        with patch.object(movie, "save_history", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                movie.undo_batch(self.library, result["batch_id"])
        recovered = movie.recover_folder(self.folder, self.staging, self.source)
        self.assertTrue(recovered["ok"])
        self.assertEqual(recovered["status"], "movie_undone")
        self.assertEqual(Path(recovered["downloaded_path"]), self.source)
        self.assertTrue(self.import_movie()["ok"])

    def test_recovery_is_idempotent_and_refuses_two_copies(self):
        with patch.object(movie, "save_history", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.import_movie()
        self.source.write_bytes(b"movie")
        self.assertFalse(movie.recover_folder(self.folder, self.staging, self.source)["ok"])
        self.source.unlink()
        self.assertTrue(movie.recover_folder(self.folder, self.staging, self.source)["ok"])
        before = (self.folder / movie.HISTORY_NAME).read_bytes()
        self.assertTrue(movie.recover_folder(self.folder, self.staging, self.source)["ok"])
        self.assertEqual((self.folder / movie.HISTORY_NAME).read_bytes(), before)

    def test_history_save_failure_rolls_back(self):
        with patch.object(movie, "save_history", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                self.import_movie()
        self.assertEqual(self.source.read_bytes(), b"movie")

    def test_recovery_restores_backup_when_replacement_stops_before_import(self):
        self.folder.mkdir(parents=True)
        old = self.folder / (self.folder.name + ".mkv")
        old.write_bytes(b"old movie")
        original_move = movie.move_and_record
        def stop_before_import(plan, *args):
            if plan.operation == "import":
                raise KeyboardInterrupt
            return original_move(plan, *args)
        with patch.object(movie, "move_and_record", side_effect=stop_before_import):
            with self.assertRaises(KeyboardInterrupt):
                self.import_movie(replace_existing=True)
        self.assertFalse(old.exists())
        recovered = movie.recover_folder(self.folder, self.staging, self.source)
        self.assertTrue(recovered["ok"])
        self.assertEqual(old.read_bytes(), b"old movie")
        self.assertEqual(self.source.read_bytes(), b"movie")


if __name__ == "__main__":
    unittest.main()
