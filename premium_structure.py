"""Premium-only zone, compression, support and structure-retest entry evidence."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_FLOOR
from zoneinfo import ZoneInfo

from premium_indicators import IndicatorDataError, completed_candles
from premium_patterns import swing_points
from premium_risk_filter import minimum_distance_stop

IST = ZoneInfo("Asia/Kolkata")
STRUCTURE_POLICY = "premium_structure_v4"
CANDLE_STOP_POLICY = "completed_5m_low_v1"
SUPPORT_RETEST_TOLERANCE = .10
BOS_RETEST_TOLERANCE = .10
SUPPORT_RECOVERY_PERCENT = Decimal(".03")
STRUCTURE_ROUTES = ("support_5m_recovery", "compression_breakout", "fvg_retest",
                    "order_block_retest", "bos_retest")


def _session(bars, now):
    return [bar for bar in bars
            if datetime.fromtimestamp(bar["_timestamp_epoch"], IST).date() == now.date()]


def _snapshot(bars):
    return [{key: bar[key] for key in ("timestamp", "open", "high", "low", "close", "volume")
             if key in bar} for bar in bars]


def _contiguous(bars, seconds):
    for index in range(len(bars) - 1, 0, -1):
        if bars[index]["_timestamp_epoch"] - bars[index - 1]["_timestamp_epoch"] != seconds:
            return bars[index:]
    return bars


def _target(bars, price, tick, *, above=True):
    highs = [level for _, kind, level in swing_points(bars) if kind == "H" and level > price]
    if not highs:
        return None
    return float(Decimal(str(min(highs))) + (Decimal(str(tick)) if above else Decimal("0")))


def _confirmed_highs(bars):
    return sorted({float(level) for index, kind, level in swing_points(bars)
                   if kind == "H"
                   and bars[index]["_timestamp_epoch"] - bars[index - 1]["_timestamp_epoch"] == 300
                   and bars[index + 1]["_timestamp_epoch"] - bars[index]["_timestamp_epoch"] == 300})


def _tick_down(level, tick):
    tick = Decimal(str(tick))
    return float((Decimal(str(level)) / tick).to_integral_value(rounding=ROUND_FLOOR) * tick)


def _entry_risk_levels(five, price, stamp, now, tick, *, primary=None):
    """Widen the prior 5m low to 5% and select observable swing targets."""
    bars = [bar for bar in completed_candles(five, now, timeframe_seconds=300)
            if bar["_timestamp_epoch"] + 300 <= stamp]
    for index in range(len(bars) - 1, 0, -1):
        if bars[index]["_timestamp_epoch"] - bars[index - 1]["_timestamp_epoch"] != 300:
            bars = bars[index:]
            break
    if not bars or stamp - (bars[-1]["_timestamp_epoch"] + 300) > 375:
        raise IndicatorDataError("index_previous_5m_candle_missing")
    bars = bars[-80:]
    stop = float(bars[-1]["low"])
    if not 0 < stop < price:
        raise IndicatorDataError("index_previous_5m_low_not_below_entry")
    stop = minimum_distance_stop(price, stop, tick)
    risk = Decimal(str(price)) - Decimal(str(stop))
    candidates = {float(level) for _, kind, level in swing_points(bars)
                  if kind == "H" and level > price}
    targets = sorted({_tick_down(level, tick) for level in candidates})
    target = _tick_down(primary, tick) if primary is not None else next((level for level in targets
                   if Decimal(str(level)) - Decimal(str(price)) >= risk), None)
    if target is None or target <= price:
        raise IndicatorDataError("index_no_target_above_1r")
    return stop, target


def _finalize_candidate(candidate, five, price, stamp, now, tick):
    result = dict(candidate)
    support = result["route"] == "support_5m_recovery"
    result["stop"], first = _entry_risk_levels(
        five, price, stamp, now, tick, primary=result["primary_target"] if support else None,
    )
    targets = [first]
    if support:
        bars = [bar for bar in completed_candles(five, now, timeframe_seconds=300)
                if bar["_timestamp_epoch"] + 300 <= stamp]
        highs = sorted({_tick_down(level, tick) for level in _confirmed_highs(bars)})
        higher = next((level for level in highs if level > first), None)
        if higher is not None:
            targets.append(higher)
    result.update(target=targets[-1], targets=targets)
    return result


def prepare_zones(five, now):
    """Recoverable bullish 5m gaps and untouched last-red-candle blocks."""
    bars = _contiguous(_session(completed_candles(five, now, timeframe_seconds=300), now), 300)
    if bars and not 0 <= now.timestamp() - (bars[-1]["_timestamp_epoch"] + 300) <= 375:
        raise IndicatorDataError("index_zone_history_stale")
    zones = []
    for index in range(2, len(bars)):
        left, impulse, right = bars[index - 2:index + 1]
        candidates = []
        if (right["low"] > left["high"] and impulse["close"] > impulse["open"]
                and impulse["close"] - impulse["open"] >= (impulse["high"] - impulse["low"]) * .5):
            candidates.append(("fvg_retest", left["high"], right["low"], left["_timestamp_epoch"]))
        prior_highs = [p for p in swing_points(bars[:index]) if p[1] == "H"]
        if (prior_highs and right["close"] > right["open"]
                and right["close"] > prior_highs[-1][2] >= impulse["close"]):
            red = next((bar for bar in reversed(bars[max(0, index - 5):index])
                        if bar["close"] < bar["open"]), None)
            if red:
                candidates.append(("order_block_retest", red["low"], red["high"],
                                   red["_timestamp_epoch"]))
        for route, low, high, source in candidates:
            if any((bar["close"] < low if route == "fvg_retest" else bar["low"] <= high)
                   for bar in bars[index + 1:]):
                continue
            if right["close"] <= high:
                continue
            zones.append({
                "route": route, "low": low, "high": high, "source_start": source,
                "known_at": right["_timestamp_epoch"] + 300,
                "formation_start": right["_timestamp_epoch"],
            })
    return zones


def compression_candidate(five, one, now):
    bars = _contiguous(_session(completed_candles(five, now, timeframe_seconds=300), now), 300)
    if len(bars) < 3:
        raise IndicatorDataError("index_compression_history_missing")
    trigger = bars[-1]
    end = trigger["_timestamp_epoch"] + 300
    if not 0 <= now.timestamp() - end <= 10:
        raise IndicatorDataError("index_compression_open_window_missed")
    zone, midpoint = [], None
    for start in range(len(bars) - 2):
        trial = bars[start:-1]
        anchor = sum((Decimal(str(bar["high"])) + Decimal(str(bar["low"]))) / 2
                     for bar in trial[:2]) / 2
        if (all(Decimal(str(bar["low"])) <= anchor <= Decimal(str(bar["high"]))
                for bar in trial) and Decimal(str(trigger["low"])) > anchor):
            zone, midpoint = trial, float(anchor)
            break
    if not zone:
        raise IndicatorDataError("index_compression_range_missing")
    low, high = min(bar["low"] for bar in zone), max(bar["high"] for bar in zone)
    start = zone[0]["_timestamp_epoch"]
    target = _target(bars, trigger["close"], .05, above=False)
    if target is None:
        raise IndicatorDataError("index_compression_target_missing")
    return {
        "route": "compression_breakout", "low": low, "high": high,
        "boundary": midpoint, "midpoint": midpoint, "stop": trigger["low"], "target": target,
        "source_start": start, "known_at": zone[-1]["_timestamp_epoch"] + 300,
        "trigger_start": trigger["_timestamp_epoch"], "trigger_end": end,
        "trigger_close": trigger["close"], "range_candles": len(zone),
    }


def _entry_low(five, one, stamp, now):
    start = stamp - ((stamp - now.replace(hour=9, minute=15, second=0, microsecond=0).timestamp()) % 300)
    observed = completed_candles(one, now + timedelta(seconds=60), timeframe_seconds=60)
    observed = [bar for bar in observed if start <= bar["_timestamp_epoch"] <= stamp]
    raw_five = completed_candles(five, now + timedelta(seconds=300), timeframe_seconds=300)
    observed.extend(bar for bar in raw_five if bar["_timestamp_epoch"] == start)
    if not observed:
        raise IndicatorDataError("index_zone_entry_candle_missing")
    return start, min(bar["low"] for bar in observed)


def bos_candidate(five, one, now, tick, price, stamp, previous_observation=None):
    bars = _contiguous(_session([
        bar for bar in completed_candles(five, now, timeframe_seconds=300)
        if bar["_timestamp_epoch"] + 300 <= stamp
    ], now), 300)
    if len(bars) < 7 or not 0 <= now.timestamp() - (bars[-1]["_timestamp_epoch"] + 300) <= 375:
        raise IndicatorDataError("index_bos_history_or_close_stale")
    for index in range(len(bars) - 2, 3, -1):
        prior = bars[max(0, index - 10):index]
        breakout = bars[index]
        highs = [p for p in swing_points(prior) if p[1] == "H"]
        lows = [p for p in swing_points([*prior, breakout]) if p[1] == "L"]
        if len(highs) < 2 or len(lows) < 2:
            continue
        continuation = highs[-1][2] > highs[-2][2] and lows[-1][2] > lows[-2][2]
        reversal = highs[-1][2] < highs[-2][2] and lows[-1][2] < lows[-2][2]
        if not (continuation or reversal):
            continue
        level = highs[-1][2]
        if not breakout["close"] > level >= bars[index - 1]["close"]:
            continue
        following = bars[index + 1:]
        rejection = next((bar for bar in reversed(following)
                          if level * (1 - BOS_RETEST_TOLERANCE) <= bar["low"]
                          <= level * (1 + BOS_RETEST_TOLERANCE)
                          and level <= bar["close"] <= level * (1 + BOS_RETEST_TOLERANCE)
                          and bar["low"] < min(bar["open"], bar["close"])), None)
        if (rejection is None or any(
                bar["close"] < level * (1 - BOS_RETEST_TOLERANCE) for bar in following)
                or not level < price <= level * (1 + BOS_RETEST_TOLERANCE)):
            continue
        rejection_end = rejection["_timestamp_epoch"] + 300
        if stamp < rejection_end:
            continue
        minutes = [bar for bar in completed_candles(one, now, timeframe_seconds=60)
                   if rejection_end <= bar["_timestamp_epoch"]
                   and bar["_timestamp_epoch"] + 60 <= stamp]
        confirmation = next((bar for bar in minutes if bar["close"] > rejection["high"]), None)
        if (confirmation is None or price <= rejection["high"]
                or not 0 <= stamp - (confirmation["_timestamp_epoch"] + 60) <= 75):
            continue
        return {
            "route": "bos_retest",
            "low": level * (1 - BOS_RETEST_TOLERANCE),
            "high": level * (1 + BOS_RETEST_TOLERANCE),
            "boundary": rejection["high"], "stop": rejection["low"],
            "structure_level": level,
            "structure_break": "bullish_bos" if continuation else "bullish_choch",
            "target": float(Decimal(str(highs[-2][2])) + Decimal(str(tick))),
            "source_start": prior[highs[-1][0]]["_timestamp_epoch"],
            "known_at": breakout["_timestamp_epoch"] + 300,
            "rejection_start": rejection["_timestamp_epoch"],
            "trigger_start": confirmation["_timestamp_epoch"],
            "trigger_end": confirmation["_timestamp_epoch"] + 60,
            "trigger_close": confirmation["close"],
            "previous_observation": previous_observation,
        }
    raise IndicatorDataError("index_bos_rejection_reclaim_missing")


def support_candidate(five, reference, now):
    """The legacy reference argument is retained for call-site compatibility."""
    bars = _contiguous(_session(completed_candles(five, now, timeframe_seconds=300), now), 300)
    if len(bars) < 2 or not 0 <= now.timestamp() - (bars[-1]["_timestamp_epoch"] + 300) <= 75:
        raise IndicatorDataError("index_support_5m_close_stale")
    trigger = bars[-1]
    points = swing_points(bars[:-1])
    for index, kind, support in reversed(points):
        if kind != "L":
            continue
        rebounds = [point for point in points if point[1] == "H" and point[0] > index]
        if not rebounds:
            continue
        peak_index, _, target = rebounds[0]
        recovery = Decimal(str(support)) * (1 + SUPPORT_RECOVERY_PERCENT)
        if Decimal(str(trigger["close"])) < recovery or target <= trigger["close"]:
            continue
        later = bars[peak_index + 1:]
        # A second test must be separated by a confirmed bounce, not adjacent lows.
        retest = next((bar for offset, bar in enumerate(later, peak_index + 1)
                       if support * (1 - SUPPORT_RETEST_TOLERANCE) <= bar["low"]
                       <= support * (1 + SUPPORT_RETEST_TOLERANCE)
                       and bar["low"] <= bars[offset - 1]["low"]
                       and (offset == len(bars) - 1 or bar["low"] <= bars[offset + 1]["low"])), None)
        if retest is None or any(bar["close"] < support for bar in bars[index + 1:]):
            continue
        if any(Decimal(str(bar["close"])) >= recovery
               for bar in later if retest["_timestamp_epoch"] <= bar["_timestamp_epoch"]
               < trigger["_timestamp_epoch"]):
            continue
        return {
            "route": "support_5m_recovery", "low": support, "high": support,
            "boundary": float(recovery), "stop": trigger["low"],
            "target": target, "primary_target": target,
            "source_start": bars[index]["_timestamp_epoch"],
            "known_at": bars[peak_index + 1]["_timestamp_epoch"] + 300,
            "support_setup": "premium_second_touch_recovery",
            "retest_start": retest["_timestamp_epoch"],
            "trigger_start": trigger["_timestamp_epoch"], "trigger_end": trigger["_timestamp_epoch"] + 300,
            "trigger_close": trigger["close"],
        }
    raise IndicatorDataError("index_support_5m_retest_missing")


def zone_candidate(zone, five, one, price, stamp, now, tick):
    if zone["route"] == "fvg_retest":
        return _fvg_candidate(zone, five, one, price, stamp, now, tick)
    if not zone["known_at"] <= stamp or not zone["low"] <= price <= zone["high"]:
        raise IndicatorDataError("index_zone_not_touched")
    # Use observed current 5m lows; never manufacture a candle low from entry.
    start, stop = _entry_low(five, one, stamp, now)
    if not stop < price:
        raise IndicatorDataError("index_zone_entry_low_not_below_price")
    bars = _contiguous(_session(completed_candles(five, now, timeframe_seconds=300), now), 300)
    target = _target(bars, price, tick)
    if target is None:
        raise IndicatorDataError("index_zone_target_missing")
    return {
        **zone, "stop": stop, "target": target,
        "trigger_start": start, "trigger_end": stamp,
        "trigger_close": price,
    }


def _fvg_candidate(zone, five, one, price, stamp, now, tick):
    bars = _contiguous(_session([
        bar for bar in completed_candles(five, now, timeframe_seconds=300)
        if bar["_timestamp_epoch"] + 300 <= stamp
    ], now), 300)
    highs = [level for _, kind, level in swing_points(bars) if kind == "H"]
    lows = [level for _, kind, level in swing_points(bars) if kind == "L"]
    if len(highs) < 2 or len(lows) < 2 or highs[-1] <= highs[-2] or lows[-1] <= lows[-2]:
        raise IndicatorDataError("index_fvg_bullish_structure_missing")
    minutes = [bar for bar in completed_candles(one, now, timeframe_seconds=60)
               if zone["known_at"] <= bar["_timestamp_epoch"]
               and bar["_timestamp_epoch"] + 60 <= stamp]
    touch = next((i for i, bar in enumerate(minutes)
                  if bar["low"] <= zone["high"] and bar["high"] >= zone["low"]), None)
    if touch is None:
        raise IndicatorDataError("index_fvg_retest_missing")
    after = minutes[touch:]
    if any(bar["close"] < zone["low"] for bar in after):
        raise IndicatorDataError("index_fvg_support_failed")
    trigger = next((bar for bar in after[1:]
                    if bar["close"] > zone["high"] and bar["close"] > bar["open"]), None)
    if trigger is None or price <= zone["high"]:
        raise IndicatorDataError("index_fvg_confirmation_missing")
    end = trigger["_timestamp_epoch"] + 60
    if not 0 <= stamp - end <= 75:
        raise IndicatorDataError("index_fvg_confirmation_stale")
    target = _target(bars, price, tick, above=False)
    if target is None:
        raise IndicatorDataError("index_fvg_target_missing")
    return {
        **zone, "boundary": zone["high"], "stop": bars[-1]["low"],
        "target": target, "retest_start": minutes[touch]["_timestamp_epoch"],
        "trigger_start": trigger["_timestamp_epoch"], "trigger_end": end,
        "trigger_close": trigger["close"],
    }


def build_signal(symbol, side, candidate, five, one, quote, now, tick, category="index_options"):
    from index_swing_strategy import INDEX_TRIGGER, _quote_values, premium_exchange
    premium_exchange(symbol, category)
    price, stamp = _quote_values(quote, now)
    candidate = _finalize_candidate(candidate, five, price, stamp, now, tick)
    if candidate["trigger_end"] > stamp:
        raise IndicatorDataError("index_confirmation_after_quote")
    if not 0 < candidate["stop"] < price < candidate["target"]:
        raise IndicatorDataError("index_structure_stop_target_invalid")
    if (candidate["route"] not in ("fvg_retest", "order_block_retest")
            and price < candidate.get("boundary", candidate["high"])):
        raise IndicatorDataError("index_structure_recovery_lost")
    if candidate["route"] == "compression_breakout" and not (
            candidate["trigger_end"] <= stamp <= candidate["trigger_end"] + 10):
        raise IndicatorDataError("index_compression_open_window_missed")
    identity = {key: candidate[key] for key in (
        "route", "source_start", "known_at", "low", "high",
    )}
    # Zones and BOS levels alert once; later rejection candles must not create new alerts.
    if candidate["route"] not in ("fvg_retest", "order_block_retest", "bos_retest"):
        identity["trigger_start"] = candidate["trigger_start"]
    route_id = hashlib.sha256(json.dumps(identity, sort_keys=True).encode("ascii")).hexdigest()[:24]
    chart = _session(completed_candles(five, now, timeframe_seconds=300), now)
    # Keep only the sessions needed for these routes, not ten-session pattern history.
    recent_five = five
    current_one = _session(completed_candles(one, now + timedelta(seconds=60), timeframe_seconds=60), now)
    current_one = [bar for bar in current_one if bar["_timestamp_epoch"] <= stamp]
    normalized_five = completed_candles(recent_five, now + timedelta(seconds=300), timeframe_seconds=300)
    normalized_five = [bar for bar in normalized_five if bar["_timestamp_epoch"] <= stamp]
    evidence = {"five": _snapshot(normalized_five), "one": _snapshot(current_one), "tick": tick,
                "candidate": candidate}
    return {
        "symbol": symbol, "underlying": symbol, "category": category,
        "signal": "CALL" if side == "CE" else "PUT", "option_type": side,
        "trigger": INDEX_TRIGGER, "index_swing": True, "alerts_only": True,
        "entry_policy": STRUCTURE_POLICY, "requested_stop_policy": CANDLE_STOP_POLICY,
        "setups": [candidate["route"]], "timeframe": "5min",
        "breakout_timestamp": stamp, "first_crossing_timestamp": stamp,
        "breakout_price": price, "entry": price, "entry_price": price,
        "reference_timestamp": candidate["trigger_start"], "reference_high": candidate["high"],
        "reference_low": candidate["stop"], "confirmation_end": candidate["trigger_end"],
        "stop_loss": candidate["stop"], "target": candidate["target"], "targets": candidate["targets"],
        "risk_points": price - candidate["stop"], "strike_band": "ATM", "detected_at": now.isoformat(),
        "structure_id": route_id, "structure_evidence": evidence, "chart_candles": _snapshot(chart),
        "premium_zone": {"low": candidate["low"], "high": candidate["high"],
                         "label": candidate["route"].replace("_", " "),
                         "known_at": candidate["known_at"]},
        **{key: candidate[key] for key in ("previous_session_date", "premium_reference_levels")
           if key in candidate},
    }


def valid_structure_entry(metadata, now):
    try:
        candidate = metadata["structure_evidence"]["candidate"]
        route = candidate["route"]
        price, stamp = float(metadata["breakout_price"]), float(metadata["breakout_timestamp"])
        if not (
            metadata.get("index_swing") is True and metadata.get("alerts_only") is True
            and metadata.get("trigger") == "index_swing_confirmed"
            and metadata.get("setups") == [route] and route in STRUCTURE_ROUTES
            and metadata.get("requested_stop_policy") == CANDLE_STOP_POLICY
            and metadata.get("timeframe") == "5min"
            and math.isfinite(price) and math.isfinite(stamp)
            and 0 <= now.timestamp() - stamp <= 10
            and metadata["reference_low"] == candidate["stop"]
            and metadata["reference_high"] == candidate["high"]
            and metadata["target"] == candidate["target"]
            and 0 < candidate["stop"] < price < candidate["target"]
        ):
            return False
        evidence = metadata["structure_evidence"]
        five, one, tick = evidence["five"], evidence["one"], evidence["tick"]
        symbol = metadata.get("symbol", metadata.get("underlying"))
        category = metadata.get("category", "index_options")
        if route == "compression_breakout":
            expected = compression_candidate(five, one, now)
        elif route == "bos_retest":
            expected = bos_candidate(five, one, now, tick, price, stamp, candidate["previous_observation"])
        elif route == "support_5m_recovery":
            expected = support_candidate(five, None, now)
        else:
            zones = prepare_zones(five, now)
            zone = next((item for item in zones if item["route"] == route
                         and item["source_start"] == candidate["source_start"]
                         and item["known_at"] == candidate["known_at"]), None)
            if zone is None:
                return False
            expected = zone_candidate(zone, five, one, price, stamp, now, tick)
        expected = _finalize_candidate(expected, five, price, stamp, now, tick)
        if expected != candidate:
            return False
        if metadata.get("targets") != expected["targets"]:
            return False
        signal = build_signal(symbol, metadata["option_type"], candidate, five, one,
                              {"price": price, "bid": price, "ask": price, "trade_timestamp": stamp},
                              now, tick, category)
        return (
            metadata["structure_id"] == signal["structure_id"]
            and metadata["premium_zone"] == signal["premium_zone"]
            and metadata["reference_timestamp"] == candidate["trigger_start"]
            and metadata["confirmation_end"] == candidate["trigger_end"]
            and metadata["chart_candles"] == signal["chart_candles"]
        )
    except (KeyError, TypeError, ValueError, OverflowError, IndicatorDataError, StopIteration):
        return False


def evaluate_structure(symbol, side, five, one, quote, now, tick, category="index_options"):
    from index_swing_strategy import _quote_values, premium_exchange
    premium_exchange(symbol, category)
    if side not in ("CE", "PE"):
        raise IndicatorDataError("index_unsupported")
    if tick is None or not math.isfinite(float(tick)) or float(tick) <= 0:
        raise IndicatorDataError("index_tick_missing")
    signals, diagnostics = [], []
    zone_error = None
    try:
        zones = prepare_zones(five, now)
    except IndicatorDataError as exc:
        zones, zone_error = [], exc.reason
    for route in STRUCTURE_ROUTES:
        diagnostic = {"route": route, "result": "REJECTED"}
        try:
            if route == "support_5m_recovery":
                candidate = support_candidate(five, None, now)
            elif route == "compression_breakout":
                candidate = compression_candidate(five, one, now)
            elif route == "bos_retest":
                price, stamp = _quote_values(quote, now)
                candidate = bos_candidate(five, one, now, tick, price, stamp)
            else:
                if zone_error:
                    raise IndicatorDataError(zone_error)
                available = [zone for zone in zones if zone["route"] == route]
                diagnostic["zones"] = available
                if not available:
                    raise IndicatorDataError("index_zone_missing")
                price, stamp = _quote_values(quote, now)
                zone = next((zone for zone in reversed(available)
                             if zone["low"] <= price <= zone["high"]
                             or zone["route"] == "fvg_retest" and price > zone["high"]), None)
                if zone is None:
                    raise IndicatorDataError("index_zone_not_touched")
                candidate = zone_candidate(zone, five, one, price, stamp, now, tick)
            signal = build_signal(symbol, side, candidate, five, one, quote, now, tick, category)
            if not valid_structure_entry(signal, now):
                raise IndicatorDataError("index_structure_evidence_invalid")
            signals.append(signal)
            diagnostic.update(result="QUALIFIED", entry_premium=signal["entry"],
                              stop=signal["stop_loss"], target=signal["target"])
        except IndicatorDataError as exc:
            diagnostic["reason"] = exc.reason
        diagnostics.append(diagnostic)
    return signals, diagnostics, zones
