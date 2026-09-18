from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta
from typing import Callable, Optional

import requests

from timezone_utils import ensure_timezone, get_timezone, now_local_iso


logger = logging.getLogger(__name__)


class TokenManagerAuto:
    """Refreshes Dhan access tokens in runtime memory using documented Dhan endpoints."""

    RENEW_ENDPOINT = "https://api.dhan.co/v2/RenewToken"

    def __init__(
        self,
        session: Optional[requests.sessions.Session] = None,
        now_fn: Optional[Callable[[], datetime]] = None,
        on_token_update: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.session = session or requests.Session()
        self.now_fn = now_fn or (lambda: ensure_timezone())
        self.on_token_update = on_token_update
        self.last_refresh_at: str | None = None
        self.last_error: str | None = None
        self.last_attempt_at: str | None = None
        self.last_expiry_at: str | None = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()

    def refresh_if_due(self, now: datetime | None = None) -> bool:
        current_time = ensure_timezone(now or self.now_fn())
        expiry_time = self._parse_expiry(os.getenv("ACCESS_TOKEN_EXPIRES_AT"))
        if expiry_time is None:
            if current_time.hour < 8:
                return False
            if self.last_refresh_at and self.last_refresh_at[:10] == current_time.date().isoformat():
                return False
        elif expiry_time - current_time > timedelta(minutes=15):
            return False
        return bool(self.refresh_now())

    def refresh_now(self) -> str:
        with self._lock:
            self.last_attempt_at = now_local_iso()
            token = os.getenv("ACCESS_TOKEN", "")
            client_id = os.getenv("DHAN_CLIENT_ID", "")
            if not token or not client_id:
                self.last_error = "missing_credentials"
                logger.info("Dhan token renewal skipped: missing ACCESS_TOKEN or DHAN_CLIENT_ID")
                return ""

            response = self.session.get(
                self.RENEW_ENDPOINT,
                headers={
                    "access-token": token,
                    "dhanClientId": client_id,
                    "Accept": "application/json",
                },
                timeout=10,
            )

            if response.status_code == 200:
                payload = response.json()
                refreshed_token = payload.get("accessToken", "")
                if not refreshed_token:
                    self.last_error = "missing_access_token"
                    logger.warning("Dhan token renewal failed: response missing accessToken")
                    return ""
                self._store_runtime_token(refreshed_token, payload.get("expiryTime"))
                self.last_refresh_at = now_local_iso()
                self.last_error = None
                logger.info("Dhan token renewed in runtime memory")
                return refreshed_token

            self.last_error = self._error_code_for_status(response.status_code)
            logger.warning("Dhan token renewal failed: %s", self.last_error)
            return ""

    def status(self) -> dict:
        return {
            "enabled": bool(os.getenv("ACCESS_TOKEN") and os.getenv("DHAN_CLIENT_ID")),
            "running": bool(self._thread and self._thread.is_alive()),
            "storage": "runtime_only",
            "last_attempt_at": self.last_attempt_at,
            "last_refresh_at": self.last_refresh_at,
            "last_expiry_at": self.last_expiry_at,
            "last_error": self.last_error,
        }

    def run_forever(self, poll_seconds: int = 60) -> None:
        while not self._stop_event.is_set():
            try:
                self.refresh_if_due()
            except Exception as exc:
                self.last_error = exc.__class__.__name__
                logger.error("Token refresh cycle failed: %s", exc.__class__.__name__)
            self._stop_event.wait(max(10, poll_seconds))

    def start(self, poll_seconds: int = 60) -> bool:
        if self._thread and self._thread.is_alive():
            return False
        if not os.getenv("ACCESS_TOKEN") or not os.getenv("DHAN_CLIENT_ID"):
            logger.info("Dhan token renewal disabled: ACCESS_TOKEN or DHAN_CLIENT_ID missing")
            return False
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self.run_forever,
            kwargs={"poll_seconds": poll_seconds},
            daemon=True,
            name="dhan-token-renewal",
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _store_runtime_token(self, token: str, expiry_time: str | None) -> None:
        os.environ["ACCESS_TOKEN"] = token
        if expiry_time:
            os.environ["ACCESS_TOKEN_EXPIRES_AT"] = expiry_time
            self.last_expiry_at = expiry_time
        if self.on_token_update:
            self.on_token_update(token)

    @staticmethod
    def _parse_expiry(raw_value: str | None) -> datetime | None:
        if not raw_value:
            return None
        for parser in (datetime.fromisoformat,):
            try:
                return ensure_timezone(parser(raw_value.replace("Z", "+00:00")))
            except Exception:
                continue
        return None

    @staticmethod
    def _error_code_for_status(status_code: int) -> str:
        if status_code == 401:
            return "expired_or_invalid_token"
        if status_code == 429:
            return "rate_limited"
        if status_code >= 500:
            return "upstream_error"
        return f"http_{status_code}"
