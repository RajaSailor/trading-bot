"""Completed-candle, alerts-only index option pullback entries."""

from __future__ import annotations

import math
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from exchange_calendar import previous_trading_day
from premium_indicators import IndicatorDataError, completed_candles
from premium_patterns import (
    PATTERN_NAMES, PATTERN_POLICY, PATTERN_TIMEFRAMES, bullish_patterns, contiguous_pattern_bars, scan_patterns,
    swing_points as _swing_points,
)

IST = ZoneInfo("Asia/Kolkata")
INDEX_SYMBOLS = frozenset({"NIFTY", "BANKNIFTY", "SENSEX"})
INDEX_TRIGGER = "index_swing_confirmed"
MAX_QUOTE_AGE = 10
SUPPORT_TOLERANCE = .005
RESISTANCE_TOLERANCE = .01
PREMIUM_POLICY = "premium_support_recovery_v2"


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
    if metadata.get("entry_policy") == PATTERN_POLICY:
        return _valid_pattern_entry(metadata, now)
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
        premium_only = metadata.get("entry_policy") == PREMIUM_POLICY
        if premium_only:
            previous_session = metadata.get("setups") == ["premium_previous_session_recovery"]
            support = float(metadata["premium_support"])
            tested_low = float(metadata["retest_low"])
            previous_start = float(metadata["previous_minute_start"])
            support_end = float(metadata["support_known_at"])
            recovery_open = float(metadata["one_minute_open"])
            support_candle = float(metadata["support_candle_timestamp"])
            confirmation_low = float(metadata["premium_confirmation_low"])
            if not (
                math.isfinite(support) and support > 0
                and math.isfinite(tested_low) and tested_low > 0
                and math.isfinite(support_end) and support_end > 0
                and support_end <= previous_start
                and support_end <= five_end
                and math.isfinite(recovery_open) and recovery_open > 0
                and trigger_close > recovery_open
                and math.isfinite(support_candle) and support_candle > 0
                and support_candle + 300 <= support_end
                and stop == confirmation_low
                and (previous_session or 0 <= now.timestamp() - five_end <= 375)
                and previous_start + 60 == one_start
                and tested_low <= support * (1 + SUPPORT_TOLERANCE)
                and trigger_close > support
                and metadata.get("setups") in (
                    ["premium_support_retest"], ["premium_average_reclaim"],
                    ["premium_previous_session_recovery"],
                )
            ):
                return False
            if (metadata["setups"] != ["premium_average_reclaim"]
                    and tested_low < support * (1 - SUPPORT_TOLERANCE)):
                return False
            if metadata["setups"] == ["premium_average_reclaim"] and tested_low > support:
                return False
            if previous_session:
                exchange = "BSE" if metadata.get("symbol", metadata.get("underlying")) == "SENSEX" else "NSE"
                day = previous_trading_day(exchange, now.astimezone(IST).date())
                if (metadata.get("previous_session_date") != day.isoformat()
                        or datetime.fromtimestamp(support_candle, IST).date() != day
                        or datetime.fromtimestamp(support_end - 1, IST).date() != day):
                    return False
        return (
            metadata.get("index_swing") is True and metadata.get("alerts_only") is True
            and metadata.get("trigger") == INDEX_TRIGGER
            and 0 <= now.timestamp() - stamp <= MAX_QUOTE_AGE
            and (premium_only or five_end <= one_start) and one_end <= now.timestamp()
            and 0 <= now.timestamp() - one_end <= 75
            and (price > max(support, pullback_high) if premium_only else price > trigger_close)
            and trigger_close > pullback_high
            and 0 < stop < price < target
            and bool(metadata.get("setups"))
        )
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def _quote_values(quote, now):
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
    return price, stamp


def _valid_pattern_entry(metadata, now):
    try:
        pattern = metadata["premium_pattern"]
        minutes = pattern["timeframe"]
        if minutes not in PATTERN_TIMEFRAMES or pattern["name"] not in PATTERN_NAMES:
            return False
        bars = completed_candles(metadata["chart_candles"], now, timeframe_seconds=minutes * 60)
        exchange = "BSE" if metadata.get("symbol", metadata.get("underlying")) == "SENSEX" else "NSE"
        bars = contiguous_pattern_bars(bars, minutes, exchange)
        matches = bullish_patterns(bars, minutes)
        match = next((item for item in matches if item == pattern), None)
        price, stamp = float(metadata["breakout_price"]), float(metadata["breakout_timestamp"])
        return (
            match is not None
            and metadata.get("index_swing") is True and metadata.get("alerts_only") is True
            and metadata.get("trigger") == INDEX_TRIGGER
            and metadata.get("setups") == ["premium_chart_pattern"]
            and metadata.get("timeframe") == f"{minutes}min"
            and math.isfinite(price) and math.isfinite(stamp)
            and 0 <= now.timestamp() - stamp <= MAX_QUOTE_AGE
            and 0 <= now.timestamp() - pattern["confirmed_end"] <= 75
            and datetime.fromtimestamp(pattern["confirmed_start"], IST).date() == now.date()
            and metadata["reference_high"] == pattern["boundary"]
            and metadata["reference_low"] == pattern["stop"]
            and metadata["target"] == pattern["target"]
            and 0 < pattern["stop"] < price < pattern["target"]
            and price > pattern["boundary"]
        )
    except (KeyError, TypeError, ValueError, OverflowError, IndicatorDataError):
        return False


class IndexSwingStrategy:
    def prepare(self, symbol, side, spot_candles, premium_candles, now):
        try:
            reference = self._prepare_intraday(symbol, side, spot_candles, premium_candles, now)
        except IndicatorDataError as exc:
            if exc.reason not in {
                "index_insufficient_history", "index_confirmation_stale",
                "index_premium_gap", "index_premium_support_missing",
            }:
                raise
            reference = None
            intraday_error = exc
        bars = completed_candles(premium_candles, now, timeframe_seconds=300)
        exchange = "BSE" if symbol == "SENSEX" else "NSE"
        day = previous_trading_day(exchange, now.astimezone(IST).date())
        prior = [bar for bar in bars
                 if datetime.fromtimestamp(bar["_timestamp_epoch"], IST).date() == day]
        if prior:
            known_at = prior[-1]["_timestamp_epoch"] + 300
            pivots = [
                point for point in _swing_points(prior)
                if prior[point[0]]["_timestamp_epoch"] - prior[point[0] - 1]["_timestamp_epoch"] == 300
                and prior[point[0] + 1]["_timestamp_epoch"] - prior[point[0]]["_timestamp_epoch"] == 300
            ]
            low_index = min(range(len(prior)), key=lambda i: prior[i]["low"])
            high_index = max(range(len(prior)), key=lambda i: prior[i]["high"])
            levels = [(i, "support" if kind == "L" else "resistance", price)
                      for i, kind, price in pivots]
            levels.extend([(low_index, "support", prior[low_index]["low"]),
                           (high_index, "resistance", prior[high_index]["high"])])
            resistances = sorted({price for _, kind, price in levels if kind == "resistance"})
            candidates = []
            seen = set()
            for index, kind, level in levels:
                if level in seen:
                    continue
                seen.add(level)
                target = next((price for price in resistances
                               if price > level * (1 + SUPPORT_TOLERANCE)), None)
                if target is None:
                    continue
                candidates.append({
                    "setup": "premium_previous_session_recovery", "support": level,
                    "stop": prior[index]["low"], "target": target, "known_at": known_at,
                    "support_candle": prior[index], "pullback_lows": [level],
                    "previous_session_date": day.isoformat(), "previous_level_kind": kind,
                    "premium_reference_levels": [
                        {"price": prior[low_index]["low"], "label": "Previous session low"},
                        {"price": prior[high_index]["high"], "label": "Previous session high"},
                    ],
                })
            if candidates:
                if reference is None:
                    reference = {
                        "candle": prior[-1], "timestamp": prior[-1]["timestamp"], "armed": True,
                        "confirmation_end": known_at, "candidates": [], "setups": [],
                    }
                reference["candidates"].extend(candidates)
                reference["setups"] = sorted({item["setup"] for item in reference["candidates"]})
        if reference is None:
            raise intraday_error
        return reference

    def _prepare_intraday(self, symbol, side, spot_candles, premium_candles, now):
        # spot_candles is retained only for call-site compatibility, never evaluated.
        if symbol not in INDEX_SYMBOLS or side not in ("CE", "PE"):
            raise IndicatorDataError("index_unsupported")
        premium = completed_candles(premium_candles, now, timeframe_seconds=300)
        session = _session(premium, now)
        if len(session) < 3:
            raise IndicatorDataError("index_insufficient_history")
        latest = session[-1]
        end = latest["_timestamp_epoch"] + 300
        if not 0 <= now.timestamp() - end <= 375:
            raise IndicatorDataError("index_confirmation_stale")
        for index in range(len(session) - 1, 0, -1):
            if session[index]["_timestamp_epoch"] - session[index - 1]["_timestamp_epoch"] != 300:
                session = session[index:]
                break
        if len(session) < 3:
            raise IndicatorDataError("index_premium_gap")
        points = _swing_points(session)
        lows = [point for point in points if point[1] == "L"]
        highs = [point for point in points if point[1] == "H"]
        candidates = []
        for index, _, level in reversed(lows):
            later_highs = [point for point in highs if point[0] > index]
            if not later_highs:
                continue
            if any(bar["close"] < level * (1 - SUPPORT_TOLERANCE)
                   for bar in session[index + 1:]):
                continue
            target_index, _, target = min(later_highs, key=lambda point: point[2])
            if target <= level:
                continue
            candidates.append({
                "setup": "premium_support_retest", "support": level,
                "stop": session[index]["low"], "target": target,
                "known_at": session[target_index + 1]["_timestamp_epoch"] + 300,
                "support_candle": session[index], "pullback_lows": [level],
            })
        # Repeated rejections define resistance; use only lows between/after
        # those already-confirmed highs, never unconfirmed current extremes.
        for index, _, resistance in reversed(highs):
            matching = [point for point in highs if point[0] <= index
                        and abs(point[2] - resistance) <= resistance * RESISTANCE_TOLERANCE]
            if len(matching) < 2:
                continue
            floor_points = [point for point in lows if point[0] > matching[0][0]
                            and point[0] <= index]
            if len(floor_points) < 2:
                continue
            floor_values = [point[2] for point in floor_points]
            support = sum(floor_values) / len(floor_values)
            support_index = min(floor_points, key=lambda point: point[2])[0]
            if resistance <= support:
                continue
            if any(bar["close"] < session[support_index]["low"]
                   for bar in session[index + 1:]):
                continue
            candidates.insert(0, {
                "setup": "premium_average_reclaim", "support": support,
                "stop": session[support_index]["low"],
                "target": min(point[2] for point in matching),
                "known_at": session[index + 1]["_timestamp_epoch"] + 300,
                "support_candle": session[support_index],
                "pullback_lows": floor_values,
            })
            break
        if not candidates:
            raise IndicatorDataError("index_premium_support_missing")
        return {
            "candle": latest, "timestamp": latest["timestamp"], "armed": True,
            "confirmation_end": end, "candidates": candidates,
            "setups": sorted({item["setup"] for item in candidates}),
        }

    def evaluate(self, symbol, side, reference, one_minute_candles, quote, now):
        bars = completed_candles(one_minute_candles, now, timeframe_seconds=60)
        if not bars:
            raise IndicatorDataError("index_one_minute_missing")
        if len(bars) < 2:
            raise IndicatorDataError("index_one_minute_missing")
        pullback, last = bars[-2:]
        if (last["_timestamp_epoch"] - pullback["_timestamp_epoch"] != 60
                or not 0 <= now.timestamp() - (last["_timestamp_epoch"] + 60) <= 75
                or datetime.fromtimestamp(pullback["_timestamp_epoch"], IST).date() != now.date()):
            raise IndicatorDataError("index_one_minute_stale_or_gap")
        if last["close"] <= last["open"] or last["close"] <= pullback["high"]:
            raise IndicatorDataError("index_recovery_close_missing")
        matched = []
        for candidate in reference["candidates"]:
            support = candidate["support"]
            if candidate["known_at"] > pullback["_timestamp_epoch"]:
                continue
            if min(pullback["low"], last["low"]) > support * (1 + SUPPORT_TOLERANCE):
                continue
            if (candidate["setup"] == "premium_average_reclaim"
                    and min(pullback["low"], last["low"]) > support):
                continue
            if (candidate["setup"] != "premium_average_reclaim"
                    and min(pullback["low"], last["low"]) < support * (1 - SUPPORT_TOLERANCE)):
                continue
            if last["close"] <= support:
                continue
            if candidate["stop"] >= last["close"]:
                continue
            matched.append(candidate)
        if not matched:
            raise IndicatorDataError("index_premium_retest_missing")
        price, stamp = _quote_values(quote, now)
        matched = [item for item in matched
                   if price > max(item["support"], item["stop"], pullback["high"])]
        if not matched:
            raise IndicatorDataError("index_entry_risk_or_timing")
        candidate = next((item for item in matched if item["target"] > price), None)
        if candidate is None:
            raise IndicatorDataError("index_target_passed")
        candle = candidate["support_candle"]
        signal = {
            "symbol": symbol, "underlying": symbol, "category": "index_options",
            "signal": "CALL" if side == "CE" else "PUT", "option_type": side,
            "trigger": INDEX_TRIGGER, "index_swing": True, "alerts_only": True,
            "timeframe": "5min", "confirmation_end": reference["confirmation_end"],
            "one_minute_start": last["_timestamp_epoch"], "one_minute_close": last["close"],
            "one_minute_open": last["open"],
            "reference_timestamp": pullback["timestamp"], "reference_color": "RECOVERY",
            "reference_high": pullback["high"], "reference_low": candidate["stop"],
            "breakout_timestamp": stamp, "first_crossing_timestamp": stamp,
            "breakout_price": price, "entry": price, "entry_price": price,
            "strike_band": "ATM",
            "stop_loss": candidate["stop"], "target": candidate["target"],
            "targets": [candidate["target"]],
            "risk_points": price - candidate["stop"], "detected_at": now.isoformat(),
            "setups": [candidate["setup"]], "entry_policy": PREMIUM_POLICY,
            "premium_support": candidate["support"], "support_known_at": candidate["known_at"],
            "pullback_lows": candidate["pullback_lows"],
            "retest_low": min(pullback["low"], last["low"]),
            "previous_minute_start": pullback["_timestamp_epoch"],
            "pullback_low": pullback["low"], "premium_confirmation_high": candle["high"],
            "premium_confirmation_low": candle["low"],
            "support_candle_timestamp": candle["_timestamp_epoch"],
        }
        if candidate["setup"] == "premium_previous_session_recovery":
            signal.update({
                key: candidate[key] for key in (
                    "previous_session_date", "previous_level_kind", "premium_reference_levels",
                )
            })
        if not valid_entry(signal, now):
            raise IndicatorDataError("index_entry_risk_or_timing")
        return signal

    def evaluate_patterns(self, symbol, side, candles, quote, now):
        if symbol not in INDEX_SYMBOLS or side not in ("CE", "PE"):
            raise IndicatorDataError("index_unsupported")
        hits, diagnostics = scan_patterns(candles, now, "BSE" if symbol == "SENSEX" else "NSE")
        if not hits:
            return [], diagnostics
        price, stamp = _quote_values(quote, now)
        signals = []
        for hit in hits:
            pattern = hit["pattern"]
            if not max(pattern["boundary"], pattern["stop"]) < price < pattern["target"]:
                for item in diagnostics:
                    if item["timeframe"] == pattern["timeframe"]:
                        item["entry_rejection"] = "index_pattern_price_outside_entry"
                continue
            signal = {
                "symbol": symbol, "underlying": symbol, "category": "index_options",
                "signal": "CALL" if side == "CE" else "PUT", "option_type": side,
                "trigger": INDEX_TRIGGER, "index_swing": True, "alerts_only": True,
                "entry_policy": PATTERN_POLICY, "setups": ["premium_chart_pattern"],
                "timeframe": f"{pattern['timeframe']}min",
                "reference_timestamp": pattern["confirmed_start"],
                "reference_high": pattern["boundary"], "reference_low": pattern["stop"],
                "confirmation_end": pattern["confirmed_end"],
                "breakout_timestamp": stamp, "first_crossing_timestamp": stamp,
                "breakout_price": price, "entry": price, "entry_price": price,
                "stop_loss": pattern["stop"], "target": pattern["target"],
                "targets": [pattern["target"]], "risk_points": price - pattern["stop"],
                "strike_band": "ATM", "detected_at": now.isoformat(),
                "premium_pattern": pattern, "chart_candles": hit["chart_candles"],
            }
            if not valid_entry(signal, now):
                raise IndicatorDataError("index_pattern_evidence_invalid")
            signals.append(signal)
        return signals, diagnostics


def describe_setup(metadata):
    if metadata.get("entry_policy") == PATTERN_POLICY:
        pattern = metadata["premium_pattern"]
        return (
            f"Setup: Confirmed {pattern['name'].replace('_', ' ')} | "
            f"{pattern['timeframe']}m premium chart\n"
            f"Completed close above breakout boundary {pattern['boundary']:.2f}.\n"
            "Independent pattern entry; support recovery is not required.\n"
            "Target: pattern measured move; stop: pattern invalidation low.\n"
            "Spot selects ATM only. Alerts only; no broker orders."
        )
    if metadata.get("entry_policy") == PREMIUM_POLICY:
        label = {
            "premium_support_retest": "Repeated premium support / W recovery",
            "premium_average_reclaim": "Average pullback support reclaim",
            "premium_previous_session_recovery": "Previous-session premium level recovery",
        }[metadata["setups"][0]]
        source = ""
        if metadata["setups"] == ["premium_previous_session_recovery"]:
            source = (f"Reference session: {metadata['previous_session_date']} | "
                      f"Prior {metadata['previous_level_kind']} acting as support\n")
        return (
            f"Setup: {label}\n"
            f"{source}"
            f"Premium support: {metadata['premium_support']:.2f} | "
            f"Retest low: {metadata['retest_low']:.2f}\n"
            f"Recovery: 1m close {metadata['one_minute_close']:.2f} above "
            f"previous 1m high {metadata['reference_high']:.2f}\n"
            "Spot is used for ATM selection only, not entry confirmation.\n"
            "Alerts only; stop updates are advisory, not broker orders."
        )
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
