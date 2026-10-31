"""Completed-candle, alerts-only index reversals and opening-range retests."""

from __future__ import annotations

import math
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from premium_indicators import IndicatorDataError, completed_candles
from price_action_confirmations import (
    CONFIRMATION_NAMES, confirmations, gap_context, valid_confirmation,
)

IST = ZoneInfo("Asia/Kolkata")
INDEX_SYMBOLS = frozenset({"NIFTY", "BANKNIFTY", "SENSEX"})
INDEX_TRIGGER = "index_swing_confirmed"
MIN_REWARD_RISK = 1.5
MAX_STOP_FRACTION = .15
CONFIRMATION_LIFETIME = 120


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


def spot_setups(bars, side, now):
    session = _session(bars, now)
    if len(session) < 3 or side not in ("CE", "PE"):
        raise IndicatorDataError("index_spot_history")
    first, previous, current = session[0], session[-2], session[-1]
    opening = now.astimezone(IST).replace(hour=9, minute=15, second=0, microsecond=0).timestamp()
    bullish = side == "CE"
    if current["_timestamp_epoch"] - previous["_timestamp_epoch"] != 300:
        raise IndicatorDataError("index_spot_gap")
    direction = (current["close"] > current["open"] and current["close"] > previous["high"]
                 if bullish else
                 current["close"] < current["open"] and current["close"] < previous["low"])
    if not direction:
        raise IndicatorDataError("index_spot_direction")
    available_zones = zones(bars[:-1], now)
    tolerance = current["close"] * .0008
    matched, labels = None, []
    for zone in available_zones:
        touched = (previous["low"] <= zone["high"] + tolerance
                   and previous["high"] >= zone["low"] - tolerance)
        reclaimed = (current["close"] > zone["high"] and previous["close"] >= zone["low"] - tolerance
                     if bullish else
                     current["close"] < zone["low"] and previous["close"] <= zone["high"] + tolerance)
        if touched and reclaimed:
            matched = zone
            labels.append("support_reclaim" if bullish else "resistance_rejection")
            break
    boundary = first["high"] if bullish else first["low"]
    # Missing opening data must not turn a later candle into the opening range.
    if first["_timestamp_epoch"] == opening:
        for index in range(max(1, len(session) - 13), len(session) - 1):
            breakout = session[index]
            crossed = (breakout["close"] > boundary and session[index - 1]["close"] <= boundary
                       if bullish else
                       breakout["close"] < boundary and session[index - 1]["close"] >= boundary)
            if not crossed:
                continue
            pause = session[index + 1:-1]
            if not pause or len(pause) > 3:
                continue
            sequence = session[index:]
            if any(right["_timestamp_epoch"] - left["_timestamp_epoch"] != 300
                   for left, right in zip(sequence, sequence[1:])):
                continue
            holds = all(bar["close"] > boundary if bullish else bar["close"] < boundary for bar in pause)
            if not holds:
                continue
            tested = any(bar["low"] <= boundary + tolerance if bullish
                         else bar["high"] >= boundary - tolerance for bar in pause)
            resumed = (current["close"] > max(bar["high"] for bar in pause) if bullish
                       else current["close"] < min(bar["low"] for bar in pause))
            if not resumed:
                continue
            if tested:
                labels.append("opening_range_retest")
            else:
                size = breakout["high"] - breakout["low"]
                body = abs(breakout["close"] - breakout["open"])
                strong = size > 0 and body >= size * .5 and (
                    breakout["close"] >= breakout["low"] + .75 * size if bullish
                    else breakout["close"] <= breakout["low"] + .25 * size)
                shallow = (min(bar["low"] for bar in pause) >= breakout["low"] + size * .5
                           if bullish else
                           max(bar["high"] for bar in pause) <= breakout["high"] - size * .5)
                counter_candle = any(bar["close"] <= bar["open"] if bullish
                                    else bar["close"] >= bar["open"] for bar in pause)
                if strong and shallow and counter_candle:
                    labels.append("shallow_pullback_continuation")
            if labels:
                matched = matched or {"low": boundary, "high": boundary, "touches": 1}
    if not labels:
        raise IndicatorDataError("index_no_setup")
    opposing = [zone["low"] if bullish else zone["high"] for zone in available_zones
                if (zone["low"] > current["close"] if bullish else zone["high"] < current["close"])]
    return {
        "setups": sorted(set(labels)), "spot_zone": matched,
        "spot_direction": "bullish" if bullish else "bearish",
        "spot_ltp": current["close"],
        "spot_next_zone": (min(opposing) if bullish else max(opposing)) if opposing else None,
        "opening_high": first["high"] if first["_timestamp_epoch"] == opening else None,
        "opening_low": first["low"] if first["_timestamp_epoch"] == opening else None,
    }


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
        numbers = (stamp, five_end, one_start, price, stop, target, trigger_close)
        if not all(math.isfinite(value) and value > 0 for value in numbers):
            return False
        risk = price - stop
        return (
            metadata.get("index_swing") is True and metadata.get("alerts_only") is True
            and metadata.get("trigger") == INDEX_TRIGGER
            and 0 <= now.timestamp() - stamp <= 10
            and five_end <= one_start and one_end <= five_end + CONFIRMATION_LIFETIME
            and 0 <= now.timestamp() - one_end <= 75
            and price > float(metadata["reference_high"])
            and trigger_close > float(metadata["reference_high"])
            and 0 < risk <= price * MAX_STOP_FRACTION
            and (target - price) / risk >= MIN_REWARD_RISK
            and price <= trigger_close + .25 * (trigger_close - stop)
            and bool(metadata.get("setups"))
            and valid_confirmation(metadata)
        )
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


class IndexSwingStrategy:
    def prepare(self, symbol, side, spot_candles, premium_candles, now):
        if symbol not in INDEX_SYMBOLS:
            raise IndicatorDataError("index_unsupported")
        spot = completed_candles(spot_candles, now, timeframe_seconds=300)
        premium = completed_candles(premium_candles, now, timeframe_seconds=300)
        if len(premium) < 12 or not spot:
            raise IndicatorDataError("index_insufficient_history")
        previous, green = premium[-2:]
        end = green["_timestamp_epoch"] + 300
        if (datetime.fromtimestamp(green["_timestamp_epoch"], IST).date() != now.astimezone(IST).date()
                or not 0 <= now.timestamp() - end <= CONFIRMATION_LIFETIME + 75
                or spot[-1]["_timestamp_epoch"] != green["_timestamp_epoch"]
                or green["_timestamp_epoch"] - previous["_timestamp_epoch"] != 300):
            raise IndicatorDataError("index_confirmation_stale")
        if not (previous["close"] < previous["open"] and green["close"] > green["open"]
                and green["close"] > previous["high"]):
            raise IndicatorDataError("index_premium_confirmation")
        volumes = [bar["volume"] for bar in premium[-11:-1]]
        if (green["volume"] is None or any(v is None or v <= 0 for v in volumes)
                or green["volume"] < sum(volumes) / len(volumes)):
            raise IndicatorDataError("index_volume_confirmation")
        context = spot_setups(spot, side, now)
        context["price_action_confirmations"] = confirmations(spot, side)
        if not context["price_action_confirmations"]:
            raise IndicatorDataError("index_price_action_confirmation")
        context["gap_context"] = gap_context(spot, now)
        if (context["spot_next_zone"] is not None
                and abs(context["spot_next_zone"] - context["spot_ltp"]) <= context["spot_ltp"] * .0008):
            raise IndicatorDataError("index_spot_room")
        targets = [high for high in swings(premium[:-1], "high") if high > green["close"]]
        if not targets:
            raise IndicatorDataError("index_no_swing_target")
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
        if last["close"] <= last["open"]:
            raise IndicatorDataError("index_one_minute_not_green")
        try:
            price, bid, ask = (float(quote[key]) for key in ("price", "bid", "ask"))
            stamp = float(quote.get("trade_timestamp", quote.get("timestamp")))
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise IndicatorDataError("index_quote_missing") from exc
        if (not all(math.isfinite(value) and value > 0 for value in (price, bid, ask, stamp))
                or bid > ask or (ask - bid) / ask > .02):
            raise IndicatorDataError("index_spread")
        targets = [level for level in reference["swing_targets"] if level > price]
        if not targets:
            raise IndicatorDataError("index_target_passed")
        signal = {
            "symbol": symbol, "underlying": symbol, "category": "index_options",
            "signal": "CALL" if side == "CE" else "PUT", "option_type": side,
            "trigger": INDEX_TRIGGER, "index_swing": True, "alerts_only": True,
            "timeframe": "5min", "confirmation_end": reference["confirmation_end"],
            "one_minute_start": last["_timestamp_epoch"], "one_minute_close": last["close"],
            "reference_timestamp": reference["timestamp"], "reference_color": "GREEN",
            "reference_high": reference["high"], "reference_low": reference["low"],
            "breakout_timestamp": stamp, "first_crossing_timestamp": stamp,
            "breakout_price": price, "entry": price, "entry_price": price,
            "stop_loss": reference["low"], "target": targets[0], "targets": [targets[0]],
            "risk_points": price - reference["low"], "detected_at": now.isoformat(),
            **{key: reference[key] for key in (
                "setups", "spot_zone", "spot_direction", "spot_ltp", "spot_next_zone",
                "opening_high", "opening_low",
                "price_action_confirmations", "gap_context",
            )},
        }
        if not valid_entry(signal, now):
            raise IndicatorDataError("index_entry_risk_or_timing")
        return signal


def describe_setup(metadata):
    labels = {
        "support_reclaim": "Support held/reclaimed",
        "resistance_rejection": "Resistance rejected / bearish breakdown",
        "opening_range_retest": "Opening-range breakout and retest",
        "shallow_pullback_continuation": "Shallow-pullback continuation",
    }
    setup = ", ".join(labels[name] for name in metadata.get("setups", []) if name in labels)
    zone = metadata.get("spot_zone") or {}
    evidence = ", ".join(dict.fromkeys(
        CONFIRMATION_NAMES[item["kind"]]
        for item in metadata.get("price_action_confirmations", [])
        if item.get("kind") in CONFIRMATION_NAMES
    ))
    gap = metadata.get("gap_context") or {"direction": "unavailable"}
    gap_label = gap["direction"].replace("_", " ")
    if gap.get("percent") is not None:
        gap_label += f" {gap['percent']:+.2f}%"
    return (
        f"Setup: {setup}\n"
        f"Spot: {metadata['spot_direction']} | Zone {zone['low']:.2f}-{zone['high']:.2f}\n"
        f"Price action: {evidence or 'legacy / not recorded'}\n"
        f"Opening: {gap_label} (context only)\n"
        "Confirmation: 5m option close above prior red high, then later green 1m close.\n"
        "Alerts only; stop updates are advisory, not broker orders."
    )
