from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from market_calendar import MarketCalendar


logger = logging.getLogger(__name__)


class SchedulerManager:
    """Internal scheduler to run bot only in configured market windows."""

    def __init__(self, on_start, on_stop, now_fn=None) -> None:
        self.market_start = os.getenv("MARKET_START_TIME", "09:00")
        self.market_end = os.getenv("MARKET_END_TIME", "23:30")
        self.timezone = ZoneInfo(os.getenv("TIMEZONE", "Asia/Kolkata"))
        self.on_start = on_start
        self.on_stop = on_stop
        self.now_fn = now_fn or (lambda: datetime.now(self.timezone))
        self.is_running = False
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def tick(self, now: datetime | None = None) -> bool:
        now = now or self.now_fn()
        should_run = self._should_run(now)

        if should_run and not self.is_running:
            self.on_start()
            self.is_running = True
            logger.info("✅ Scheduler started bot at %s", now.isoformat())
            return True

        if not should_run and self.is_running:
            self.on_stop()
            self.is_running = False
            logger.info("⏹️ Scheduler stopped bot at %s", now.isoformat())
            return True

        return False

    def run_forever(self, poll_seconds: int = 30) -> None:
        while not self._stop_event.is_set():
            try:
                self.tick()
            except Exception as exc:
                logger.error("Scheduler tick failed: %s", exc, exc_info=True)
            time.sleep(max(5, poll_seconds))

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self.run_forever, daemon=True, name="market-scheduler")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _should_run(self, now: datetime) -> bool:
        if not MarketCalendar.is_trading_day(now.date()):
            return False

        start_hour, start_min = self._split_time(self.market_start)
        end_hour, end_min = self._split_time(self.market_end)
        start_minutes = start_hour * 60 + start_min
        end_minutes = end_hour * 60 + end_min
        now_minutes = now.hour * 60 + now.minute
        return start_minutes <= now_minutes <= end_minutes

    @staticmethod
    def _split_time(value: str) -> tuple[int, int]:
        hh, mm = value.split(":", 1)
        return int(hh), int(mm)
