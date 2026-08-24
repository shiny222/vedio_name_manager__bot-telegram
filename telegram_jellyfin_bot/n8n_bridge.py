"""Small, optional client for the passive n8n media-identification webhook."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any
import uuid

import aiohttp

from .config import Config


@dataclass(frozen=True)
class MediaIdentification:
    title_query: str | None
    season: int | None
    episode: int | None
    year: int | None
    confidence: float
    needs_user_input: bool
    question: str | None


class N8nMediaIdentifier:
    """Call n8n without granting it access to Telegram, storage, or Jellyfin."""

    def __init__(self, config: Config, session: aiohttp.ClientSession):
        self.config = config
        self.session = session

    @property
    def configured(self) -> bool:
        return self.config.n8n_agent_enabled and bool(self.config.n8n_agent_url)

    @staticmethod
    def _optional_positive_integer(value: Any) -> int | None:
        if value is None or value == "":
            return None
        if isinstance(value, bool):
            return None
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
        return number if number > 0 else None

    @staticmethod
    def _response_object(value: Any) -> dict[str, Any]:
        """Normalize harmless n8n response wrappers without weakening checks.

        Depending on the Respond to Webhook node/version, one item can arrive
        as an object, a one-item array, or a JSON-encoded object string. Only
        those single-result forms are accepted. The trusted request fields are
        still verified by ``identify`` after normalization.
        """
        for _ in range(3):
            if isinstance(value, dict):
                return value
            if isinstance(value, list):
                if len(value) != 1:
                    raise RuntimeError(
                        "n8n webhook returned multiple identification results."
                    )
                value = value[0]
                continue
            if isinstance(value, str):
                if len(value) > 100_000:
                    raise RuntimeError("n8n webhook response is unexpectedly large.")
                try:
                    value = json.loads(value)
                except json.JSONDecodeError as exc:
                    raise RuntimeError(
                        "n8n webhook returned a JSON string that does not contain "
                        "an identification object."
                    ) from exc
                continue
            break
        raise RuntimeError(
            "n8n webhook response must contain one JSON identification object."
        )

    async def identify(
        self,
        *,
        chat_id: int,
        media_kind: str,
        library_key: str,
        filename: str,
        caption: str = "",
    ) -> MediaIdentification:
        if not self.configured:
            raise RuntimeError("AI filename identification is not configured.")
        if media_kind not in {"series", "movie"}:
            raise ValueError("media_kind must be series or movie.")

        request_id = str(uuid.uuid4())
        # The first published workflow used singular movie keys while the bot's
        # four-library config uses plural keys. Keep that deployed workflow
        # callable; the mapping is fixed and never lets AI select a destination.
        webhook_library_key = {
            "animation_movies": "animation_movie",
            "video_movies": "video_movie",
            "anime_movies": "anime_movie",
        }.get(library_key, library_key)
        payload = {
            "request_id": request_id,
            "chat_id": str(int(chat_id)),
            "media_kind": media_kind,
            "library_key": webhook_library_key,
            "filename": filename,
            "caption": caption,
        }
        headers = {"Content-Type": "application/json"}
        if self.config.n8n_agent_secret:
            headers["X-Video-Manager-Secret"] = self.config.n8n_agent_secret

        timeout = aiohttp.ClientTimeout(
            total=self.config.n8n_agent_timeout_seconds
        )
        try:
            async with self.session.post(
                self.config.n8n_agent_url,
                json=payload,
                headers=headers,
                timeout=timeout,
                allow_redirects=False,
            ) as response:
                body = await response.text()
                if not 200 <= response.status < 300:
                    raise RuntimeError(
                        f"n8n webhook returned HTTP {response.status}: {body[:300]}"
                    )
                try:
                    result = await response.json(content_type=None)
                except (ValueError, TypeError) as exc:
                    raise RuntimeError(
                        "n8n webhook did not return valid JSON. Check that the URL "
                        "comes from the Webhook node, not the workflow editor page."
                    ) from exc
        except TimeoutError as exc:
            raise RuntimeError("n8n filename identification timed out.") from exc
        except aiohttp.ClientError as exc:
            raise RuntimeError(f"Could not reach the n8n webhook: {exc}") from exc

        result = self._response_object(result)
        if result.get("request_id") != request_id:
            raise RuntimeError("n8n webhook returned the wrong request_id.")
        if result.get("media_kind") != media_kind:
            raise RuntimeError("n8n webhook changed the trusted media kind.")
        if result.get("library_key") != webhook_library_key:
            raise RuntimeError("n8n webhook changed the trusted library key.")
        if result.get("ok") is not True:
            detail = str(result.get("error") or "AI identification failed.")
            raise RuntimeError(detail[:500])

        title = str(result.get("title_query") or "").strip()[:300] or None
        question = str(result.get("question") or "").strip()[:500] or None
        try:
            confidence = float(result.get("confidence", 0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))

        year = self._optional_positive_integer(result.get("year"))
        if year is not None and not 1870 <= year <= 2100:
            year = None
        return MediaIdentification(
            title_query=title,
            season=(
                self._optional_positive_integer(result.get("season"))
                if media_kind == "series"
                else None
            ),
            episode=(
                self._optional_positive_integer(result.get("episode"))
                if media_kind == "series"
                else None
            ),
            year=year,
            confidence=confidence,
            needs_user_input=bool(result.get("needs_user_input")) or not title,
            question=question,
        )
