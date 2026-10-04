"""Monotonic-clock latency instrumentation for the live alert path.

Stages recorded per alert (all ``time.monotonic()`` seconds):
  * ``receive``    - live price / candle data received by the scanner
  * ``evaluate``   - breakout rule evaluated (signal decided)
  * ``queue``      - signal accepted into the signal queue
  * ``send_start`` - Telegram HTTP request about to start

The tracker keeps a bounded window of recent samples and periodically logs
p50/p95/p99 (milliseconds) for each stage-to-stage span. These numbers measure
the bot's internal processing only; Telegram/network delivery to the phone is
outside the bot's control and is not included.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Deque, Dict, Iterable, Mapping, Optional

logger = logging.getLogger(__name__)

STAGES = ("receive", "evaluate", "queue", "send_start")
SPANS = (
    ("receive", "evaluate"),
    ("evaluate", "queue"),
    ("queue", "send_start"),
    ("receive", "send_start"),
)


def now_mark() -> float:
    return time.monotonic()


def percentile(values: Iterable[float], pct: float) -> Optional[float]:
    ordered = sorted(values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    rank = (pct / 100.0) * (len(ordered) - 1)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight


class LatencyTracker:
    def __init__(self, window: int = 1000, log_interval_seconds: float = 300.0) -> None:
        self._samples: Dict[str, Deque[float]] = {
            f"{start}->{end}": deque(maxlen=window) for start, end in SPANS
        }
        self._lock = threading.Lock()
        self._log_interval = log_interval_seconds
        self._last_log = time.monotonic()
        self._count = 0

    def record(self, marks: Mapping[str, float], log: bool = True) -> Dict[str, float]:
        """Record stage marks (monotonic seconds); returns span durations in ms."""
        spans: Dict[str, float] = {}
        for start, end in SPANS:
            if start in marks and end in marks:
                try:
                    spans[f"{start}->{end}"] = max(0.0, (float(marks[end]) - float(marks[start])) * 1000.0)
                except (TypeError, ValueError):
                    continue
        if not spans:
            return spans
        with self._lock:
            for name, value in spans.items():
                self._samples[name].append(value)
            self._count += 1
            due = time.monotonic() - self._last_log >= self._log_interval
        total = spans.get("receive->send_start")
        if total is not None:
            logger.info("⏱️ Alert internal latency receive->send_start=%.1f ms", total)
        if log and due:
            self.log_summary()
        return spans

    def summary(self) -> Dict[str, Dict[str, Optional[float]]]:
        with self._lock:
            snapshot = {name: list(values) for name, values in self._samples.items()}
        return {
            name: {
                "count": float(len(values)),
                "p50": percentile(values, 50),
                "p95": percentile(values, 95),
                "p99": percentile(values, 99),
            }
            for name, values in snapshot.items()
        }

    def log_summary(self) -> None:
        with self._lock:
            self._last_log = time.monotonic()
        for name, stats in self.summary().items():
            if not stats["count"]:
                continue
            logger.info(
                "⏱️ Latency %s (n=%d): p50=%.1f ms p95=%.1f ms p99=%.1f ms",
                name,
                int(stats["count"]),
                stats["p50"],
                stats["p95"],
                stats["p99"],
            )


_shared_tracker: Optional[LatencyTracker] = None
_shared_lock = threading.Lock()


def get_latency_tracker() -> LatencyTracker:
    global _shared_tracker
    with _shared_lock:
        if _shared_tracker is None:
            _shared_tracker = LatencyTracker()
        return _shared_tracker
