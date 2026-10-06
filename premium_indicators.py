"""Fail-closed confirmations calculated from listed option-premium candles."""

from __future__ import annotations

import math
from datetime import date, datetime, time, timezone
from typing import Optional, Sequence
from zoneinfo import ZoneInfo

from exchange_calendar import MCX, exchange_for_instrument, session_windows


IST = ZoneInfo("Asia/Kolkata")
TIMEFRAME_SECONDS = 10 * 60
MIN_HISTORY = 34
MAX_CLOSED_BAR_AGE_SECONDS = 20 * 60
EMA_PERIOD = 9
RSI_PERIOD = 14
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
PSAR_STEP = 0.02
PSAR_MAXIMUM = 0.2


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
    """Validate, deduplicate, order and retain only completed, non-future bars."""
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
            values = {key: float(candle[key]) for key in ("open", "high", "low", "close", "volume")}
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise IndicatorDataError("invalid_ohlcv") from exc
        if not all(math.isfinite(value) for value in values.values()):
            raise IndicatorDataError("invalid_ohlcv")
        if (
            min(values["open"], values["high"], values["low"], values["close"]) <= 0
            or values["volume"] <= 0
            or values["high"] < max(values["open"], values["close"], values["low"])
            or values["low"] > min(values["open"], values["close"], values["high"])
        ):
            raise IndicatorDataError("invalid_ohlcv")
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


def parabolic_sar(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Optional[Sequence[float]] = None,
    step: float = PSAR_STEP,
    maximum: float = PSAR_MAXIMUM,
) -> list[Optional[float]]:
    """Standard two-bar-clamped Parabolic SAR with 0.02 step / 0.2 cap."""
    result: list[Optional[float]] = [None] * len(highs)
    if len(highs) < 2 or len(lows) != len(highs) or (closes is not None and len(closes) != len(highs)):
        return result
    rising = closes[1] >= closes[0] if closes is not None else highs[1] + lows[1] >= highs[0] + lows[0]
    sar = lows[0] if rising else highs[0]
    extreme = max(highs[0], highs[1]) if rising else min(lows[0], lows[1])
    acceleration = step
    result[1] = sar
    for index in range(2, len(highs)):
        sar += acceleration * (extreme - sar)
        if rising:
            sar = min(sar, lows[index - 1], lows[index - 2])
            if lows[index] < sar:
                sar, rising, extreme, acceleration = extreme, False, lows[index], step
            elif highs[index] > extreme:
                extreme = highs[index]
                acceleration = min(maximum, acceleration + step)
        else:
            sar = max(sar, highs[index - 1], highs[index - 2])
            if highs[index] > sar:
                sar, rising, extreme, acceleration = extreme, True, highs[index], step
            elif lows[index] < extreme:
                extreme = lows[index]
                acceleration = min(maximum, acceleration + step)
        result[index] = sar
    return result


def _exchange_session(exchange: str, day: date) -> tuple[tuple[time, time], ...]:
    return session_windows(exchange, day)


def confirmation_status(values: dict) -> dict:
    """Evaluate the five strict, mandatory long-premium confirmations."""
    return {
        "above_vwap": values["premium"] > values["vwap"],
        "rsi14_above_30_and_rising": values["rsi14"] > 30 and values["rsi14"] > values["rsi14_previous"],
        "above_ema9": values["premium"] > values["ema9"],
        "macd_above_signal": values["macd12_26"] > values["macd_signal9"],
        "psar_below_premium": values["psar_0_02_0_2"] < values["premium"],
    }


def calculate_confirmations(
    candles: Sequence[dict],
    now: datetime,
    instrument,
    provisional_candle: Optional[dict] = None,
) -> dict:
    """Return mandatory AND confirmations and explicit evidence metadata.

    Previous-session bars warm the SMA-seeded EMA/MACD and Wilder RSI/PSAR.
    VWAP uses only completed bars from today's exchange session and HLC3*volume.
    A provisional live bar may affect close-based indicators, but never VWAP.
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

    exchange = exchange_for_instrument(instrument)
    session_day = now.date()
    windows = _exchange_session(exchange, session_day)
    session_starts = [datetime.combine(session_day, start, IST).timestamp() for start, _ in windows]
    session_start = min(session_starts) if session_starts else None
    session_bars = [
        candle for candle in closed
        if session_start is not None
        and session_start <= candle["_timestamp_epoch"] <= now.timestamp()
        and datetime.fromtimestamp(candle["_timestamp_epoch"], IST).date() == session_day
    ]
    volume_total = sum(candle["volume"] for candle in session_bars)
    if not session_bars or not math.isfinite(volume_total) or volume_total <= 0:
        raise IndicatorDataError("no_session_volume")
    vwap = sum(
        ((candle["high"] + candle["low"] + candle["close"]) / 3.0) * candle["volume"]
        for candle in session_bars
    ) / volume_total
    vwap_as_of = session_bars[-1]["_timestamp_epoch"] + TIMEFRAME_SECONDS
    if now.timestamp() - vwap_as_of > MAX_CLOSED_BAR_AGE_SECONDS:
        raise IndicatorDataError("stale_vwap")

    price_bars = list(closed)
    evidence_mode = "completed"
    if provisional_candle is not None:
        provisional = dict(provisional_candle)
        provisional["volume"] = 0.0
        provisional["_timestamp_epoch"] = _epoch(provisional["timestamp"])
        price_bars.append(provisional)
        evidence_mode = "live_provisional_ltp"
    closes = [candle["close"] for candle in price_bars]
    highs = [candle["high"] for candle in price_bars]
    lows = [candle["low"] for candle in price_bars]
    ema_values = ema(closes, EMA_PERIOD)
    rsi_values = wilder_rsi(closes)
    macd_line, macd_signal = macd(closes)
    sar_values = parabolic_sar(highs, lows, closes)
    current_index = len(closes) - 1
    previous_index = current_index - 1
    values = {
        "premium": closes[current_index],
        "vwap": vwap,
        "rsi14": rsi_values[current_index],
        "rsi14_previous": rsi_values[previous_index],
        "ema9": ema_values[current_index],
        "macd12_26": macd_line[current_index],
        "macd_signal9": macd_signal[current_index],
        "psar_0_02_0_2": sar_values[current_index],
    }
    if any(value is None or not math.isfinite(float(value)) for value in values.values()):
        raise IndicatorDataError("indicator_warmup")
    if not all(math.isfinite(float(value)) for value in values.values()):
        raise IndicatorDataError("invalid_indicator")
    passed = confirmation_status(values)
    return {
        "passed": passed,
        "values": {key: round(float(value), 6) for key, value in values.items()},
        "evidence_mode": evidence_mode,
        "vwap_as_of": datetime.fromtimestamp(vwap_as_of, IST).isoformat(timespec="seconds"),
        "indicator_as_of": datetime.fromtimestamp(
            price_bars[-1]["_timestamp_epoch"], IST
        ).isoformat(timespec="seconds"),
        "session_date": session_day.isoformat(),
        "vwap_volume": round(volume_total, 6),
        "ready": all(passed.values()),
    }
