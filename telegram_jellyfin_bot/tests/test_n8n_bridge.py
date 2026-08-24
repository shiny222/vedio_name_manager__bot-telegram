from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace

from telegram_jellyfin_bot.n8n_bridge import N8nMediaIdentifier


class _FakeResponse:
    def __init__(self, payload: object, status: int = 200):
        self.payload = payload
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def text(self) -> str:
        return "response body"

    async def json(self, content_type=None):
        return self.payload


class _FakeSession:
    def __init__(self):
        self.request: dict | None = None

    def post(self, url, **kwargs):
        self.request = {"url": url, **kwargs}
        sent = kwargs["json"]
        return _FakeResponse({
            "ok": True,
            "request_id": sent["request_id"],
            "media_kind": sent["media_kind"],
            "library_key": sent["library_key"],
            "title_query": "The Last Whale Singer",
            "season": None,
            "episode": None,
            "year": 2025,
            "confidence": 0.94,
            "needs_user_input": False,
            "question": None,
        })


class N8nMediaIdentifierTests(unittest.TestCase):
    @staticmethod
    def _config():
        return SimpleNamespace(
            n8n_agent_enabled=True,
            n8n_agent_url="http://n8n:5678/webhook/media-identify",
            n8n_agent_secret="",
            n8n_agent_timeout_seconds=45,
        )

    def test_calls_webhook_with_auth_and_preserves_trusted_fields(self):
        async def exercise():
            config = SimpleNamespace(
                n8n_agent_enabled=True,
                n8n_agent_url="http://n8n:5678/webhook/media-identify",
                n8n_agent_secret="shared-secret",
                n8n_agent_timeout_seconds=45,
            )
            session = _FakeSession()
            client = N8nMediaIdentifier(config, session)
            result = await client.identify(
                chat_id=123,
                media_kind="movie",
                library_key="animation_movies",
                filename="The.Last.Whale.Singer.2025.1080p.mkv",
                caption="",
            )

            self.assertEqual(session.request["url"], config.n8n_agent_url)
            self.assertEqual(
                session.request["headers"]["X-Video-Manager-Secret"],
                "shared-secret",
            )
            # Compatibility with the first imported n8n workflow is explicit;
            # it cannot redirect the local selected library.
            self.assertEqual(
                session.request["json"]["library_key"], "animation_movie"
            )
            self.assertEqual(result.title_query, "The Last Whale Singer")
            self.assertEqual(result.year, 2025)
            self.assertAlmostEqual(result.confidence, 0.94)

        asyncio.run(exercise())

    def test_accepts_single_item_array_returned_by_n8n(self):
        class ArraySession(_FakeSession):
            def post(self, url, **kwargs):
                response = super().post(url, **kwargs)
                response.payload = [response.payload]
                return response

        async def exercise():
            client = N8nMediaIdentifier(self._config(), ArraySession())
            result = await client.identify(
                chat_id=123,
                media_kind="series",
                library_key="animation_series",
                filename="Example.S01E02.mkv",
            )
            self.assertEqual(result.title_query, "The Last Whale Singer")

        asyncio.run(exercise())

    def test_accepts_json_encoded_object_returned_by_n8n(self):
        class EncodedSession(_FakeSession):
            def post(self, url, **kwargs):
                response = super().post(url, **kwargs)
                response.payload = json.dumps(response.payload)
                return response

        async def exercise():
            client = N8nMediaIdentifier(self._config(), EncodedSession())
            result = await client.identify(
                chat_id=123,
                media_kind="movie",
                library_key="video_movies",
                filename="Example.mkv",
            )
            self.assertEqual(result.year, 2025)

        asyncio.run(exercise())

    def test_maps_plural_anime_movie_key_for_workflow_compatibility(self):
        async def exercise():
            session = _FakeSession()
            client = N8nMediaIdentifier(self._config(), session)
            await client.identify(
                chat_id=123,
                media_kind="movie",
                library_key="anime_movies",
                filename="Anime.Movie.2026.mkv",
            )
            self.assertEqual(session.request["json"]["library_key"], "anime_movie")

        asyncio.run(exercise())

    def test_rejects_multiple_identification_results(self):
        class MultipleSession(_FakeSession):
            def post(self, url, **kwargs):
                response = super().post(url, **kwargs)
                response.payload = [response.payload, response.payload]
                return response

        async def exercise():
            client = N8nMediaIdentifier(self._config(), MultipleSession())
            with self.assertRaisesRegex(RuntimeError, "multiple"):
                await client.identify(
                    chat_id=123,
                    media_kind="movie",
                    library_key="video_movies",
                    filename="Example.mkv",
                )

        asyncio.run(exercise())

    def test_rejects_a_response_that_changes_media_kind(self):
        class WrongKindSession(_FakeSession):
            def post(self, url, **kwargs):
                response = super().post(url, **kwargs)
                response.payload["media_kind"] = "series"
                return response

        async def exercise():
            config = SimpleNamespace(
                n8n_agent_enabled=True,
                n8n_agent_url="http://n8n:5678/webhook/media-identify",
                n8n_agent_secret="",
                n8n_agent_timeout_seconds=45,
            )
            client = N8nMediaIdentifier(config, WrongKindSession())
            with self.assertRaisesRegex(RuntimeError, "trusted media kind"):
                await client.identify(
                    chat_id=123,
                    media_kind="movie",
                    library_key="video_movies",
                    filename="Example.mkv",
                )

        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
