"""Completed-candle, alerts-only index option pullback entries."""

from __future__ import annotations

import math
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from premium_indicators import IndicatorDataError, completed_candles

IST = ZoneInfo("Asia/Kolkata")
INDEX_SYMBOLS = frozenset({"NIFTY", "BANKNIFTY", "SENSEX"})
INDEX_TRIGGER = "index_swing_confirmed"
MAX_SETUP_AGE = 15 * 60
MAX_QUOTE_AGE = 10


def enabled():
    return os.getenv("INDEX_SWING_STRATEGY_ENABLED", "false").strip().lower() in {
        "true", "1", "yes", "on",
    }


def _session(bars, now):
    day = now.astimezone(IST).date()
    return [bar for bar in bars
            if datetime.fromtimestamp(bar["_timestamp_epoch"], IST).date() == day]


def swings(bars, kind):
    """A pivot is known only after its right-hand neighbour has closed."""
    compare = min if kind == "low" else max
    return [middle[kind] for left, middle, right in zip(bars, bars[1:], bars[2:])
            if (middle[kind] < compare(left[kind], right[kind]) if kind == "low"
                else middle[kind] > compare(left[kind], right[kind]))]


def _swing_points(bars):
    points = []
    for index in range(1, len(bars) - 1):
        middle = bars[index]
        if middle["low"] < min(bars[index - 1]["low"], bars[index + 1]["low"]):
            points.append((index, "L", middle["low"]))
        if middle["high"] > max(bars[index - 1]["high"], bars[index + 1]["high"]):
            points.append((index, "H", middle["high"]))
    return sorted(points)


def detect_patterns(bars, timeframe):
    """Return only completed pivot patterns whose boundary has been closed through."""
    points = _swing_points(bars)
    if len(points) < 3 or not bars:
        return []
    found = []
    latest = bars[-1]["close"]
    for i in range(max(0, len(points) - 8), len(points) - 2):
        a, b, c = points[i:i + 3]
        tolerance = max(abs(a[2]), abs(c[2])) * .002
        if (a[1], b[1], c[1]) == ("L", "H", "L"):
            neckline = b[2]
            if abs(a[2] - c[2]) <= tolerance and latest > neckline:
                found.append({"name": "double_bottom", "side": "CE", "timeframe": timeframe,
                              "boundary": neckline, "formed_at": bars[c[0]]["_timestamp_epoch"]})
        if (a[1], b[1], c[1]) == ("H", "L", "H"):
            neckline = b[2]
            if abs(a[2] - c[2]) <= tolerance and latest < neckline:
                found.append({"name": "double_top", "side": "PE", "timeframe": timeframe,
                              "boundary": neckline, "formed_at": bars[c[0]]["_timestamp_epoch"]})
    for i in range(max(0, len(points) - 8), len(points) - 4):
        seq = points[i:i + 5]
        tolerance = max(abs(seq[0][2]), abs(seq[-1][2])) * .002
        if tuple(item[1] for item in seq) == ("H", "L", "H", "L", "H"):
            shoulder = (seq[0][2] + seq[4][2]) / 2
            neckline = min(seq[1][2], seq[3][2])
            if seq[2][2] > shoulder + tolerance and abs(seq[0][2] - seq[4][2]) <= tolerance \
                    and latest < neckline:
                found.append({"name": "head_and_shoulders", "side": "PE", "timeframe": timeframe,
                              "boundary": neckline, "formed_at": bars[seq[-1][0]]["_timestamp_epoch"]})
        if tuple(item[1] for item in seq) == ("L", "H", "L", "H", "L"):
            shoulder = (seq[0][2] + seq[4][2]) / 2
            neckline = max(seq[1][2], seq[3][2])
            if seq[2][2] < shoulder - tolerance and abs(seq[0][2] - seq[4][2]) <= tolerance \
                    and latest > neckline:
                found.append({"name": "inverse_head_and_shoulders", "side": "CE",
                              "timeframe": timeframe, "boundary": neckline,
                              "formed_at": bars[seq[-1][0]]["_timestamp_epoch"]})
    return found


def _aggregate(bars, minutes):
    seconds = minutes * 60
    groups = {}
    for bar in bars:
        stamp = bar["_timestamp_epoch"]
        start = int(stamp // seconds) * seconds
        groups.setdefault(start, []).append(bar)
    result = []
    for start, group in sorted(groups.items()):
        if [bar["_timestamp_epoch"] for bar in group] != [
            start + offset * 300 for offset in range(minutes // 5)
        ]:
            continue
        result.append({
            "_timestamp_epoch": start, "timestamp": start,
            "open": group[0]["open"], "high": max(item["high"] for item in group),
            "low": min(item["low"] for item in group), "close": group[-1]["close"],
            "volume": sum(item.get("volume") or 0 for item in group),
        })
    return result


def spot_setups(bars, side, now):
    """Opening-range, intraday reversal and completed spot-pattern routes."""
    session = _session(bars, now)
    if len(session) < 3 or side not in ("CE", "PE"):
        raise IndicatorDataError("index_spot_history")
    previous, current = session[-2:]
    if current["_timestamp_epoch"] - previous["_timestamp_epoch"] != 300:
        raise IndicatorDataError("index_spot_gap")
    bullish = side == "CE"
    recent = session[-11:-1]
    if len(recent) >= 6:
        recent_high = max(bar["high"] for bar in recent)
        recent_low = min(bar["low"] for bar in recent)
        colors = [1 if bar["close"] > bar["open"] else -1
                  for bar in recent if bar["close"] != bar["open"]]
        alternations = sum(left != right for left, right in zip(colors, colors[1:]))
        narrow = (recent_high - recent_low) / max(current["close"], 1) <= .003
        inside = recent_low <= current["close"] <= recent_high
        if narrow and inside and alternations >= 5:
            raise IndicatorDataError("index_sideways_range")
    setups = []
    opening = now.astimezone(IST).replace(hour=9, minute=15, second=0, microsecond=0).timestamp()
    first = next((bar for bar in session if bar["_timestamp_epoch"] == opening), None)
    if first is not None:
        boundary = first["high"] if bullish else first["low"]
        crossed = any(
            (bar["close"] > boundary and prior["close"] <= boundary if bullish
             else bar["close"] < boundary and prior["close"] >= boundary)
            for prior, bar in zip(session[:-1], session[1:])
        )
        holding = current["close"] > boundary if bullish else current["close"] < boundary
        if crossed and holding:
            setups.append("opening_range_break")
    pivots = _swing_points(session[:-1])
    if pivots:
        pivot_index, pivot_kind, pivot_price = pivots[-1]
        reclaimed = (pivot_kind == "L" and bullish and previous["low"] <= pivot_price * 1.0008
                     and current["close"] > previous["high"])
        rejected = (pivot_kind == "H" and not bullish and previous["high"] >= pivot_price * .9992
                    and current["close"] < previous["low"])
        if reclaimed or rejected:
            setups.append("intraday_support_reclaim" if bullish else "intraday_resistance_rejection")
    pattern_hits = []
    for timeframe in (5, 15, 30, 60):
        aggregated = session if timeframe == 5 else _aggregate(session, timeframe)
        pattern_hits.extend(item for item in detect_patterns(aggregated, timeframe)
                            if item["side"] == side)
    if pattern_hits:
        setups.append("spot_pattern_breakout")
    if not setups:
        raise IndicatorDataError("index_no_setup")
    opposing = [bar["high"] if bullish else bar["low"] for bar in session[:-1]
                if (bar["high"] > current["close"] if bullish else bar["low"] < current["close"])]
    return {
        "setups": setups,
        "spot_direction": "bullish" if bullish else "bearish",
        "spot_ltp": current["close"],
        "spot_next_zone": (min(opposing) if bullish else max(opposing)) if opposing else None,
        "opening_high": first["high"] if first else None,
        "opening_low": first["low"] if first else None,
        "spot_patterns": pattern_hits,
        "spot_zone": {"low": pivot_price, "high": pivot_price, "touches": 1}
        if pivots else None,
    }


def zones(bars, now):
    """Cluster previously observable pivots and previous-session levels."""
    prior = [bar for bar in bars
             if datetime.fromtimestamp(bar["_timestamp_epoch"], IST).date()
             < now.astimezone(IST).date()]
    levels = swings(bars, "low") + swings(bars, "high")
    if prior:
        day = datetime.fromtimestamp(prior[-1]["_timestamp_epoch"], IST).date()
        previous = [bar for bar in prior
                    if datetime.fromtimestamp(bar["_timestamp_epoch"], IST).date() == day]
        high, low, close = max(b["high"] for b in previous), min(b["low"] for b in previous), previous[-1]["close"]
        pivot = (high + low + close) / 3
        levels.extend([high, low, close, pivot, 2 * pivot - high, 2 * pivot - low])
    groups = []
    for level in sorted(levels):
        if groups and level - groups[-1][0] <= level * .0003:
            groups[-1].append(level)
        else:
            groups.append([level])
    return [{"low": min(group), "high": max(group), "touches": len(group)}
            for group in groups]


def aggregate_five_minutes(candles, now):
    bars = completed_candles(candles, now, timeframe_seconds=60)
    groups = {}
    for bar in bars:
        stamp = bar["_timestamp_epoch"]
        groups.setdefault(int(stamp // 300) * 300, []).append(bar)
    result = []
    for start, group in sorted(groups.items()):
        if [bar["_timestamp_epoch"] for bar in group] != [start + n * 60 for n in range(5)]:
            continue
        if start + 300 > now.timestamp():
            continue
        result.append({
            "timestamp": start, "open": group[0]["open"], "close": group[-1]["close"],
            "high": max(bar["high"] for bar in group), "low": min(bar["low"] for bar in group),
        })
    return result


def valid_entry(metadata, now):
    """Shared scanner/lifecycle admission gate for this strategy's evidence."""
    try:
        stamp = float(metadata["breakout_timestamp"])
        five_end = float(metadata["confirmation_end"])
        one_start = float(metadata["one_minute_start"])
        one_end = one_start + 60
        price = float(metadata["breakout_price"])
        stop = float(metadata["reference_low"])
        target = float(metadata["target"])
        trigger_close = float(metadata["one_minute_close"])
        pullback_high = float(metadata["reference_high"])
        numbers = (stamp, five_end, one_start, price, stop, target, trigger_close, pullback_high)
        if not all(math.isfinite(value) and value > 0 for value in numbers):
            return False
        return (
            metadata.get("index_swing") is True and metadata.get("alerts_only") is True
            and metadata.get("trigger") == INDEX_TRIGGER
            and 0 <= now.timestamp() - stamp <= MAX_QUOTE_AGE
            and five_end <= one_start and one_end <= now.timestamp()
            and 0 <= now.timestamp() - one_end <= 75
            and price > trigger_close > pullback_high
            and 0 < stop < price < target
            and bool(metadata.get("setups"))
        )
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


class IndexSwingStrategy:
    def prepare(self, symbol, side, spot_candles, premium_candles, now):
        if symbol not in INDEX_SYMBOLS:
            raise IndicatorDataError("index_unsupported")
        spot = completed_candles(spot_candles, now, timeframe_seconds=300)
        premium = completed_candles(premium_candles, now, timeframe_seconds=300)
        context = spot_setups(spot, side, now)
        if len(premium) < 3 or not spot:
            raise IndicatorDataError("index_insufficient_history")
        previous, green = premium[-2:]
        end = green["_timestamp_epoch"] + 300
        if (datetime.fromtimestamp(green["_timestamp_epoch"], IST).date() != now.astimezone(IST).date()
                or not 0 <= now.timestamp() - end <= MAX_SETUP_AGE
                or green["_timestamp_epoch"] - previous["_timestamp_epoch"] != 300):
            raise IndicatorDataError("index_confirmation_stale")
        if not (green["close"] > green["open"] and green["close"] > previous["high"]):
            raise IndicatorDataError("index_premium_confirmation")
        targets = [high for high in swings(premium, "high") if high > green["close"]]
        return {
            "candle": green, "timestamp": green["timestamp"],
            "high": green["close"], "low": green["low"], "armed": True,
            "confirmation_end": end, "swing_targets": sorted(set(targets)), **context,
        }

    def evaluate(self, symbol, side, reference, one_minute_candles, quote, now):
        bars = completed_candles(one_minute_candles, now, timeframe_seconds=60)
        if not bars:
            raise IndicatorDataError("index_one_minute_missing")
        last = bars[-1]
        if len(bars) < 2:
            raise IndicatorDataError("index_one_minute_missing")
        pullback, last = bars[-2:]
        if pullback["close"] >= pullback["open"]:
            raise IndicatorDataError("index_pullback_candle_missing")
        if last["close"] <= pullback["high"]:
            raise IndicatorDataError("index_pullback_breakout_missing")
        try:
            price, bid, ask = (float(quote[key]) for key in ("price", "bid", "ask"))
            stamp = float(quote.get("trade_timestamp", quote.get("timestamp")))
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise IndicatorDataError("index_quote_missing") from exc
        if (not all(math.isfinite(value) and value > 0 for value in (price, bid, ask, stamp))
                or bid > ask or (ask - bid) / ask > .02):
            raise IndicatorDataError("index_spread")
        if not 0 <= now.timestamp() - stamp <= MAX_QUOTE_AGE:
            raise IndicatorDataError("index_quote_stale")
        targets = [level for level in reference["swing_targets"] if level > price]
        if not targets:
            raise IndicatorDataError("index_target_passed")
        if last["_timestamp_epoch"] < reference["confirmation_end"]:
            raise IndicatorDataError("index_one_minute_not_after_breakout")
        signal = {
            "symbol": symbol, "underlying": symbol, "category": "index_options",
            "signal": "CALL" if side == "CE" else "PUT", "option_type": side,
            "trigger": INDEX_TRIGGER, "index_swing": True, "alerts_only": True,
            "timeframe": "5min", "confirmation_end": reference["confirmation_end"],
            "one_minute_start": last["_timestamp_epoch"], "one_minute_close": last["close"],
            "reference_timestamp": pullback["timestamp"], "reference_color": "RED",
            "reference_high": pullback["high"], "reference_low": reference["low"],
            "breakout_timestamp": stamp, "first_crossing_timestamp": stamp,
            "breakout_price": price, "entry": price, "entry_price": price,
            "strike_band": "ATM",
            "stop_loss": reference["low"], "target": targets[0], "targets": [targets[0]],
            "risk_points": price - reference["low"], "detected_at": now.isoformat(),
            **{key: reference[key] for key in (
                "setups", "spot_zone", "spot_direction", "spot_ltp", "spot_next_zone",
                "opening_high", "opening_low", "spot_patterns",
            )},
            "pullback_low": pullback["low"], "premium_confirmation_high": reference["high"],
            "premium_confirmation_low": reference["low"],
        }
        if not valid_entry(signal, now):
            raise IndicatorDataError("index_entry_risk_or_timing")
        return signal


def describe_setup(metadata):
    labels = {
        "opening_range_break": "Opening-range break",
        "intraday_support_reclaim": "Intraday support reclaim",
        "intraday_resistance_rejection": "Intraday resistance rejection",
        "spot_pattern_breakout": "Confirmed spot chart pattern",
    }
    setup = ", ".join(labels[name] for name in metadata.get("setups", []) if name in labels)
    patterns = ", ".join(
        f"{item['name'].replace('_', ' ')} ({item['timeframe']}m)"
        for item in metadata.get("spot_patterns", [])
    )
    zone = metadata.get("spot_zone") or {}
    return (
        f"Setup: {setup}\n"
        f"Spot: {metadata['spot_direction']} | Level "
        f"{zone.get('low', 0):.2f}-{zone.get('high', 0):.2f}\n"
        f"Spot patterns: {patterns or 'none'}\n"
        f"Option confirmation: 5m breakout then 1m pullback-high reclaim.\n"
        "Alerts only; stop updates are advisory, not broker orders."
    )
