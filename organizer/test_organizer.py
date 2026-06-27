from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
