import unittest

import anilist_tool


class AniListToolTests(unittest.TestCase):
    def test_jellyfin_folder(self):
        self.assertEqual(
            anilist_tool.jellyfin_folder(
                "BLEACH: Thousand-Year Blood War", 2022, 116674
            ),
            "BLEACH_ Thousand-Year Blood War (2022)",
        )

    def test_media_type_filter_separates_anime_movies_and_series(self):
        raw = [
            {
                "id": 116674,
                "title": {
                    "english": "BLEACH: Thousand-Year Blood War",
                    "romaji": "BLEACH: Sennen Kessen-hen",
                },
                "format": "TV",
                "seasonYear": 2022,
                "episodes": 13,
            },
            {
                "id": 185874,
                "title": {
                    "english": "BLEACH: Thousand-Year Blood War - The Calamity",
                    "romaji": "BLEACH: Sennen Kessen-hen - Kashin-tan",
                },
                "format": "MOVIE",
                "seasonYear": 2026,
            },
        ]
        series = anilist_tool.parse_results("bleach thousand year", raw, 5, "series")
        movies = anilist_tool.parse_results("bleach calamity", raw, 5, "movie")
        self.assertEqual([row["anilist_id"] for row in series], [116674])
        self.assertEqual([row["anilist_id"] for row in movies], [185874])
        self.assertEqual(series[0]["provider"], "anilist")


if __name__ == "__main__":
    unittest.main()
