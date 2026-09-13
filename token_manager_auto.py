from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests


logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


class TokenManagerAuto:
    """Auto-refresh Dhan token daily at 08:00 IST and push to Render env."""

    def __init__(self, token_generator=None, now_fn=None) -> None:
        self.token_generator = token_generator or self._generate_token
        self.now_fn = now_fn or (lambda: datetime.now(IST))
        self.last_refresh_date: str | None = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def refresh_if_due(self, now: datetime | None = None) -> bool:
        now = now or self.now_fn()
        refresh_date = now.strftime("%Y-%m-%d")
        if now.hour < 8 or self.last_refresh_date == refresh_date:
            return False

        token = self._refresh_and_persist_token()
        if not token:
            return False

        self.last_refresh_date = refresh_date
        logger.info("✅ ACCESS_TOKEN refreshed for %s", refresh_date)
        return True

    def refresh_now(self) -> str:
        return self._refresh_and_persist_token()

    def _refresh_and_persist_token(self) -> str:
        token = self.token_generator()
        if not token:
            return ""

        os.environ["ACCESS_TOKEN"] = token
        self._update_render_env("ACCESS_TOKEN", token)
        if os.getenv("RENDER_TRIGGER_REDEPLOY", "false").lower() == "true":
            self._trigger_render_redeploy()
        return token

    def run_forever(self, poll_seconds: int = 30) -> None:
        while not self._stop_event.is_set():
            try:
                self.refresh_if_due()
            except Exception as exc:
                logger.error("Token refresh cycle failed: %s", exc, exc_info=True)
            time.sleep(max(5, poll_seconds))

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self.run_forever, daemon=True, name="token-refresh")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)

    @staticmethod
    def _generate_token() -> str:
        # Production hook: replace with real Dhan auth flow using API_KEY/DHAN_PIN/DHAN_TOTP_SECRET.
        return os.getenv("ACCESS_TOKEN", "")

    def _update_render_env(self, key: str, value: str) -> None:
        api_key = os.getenv("RENDER_API_KEY")
        service_id = os.getenv("RENDER_SERVICE_ID")
        if not api_key or not service_id:
            logger.info("Render env update skipped (RENDER_API_KEY/RENDER_SERVICE_ID missing)")
            return

        url = f"https://api.render.com/v1/services/{service_id}/env-vars"
        headers = {"Authorization": "Bearer " + api_key, "Content-Type": "application/json"}
        payload = [{"key": key, "value": value}]
        response = requests.put(url, headers=headers, json=payload, timeout=15)
        if response.status_code not in {200, 201}:
            logger.warning("Render env update failed: %s %s", response.status_code, response.text)

    def _trigger_render_redeploy(self) -> None:
        api_key = os.getenv("RENDER_API_KEY")
        service_id = os.getenv("RENDER_SERVICE_ID")
        if not api_key or not service_id:
            logger.info("Render deploy hook skipped (RENDER_API_KEY/RENDER_SERVICE_ID missing)")
            return

        url = f"https://api.render.com/v1/services/{service_id}/deploys"
        headers = {"Authorization": "Bearer " + api_key}
        response = requests.post(url, headers=headers, timeout=15)
        if response.status_code not in {200, 201}:
            logger.warning("Render redeploy trigger failed: %s %s", response.status_code, response.text)
