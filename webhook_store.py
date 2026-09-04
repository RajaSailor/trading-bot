from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional


class WebhookStore:
    def __init__(
        self,
        history_limit: int = 50,
        duplicate_window_seconds: int = 60,
        retention_days: int = 7,
    ) -> None:
        self.history_limit = history_limit
        self.duplicate_window_seconds = duplicate_window_seconds
        self.retention_days = retention_days
        self._signals: List[dict] = []
        self._lock = threading.Lock()

    def store_webhook_signal(self, signal: dict) -> dict:
        with self._lock:
            self.cleanup_old_signals(self.retention_days)
            duplicate = self.check_duplicate_signal(
                signal["ticker"],
                signal["entry_price"],
                signal["signal_type"],
                self.duplicate_window_seconds,
            )
            if duplicate:
                return duplicate

            stored = {
                **signal,
                "signal_id": signal.get("signal_id") or f"tv-{int(time.time() * 1000)}",
                "stored_at": signal.get("stored_at") or datetime.now(timezone.utc).isoformat(),
            }
            self._signals.append(stored)
            self._signals = self._signals[-max(self.history_limit, 200):]
            return stored

    def get_webhook_history(self, limit: int = 50) -> List[dict]:
        with self._lock:
            self.cleanup_old_signals(self.retention_days)
            return list(reversed(self._signals[-limit:]))

    def check_duplicate_signal(
        self,
        ticker: str,
        entry_price: float,
        signal_type: str,
        time_window: int = 60,
    ) -> Optional[dict]:
        now = time.time()
        entry_value = round(float(entry_price), 2)
        signal_name = signal_type.upper()

        for signal in reversed(self._signals):
            if signal.get("ticker") != ticker:
                continue
            if round(float(signal.get("entry_price", 0.0)), 2) != entry_value:
                continue
            if signal.get("signal_type") != signal_name:
                continue
            created_at = signal.get("_created_epoch")
            if created_at is None:
                continue
            if now - float(created_at) <= time_window:
                return signal
        return None

    def cleanup_old_signals(self, days: int = 7) -> None:
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
        self._signals = [
            signal
            for signal in self._signals
            if self._parse_datetime(signal.get("stored_at")) >= cutoff
        ]

    @staticmethod
    def _parse_datetime(value: Optional[str]) -> datetime:
        if not value:
            return datetime.now(timezone.utc).replace(tzinfo=None)
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return datetime.now(timezone.utc).replace(tzinfo=None)


webhook_store = WebhookStore()
