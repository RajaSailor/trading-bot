"""Fail-closed confirmations calculated from listed option-premium candles.

Entry gates (all mandatory, strict, same option contract's premium candles):

* EMA(9): the trigger candle is above or straddles the contemporaneous EMA9
  (trigger price > EMA9, or observed trigger low <= EMA9 <= observed high).
  The reference RED candle may sit anywhere relative to EMA9.
* MACD(12,26,9): MACD line strictly above its signal line.
* RSI(14): strictly above 25 and strictly above the preceding completed
  indicator observation (rising).

Option A anti-chop: the 8 completed candles before the trigger bucket are
classified "sideways" when ``(max high - min low) / mid <= 0.20``; sideways
breakouts additionally need continuation (two distinct fresh live polls above
the same red high, or a fresh premium >= red high + 0.3R). Volume is not used.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Optional, Sequence
from zoneinfo import ZoneInfo


IST = ZoneInfo("Asia/Kolkata")
TIMEFRAME_SECONDS = 10 * 60
MIN_HISTORY = 34
MAX_CLOSED_BAR_AGE_SECONDS = 20 * 60
EMA_PERIOD = 9
RSI_PERIOD = 14
RSI_THRESHOLD = 25.0
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
SIDEWAYS_WINDOW_BARS = 8
SIDEWAYS_MAX_RANGE_FRACTION = 0.20
# Bars further apart than this (e.g. overnight/session breaks or long illiquid
# holes) are never bridged to build the anti-chop range.
SIDEWAYS_MAX_BAR_GAP_SECONDS = 3 * TIMEFRAME_SECONDS
CONTINUATION_R_MULTIPLE = 0.3
REQUIRED_LIVE_POLLS = 2
MODE_NORMAL = "normal"
MODE_TWO_POLLS = "two_fresh_polls"
MODE_MOMENTUM = "momentum_0_3r"
MODE_PENDING = "pending"
REQUIRED_CONFIRMATIONS = (
    "trigger_above_or_straddles_ema9",
    "macd_above_signal",
    "rsi14_above_25_and_rising",
)


class IndicatorDataError(ValueError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _epoch(value: object) -> float:
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, (int, float)):
        number = float(value)
        result = number / 1000 if number > 1e11 else number
        if not math.isfinite(result) or result <= 0:
            raise IndicatorDataError("invalid_timestamp")
        return result
    else:
        raw = str(value).strip()
        try:
            number = float(raw)
        except ValueError:
            moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        else:
            result = number / 1000 if number > 1e11 else number
            if not math.isfinite(result) or result <= 0:
                raise IndicatorDataError("invalid_timestamp")
            return result
    if moment.tzinfo is None:
        raise IndicatorDataError("invalid_timestamp")
    result = moment.timestamp()
    if not math.isfinite(result) or result <= 0:
        raise IndicatorDataError("invalid_timestamp")
    return result


def completed_candles(candles: Sequence[dict], now: datetime) -> list[dict]:
    """Validate, deduplicate, order and retain only completed, non-future bars.

    OHLC is mandatory; volume is optional and never used by the entry gates
    (missing/invalid volume is carried as ``None`` instead of blocking).
    """
    now_epoch = now.timestamp()
    by_timestamp: dict[float, dict] = {}
    for candle in candles:
        try:
            timestamp = _epoch(candle.get("timestamp"))
        except (AttributeError, TypeError, ValueError, OverflowError, OSError) as exc:
            raise IndicatorDataError("invalid_timestamp") from exc
        if timestamp > now_epoch:
            continue
        try:
            values = {key: float(candle[key]) for key in ("open", "high", "low", "close")}
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise IndicatorDataError("invalid_ohlc") from exc
        if not all(math.isfinite(value) for value in values.values()):
            raise IndicatorDataError("invalid_ohlc")
        if (
            min(values.values()) <= 0
            or values["high"] < max(values["open"], values["close"], values["low"])
            or values["low"] > min(values["open"], values["close"], values["high"])
        ):
            raise IndicatorDataError("invalid_ohlc")
        try:
            volume = float(candle.get("volume"))
        except (TypeError, ValueError, OverflowError):
            volume = None
        values["volume"] = volume if volume is not None and math.isfinite(volume) and volume >= 0 else None
        values["timestamp"] = candle.get("timestamp")
        values["_timestamp_epoch"] = timestamp
        if timestamp + TIMEFRAME_SECONDS <= now_epoch:
            by_timestamp[timestamp] = values
    return [by_timestamp[timestamp] for timestamp in sorted(by_timestamp)]


def ema(values: Sequence[float], period: int) -> list[Optional[float]]:
    """EMA seeded with the first complete period's SMA."""
    result: list[Optional[float]] = [None] * len(values)
    if period <= 0 or len(values) < period:
        return result
    current = sum(values[:period]) / period
    result[period - 1] = current
    multiplier = 2.0 / (period + 1)
    for index in range(period, len(values)):
        current = (values[index] - current) * multiplier + current
        result[index] = current
    return result


def wilder_rsi(values: Sequence[float], period: int = RSI_PERIOD) -> list[Optional[float]]:
    """Wilder RSI, with the first value seeded by mean gains/losses."""
    result: list[Optional[float]] = [None] * len(values)
    if period <= 0 or len(values) <= period:
        return result
    changes = [values[index] - values[index - 1] for index in range(1, len(values))]
    average_gain = sum(max(change, 0.0) for change in changes[:period]) / period
    average_loss = sum(max(-change, 0.0) for change in changes[:period]) / period

    def value(gain: float, loss: float) -> float:
        if loss == 0:
            return 50.0 if gain == 0 else 100.0
        if gain == 0:
            return 0.0
        return 100.0 - 100.0 / (1.0 + gain / loss)

    result[period] = value(average_gain, average_loss)
    for index in range(period + 1, len(values)):
        change = changes[index - 1]
        average_gain = ((period - 1) * average_gain + max(change, 0.0)) / period
        average_loss = ((period - 1) * average_loss + max(-change, 0.0)) / period
        result[index] = value(average_gain, average_loss)
    return result


def macd(
    values: Sequence[float], fast: int = MACD_FAST, slow: int = MACD_SLOW, signal_period: int = MACD_SIGNAL
) -> tuple[list[Optional[float]], list[Optional[float]]]:
    """MACD with SMA-seeded fast/slow EMAs and SMA-seeded signal EMA."""
    fast_values, slow_values = ema(values, fast), ema(values, slow)
    line: list[Optional[float]] = [None] * len(values)
    for index, (fast_value, slow_value) in enumerate(zip(fast_values, slow_values)):
        if fast_value is not None and slow_value is not None:
            line[index] = fast_value - slow_value
    first = next((index for index, value in enumerate(line) if value is not None), len(line))
    signal_values = [value for value in line[first:] if value is not None]
    seeded = ema(signal_values, signal_period)
    signal: list[Optional[float]] = [None] * len(values)
    for offset, value in enumerate(seeded):
        if value is not None:
            signal[first + offset] = value
    return line, signal




def _finite_positive(value) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def trigger_ema_position(price, low, high, ema_value) -> str:
    """Classify the trigger candle against its contemporaneous EMA9.

    ``price`` is the trigger price (completed close or fresh live LTP) and
    ``low``/``high`` the trigger candle's *actual/observed* extremes. Returns
    ``"above"`` (low > EMA), ``"straddle"`` (low <= EMA <= high) or ``"below"``
    (high < EMA, wholly below). Missing/inconsistent evidence raises
    :class:`IndicatorDataError` so the gate fails closed; no extremes are inferred.
    """
    values = [_finite_positive(item) for item in (price, low, high, ema_value)]
    if any(item is None for item in values):
        raise IndicatorDataError("missing_trigger_evidence")
    price, low, high, ema_value = values
    if not low <= price <= high:
        raise IndicatorDataError("missing_trigger_evidence")
    if low > ema_value:
        return "above"
    if low <= ema_value <= high:
        return "straddle"
    return "below"


def trigger_ema_ok(price, low, high, ema_value) -> bool:
    """True when trigger price > EMA9 or the trigger candle straddles EMA9."""
    position = trigger_ema_position(price, low, high, ema_value)
    return float(price) > float(ema_value) or position in {"above", "straddle"}


def confirmation_status(values: dict) -> dict:
    """Evaluate the three strict, mandatory long-premium confirmations."""
    return {
        "trigger_above_or_straddles_ema9": trigger_ema_ok(
            values["premium"], values["trigger_low"], values["trigger_high"], values["ema9"]
        ),
        "macd_above_signal": values["macd12_26"] > values["macd_signal9"],
        "rsi14_above_25_and_rising": (
            values["rsi14"] > RSI_THRESHOLD and values["rsi14"] > values["rsi14_previous"]
        ),
    }


def anti_chop_status(bars: Sequence[dict], trigger_epoch: float) -> dict:
    """Option A sideways classification from the 8 completed bars before the trigger.

    ``bars`` must be validated output of :func:`completed_candles`. Only bars that
    started before the trigger bucket are used (no trigger/forming/future bar).
    The window must be on the trigger's IST date and contain no gap larger than
    ``SIDEWAYS_MAX_BAR_GAP_SECONDS`` (sessions are never bridged); otherwise the
    anti-chop evidence is insufficient and the caller must fail closed.
    """
    window = [bar for bar in bars if bar["_timestamp_epoch"] < trigger_epoch][-SIDEWAYS_WINDOW_BARS:]
    if len(window) < SIDEWAYS_WINDOW_BARS:
        raise IndicatorDataError("insufficient_anti_chop_history")
    trigger_day = datetime.fromtimestamp(trigger_epoch, IST).date()
    epochs = [bar["_timestamp_epoch"] for bar in window] + [trigger_epoch]
    if any(later - earlier > SIDEWAYS_MAX_BAR_GAP_SECONDS for earlier, later in zip(epochs, epochs[1:])) or any(
        datetime.fromtimestamp(epoch, IST).date() != trigger_day for epoch in epochs
    ):
        raise IndicatorDataError("anti_chop_session_gap")
    window_high = max(bar["high"] for bar in window)
    window_low = min(bar["low"] for bar in window)
    mid_price = (window_high + window_low) / 2.0
    if not (math.isfinite(mid_price) and mid_price > 0):
        raise IndicatorDataError("invalid_anti_chop_range")
    range_fraction = round((window_high - window_low) / mid_price, 9)
    return {
        "bars": len(window),
        "window_high": round(window_high, 6),
        "window_low": round(window_low, 6),
        "mid_price": round(mid_price, 6),
        "range_fraction": range_fraction,
        "max_range_fraction": SIDEWAYS_MAX_RANGE_FRACTION,
        "sideways": range_fraction <= SIDEWAYS_MAX_RANGE_FRACTION,
        "window_start": datetime.fromtimestamp(window[0]["_timestamp_epoch"], IST).isoformat(timespec="seconds"),
        "window_end": datetime.fromtimestamp(
            window[-1]["_timestamp_epoch"] + TIMEFRAME_SECONDS, IST
        ).isoformat(timespec="seconds"),
    }


def continuation_status(
    *, sideways: bool, price, red_high, initial_stop, live_polls: int = 0, continuation_price=None
) -> dict:
    """Option A continuation decision for one breakout candidate.

    * Base breakout always requires ``price > red_high`` (equality is not a breakout).
    * Not sideways: no extra delay (``mode="normal"``).
    * Sideways: qualifies when ``price >= red_high + 0.3R`` (``R = red_high -
      initial_stop > 0``; equality counts) **or** ``live_polls >= 2`` distinct,
      consecutive fresh live poll observations above the same red high. Two polls
      are not two candles, and neither is evidence of accuracy.

    ``price`` is the breakout evidence (fresh live LTP, or a completed trigger
    candle's high). ``continuation_price`` (default ``price``) is compared with
    the 0.3R threshold; completed candles pass their actual close so a wick
    that reverted is not treated as held continuation.
    """
    price_value, high_value = _finite_positive(price), _finite_positive(red_high)
    held_value = price_value if continuation_price is None else _finite_positive(continuation_price)
    stop_value = _finite_positive(initial_stop)
    risk = None
    threshold = None
    if high_value is not None and stop_value is not None and high_value - stop_value > 0:
        risk = round(high_value - stop_value, 6)
        threshold = round(high_value + CONTINUATION_R_MULTIPLE * risk, 6)
    polls = max(0, int(live_polls or 0))
    result = {
        "sideways": bool(sideways),
        "mode": MODE_PENDING,
        "qualified": False,
        "reference_high": high_value,
        "initial_stop": stop_value,
        "r": risk,
        "momentum_threshold": threshold,
        "live_polls": polls,
        "required_live_polls": REQUIRED_LIVE_POLLS,
        "price": price_value,
        "continuation_price": held_value,
    }
    if price_value is None or high_value is None or not price_value > high_value:
        result["reason"] = "no_breakout"
        return result
    if not sideways:
        result.update(mode=MODE_NORMAL, qualified=True, reason="not_sideways")
    elif threshold is not None and held_value is not None and round(held_value, 6) >= threshold:
        result.update(mode=MODE_MOMENTUM, qualified=True, reason="reached_0_3r")
    elif polls >= REQUIRED_LIVE_POLLS:
        result.update(mode=MODE_TWO_POLLS, qualified=True, reason="two_fresh_polls")
    else:
        result["reason"] = "awaiting_continuation" if threshold is not None else "awaiting_polls_invalid_r"
    return result


def calculate_confirmations(
    candles: Sequence[dict],
    now: datetime,
    provisional_candle: Optional[dict] = None,
) -> dict:
    """Return mandatory AND confirmations, anti-chop evidence and metadata.

    Completed mode: the trigger is the latest completed candle; EMA9/MACD/RSI
    are taken at that bar and the EMA rule uses its actual OHLC. Live mode: a
    snapshot provisional bar (observed fresh LTP ticks of the forming bucket:
    first/max/min/last) is appended to a *copy* of completed history, so each
    poll recomputes from immutable completed bars and never appends a new bar.
    RSI "rising" compares against the preceding completed observation.
    Previous-session completed bars may warm EMA/MACD/RSI (>= 34 bars).
    """
    if now.tzinfo is None:
        raise IndicatorDataError("invalid_now")
    now = now.astimezone(IST)
    closed = completed_candles(candles, now)
    if len(closed) < MIN_HISTORY:
        raise IndicatorDataError("insufficient_history")
    newest_epoch = closed[-1]["_timestamp_epoch"] + TIMEFRAME_SECONDS
    if now.timestamp() - newest_epoch > MAX_CLOSED_BAR_AGE_SECONDS:
        raise IndicatorDataError("stale_candles")

    price_bars = list(closed)
    evidence_mode = "completed"
    trigger_evidence = "completed_ohlc"
    if provisional_candle is not None:
        provisional = dict(provisional_candle)
        try:
            provisional_timestamp = _epoch(provisional["timestamp"])
            provisional_values = {
                key: float(provisional[key]) for key in ("open", "high", "low", "close")
            }
        except (KeyError, TypeError, ValueError, OverflowError, OSError) as exc:
            raise IndicatorDataError("invalid_provisional_candle") from exc
        if (
            provisional_timestamp > now.timestamp()
            or provisional_timestamp <= closed[-1]["_timestamp_epoch"]
            or not all(math.isfinite(value) for value in provisional_values.values())
            or min(provisional_values.values()) <= 0
            or provisional_values["high"] < max(provisional_values.values())
            or provisional_values["low"] > min(provisional_values.values())
        ):
            raise IndicatorDataError("invalid_provisional_candle")
        provisional.update(provisional_values)
        provisional["volume"] = None
        provisional["_timestamp_epoch"] = provisional_timestamp
        if provisional.get("observed_at") is not None:
            try:
                observed_at = _epoch(provisional["observed_at"])
            except (TypeError, ValueError, OverflowError, OSError) as exc:
                raise IndicatorDataError("invalid_provisional_candle") from exc
            if observed_at > now.timestamp():
                raise IndicatorDataError("invalid_provisional_candle")
            provisional["observed_at"] = observed_at
        price_bars.append(provisional)
        evidence_mode = "live_provisional_ltp"
        trigger_evidence = "live_observed_ticks"
    trigger = price_bars[-1]
    anti_chop = anti_chop_status(closed, trigger["_timestamp_epoch"])
    closes = [candle["close"] for candle in price_bars]
    ema_values = ema(closes, EMA_PERIOD)
    rsi_values = wilder_rsi(closes)
    macd_line, macd_signal = macd(closes)
    current_index = len(closes) - 1
    previous_index = current_index - 1
    values = {
        "premium": closes[current_index],
        "trigger_high": trigger["high"],
        "trigger_low": trigger["low"],
        "rsi14": rsi_values[current_index],
        "rsi14_previous": rsi_values[previous_index],
        "ema9": ema_values[current_index],
        "macd12_26": macd_line[current_index],
        "macd_signal9": macd_signal[current_index],
    }
    if any(value is None for value in values.values()):
        raise IndicatorDataError("indicator_warmup")
    if not all(math.isfinite(float(value)) for value in values.values()):
        raise IndicatorDataError("invalid_indicator")
    passed = confirmation_status(values)
    return {
        "passed": passed,
        "values": {key: round(float(value), 6) for key, value in values.items()},
        "ema9_position": trigger_ema_position(
            values["premium"], values["trigger_low"], values["trigger_high"], values["ema9"]
        ),
        "rsi_threshold": RSI_THRESHOLD,
        "anti_chop": anti_chop,
        "evidence_mode": evidence_mode,
        "trigger_evidence": trigger_evidence,
        "indicator_as_of": datetime.fromtimestamp(trigger["_timestamp_epoch"], IST).isoformat(timespec="seconds"),
        "previous_indicator_as_of": datetime.fromtimestamp(
            price_bars[previous_index]["_timestamp_epoch"], IST
        ).isoformat(timespec="seconds"),
        "session_date": now.date().isoformat(),
        "live_quote_as_of": (
            datetime.fromtimestamp(trigger["observed_at"], IST).isoformat(timespec="seconds")
            if evidence_mode == "live_provisional_ltp" and trigger.get("observed_at") is not None
            else None
        ),
        "ready": all(passed.values()),
    }
