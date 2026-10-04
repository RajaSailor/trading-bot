"""Exchange session + holiday calendar used to gate the live option scanner.

Sessions (IST):
  * NSE (NIFTY, BANKNIFTY, NIFTY 50 stocks): 09:15 - 15:30
  * BSE (SENSEX):                            09:15 - 15:30
  * MCX (GOLD, SILVER, CRUDE OIL, NATURALGAS): 09:00 - 23:30, extended to 23:55
    while the US is on standard time (MCX follows US DST for international-linked
    commodities). On "morning closed" MCX holidays only the evening session
    (17:00 - close) trades; on "evening closed" holidays only 09:00 - 17:00.

Holiday lists follow the official NSE / BSE / MCX 2026 circulars. Additional
closures (or a future year) can be supplied at runtime without a code change via
``EXCHANGE_EXTRA_HOLIDAYS`` (comma separated ``EXCHANGE:YYYY-MM-DD``; use ``ALL``
for every exchange), e.g. ``EXCHANGE_EXTRA_HOLIDAYS=ALL:2026-11-09,MCX:2026-12-31``.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, time, timedelta
from typing import Dict, FrozenSet, Iterable, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
_US_EASTERN = ZoneInfo("America/New_York")

NSE = "NSE"
BSE = "BSE"
MCX = "MCX"
EXCHANGES = (NSE, BSE, MCX)

EQUITY_SESSION = (time(9, 15), time(15, 30))
MCX_OPEN = time(9, 0)
MCX_EVENING_OPEN = time(17, 0)
MCX_CLOSE_US_DST = time(23, 30)
MCX_CLOSE_US_STANDARD = time(23, 55)

# NSE equity / equity-derivatives trading holidays 2026 (weekday closures).
NSE_HOLIDAYS_2026: FrozenSet[date] = frozenset(
    {
        date(2026, 1, 26),   # Republic Day
        date(2026, 3, 3),    # Holi
        date(2026, 3, 26),   # Shri Ram Navami
        date(2026, 3, 31),   # Shri Mahavir Jayanti
        date(2026, 4, 3),    # Good Friday
        date(2026, 4, 14),   # Dr. Baba Saheb Ambedkar Jayanti
        date(2026, 5, 1),    # Maharashtra Day
        date(2026, 5, 28),   # Bakri Id
        date(2026, 6, 26),   # Muharram
        date(2026, 9, 14),   # Ganesh Chaturthi
        date(2026, 10, 2),   # Mahatma Gandhi Jayanti
        date(2026, 10, 20),  # Dussehra
        date(2026, 11, 10),  # Diwali - Balipratipada
        date(2026, 11, 24),  # Prakash Gurpurb Sri Guru Nanak Dev
        date(2026, 12, 25),  # Christmas
    }
)
# BSE equity-derivatives holidays match NSE for 2026.
BSE_HOLIDAYS_2026: FrozenSet[date] = NSE_HOLIDAYS_2026

# MCX: both sessions closed.
MCX_FULL_HOLIDAYS_2026: FrozenSet[date] = frozenset(
    {
        date(2026, 1, 26),
        date(2026, 4, 3),
        date(2026, 10, 2),
        date(2026, 12, 25),
    }
)
# MCX: morning session closed, evening session (17:00 onwards) open.
MCX_MORNING_CLOSED_2026: FrozenSet[date] = frozenset(
    {
        date(2026, 3, 3),
        date(2026, 3, 26),
        date(2026, 3, 31),
        date(2026, 4, 14),
        date(2026, 5, 1),
        date(2026, 5, 28),
        date(2026, 6, 26),
        date(2026, 9, 14),
        date(2026, 10, 20),
        date(2026, 11, 10),
        date(2026, 11, 24),
    }
)
# MCX: morning session open, evening session closed.
MCX_EVENING_CLOSED_2026: FrozenSet[date] = frozenset({date(2026, 1, 1)})

KNOWN_CALENDAR_YEARS = frozenset({2026})

_FULL_HOLIDAYS: Dict[str, FrozenSet[date]] = {
    NSE: NSE_HOLIDAYS_2026,
    BSE: BSE_HOLIDAYS_2026,
    MCX: MCX_FULL_HOLIDAYS_2026,
}

_warned_years: set[int] = set()


def _to_ist(now: Optional[datetime]) -> datetime:
    if now is None:
        return datetime.now(IST)
    if now.tzinfo is None:
        return now.replace(tzinfo=IST)
    return now.astimezone(IST)


def _extra_holidays(exchange: str) -> FrozenSet[date]:
    raw = os.getenv("EXCHANGE_EXTRA_HOLIDAYS", "")
    if not raw.strip():
        return frozenset()
    result = set()
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        scope, _, day = item.partition(":")
        if not day:
            scope, day = "ALL", scope
        scope = scope.strip().upper()
        if scope not in {"ALL", exchange}:
            continue
        try:
            result.add(date.fromisoformat(day.strip()))
        except ValueError:
            logger.warning("Ignoring invalid EXCHANGE_EXTRA_HOLIDAYS entry: %s", item)
    return frozenset(result)


def _warn_unknown_year(day: date) -> None:
    if day.year in KNOWN_CALENDAR_YEARS or day.year in _warned_years:
        return
    _warned_years.add(day.year)
    logger.warning(
        "⚠️ No exchange holiday list for %s; only weekends are skipped. "
        "Set EXCHANGE_EXTRA_HOLIDAYS until the calendar is updated.",
        day.year,
    )


def exchange_for_instrument(instrument) -> str:
    """Exchange whose calendar governs an option underlying."""
    category = str(getattr(instrument, "category", "") or "")
    symbol = str(getattr(instrument, "symbol", "") or "").upper()
    if category.startswith("commodity"):
        return MCX
    if symbol == "SENSEX":
        return BSE
    return NSE


def is_holiday(exchange: str, day: date) -> bool:
    """True when the exchange is closed for the whole day (weekday holiday)."""
    exchange = exchange.upper()
    _warn_unknown_year(day)
    return day in _FULL_HOLIDAYS.get(exchange, frozenset()) or day in _extra_holidays(exchange)


def is_trading_day(exchange: str, day: date) -> bool:
    if day.weekday() >= 5:
        return False
    return not is_holiday(exchange, day)


def mcx_close_time(day: date) -> time:
    """MCX closes 23:30 while the US observes DST, otherwise 23:55."""
    probe = datetime.combine(day, time(12, 0), tzinfo=_US_EASTERN)
    return MCX_CLOSE_US_DST if probe.dst() else MCX_CLOSE_US_STANDARD


def session_windows(exchange: str, day: date) -> tuple[tuple[time, time], ...]:
    """Trading windows (IST) for ``exchange`` on ``day``; empty when closed."""
    exchange = exchange.upper()
    if not is_trading_day(exchange, day):
        return ()
    if exchange in {NSE, BSE}:
        return (EQUITY_SESSION,)
    if exchange == MCX:
        close = mcx_close_time(day)
        if day in MCX_MORNING_CLOSED_2026:
            return ((MCX_EVENING_OPEN, close),)
        if day in MCX_EVENING_CLOSED_2026:
            return ((MCX_OPEN, MCX_EVENING_OPEN),)
        return ((MCX_OPEN, close),)
    return ()


def is_exchange_open(exchange: str, now: Optional[datetime] = None) -> bool:
    current = _to_ist(now)
    clock = current.time().replace(tzinfo=None)
    return any(start <= clock <= end for start, end in session_windows(exchange, current.date()))


def open_exchanges(now: Optional[datetime] = None, exchanges: Iterable[str] = EXCHANGES) -> set[str]:
    return {exchange for exchange in exchanges if is_exchange_open(exchange, now)}


def previous_trading_day(exchange: str, day: date, max_lookback_days: int = 15) -> date:
    """Most recent trading day strictly before ``day`` (falls back to ``day - 1``)."""
    candidate = day - timedelta(days=1)
    for _ in range(max_lookback_days):
        if is_trading_day(exchange, candidate):
            return candidate
        candidate -= timedelta(days=1)
    return day - timedelta(days=1)


def next_session_start(exchange: str, now: Optional[datetime] = None, max_days: int = 15) -> Optional[datetime]:
    """Start of the next (or current) trading window for ``exchange``."""
    current = _to_ist(now)
    for offset in range(max_days):
        day = current.date() + timedelta(days=offset)
        for start, end in session_windows(exchange, day):
            start_dt = datetime.combine(day, start, tzinfo=IST)
            end_dt = datetime.combine(day, end, tzinfo=IST)
            if end_dt >= current:
                return max(start_dt, current)
    return None


def seconds_until_next_open(now: Optional[datetime] = None, exchanges: Iterable[str] = EXCHANGES) -> Optional[float]:
    current = _to_ist(now)
    starts = [start for start in (next_session_start(ex, current) for ex in exchanges) if start is not None]
    if not starts:
        return None
    return max(0.0, (min(starts) - current).total_seconds())
