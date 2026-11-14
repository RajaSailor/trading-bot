"""Premium-only zone, compression, support and structure-retest entry evidence."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from premium_indicators import IndicatorDataError, completed_candles
from premium_patterns import swing_points

IST = ZoneInfo("Asia/Kolkata")
STRUCTURE_POLICY = "premium_structure_v3"
CANDLE_STOP_POLICY = "completed_5m_low_v1"
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


def prepare_zones(five, now):
    """Unmitigated bullish 5m gaps and last-red-candle blocks before a BOS."""
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
            if any(bar["low"] <= high for bar in bars[index + 1:]):
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
    minutes = _session(completed_candles(one, now, timeframe_seconds=60), now)
    if not minutes:
        raise IndicatorDataError("index_compression_one_minute_missing")
    trigger = minutes[-1]
    if not 0 <= now.timestamp() - (trigger["_timestamp_epoch"] + 60) <= 75:
        raise IndicatorDataError("index_compression_close_stale")
    # Freeze the range before the breakout minute, including at a 5m boundary.
    before = datetime.fromtimestamp(trigger["_timestamp_epoch"], IST)
    bars = _contiguous(_session(completed_candles(five, before, timeframe_seconds=300), now), 300)
    if len(bars) < 3:
        raise IndicatorDataError("index_compression_history_missing")
    zone = []
    for bar in reversed(bars):
        trial = [bar, *zone]
        low, high = min(item["low"] for item in trial), max(item["high"] for item in trial)
        if high - low > low * .10:
            break
        zone = trial
    if len(zone) < 2:
        raise IndicatorDataError("index_compression_range_missing")
    low, high = min(bar["low"] for bar in zone), max(bar["high"] for bar in zone)
    start = zone[0]["_timestamp_epoch"]
    prior = [bar for bar in bars if bar["_timestamp_epoch"] < start]
    target = _target([*prior, zone[0]], high, .05, above=False)
    if target is None:
        raise IndicatorDataError("index_compression_decline_or_target_missing")
    after_range = [bar for bar in minutes
                   if zone[-1]["_timestamp_epoch"] + 300 <= bar["_timestamp_epoch"]
                   < trigger["_timestamp_epoch"]]
    if any(bar["close"] > high or bar["close"] < low for bar in after_range):
        raise IndicatorDataError("index_compression_already_broken")
    if trigger["close"] <= high or trigger["low"] < low:
        raise IndicatorDataError("index_compression_breakout_missing")
    return {
        "route": "compression_breakout", "low": low, "high": high, "stop": low, "target": target,
        "source_start": start, "known_at": zone[-1]["_timestamp_epoch"] + 300,
        "trigger_start": trigger["_timestamp_epoch"], "trigger_end": trigger["_timestamp_epoch"] + 60,
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
    bars = _contiguous(_session(completed_candles(five, now, timeframe_seconds=300), now), 300)
    if len(bars) < 7 or not 0 <= now.timestamp() - (bars[-1]["_timestamp_epoch"] + 300) <= 375:
        raise IndicatorDataError("index_bos_history_or_close_stale")
    for index in range(len(bars) - 2, 3, -1):
        prior = bars[:index]
        highs = [p for p in swing_points(prior) if p[1] == "H"]
        if len(highs) < 2 or highs[-1][2] >= highs[-2][2]:
            continue
        level = highs[-1][2]
        breakout = bars[index]
        if not breakout["close"] > level >= bars[index - 1]["close"]:
            continue
        following = bars[index + 1:]
        rejection = next((bar for bar in reversed(following)
                          if level * .995 <= bar["low"] <= level * 1.005
                          and level * .995 <= bar["close"] <= level * 1.005
                          and bar["low"] < min(bar["open"], bar["close"])), None)
        if (rejection is None or any(bar["close"] < level * .995 for bar in following)
                or not level < price <= level * 1.005):
            continue
        rejection_end = rejection["_timestamp_epoch"] + 300
        if stamp < rejection_end:
            continue
        if previous_observation is None:
            minutes = completed_candles(one, now, timeframe_seconds=60)
            minutes = [bar for bar in minutes if rejection_end <= bar["_timestamp_epoch"] + 60 < stamp]
            if not minutes:
                continue
            previous_price, previous_stamp = minutes[-1]["close"], minutes[-1]["_timestamp_epoch"] + 60
            if not 0 <= stamp - previous_stamp <= 75:
                continue
        else:
            previous_price, previous_stamp = previous_observation
            if (not all(math.isfinite(v) and v > 0 for v in (previous_price, previous_stamp))
                    or not 0 < stamp - previous_stamp <= 10 or previous_stamp < rejection_end):
                continue
        if previous_price > level:
            continue
        start, stop = _entry_low(five, one, stamp, now)
        return {
            "route": "bos_retest", "low": level * .995, "high": level * 1.005,
            "boundary": level, "stop": stop,
            "target": float(Decimal(str(highs[-2][2])) + Decimal(str(tick))),
            "source_start": prior[highs[-1][0]]["_timestamp_epoch"],
            "known_at": breakout["_timestamp_epoch"] + 300,
            "rejection_start": rejection["_timestamp_epoch"],
            "trigger_start": start, "trigger_end": stamp, "trigger_close": price,
            "previous_observation": previous_observation,
        }
    raise IndicatorDataError("index_bos_rejection_reclaim_missing")


def support_candidate(five, reference, now):
    bars = _contiguous(_session(completed_candles(five, now, timeframe_seconds=300), now), 300)
    if len(bars) < 2 or not 0 <= now.timestamp() - (bars[-1]["_timestamp_epoch"] + 300) <= 75:
        raise IndicatorDataError("index_support_5m_close_stale")
    previous, trigger = bars[-2:]
    if trigger["close"] <= previous["high"]:
        raise IndicatorDataError("index_support_5m_recovery_missing")
    for item in reference["candidates"]:
        low = min(previous["low"], trigger["low"])
        support = item["support"]
        if item["known_at"] > previous["_timestamp_epoch"]:
            continue
        if not support * .995 <= low <= support * 1.005:
            continue
        if trigger["close"] <= support or item["target"] <= trigger["close"]:
            continue
        return {
            "route": "support_5m_recovery", "low": support, "high": support,
            "boundary": previous["high"],
            "stop": item["stop"], "target": item["target"],
            "source_start": item["support_candle"]["_timestamp_epoch"],
            "known_at": item["known_at"], "support_setup": item["setup"],
            "trigger_start": trigger["_timestamp_epoch"], "trigger_end": trigger["_timestamp_epoch"] + 300,
            "trigger_close": trigger["close"],
            **{key: item[key] for key in ("previous_session_date", "premium_reference_levels")
               if key in item},
        }
    raise IndicatorDataError("index_support_5m_retest_missing")


def zone_candidate(zone, five, one, price, stamp, now, tick):
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


def build_signal(symbol, side, candidate, five, one, quote, now, tick, category="index_options"):
    from index_swing_strategy import INDEX_TRIGGER, _quote_values, premium_exchange
    premium_exchange(symbol, category)
    price, stamp = _quote_values(quote, now)
    if not 0 < candidate["stop"] < price < candidate["target"]:
        raise IndicatorDataError("index_structure_stop_target_invalid")
    if (candidate["route"] not in ("fvg_retest", "order_block_retest")
            and price <= candidate.get("boundary", candidate["high"])):
        raise IndicatorDataError("index_structure_recovery_lost")
    identity = {key: candidate[key] for key in (
        "route", "source_start", "known_at", "low", "high",
    )}
    # Zones alert once per zone; candle-triggered routes need a distinct trigger.
    if candidate["route"] not in ("fvg_retest", "order_block_retest", "bos_retest"):
        identity["trigger_start"] = candidate["trigger_start"]
    if candidate["route"] == "bos_retest":
        identity["rejection_start"] = candidate["rejection_start"]
    route_id = hashlib.sha256(json.dumps(identity, sort_keys=True).encode("ascii")).hexdigest()[:24]
    chart = _session(completed_candles(five, now, timeframe_seconds=300), now)
    # Keep only the sessions needed for these routes, not ten-session pattern history.
    recent_five = five[-361:]
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
        "stop_loss": candidate["stop"], "target": candidate["target"], "targets": [candidate["target"]],
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
            from index_swing_strategy import IndexSwingStrategy
            reference = IndexSwingStrategy().prepare(symbol, metadata["option_type"], [], five, now, category)
            expected = support_candidate(five, reference, now)
        else:
            zones = prepare_zones(five, now)
            zone = next((item for item in zones if item["route"] == route
                         and item["source_start"] == candidate["source_start"]
                         and item["known_at"] == candidate["known_at"]), None)
            if zone is None:
                return False
            expected = zone_candidate(zone, five, one, price, stamp, now, tick)
        if expected != candidate:
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
    from index_swing_strategy import IndexSwingStrategy, _quote_values, premium_exchange
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
                reference = IndexSwingStrategy().prepare(symbol, side, [], five, now, category)
                candidate = support_candidate(five, reference, now)
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
                             if zone["low"] <= price <= zone["high"]), None)
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
