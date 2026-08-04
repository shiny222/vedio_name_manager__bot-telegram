import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import imdb_tool


class ImdbToolTests(unittest.TestCase):
    def test_jellyfin_folder(self):
        self.assertEqual(
            imdb_tool.jellyfin_folder("Dr. Stone", 2019, "tt9679542"),
            "Dr. Stone (2019) [imdbid-tt9679542]",
        )

    def test_fuzzy_results_filter_people_and_episodes(self):
        raw = [
            {"id": "nm0001", "l": "Dr Person", "q": "actor"},
            {"id": "tt9679542", "l": "Dr. Stone", "q": "TV series", "y": 2019},
            {"id": "tt9999999", "l": "Dr. Stone Episode", "q": "TV episode", "y": 2020},
        ]
        results = imdb_tool.parse_results("dr ston", raw, 5)
        self.assertEqual([row["imdb_id"] for row in results], ["tt9679542"])

    def test_media_type_filter_separates_movies_and_series(self):
        raw = [
            {"id": "tt0816692", "l": "Interstellar", "q": "feature", "y": 2014},
            {"id": "tt9990001", "l": "Interstellar Files", "q": "TV series", "y": 2022},
        ]
        movies = imdb_tool.parse_results("interstellar", raw, 5, "movie")
        series = imdb_tool.parse_results("interstellar", raw, 5, "series")
        self.assertEqual([row["imdb_id"] for row in movies], ["tt0816692"])
        self.assertEqual([row["imdb_id"] for row in series], ["tt9990001"])


if __name__ == "__main__":
    unittest.main()
