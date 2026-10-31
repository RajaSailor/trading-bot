"""Observable spot-price proxies, not evidence of institutional positioning."""

from __future__ import annotations

import math
from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
CONFIRMATION_NAMES = {
    "fvg_retest": "Fair value gap retest",
    "order_block_retest": "Order-block retest",
    "liquidity_sweep": "Liquidity sweep and reclaim",
    "supply_demand_retest": "Supply/demand-base retest",
}


def _consecutive(bars):
    return bool(bars) and all(
        right["_timestamp_epoch"] - left["_timestamp_epoch"] == 300
        and datetime.fromtimestamp(right["_timestamp_epoch"], IST).date()
        == datetime.fromtimestamp(left["_timestamp_epoch"], IST).date()
        for left, right in zip(bars, bars[1:])
    )


def _displacement(bar):
    size = bar["high"] - bar["low"]
    return (size > 0 and bar["close"] - bar["open"] >= size * .5
            and bar["close"] >= bar["low"] + .75 * size)


def _pivots(bars, field):
    return [(middle, right["_timestamp_epoch"] + 300)
            for left, middle, right in zip(bars, bars[1:], bars[2:])
            if _consecutive([left, middle, right])
            and (middle[field] < min(left[field], right[field]) if field == "low"
                 else middle[field] > max(left[field], right[field]))]


def confirmations(bars, side):
    """Return retests/sweeps relevant to the latest completed spot confirmation.

    PE is reflected so the same directional rules apply without weaker mirrors.
    Formations must exist before the retest bar starts; invalidated zones expire.
    """
    if side not in ("CE", "PE") or len(bars) < 3:
        return []
    bullish = side == "CE"
    directed = [dict(bar) if bullish else {
        **bar, "open": -bar["open"], "close": -bar["close"],
        "high": -bar["low"], "low": -bar["high"],
    } for bar in bars]
    current = directed[-1]
    if not _consecutive(directed[-2:]):
        return []
    result = []

    def evidence(kind, low, high, formed_at, retest_at):
        actual_low, actual_high = (low, high) if bullish else (-high, -low)
        item = {
            "kind": kind, "side": side, "zone_low": actual_low, "zone_high": actual_high,
            "formed_at": formed_at, "retest_at": retest_at,
            "confirmed_at": current["_timestamp_epoch"] + 300,
        }
        if item not in result:
            result.append(item)

    def retest(kind, low, high, formation_index):
        formed_at = directed[formation_index]["_timestamp_epoch"] + 300
        if current["close"] <= high:
            return
        for touch_index in (len(directed) - 2, len(directed) - 1):
            touch = directed[touch_index]
            if formed_at > touch["_timestamp_epoch"]:
                continue
            if any(bar["close"] < low for bar in directed[formation_index + 1:]):
                continue
            if touch["low"] <= high and touch["high"] >= low and touch["close"] >= low:
                evidence(kind, low, high, formed_at, touch["_timestamp_epoch"])
                return

    # Three same-session consecutive candles only: an overnight gap is not an FVG.
    for index in range(2, len(directed) - 1):
        first, middle, last = directed[index - 2:index + 1]
        if (_consecutive([first, middle, last]) and _displacement(middle)
                and last["low"] > first["high"]):
            retest("fvg_retest", first["high"], last["low"], index)

    for index in range(3, len(directed) - 1):
        impulse = directed[index]
        if not _displacement(impulse):
            continue
        preceding = directed[:index]
        known_highs = _pivots(preceding, "high")
        # Last opposite candle within three contiguous bars before displacement.
        for origin in range(index - 1, max(-1, index - 4), -1):
            block = directed[origin]
            if not _consecutive(directed[origin:index + 1]):
                break
            if block["close"] >= block["open"]:
                continue
            if (known_highs and directed[index - 1]["close"] <= known_highs[-1][0]["high"]
                    < impulse["close"]):
                retest("order_block_retest", block["low"], block["high"], index)
            break
        # A compact 2-3 candle base, followed by a strong close away from it.
        for length in (2, 3):
            base = directed[index - length:index]
            reference = directed[max(0, index - length - 10):index - length]
            if not reference or not _consecutive([*base, impulse]):
                continue
            average_range = sum(bar["high"] - bar["low"] for bar in reference) / len(reference)
            low, high = min(bar["low"] for bar in base), max(bar["high"] for bar in base)
            compact = all(abs(bar["close"] - bar["open"]) <=
                          (bar["high"] - bar["low"]) * .5 for bar in base)
            if (compact and 0 < high - low <= 1.5 * average_range
                    and impulse["close"] > high):
                retest("supply_demand_retest", low, high, index)
                break

    for touch_index in (len(directed) - 2, len(directed) - 1):
        touch = directed[touch_index]
        known = _pivots(directed[:touch_index], "low")
        levels = [(pivot["low"], known_at) for pivot, known_at in known]
        day = datetime.fromtimestamp(touch["_timestamp_epoch"], IST).date()
        session = [bar for bar in directed[:touch_index]
                   if datetime.fromtimestamp(bar["_timestamp_epoch"], IST).date() == day]
        if session:
            first = session[0]
            opening = datetime.fromtimestamp(first["_timestamp_epoch"], IST)
            if (opening.hour, opening.minute) == (9, 15):
                levels.append((first["low"], first["_timestamp_epoch"] + 300))
        for level, formed_at in levels:
            already_broken = any(bar["close"] < level for bar in directed[:touch_index]
                                 if bar["_timestamp_epoch"] >= formed_at)
            if (not already_broken and formed_at <= touch["_timestamp_epoch"]
                    and touch["low"] < level < touch["close"] and current["close"] > level):
                evidence("liquidity_sweep", level, level, formed_at, touch["_timestamp_epoch"])
    return result


def valid_confirmation(metadata):
    """Durable admission rejects missing, stale, future or wrong-side evidence."""
    items = metadata.get("price_action_confirmations")
    if not isinstance(items, list):
        return False
    try:
        end = float(metadata["confirmation_end"])
        for item in items:
            if not isinstance(item, dict):
                continue
            if (item.get("kind") not in CONFIRMATION_NAMES
                    or item.get("side") != metadata.get("option_type")):
                continue
            low, high, formed, retest, confirmed = [
                float(item[key]) for key in
                ("zone_low", "zone_high", "formed_at", "retest_at", "confirmed_at")
            ]
            if (all(math.isfinite(value) and value > 0
                    for value in (low, high, formed, retest, confirmed))
                    and low <= high and formed <= retest
                    and confirmed == end and retest in (end - 600, end - 300)):
                return True
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
    return False


def gap_context(bars, now):
    today = now.astimezone(IST).date()
    session = [bar for bar in bars
               if datetime.fromtimestamp(bar["_timestamp_epoch"], IST).date() == today]
    prior = [bar for bar in bars
             if datetime.fromtimestamp(bar["_timestamp_epoch"], IST).date() < today]
    if not session or not prior:
        return {"direction": "unavailable"}
    opening = datetime.fromtimestamp(session[0]["_timestamp_epoch"], IST)
    if (opening.hour, opening.minute) != (9, 15):
        return {"direction": "unavailable"}
    points = session[0]["open"] - prior[-1]["close"]
    return {
        "direction": "gap_up" if points > 0 else "gap_down" if points < 0 else "flat",
        "points": round(points, 2), "percent": round(100 * points / prior[-1]["close"], 4),
        "previous_close": prior[-1]["close"], "opening_price": session[0]["open"],
    }
