from __future__ import annotations

import logging
from datetime import datetime, timezone

import aiohttp

from .config import Config
from .state_store import StateStore

LOG = logging.getLogger(__name__)


class JellyfinBridge:
    """Small optional client for trusted Jellyfin administrative actions."""

    def __init__(
        self, config: Config, store: StateStore, session: aiohttp.ClientSession
    ):
        self.config = config
        self.store = store
        self.session = session
        self.active = False

    @property
    def configured(self) -> bool:
        return bool(
            self.config.jellyfin_server_url
            and self.config.jellyfin_api_key
            and not self.config.jellyfin_api_key.startswith("PUT_")
        )

    def _headers(self) -> dict[str, str]:
        # Never log this dictionary: it contains the administrator API key.
        return {"X-Emby-Token": self.config.jellyfin_api_key}

    async def scan_library(self) -> str:
        if not self.configured:
            raise ValueError(
                "Jellyfin تنظیم نشده است. jellyfin_server_url و "
                "jellyfin_api_key را در config.json وارد کنید."
            )
        if self.active:
            raise RuntimeError("یک درخواست Jellyfin در حال اجرا است.")
        self.active = True
        try:
            timeout = aiohttp.ClientTimeout(
                total=self.config.jellyfin_request_timeout_seconds
            )
            url = f"{self.config.jellyfin_server_url}/Library/Refresh"
            async with self.session.post(
                url, headers=self._headers(), timeout=timeout
            ) as response:
                body = await response.text()
                if response.status not in {200, 204}:
                    raise RuntimeError(
                        f"Jellyfin HTTP {response.status}: {body[:300]}"
                    )
            requested_at = datetime.now(timezone.utc).isoformat()
            self.store.set_setting("latest_jellyfin_scan_request", requested_at)
            self.store.set_setting("latest_jellyfin_scan_result", "accepted")
            LOG.info("Jellyfin library scan request accepted at %s", requested_at)
            return requested_at
        except Exception as exc:
            self.store.set_setting("latest_jellyfin_scan_result", f"failed: {exc}")
            raise
        finally:
            self.active = False

    async def server_status(self) -> dict:
        if not self.configured:
            raise ValueError(
                "Jellyfin تنظیم نشده است. jellyfin_server_url و "
                "jellyfin_api_key را در config.json وارد کنید."
            )
        timeout = aiohttp.ClientTimeout(
            total=self.config.jellyfin_request_timeout_seconds
        )
        url = f"{self.config.jellyfin_server_url}/System/Info"
        async with self.session.get(
            url, headers=self._headers(), timeout=timeout
        ) as response:
            body = await response.text()
            if response.status != 200:
                raise RuntimeError(f"Jellyfin HTTP {response.status}: {body[:300]}")
            try:
                return await response.json(content_type=None)
            except Exception as exc:
                raise RuntimeError("پاسخ وضعیت Jellyfin معتبر نیست.") from exc

    def last_scan_summary(self) -> str:
        requested = self.store.get_setting(
            "latest_jellyfin_scan_request", "هنوز ثبت نشده"
        )
        result = self.store.get_setting(
            "latest_jellyfin_scan_result", "هنوز ثبت نشده"
        )
        return f"آخرین درخواست Scan: {requested}\nنتیجه درخواست: {result}"
