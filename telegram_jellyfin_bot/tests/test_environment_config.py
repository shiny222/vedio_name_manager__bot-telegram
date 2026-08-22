from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from telegram_jellyfin_bot.config import load_config


class EnvironmentConfigTests(unittest.TestCase):
    def test_loads_docker_environment_without_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            environment = {
                "VIDEO_MANAGER_CONFIG_MODE": "env",
                "BOT_TOKEN": "123456:test-token",
                "JELLYFIN_LIBRARY_PATH": str(root / "series"),
                "JELLYFIN_MOVIE_LIBRARY_PATH": str(root / "movies"),
                "MOVIE_STAGING_PATH": str(root / "staging"),
                "DATA_PATH": str(root / "data"),
                "LOGS_PATH": str(root / "logs"),
                "LOCAL_BOT_API_BASE_URL": "http://telegram-bot-api:8081/bot",
                "LOCAL_BOT_API_BASE_FILE_URL": "http://telegram-bot-api:8081/file/bot",
                "JELLYFIN_SERVER_URL": "http://jellyfin:8096",
                "ALLOWED_CHAT_IDS": "-100123, 987654",
                "ALLOWED_VIDEO_EXTENSIONS": ".mkv,.mp4",
                "CONFIRM_BEFORE_DOWNLOAD": "yes",
                "ASK_BEFORE_OVERWRITE": "false",
            }
            with mock.patch.dict(os.environ, environment, clear=True):
                config = load_config(create_from_example=False)

            self.assertEqual(config.bot_token, "123456:test-token")
            self.assertEqual(config.allowed_chat_ids, {-100123, 987654})
            self.assertEqual(config.allowed_video_extensions, {".mkv", ".mp4"})
            self.assertEqual(config.local_bot_api_base_url, "http://telegram-bot-api:8081/bot")
            self.assertEqual(config.jellyfin_server_url, "http://jellyfin:8096")
            self.assertTrue(config.confirm_before_download)
            self.assertFalse(config.ask_before_overwrite)
            self.assertEqual(config.sorter_command[0], sys.executable)
            self.assertTrue(Path(config.sorter_command[1]).is_file())
            self.assertTrue(config.jellyfin_library_path.is_dir())
            self.assertTrue(config.jellyfin_movie_library_path.is_dir())

    def test_rejects_invalid_environment_boolean(self) -> None:
        environment = {
            "VIDEO_MANAGER_CONFIG_MODE": "env",
            "BOT_TOKEN": "123456:test-token",
            "JELLYFIN_LIBRARY_PATH": tempfile.gettempdir(),
            "CONFIRM_BEFORE_DOWNLOAD": "sometimes",
        }
        with mock.patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(ValueError, "CONFIRM_BEFORE_DOWNLOAD"):
                load_config(create_from_example=False)


if __name__ == "__main__":
    unittest.main()
