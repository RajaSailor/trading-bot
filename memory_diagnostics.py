"""Lightweight process memory diagnostics (no third-party dependencies)."""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def current_rss_mb() -> Optional[float]:
    """Resident set size of this process in MB (Linux ``/proc``; falls back to peak RSS)."""
    try:
        with open(f"/proc/{os.getpid()}/status", "r", encoding="ascii", errors="ignore") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except OSError:
        pass
    try:
        import resource

        peak_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return peak_kb / 1024.0
    except Exception:  # pragma: no cover - platform specific
        return None


def log_rss(label: str, level: int = logging.INFO) -> Optional[float]:
    rss = current_rss_mb()
    if rss is None:
        logger.log(level, "🧠 Memory [%s]: RSS unavailable", label)
    else:
        logger.log(level, "🧠 Memory [%s]: RSS=%.1f MB", label, rss)
    return rss
