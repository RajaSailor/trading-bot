from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Dict


@dataclass
class SignalStamp:
    reference_timestamp: str
    breakout_timestamp: str
    seen_at: datetime


class LiveSignalDetector:
    """Suppress historical and duplicate signals; emit only fresh live breakouts."""

    def __init__(self, freshness_minutes: int = 20, cache_size: int = 5000) -> None:
        self.freshness = timedelta(minutes=freshness_minutes)
        self.cache_size = cache_size
        self._signals: Dict[str, SignalStamp] = {}
        self._order: list[str] = []

    def should_emit(
        self,
        key: str,
        reference_timestamp: str,
        breakout_timestamp: str,
        now: datetime | None = None,
    ) -> bool:
        now = now or datetime.now(UTC)
        existing = self._signals.get(key)
        if existing and existing.breakout_timestamp == breakout_timestamp:
            return False

        if not self._is_fresh_timestamp(breakout_timestamp, now):
            return False

        self._signals[key] = SignalStamp(
            reference_timestamp=reference_timestamp,
            breakout_timestamp=breakout_timestamp,
            seen_at=now,
        )
        self._order.append(key)
        self._evict_if_needed()
        return True

    def _evict_if_needed(self) -> None:
        while len(self._order) > self.cache_size:
            expired_key = self._order.pop(0)
            self._signals.pop(expired_key, None)

    def _is_fresh_timestamp(self, timestamp: str | None, now: datetime) -> bool:
        if not timestamp:
            return True

        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%H:%M", "%H:%M:%S"):
            try:
                parsed = datetime.strptime(timestamp, fmt)
                if fmt.startswith("%H"):
                    return True
                return abs(now - parsed) <= self.freshness
            except ValueError:
                continue

        return True
