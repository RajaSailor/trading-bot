"""Completed bullish premium structures and session-aligned candle aggregation."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from exchange_calendar import previous_trading_day, session_windows
from premium_indicators import completed_candles

IST = ZoneInfo("Asia/Kolkata")
INDEX_PATTERN_TIMEFRAMES = (2, 3, 5, 10, 15, 30, 60)
ASSET_PATTERN_TIMEFRAMES = (5, 10, 15, 30, 60, 120, 240, 1440)
PATTERN_TIMEFRAMES = tuple(sorted(set(INDEX_PATTERN_TIMEFRAMES + ASSET_PATTERN_TIMEFRAMES)))
PATTERN_NAMES = (
    "double_bottom", "triple_bottom", "inverse_head_and_shoulders",
    "ascending_triangle", "falling_wedge", "bullish_flag",
)
PATTERN_POLICY = "premium_pattern_breakout_v1"
PATTERN_LOOKBACK_SESSIONS = 10
PATTERN_WINDOW = 80


def swing_points(bars):
    points = []
    for index in range(1, len(bars) - 1):
        middle = bars[index]
        if middle["low"] < min(bars[index - 1]["low"], bars[index + 1]["low"]):
            points.append((index, "L", middle["low"]))
        if middle["high"] > max(bars[index - 1]["high"], bars[index + 1]["high"]):
            points.append((index, "H", middle["high"]))
    return sorted(points)


def aggregate_minutes(candles, minutes, now, exchange="NSE"):
    """Build full bars anchored to the exchange session, never across gaps."""
    if minutes not in PATTERN_TIMEFRAMES or minutes == 1440:
        raise ValueError("unsupported_pattern_timeframe")
    base = completed_candles(candles, now, timeframe_seconds=60)
    groups = {}
    seconds = minutes * 60
    for bar in base:
        stamp = bar["_timestamp_epoch"]
        moment = datetime.fromtimestamp(stamp, IST)
        window = next(((start, end) for start, end in session_windows(exchange, moment.date())
                       if start <= moment.time().replace(tzinfo=None) < end), None)
        if window is None:
            continue
        opening = datetime.combine(moment.date(), window[0], tzinfo=IST).timestamp()
        closing = datetime.combine(moment.date(), window[1], tzinfo=IST).timestamp()
        start = opening + int((stamp - opening) // seconds) * seconds
        if start + seconds <= min(now.timestamp(), closing):
            groups.setdefault(start, []).append(bar)
    result = []
    for start, group in sorted(groups.items()):
        if [bar["_timestamp_epoch"] for bar in group] != [
            start + offset * 60 for offset in range(minutes)
        ]:
            continue
        result.append({
            "timestamp": start, "_timestamp_epoch": start,
            "open": group[0]["open"], "close": group[-1]["close"],
            "high": max(bar["high"] for bar in group),
            "low": min(bar["low"] for bar in group),
            "volume": sum(bar.get("volume") or 0 for bar in group),
        })
    return result


def completed_daily_candles(candles, now, exchange):
    """Normalize Dhan daily bars to exchange session opens and real close times."""
    result = []
    for bar in completed_candles(candles, now + timedelta(days=1), timeframe_seconds=86400):
        day = datetime.fromtimestamp(bar["_timestamp_epoch"], IST).date()
        windows = session_windows(exchange, day)
        if not windows:
            continue
        opening = datetime.combine(day, windows[0][0], tzinfo=IST)
        closing = datetime.combine(day, windows[-1][1], tzinfo=IST)
        if closing.timestamp() > now.timestamp():
            continue
        result.append({
            **bar, "timestamp": opening.timestamp(), "_timestamp_epoch": opening.timestamp(),
            "end_timestamp": closing.timestamp(), "_end_timestamp_epoch": closing.timestamp(),
        })
    result.sort(key=lambda item: item["_timestamp_epoch"])
    return result


def contiguous_daily_candles(bars, exchange):
    for index in range(len(bars) - 1, 0, -1):
        previous, current = bars[index - 1:index + 1]
        left = datetime.fromtimestamp(previous["_timestamp_epoch"], IST).date()
        right = datetime.fromtimestamp(current["_timestamp_epoch"], IST).date()
        if previous_trading_day(exchange, right) != left:
            return bars[index:]
    return bars


def _alternating_points(bars):
    points = swing_points(bars)
    ambiguous = {index for index, _, _ in points
                 if sum(point[0] == index for point in points) > 1}
    result = []
    for point in points:
        if point[0] in ambiguous:
            continue
        if result and result[-1][1] == point[1]:
            if (point[2] < result[-1][2] if point[1] == "L" else point[2] > result[-1][2]):
                result[-1] = point
        else:
            result.append(point)
    return result


def contiguous_pattern_bars(bars, minutes, exchange="NSE"):
    """Keep the suffix without missing full buckets, allowing closed sessions."""
    for index in range(len(bars) - 1, 0, -1):
        previous, current = bars[index - 1:index + 1]
        left = datetime.fromtimestamp(previous["_timestamp_epoch"], IST)
        right = datetime.fromtimestamp(current["_timestamp_epoch"], IST)
        if left.date() == right.date():
            contiguous = current["_timestamp_epoch"] - previous["_timestamp_epoch"] == minutes * 60
        else:
            left_windows = session_windows(exchange, left.date())
            right_windows = session_windows(exchange, right.date())
            contiguous = False
            if left_windows and right_windows:
                opening = datetime.combine(left.date(), left_windows[-1][0], tzinfo=IST)
                closing = datetime.combine(left.date(), left_windows[-1][1], tzinfo=IST)
                count = int((closing - opening).total_seconds() // (minutes * 60))
                last_start = opening + timedelta(minutes=(count - 1) * minutes)
                first_start = datetime.combine(right.date(), right_windows[0][0], tzinfo=IST)
                contiguous = (
                    count > 0 and previous_trading_day(exchange, right.date()) == left.date()
                    and left == last_start and right == first_start
                )
        if not contiguous:
            return bars[index:]
    return bars


def _line(first, last):
    slope = (last[2] - first[2]) / (last[0] - first[0])
    return lambda index: first[2] + slope * (index - first[0]), slope


def _near(values, tolerance=.005):
    return max(values) - min(values) <= max(values) * tolerance


def bullish_patterns(bars, minutes):
    """Confirm a first green close through a previously observable boundary."""
    bars = bars[-PATTERN_WINDOW:]
    if len(bars) < 7:
        return []
    # The breakout cannot also be the candle that first confirms a pivot.
    points = _alternating_points(bars[:-1])
    last_index = len(bars) - 1
    last = bars[-1]
    if last["close"] <= last["open"]:
        return []
    found = []

    def add(name, seq, boundary_at, stop, height, lines=None, extra_points=()):
        boundary = boundary_at(last_index)
        if (height <= 0 or stop <= 0 or last["close"] <= boundary
                or bars[-2]["close"] > boundary_at(last_index - 1)
                or any(bar["close"] > boundary_at(index)
                       for index, bar in enumerate(bars[seq[-1][0] + 1:-1],
                                                   seq[-1][0] + 1))
                or any(bar["close"] < stop for bar in bars[seq[-1][0] + 1:])):
            return
        vertices = [
            {"timestamp": bars[index]["_timestamp_epoch"], "price": price, "label": kind}
            for index, kind, price in (*extra_points, *seq)
        ]
        labels = {
            "double_bottom": ("Bottom 1", "Neckline", "Bottom 2"),
            "triple_bottom": ("Bottom 1", "Neckline", "Bottom 2", "Neckline", "Bottom 3"),
            "inverse_head_and_shoulders": ("Left shoulder", "Neckline", "Head", "Neckline", "Right shoulder"),
        }.get(name)
        if labels:
            for point, label in zip(vertices, labels):
                point["label"] = label
        segments = lines or [{
            "label": "Neckline / breakout",
            "points": [
                {"timestamp": bars[seq[0][0]]["_timestamp_epoch"],
                 "price": boundary_at(seq[0][0])},
                {"timestamp": last["_timestamp_epoch"], "price": boundary},
            ],
        }]
        identity = json.dumps([name, minutes, vertices], sort_keys=True)
        found.append({
            "name": name, "timeframe": minutes, "direction": "bullish_premium",
            "boundary": boundary, "stop": stop, "target": boundary + height,
            "points": vertices, "lines": segments,
            "confirmed_start": last["_timestamp_epoch"],
            "confirmed_end": last.get("_end_timestamp_epoch", last["_timestamp_epoch"] + minutes * 60),
            "pattern_id": hashlib.sha256(identity.encode("ascii")).hexdigest()[:24],
        })

    three = points[-3:]
    if len(three) == 3 and tuple(p[1] for p in three) == ("L", "H", "L"):
        a, b, c = three
        if _near([a[2], c[2]]):
            add("double_bottom", three, lambda _: b[2],
                min(a[2], c[2]), b[2] - min(a[2], c[2]))
    five = points[-5:]
    kinds = tuple(p[1] for p in five)
    if kinds == ("L", "H", "L", "H", "L"):
        a, b, c, d, e = five
        boundary = max(b[2], d[2])
        if _near([a[2], c[2], e[2]]):
            add("triple_bottom", five, lambda _: boundary,
                min(a[2], c[2], e[2]), boundary - min(a[2], c[2], e[2]))
        if _near([a[2], e[2]], .01) and c[2] < min(a[2], e[2]) * .995:
            add("inverse_head_and_shoulders", five, lambda _: boundary,
                e[2], boundary - c[2])
    if kinds == ("H", "L", "H", "L", "H"):
        a, b, c, d, e = five
        boundary = max(a[2], c[2], e[2])
        if _near([a[2], c[2], e[2]]) and d[2] > b[2] * 1.005:
            lines = [{
                "label": "Triangle resistance",
                "points": [{"timestamp": bars[a[0]]["_timestamp_epoch"], "price": boundary},
                           {"timestamp": last["_timestamp_epoch"], "price": boundary}],
            }, {
                "label": "Rising support",
                "points": [{"timestamp": bars[p[0]]["_timestamp_epoch"], "price": p[2]}
                           for p in (b, d)],
            }]
            add("ascending_triangle", five, lambda _: boundary,
                d[2], boundary - b[2], lines)
    six = points[-6:]
    if tuple(p[1] for p in six) == ("H", "L", "H", "L", "H", "L"):
        highs, lows = six[::2], six[1::2]
        if (_near([p[2] for p in highs])
                and all(right[2] > left[2] * 1.005 for left, right in zip(lows, lows[1:]))):
            boundary = max(p[2] for p in highs)
            triangle_lines = [{
                "label": "Triangle resistance",
                "points": [{"timestamp": bars[highs[0][0]]["_timestamp_epoch"], "price": boundary},
                           {"timestamp": last["_timestamp_epoch"], "price": boundary}],
            }, {
                "label": "Rising support",
                "points": [{"timestamp": bars[p[0]]["_timestamp_epoch"], "price": p[2]} for p in lows],
            }]
            add("ascending_triangle", six, lambda _: boundary, lows[-1][2],
                boundary - lows[0][2], triangle_lines)
        upper, upper_slope = _line(highs[0], highs[-1])
        lower, lower_slope = _line(lows[0], lows[-1])
        fits = all(abs(p[2] - line(p[0])) <= p[2] * .01
                   for line, vertices in ((upper, highs), (lower, lows)) for p in vertices)
        width = upper(six[0][0]) - lower(six[0][0])
        end_width = upper(last_index) - lower(last_index)
        lines = [
            {"label": label, "points": [
                {"timestamp": bars[index]["_timestamp_epoch"], "price": line(index)}
                for index in range(six[0][0], last_index + 1)
            ]}
            for label, line in (("Upper boundary", upper), ("Lower boundary", lower))
        ]
        if fits and upper_slope < lower_slope < 0 and 0 < end_width < width:
            add("falling_wedge", six, upper, lows[-1][2], width, lines)
        parallel = (upper_slope < 0 and lower_slope < 0
                    and abs(upper_slope - lower_slope)
                    <= max(abs(upper_slope), abs(lower_slope)) * .25)
        pole_start = max(0, six[0][0] - 8)
        before = bars[pole_start:six[0][0]]
        if fits and parallel and width > 0 and end_width > 0 and before:
            pole_index = min(range(pole_start, six[0][0]), key=lambda i: bars[i]["low"])
            pole = highs[0][2] - bars[pole_index]["low"]
            if (pole >= max(width * 3, highs[0][2] * .05)
                    and min(p[2] for p in lows) >= highs[0][2] - pole * .5):
                add("bullish_flag", six, upper, lows[-1][2], pole, lines,
                    [(pole_index, "Pole", bars[pole_index]["low"])])
    return found


def scan_patterns(candles, now, exchange="NSE", category="index_options", daily_candles=None):
    hits, diagnostics = [], []
    timeframes = INDEX_PATTERN_TIMEFRAMES if category == "index_options" else ASSET_PATTERN_TIMEFRAMES
    for minutes in timeframes:
        available = (completed_daily_candles(daily_candles or [], now, exchange) if minutes == 1440
                     else aggregate_minutes(candles, minutes, now, exchange))
        bars = (contiguous_daily_candles(available, exchange) if minutes == 1440 else
                contiguous_pattern_bars(available, minutes, exchange))
        status = {"timeframe": minutes, "completed_bars": len(bars),
                  "available_bars": len(available), "gap_excluded_bars": len(available) - len(bars)}
        if len(bars) < 7:
            status["status"] = "INSUFFICIENT_HISTORY"
        elif (minutes == 1440 and datetime.fromtimestamp(
                bars[-1]["_timestamp_epoch"], IST).date() != previous_trading_day(exchange, now.date())):
            status["status"] = "AWAITING_TODAY_CLOSE"
        elif (minutes != 1440 and not 0 <= now.timestamp()
              - (bars[-1]["_timestamp_epoch"] + minutes * 60) <= 75):
            status["status"] = "AWAITING_FRESH_CLOSE"
        elif (minutes != 1440 and datetime.fromtimestamp(
                bars[-1]["_timestamp_epoch"], IST).date() != now.date()):
            status["status"] = "AWAITING_TODAY_CLOSE"
        else:
            patterns = bullish_patterns(bars, minutes)
            status["status"] = "CONFIRMED" if patterns else "NO_CONFIRMED_PATTERN"
            for pattern in patterns:
                snapshot = [{key: bar[key] for key in (
                    "timestamp", "open", "high", "low", "close", "end_timestamp",
                ) if key in bar}
                            for bar in bars[-PATTERN_WINDOW:]]
                hits.append({"pattern": pattern, "chart_candles": snapshot})
        diagnostics.append(status)
    return hits, diagnostics
