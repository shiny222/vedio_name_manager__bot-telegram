from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

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

    def test_numeric_fallback_rejects_technical_metadata(self):
        self.assertEqual(
            organizer.detect_episode(Path("video_001.mkv")),
            (1, 1),
        )
        for filename in (
            "Show 10bit.mkv",
            "[Group2] Show.mkv",
            "Show x264 10bit.mkv",
            "Show AAC2.mkv",
            "Show AAC 2.mkv",
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
    def test_episode_title_excludes_folder_year_and_provider_id(self):
        self.assertEqual(
            organizer.series_file_title(
                "Correct Official Title (2026) [imdbid-tt40548519]"
            ),
            "Correct Official Title",
        )

    def test_existing_resort_and_simple_back_forward(self):
        with tempfile.TemporaryDirectory() as td:
            series = Path(td) / "Correct Show (2026) [imdbid-tt123]"
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
                organizer.change_sort_revision(series, "back", revision=1), 1
            )

            self.assertEqual(
                organizer.change_sort_revision(series, "forward"), 0
            )
            self.assertTrue(renamed.exists())
            self.assertFalse(old_file.exists())
            self.assertEqual(
                organizer.change_sort_revision(series, "forward", revision=1),
                1,
            )

    def test_undo_missing_batch_reports_failure(self):
        with tempfile.TemporaryDirectory() as td:
            library = Path(td) / "Library"
            library.mkdir()
            self.assertEqual(
                organizer.undo_batch(library, "does-not-exist"),
                1,
            )

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

    def test_resort_handles_unsorted_and_nested_old_layouts(self):
        with tempfile.TemporaryDirectory() as td:
            series = Path(td) / "Correct Show"
            nested = series / "_Unsorted" / "480p"
            nested.mkdir(parents=True)
            old_file = nested / "Old.Show.S02E03.mkv"
            old_file.write_bytes(b"episode")

            self.assertEqual(organizer.resort_existing(series), 0)
            self.assertTrue(
                series.joinpath(
                    "Season 02", "Correct Show - S02E03.mkv"
                ).exists()
            )


class OperationSafetyTests(unittest.TestCase):
    def test_failed_moves_make_the_batch_fail(self):
        with tempfile.TemporaryDirectory() as td:
            series = Path(td) / "Show"
            series.mkdir()
            source = series / "Show.S01E01.mkv"
            source.write_bytes(b"episode")

            with patch.object(organizer, "move_and_record", return_value=False):
                self.assertEqual(organizer.run_organizer(series), 1)
            self.assertTrue(source.exists())

    def test_successful_move_has_before_and_after_journal_phases(self):
        with tempfile.TemporaryDirectory() as td:
            series = Path(td) / "Show"
            series.mkdir()
            (series / "Show.S01E01.mkv").write_bytes(b"episode")

            self.assertEqual(organizer.run_organizer(series), 0)
            journal = series / "Season 01" / organizer.JOURNAL_NAME
            events = [
                json.loads(line)
                for line in journal.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(
                [event["phase"] for event in events],
                ["move-planned", "move-verified", "move-done"],
            )
            self.assertEqual(
                len({event["operation_id"] for event in events}),
                1,
            )

    def test_undo_history_save_failure_rolls_the_file_back(self):
        with tempfile.TemporaryDirectory() as td:
            series = Path(td) / "Show"
            season = series / "Season 01"
            season.mkdir(parents=True)
            current = season / "Show - S01E01.mkv"
            current.write_bytes(b"episode")
            original = series / "downloaded.mkv"
            history = season / organizer.HISTORY_NAME
            history.write_text(
                json.dumps(
                    [{
                        "timestamp": organizer.now_iso(),
                        "original_full_path": str(original.resolve()),
                        "new_full_path": str(current.resolve()),
                        "original_filename": original.name,
                        "new_filename": current.name,
                        "file_size": current.stat().st_size,
                        "file_type": "video",
                        "status": "done",
                        "batch_id": "test-batch",
                    }]
                ),
                encoding="utf-8",
            )

            with patch.object(organizer, "save_history", return_value=False):
                restored, skipped = organizer.undo_records([history])
            self.assertEqual((restored, skipped), (0, 1))
            self.assertTrue(current.exists())
            self.assertFalse(original.exists())
            record = json.loads(history.read_text(encoding="utf-8"))[0]
            self.assertEqual(record["status"], "done")
            phases = [
                json.loads(line)["phase"]
                for line in season.joinpath(organizer.JOURNAL_NAME)
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual(
                phases,
                [
                    "undo-planned",
                    "undo-history-save-failed",
                    "undo-rolled-back",
                ],
            )

    def test_dry_run_reserves_duplicate_destinations_like_a_real_run(self):
        with tempfile.TemporaryDirectory() as td:
            series = Path(td) / "Show"
            series.mkdir()
            (series / "A.S01E01.mkv").write_bytes(b"first")
            (series / "B.S01E01.mkv").write_bytes(b"second")

            with self.assertLogs(organizer.LOG, level="INFO") as captured:
                self.assertEqual(
                    organizer.run_organizer(series, dry_run=True),
                    0,
                )
            output = "\n".join(captured.output)
            self.assertIn(
                str(series / "Season 01" / "Show - S01E01.mkv"),
                output,
            )
            self.assertIn(
                str(series / "_Conflicts" / "B.S01E01.mkv"),
                output,
            )
            self.assertFalse((series / "Season 01").exists())
            self.assertFalse((series / "_Conflicts").exists())

    def test_dry_run_reports_insufficient_destination_space(self):
        with tempfile.TemporaryDirectory() as td:
            series = Path(td) / "Show"
            series.mkdir()
            source = series / "Show.S01E01.mkv"
            source.write_bytes(b"episode")

            with (
                patch.object(
                    organizer,
                    "_copy_space_required",
                    return_value=source.stat().st_size,
                ),
                patch.object(
                    organizer,
                    "_destination_free_space",
                    return_value=0,
                ),
                self.assertLogs(organizer.LOG, level="ERROR") as captured,
            ):
                self.assertEqual(
                    organizer.run_organizer(series, dry_run=True),
                    1,
                )
            self.assertIn("NOT ENOUGH SPACE", "\n".join(captured.output))
            self.assertTrue(source.exists())
            self.assertFalse((series / organizer.HISTORY_NAME).exists())


class ManualRecoveryTests(unittest.TestCase):
    def test_recovers_completed_move_missing_rename_history(self):
        with tempfile.TemporaryDirectory() as td:
            series = Path(td) / "Show"
            season = series / "Season 01"
            season.mkdir(parents=True)
            original = series / "downloaded.mkv"
            destination = season / "Show - S01E01.mkv"
            destination.write_bytes(b"episode")
            record = organizer.HistoryRecord(
                timestamp=organizer.now_iso(),
                original_full_path=str(original.resolve()),
                new_full_path=str(destination.resolve()),
                original_filename=original.name,
                new_filename=destination.name,
                file_size=destination.stat().st_size,
                file_type="video",
                status="done",
                batch_id="recovery-batch",
                operation="organize",
                operation_id="incomplete-operation",
            )
            self.assertTrue(
                organizer.append_journal(
                    season,
                    "move-planned",
                    organizer.asdict(record),
                )
            )

            self.assertEqual(organizer.recover_folder(series), 0)
            history = json.loads(
                season.joinpath(organizer.HISTORY_NAME).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(len(history), 1)
            self.assertEqual(
                history[0]["operation_id"],
                "incomplete-operation",
            )
            phases = [
                json.loads(line)["phase"]
                for line in season.joinpath(organizer.JOURNAL_NAME)
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual(
                phases,
                ["move-planned", "move-recovered-done"],
            )

    def test_recovers_undo_that_moved_before_history_was_updated(self):
        with tempfile.TemporaryDirectory() as td:
            series = Path(td) / "Show"
            season = series / "Season 01"
            season.mkdir(parents=True)
            original = series / "downloaded.mkv"
            current = season / "Show - S01E01.mkv"
            original.write_bytes(b"episode")
            history = season / organizer.HISTORY_NAME
            history.write_text(
                json.dumps(
                    [{
                        "timestamp": organizer.now_iso(),
                        "original_full_path": str(original.resolve()),
                        "new_full_path": str(current.resolve()),
                        "original_filename": original.name,
                        "new_filename": current.name,
                        "file_size": original.stat().st_size,
                        "file_type": "video",
                        "status": "done",
                        "batch_id": "undo-recovery-batch",
                        "operation": "organize",
                        "operation_id": "original-move",
                    }]
                ),
                encoding="utf-8",
            )
            details = {
                "operation_id": "incomplete-undo",
                "related_operation_id": "original-move",
                "history_path": str(history.resolve()),
                "history_index": 0,
                "batch_id": "undo-recovery-batch",
                "original_full_path": str(original.resolve()),
                "new_full_path": str(current.resolve()),
                "file_size": original.stat().st_size,
                "action": "undo",
            }
            self.assertTrue(
                organizer.append_journal(season, "undo-planned", details)
            )

            self.assertEqual(organizer.recover_folder(series), 0)
            record = json.loads(history.read_text(encoding="utf-8"))[0]
            self.assertEqual(record["status"], "undone")
            self.assertEqual(record["previous_status"], "done")
            self.assertTrue(original.exists())
            self.assertFalse(current.exists())

    def test_recovery_does_not_scan_sibling_series(self):
        with tempfile.TemporaryDirectory() as td:
            library = Path(td) / "Library"
            selected = library / "Selected"
            sibling = library / "Sibling"
            selected.mkdir(parents=True)
            sibling.mkdir()
            sibling_source = sibling / "source.mkv"
            sibling_source.write_bytes(b"episode")
            details = {
                "timestamp": organizer.now_iso(),
                "operation_id": "sibling-operation",
                "original_full_path": str(sibling_source.resolve()),
                "new_full_path": str(
                    (sibling / "Season 01" / "Sibling - S01E01.mkv").resolve()
                ),
                "original_filename": sibling_source.name,
                "new_filename": "Sibling - S01E01.mkv",
                "file_size": sibling_source.stat().st_size,
                "file_type": "video",
                "status": "done",
                "batch_id": "sibling-batch",
                "operation": "organize",
            }
            self.assertTrue(
                organizer.append_journal(sibling, "move-planned", details)
            )

            self.assertEqual(organizer.recover_folder(selected), 0)
            sibling_events = sibling.joinpath(
                organizer.JOURNAL_NAME
            ).read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(sibling_events), 1)
            self.assertFalse(
                sibling.joinpath(organizer.HISTORY_NAME).exists()
            )


class MetadataRenameTests(unittest.TestCase):
    def test_manual_metadata_fix_renames_only_episode_sidecars(self):
        with tempfile.TemporaryDirectory() as td:
            series = Path(td) / "Correct Show (2026) [imdbid-tt123]"
            season = series / "Season 01"
            season.mkdir(parents=True)
            video = season / "Correct Show - S01E01.mkv"
            old_nfo = season / "Old Show - S01E01.nfo"
            old_image = season / "Old Show - S01E01.png"
            poster = series / "poster.jpg"
            season_nfo = season / "season.nfo"
            video.write_bytes(b"episode")
            old_nfo.write_text("<episodedetails />", encoding="utf-8")
            old_image.write_bytes(b"image")
            poster.write_bytes(b"poster")
            season_nfo.write_text("<season />", encoding="utf-8")

            self.assertEqual(
                organizer.fix_episode_metadata(series),
                0,
            )
            self.assertTrue(video.exists())
            self.assertTrue(
                season.joinpath("Correct Show - S01E01.nfo").exists()
            )
            self.assertTrue(
                season.joinpath("Correct Show - S01E01-thumb.png").exists()
            )
            self.assertTrue(poster.exists())
            self.assertTrue(season_nfo.exists())
            history = json.loads(
                season.joinpath(organizer.HISTORY_NAME).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                {record["file_type"] for record in history},
                {"metadata", "artwork"},
            )
            self.assertTrue(
                all(record["operation"] == "fix-metadata" for record in history)
            )

    def test_metadata_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            series = Path(td) / "Show"
            season = series / "Season 01"
            season.mkdir(parents=True)
            video = season / "Show - S01E01.mkv"
            old_nfo = season / "Old - S01E01.nfo"
            video.write_bytes(b"episode")
            old_nfo.write_text("<episodedetails />", encoding="utf-8")

            self.assertEqual(
                organizer.fix_episode_metadata(series, dry_run=True),
                0,
            )
            self.assertTrue(old_nfo.exists())
            self.assertFalse(season.joinpath("Show - S01E01.nfo").exists())
            self.assertFalse(
                season.joinpath(organizer.HISTORY_NAME).exists()
            )


if __name__ == "__main__":
    unittest.main()
