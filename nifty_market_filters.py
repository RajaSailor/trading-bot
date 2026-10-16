"""Optional, underlying-only confirmations for NIFTY premium entries."""

from __future__ import annotations

import math
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from exchange_calendar import previous_trading_day
from premium_indicators import IndicatorDataError, completed_candles, ema

IST = ZoneInfo("Asia/Kolkata")


def _enabled(name):
    return os.getenv(name, "false").strip().lower() in {"true", "1", "yes", "on"}


def _number(name, default, low, high):
    try:
        value = float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) and low <= value <= high else default


def market_context(candles, now, minutes=15):
    """Use completed spot candles only; zero-volume indices use an explicit TWAP."""
    bars = completed_candles(candles, now, timeframe_seconds=minutes * 60)
    today = now.astimezone(IST).date()
    session = [bar for bar in bars
               if datetime.fromtimestamp(bar["_timestamp_epoch"], IST).date() == today]
    if not session or now.timestamp() - (session[-1]["_timestamp_epoch"] + minutes * 60) > minutes * 60 + 60:
        raise IndicatorDataError("underlying_stale")
    typicals = [(bar["high"] + bar["low"] + bar["close"]) / 3 for bar in session]
    volumes = [bar["volume"] for bar in session]
    if all(volume is not None and volume > 0 for volume in volumes):
        average = sum(price * volume for price, volume in zip(typicals, volumes)) / sum(volumes)
        average_kind = "vwap"
    else:
        average = sum(typicals) / len(typicals)
        average_kind = "twap"
    spot = session[-1]["close"]
    averages = ema([bar["close"] for bar in bars], 20)
    regime = "unavailable"
    if len(bars) >= 21:
        current, previous = averages[-1], averages[-2]
        regime = "neutral"
        if spot > average and spot > current and current > previous:
            regime = "bullish"
        elif spot < average and spot < current and current < previous:
            regime = "bearish"

    previous_day = previous_trading_day("NSE", today)
    prior = [bar for bar in bars
             if datetime.fromtimestamp(bar["_timestamp_epoch"], IST).date() == previous_day]
    supports, resistances = [], []
    if prior:
        high, low, close = max(bar["high"] for bar in prior), min(bar["low"] for bar in prior), prior[-1]["close"]
        pivot = (high + low + close) / 3
        supports.extend([low, pivot, 2 * pivot - high, pivot - (high - low)])
        resistances.extend([high, pivot, 2 * pivot - low, pivot + (high - low)])
    recent = session[-20:]
    for left, middle, right in zip(recent, recent[1:], recent[2:]):
        if middle["high"] > max(left["high"], right["high"]):
            resistances.append(middle["high"])
        if middle["low"] < min(left["low"], right["low"]):
            supports.append(middle["low"])
    return {
        "regime": regime, "spot": spot, "session_average": average,
        "average_kind": average_kind, "supports": supports, "resistances": resistances,
        "previous_day_available": bool(prior), "as_of": session[-1]["_timestamp_epoch"] + minutes * 60,
    }


class NiftyMarketFilter:
    def __init__(self, data_manager):
        self.data_manager = data_manager
        self.direction_enabled = _enabled("NIFTY_DIRECTION_FILTER_ENABLED")
        self.sr_enabled = _enabled("NIFTY_SR_FILTER_ENABLED")
        minutes = _number("NIFTY_DIRECTION_HTF_MINUTES", 15, 5, 15)
        self.minutes = int(minutes) if minutes in (5, 15) else 15
        self.buffer_pct = _number("NIFTY_SR_BUFFER_PCT", 0.10, 0, 5)
        policy = os.getenv("NIFTY_DIRECTION_NEUTRAL_POLICY", "block").strip().lower()
        self.neutral_policy = policy if policy in ("block", "stronger") else "block"
        self.neutral_breakout_pct = _number("NIFTY_DIRECTION_NEUTRAL_BREAKOUT_PCT", 0.5, 0.01, 10)
        self._candles = []
        self._bucket = None
        self._retry_at = 0
        self.last_context = {}

    @property
    def enabled(self):
        return self.direction_enabled or self.sr_enabled

    def refresh(self, now):
        if not self.enabled:
            return
        local = now.astimezone(IST)
        anchor = local.replace(hour=9, minute=15, second=0, microsecond=0).timestamp()
        bucket = (local.date(), int((now.timestamp() - anchor) // (self.minutes * 60)))
        if self._bucket == bucket and (not self._retry_at or now.timestamp() < self._retry_at):
            return
        retry = self._bucket == bucket and bool(self._retry_at)
        self._bucket = bucket
        self._retry_at = 0
        try:
            ref = self.data_manager.resolve_underlying("NIFTY", now.astimezone(IST).date())
            if ref is None or ref.exchange_segment != "IDX_I" or ref.instrument_type != "INDEX":
                raise ValueError("missing underlying")
            day = now.astimezone(IST).date()
            candles = self.data_manager.fetch_contract_candles(
                ref.security_id, ref.exchange_segment, ref.instrument_type,
                previous_trading_day("NSE", day).isoformat(), day.isoformat(),
                self.minutes, symbol="NIFTY", now=now.timestamp(), force_refresh=retry,
            )
            self._candles = candles or []
            context = market_context(self._candles, now, self.minutes)
            expected_completion = anchor + bucket[1] * self.minutes * 60
            if context["as_of"] < expected_completion:
                self._retry_at = now.timestamp() + 120
        except Exception:
            self._candles = []
            self._retry_at = now.timestamp() + 120

    def rejection(self, side, signal, now):
        if not self.enabled:
            return None
        try:
            context = market_context(self._candles, now, self.minutes)
        except (IndicatorDataError, TypeError, ValueError, OverflowError, OSError):
            self.last_context = {"error": "underlying_unavailable_or_stale"}
            return "direction_filter_reject" if self.direction_enabled else "sr_filter_reject"
        self.last_context = context
        regime = context["regime"]
        if self.direction_enabled:
            allowed = regime == ("bullish" if side == "CE" else "bearish")
            if regime == "neutral" and self.neutral_policy == "stronger":
                allowed = float(signal["breakout_price"]) >= float(signal["reference_high"]) * (
                    1 + self.neutral_breakout_pct / 100)
            if not allowed:
                return "direction_filter_reject"
        if self.sr_enabled:
            if not context["previous_day_available"]:
                return "sr_filter_reject"
            spot = context["spot"]
            buffer = spot * self.buffer_pct / 100
            levels = context["resistances"] if side == "CE" else context["supports"]
            if any(0 <= (level - spot if side == "CE" else spot - level) <= buffer for level in levels):
                return "sr_filter_reject"
        return None
