from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

import aiohttp

from .config import Config
from .state_store import StateStore

LOG = logging.getLogger(__name__)
ScanUpdate = Callable[[dict], Awaitable[None]]


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

    def _request_timeout(self) -> aiohttp.ClientTimeout:
        return aiohttp.ClientTimeout(
            total=self.config.jellyfin_request_timeout_seconds
        )

    async def _post_library_scan(self) -> str:
        timeout = self._request_timeout()
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

    async def library_scan_status(self) -> dict:
        """Return Jellyfin's Scan Media Library scheduled-task state."""
        if not self.configured:
            raise ValueError(
                "Jellyfin is not configured. Set jellyfin_server_url and "
                "jellyfin_api_key in config.json."
            )
        url = f"{self.config.jellyfin_server_url}/ScheduledTasks"
        async with self.session.get(
            url,
            headers=self._headers(),
            params={"IsEnabled": "true"},
            timeout=self._request_timeout(),
        ) as response:
            body = await response.text()
            if response.status != 200:
                raise RuntimeError(
                    f"Jellyfin HTTP {response.status}: {body[:300]}"
                )
            try:
                tasks = await response.json(content_type=None)
            except Exception as exc:
                raise RuntimeError(
                    "Jellyfin scheduled-task response is not valid."
                ) from exc
        if not isinstance(tasks, list):
            raise RuntimeError("Jellyfin scheduled-task response is not a list.")
        for task in tasks:
            if not isinstance(task, dict):
                continue
            key = str(task.get("Key", "")).casefold()
            name = str(task.get("Name", "")).casefold()
            if key == "refreshlibrary" or name == "scan media library":
                return task
        raise RuntimeError(
            "Jellyfin did not report its Scan Media Library scheduled task."
        )

    @staticmethod
    def _execution_signature(task: dict | None) -> tuple[str, str, str]:
        result = (task or {}).get("LastExecutionResult") or {}
        if not isinstance(result, dict):
            return "", "", ""
        return (
            str(result.get("StartTimeUtc", "")),
            str(result.get("EndTimeUtc", "")),
            str(result.get("Status", "")),
        )

    @staticmethod
    def _execution_started_near_request(task: dict, requested_at: str) -> bool:
        signature = JellyfinBridge._execution_signature(task)
        try:
            started = datetime.fromisoformat(
                signature[0].replace("Z", "+00:00")
            )
            requested = datetime.fromisoformat(
                requested_at.replace("Z", "+00:00")
            )
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            if requested.tzinfo is None:
                requested = requested.replace(tzinfo=timezone.utc)
            return started >= requested - timedelta(seconds=5)
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _scan_result(task: dict, requested_at: str) -> dict:
        execution = task.get("LastExecutionResult") or {}
        if not isinstance(execution, dict):
            execution = {}
        return {
            "requested_at": requested_at,
            "state": str(task.get("State", "unknown")),
            "progress": task.get("CurrentProgressPercentage"),
            "status": str(execution.get("Status", "unknown")),
            "started_at": str(execution.get("StartTimeUtc", "")),
            "completed_at": str(execution.get("EndTimeUtc", "")),
        }

    async def _wait_for_library_scan(
        self,
        requested_at: str,
        baseline_task: dict | None,
        on_update: ScanUpdate | None,
        poll_interval_seconds: float,
        timeout_seconds: float,
    ) -> dict:
        baseline_signature = self._execution_signature(baseline_task)
        observed_running = str(
            (baseline_task or {}).get("State", "")
        ).casefold() == "running"
        reported_running = False
        last_progress_bucket = -1
        deadline = time.monotonic() + timeout_seconds
        consecutive_errors = 0

        while time.monotonic() < deadline:
            try:
                task = await self.library_scan_status()
                consecutive_errors = 0
            except Exception:
                consecutive_errors += 1
                if consecutive_errors >= 3:
                    raise
                if poll_interval_seconds > 0:
                    await asyncio.sleep(poll_interval_seconds)
                continue

            state = str(task.get("State", "")).casefold()
            progress_value = task.get("CurrentProgressPercentage")
            try:
                progress = max(0.0, min(100.0, float(progress_value)))
            except (TypeError, ValueError):
                progress = None

            if state == "running":
                observed_running = True
                first_running_update = not reported_running
                if first_running_update and on_update:
                    await on_update(
                        {
                            "phase": "running",
                            "progress": progress,
                        }
                    )
                reported_running = True
                if progress is not None:
                    bucket = int(progress // 25) * 25
                    if bucket > last_progress_bucket:
                        last_progress_bucket = bucket
                        self.store.set_setting(
                            "latest_jellyfin_scan_progress",
                            f"{progress:.0f}%",
                        )
                        if bucket > 0 and not first_running_update and on_update:
                            await on_update(
                                {
                                    "phase": "progress",
                                    "progress": progress,
                                }
                            )

            signature = self._execution_signature(task)
            new_execution_finished = (
                (
                    signature != baseline_signature
                    if baseline_task is not None
                    else self._execution_started_near_request(
                        task, requested_at
                    )
                )
                and bool(signature[1])
                and state == "idle"
            )
            if (observed_running and state == "idle") or new_execution_finished:
                result = self._scan_result(task, requested_at)
                status = result["status"].casefold()
                self.store.set_setting(
                    "latest_jellyfin_scan_result",
                    status or "unknown",
                )
                self.store.set_setting(
                    "latest_jellyfin_scan_completed",
                    result["completed_at"] or datetime.now(timezone.utc).isoformat(),
                )
                self.store.set_setting(
                    "latest_jellyfin_scan_progress",
                    "100%" if status == "completed" else "unknown",
                )
                return result

            if poll_interval_seconds > 0:
                await asyncio.sleep(poll_interval_seconds)

        self.store.set_setting("latest_jellyfin_scan_result", "monitor-timeout")
        raise TimeoutError(
            "Jellyfin did not report scan completion before the monitoring "
            f"timeout ({timeout_seconds:.0f} seconds)."
        )

    async def scan_library(self) -> str:
        """Request a scan without waiting; retained for direct integrations."""
        if not self.configured:
            raise ValueError(
                "Jellyfin is not configured. Set jellyfin_server_url and "
                "jellyfin_api_key in config.json."
            )
        if self.active:
            raise RuntimeError("Another Jellyfin request is already running.")
        self.active = True
        try:
            return await self._post_library_scan()
        except Exception as exc:
            self.store.set_setting("latest_jellyfin_scan_result", f"failed: {exc}")
            raise
        finally:
            self.active = False

    async def scan_library_and_wait(
        self,
        on_update: ScanUpdate | None = None,
        *,
        poll_interval_seconds: float | None = None,
        timeout_seconds: float | None = None,
    ) -> dict:
        """Request a scan and monitor only that requested/active scan to completion."""
        if not self.configured:
            raise ValueError(
                "Jellyfin is not configured. Set jellyfin_server_url and "
                "jellyfin_api_key in config.json."
            )
        if self.active:
            raise RuntimeError("A Jellyfin scan is already being monitored.")
        self.active = True
        try:
            try:
                baseline_task = await self.library_scan_status()
            except Exception as exc:
                LOG.warning("Could not capture Jellyfin scan baseline: %s", exc)
                baseline_task = None

            already_running = (
                str((baseline_task or {}).get("State", "")).casefold()
                == "running"
            )
            if already_running:
                requested_at = datetime.now(timezone.utc).isoformat()
                self.store.set_setting(
                    "latest_jellyfin_scan_request", requested_at
                )
                self.store.set_setting(
                    "latest_jellyfin_scan_result", "monitoring-existing"
                )
                if on_update:
                    await on_update(
                        {
                            "phase": "already-running",
                            "requested_at": requested_at,
                        }
                    )
            else:
                requested_at = await self._post_library_scan()
                if on_update:
                    await on_update(
                        {
                            "phase": "accepted",
                            "requested_at": requested_at,
                        }
                    )

            return await self._wait_for_library_scan(
                requested_at,
                baseline_task,
                on_update,
                (
                    self.config.jellyfin_scan_poll_interval_seconds
                    if poll_interval_seconds is None
                    else max(0.0, poll_interval_seconds)
                ),
                (
                    self.config.jellyfin_scan_monitor_timeout_seconds
                    if timeout_seconds is None
                    else max(0.1, timeout_seconds)
                ),
            )
        except TimeoutError:
            raise
        except Exception as exc:
            current = self.store.get_setting(
                "latest_jellyfin_scan_result", ""
            )
            label = (
                "monitor-error"
                if current in {"accepted", "monitoring-existing"}
                else "failed"
            )
            self.store.set_setting(
                "latest_jellyfin_scan_result", f"{label}: {exc}"
            )
            raise
        finally:
            self.active = False

    async def server_status(self) -> dict:
        if not self.configured:
            raise ValueError(
                "Jellyfin is not configured. Set jellyfin_server_url and "
                "jellyfin_api_key in config.json."
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
                raise RuntimeError("Jellyfin status response is not valid.") from exc

    def last_scan_summary(self) -> str:
        requested = self.store.get_setting(
            "latest_jellyfin_scan_request", "not recorded yet"
        )
        result = self.store.get_setting(
            "latest_jellyfin_scan_result", "not recorded yet"
        )
        completed = self.store.get_setting(
            "latest_jellyfin_scan_completed", "not recorded yet"
        )
        progress = self.store.get_setting(
            "latest_jellyfin_scan_progress", "not recorded yet"
        )
        return (
            f"Latest scan request: {requested}\n"
            f"Scan result: {result}\n"
            f"Progress: {progress}\n"
            f"Completed at: {completed}"
        )
