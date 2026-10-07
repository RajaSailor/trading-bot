from copy import deepcopy
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from premium_indicators import (
    REQUIRED_CONFIRMATIONS, IndicatorDataError, calculate_confirmations,
    completed_candles, confirmation_status, ema, macd, trigger_ema_ok,
    trigger_ema_position, wilder_rsi,
)


IST = ZoneInfo("Asia/Kolkata")
SESSION_START = datetime(2026, 10, 5, 9, 15, tzinfo=IST)
NARROW_DAY = [
    (99, 101, 98, 100), (100, 102, 99, 101), (101, 103, 100, 102), (102, 104, 101, 103),
    (103, 104, 100, 101), (101, 105, 99, 104), (104, 106, 101, 103), (103, 106, 95, 98),
    (98, 110, 97, 109),
]
RISING_DAY = [
    (85, 88, 84, 87), (87, 91, 86, 90), (90, 94, 89, 93), (93, 97, 92, 96), (96, 100, 95, 99),
    (99, 103, 98, 102), (102, 106, 101, 105), (105, 110, 98, 100), (100, 113, 99, 112),
]


def build(today, volume=None):
    candles = []
    for index in range(38):
        start = datetime(2026, 10, 1, 9, 15, tzinfo=IST) + timedelta(minutes=10 * index)
        close = 80 + index * 0.25 + (0.15 if index % 3 else -0.1)
        open_price = close - 0.1 if index % 4 else close + 0.08
        candles.append({
            "timestamp": start.isoformat(), "open": open_price, "high": max(open_price, close) + 0.4,
            "low": min(open_price, close) - 0.4, "close": close,
        })
    for index, (open_price, high, low, close) in enumerate(today):
        candles.append({
            "timestamp": (SESSION_START + timedelta(minutes=10 * index)).isoformat(),
            "open": open_price, "high": high, "low": low, "close": close,
        })
    if volume is not None:
        for candle in candles:
            candle["volume"] = volume
    return candles, SESSION_START + timedelta(minutes=10 * len(today), seconds=5)


def provisional(now, price=107, **overrides):
    return {
        "timestamp": now.replace(second=0, microsecond=0).timestamp(),
        "open": price, "high": price, "low": price, "close": price,
        "observed_at": now.timestamp(), **overrides,
    }


def test_known_sma_seeded_ema_wilder_rsi_and_macd_fixtures():
    assert ema([1, 2, 3, 4, 5], 3) == [None, None, 2.0, 3.0, 4.0]
    assert wilder_rsi(list(range(15))) == [None] * 14 + [100.0]
    assert wilder_rsi([5] * 15)[-1] == 50.0
    assert wilder_rsi(list(range(15, 0, -1)))[-1] == 0.0
    line, signal = macd(list(range(1, 35)))
    assert line[24] is None and line[25] == pytest.approx(7.0)
    assert signal[32] is None and signal[33] == pytest.approx(7.0)
    # A loss after the seed uses Wilder's 13/14 smoothing, not a new simple average.
    assert wilder_rsi(list(range(15)) + [12])[-1] == pytest.approx(100 * 13 / 15)


@pytest.mark.parametrize("today", [RISING_DAY, NARROW_DAY])
def test_only_three_gates_and_prior_session_warmup_without_volume(today):
    candles, now = build(today)
    before = deepcopy(candles)
    result = calculate_confirmations(candles, now)
    assert REQUIRED_CONFIRMATIONS == (
        "trigger_above_or_straddles_ema9", "macd_above_signal", "rsi14_above_25_and_rising",
    )
    assert result["passed"] == {name: True for name in REQUIRED_CONFIRMATIONS}
    assert result["ready"]
    assert candles == before
    assert calculate_confirmations(build(today, volume=0)[0], now)["values"] == result["values"]
    assert calculate_confirmations(build(today, volume=500)[0], now)["values"] == result["values"]


def test_previous_session_warmup_allows_first_current_session_bar():
    candles, now = build(NARROW_DAY[:1])
    candles[-2].update(close=85, low=84)
    result = calculate_confirmations(candles, now)
    assert result["ready"]
    assert result["passed"] == {name: True for name in REQUIRED_CONFIRMATIONS}


@pytest.mark.parametrize(
    ("field", "value"),
    [("rsi14", 25.0), ("rsi14", 20.0), ("rsi14_previous", 60.0),
     ("rsi14_previous", 65.0), ("macd_signal9", 2.0), ("macd_signal9", 3.0), ("ema9", 121.0)],
)
def test_each_confirmation_is_strict_and_mandatory(field, value):
    values = {
        "premium": 100.0, "trigger_low": 95.0, "trigger_high": 102.0, "ema9": 80.0,
        "rsi14": 60.0, "rsi14_previous": 55.0, "macd12_26": 2.0, "macd_signal9": 1.0,
    }
    assert all(confirmation_status(values).values())
    values[field] = value
    assert not all(confirmation_status(values).values())


def test_rsi_just_above_25_and_negative_macd_above_signal_pass():
    values = {
        "premium": 100.0, "trigger_low": 95.0, "trigger_high": 102.0, "ema9": 80.0,
        "rsi14": 25.0001, "rsi14_previous": 25.0, "macd12_26": -2.0, "macd_signal9": -3.0,
    }
    assert all(confirmation_status(values).values())


@pytest.mark.parametrize(
    ("price", "low", "high", "ema_value", "position", "ok"),
    [(105, 102, 106, 100, "above", True), (105, 99, 106, 100, "straddle", True),
     (99.5, 99, 101, 100, "straddle", True), (100, 100, 101, 100, "straddle", True),
     (99, 97, 100, 100, "straddle", True), (99, 97, 99.5, 100, "below", False)],
)
def test_trigger_ema_above_straddle_below(price, low, high, ema_value, position, ok):
    assert trigger_ema_position(price, low, high, ema_value) == position
    assert trigger_ema_ok(price, low, high, ema_value) is ok


@pytest.mark.parametrize(
    "evidence",
    [(None, 99, 101, 100), (100, None, 101, 100), (100, 99, None, 100),
     (100, 99, 101, None), (float("nan"), 99, 101, 100), (102, 99, 101, 100)],
)
def test_missing_or_inconsistent_trigger_evidence_fails_closed(evidence):
    with pytest.raises(IndicatorDataError, match="missing_trigger_evidence"):
        trigger_ema_position(*evidence)


@pytest.mark.parametrize(
    ("red", "placement"),
    [((106, 108, 104.5, 105), "above"), ((103, 106, 95, 98), "straddle"),
     ((97, 97.5, 93, 94), "below")],
)
def test_reference_red_candle_may_sit_anywhere_relative_to_ema(red, placement):
    today = list(NARROW_DAY)
    today[-2] = red
    candles, now = build(today)
    red_ema = ema([c["close"] for c in candles], 9)[-2]
    assert ("above" if red[2] > red_ema else "below" if red[1] < red_ema else "straddle") == placement
    assert calculate_confirmations(candles, now)["passed"]["trigger_above_or_straddles_ema9"]


def test_live_snapshot_is_pure_and_rsi_compares_to_preceding_completed_bar():
    candles, now = build(NARROW_DAY[:-1])
    now += timedelta(seconds=35)
    before = deepcopy(candles)
    live = provisional(now, open=106.5, high=107.5, low=106.5)
    live_before = dict(live)
    first = calculate_confirmations(candles, now, live)
    assert first == calculate_confirmations(candles, now, live)
    assert candles == before and live == live_before
    assert first["evidence_mode"] == "live_provisional_ltp"
    assert first["trigger_evidence"] == "live_observed_ticks"
    assert first["values"]["premium"] == 107
    assert first["values"]["trigger_low"] == 106.5 and first["values"]["trigger_high"] == 107.5
    assert first["values"]["rsi14_previous"] == pytest.approx(wilder_rsi([c["close"] for c in candles])[-1])
    assert first["previous_indicator_as_of"] == "2026-10-05T10:25:00+05:30"
    assert first["live_quote_as_of"] == "2026-10-05T10:35:40+05:30"


def test_live_ema_straddle_uses_only_observed_current_bucket_extremes():
    candles, now = build(NARROW_DAY[:-1])
    now += timedelta(seconds=35)
    live = provisional(now, price=100)
    ema_now = calculate_confirmations(candles, now, live)["values"]["ema9"]
    straddle = calculate_confirmations(candles, now, {
        **live, "open": ema_now - 1, "high": ema_now + 1, "low": ema_now - 1, "close": ema_now - 0.5,
    })
    assert straddle["passed"]["trigger_above_or_straddles_ema9"]
    below = calculate_confirmations(candles, now, provisional(now, price=90))
    assert below["ema9_position"] == "below"
    assert not below["passed"]["trigger_above_or_straddles_ema9"]


@pytest.mark.parametrize("mutation", [
    {"close": float("nan")}, {"timestamp": "2026-10-05T10:36:00+05:30"},
    {"timestamp": "2026-10-05T10:25:00+05:30"}, {"timestamp": "2026-10-05T10:30:00+05:30"},
    {"observed_at": "2026-10-05T10:34:59+05:30"},
    {"observed_at": "2026-10-05T10:36:00+05:30"},
])
def test_invalid_future_or_old_bucket_provisional_live_candle_fails_closed(mutation):
    candles, now = build(NARROW_DAY[:-1])
    with pytest.raises(IndicatorDataError, match="invalid_provisional_candle"):
        calculate_confirmations(candles, now, provisional(now, **mutation))


@pytest.mark.parametrize("mutate", [
    lambda candle: candle.pop("close"), lambda candle: candle.update(high=float("inf")),
    lambda candle: candle.update(low=0),
])
def test_invalid_or_missing_ohlc_fails_closed(mutate):
    candles, now = build(RISING_DAY)
    mutate(candles[-1])
    with pytest.raises(IndicatorDataError, match="invalid_ohlc"):
        calculate_confirmations(candles, now)


@pytest.mark.parametrize("volume", [None, float("nan"), -1, "bad", 0])
def test_missing_or_invalid_volume_does_not_block(volume):
    candles, now = build(RISING_DAY)
    for candle in candles:
        candle["volume"] = volume
    assert calculate_confirmations(candles, now)["ready"]


def test_stale_inadequate_warmup_and_naive_now_fail_closed():
    candles, now = build(RISING_DAY)
    for history, moment, reason in [
        (candles[:-3], now, "stale_candles"), (candles[-33:], now, "insufficient_history"),
        (candles, now.replace(tzinfo=None), "invalid_now"),
    ]:
        with pytest.raises(IndicatorDataError, match=reason):
            calculate_confirmations(history, moment)


def test_candles_are_deduplicated_ordered_and_forming_future_bars_excluded():
    candles, now = build(RISING_DAY)
    duplicate = dict(candles[-1], close=111.5, high=113)
    future = dict(candles[-1], timestamp=(now + timedelta(minutes=20)).isoformat())
    forming = dict(candles[-1], timestamp=now.replace(second=0).isoformat())
    normalized = completed_candles([candles[-2], duplicate, candles[-3], candles[-1], future, forming], now)
    assert [c["timestamp"] for c in normalized] == sorted(c["timestamp"] for c in normalized)
    assert len(normalized) == 3 and normalized[-1]["close"] == candles[-1]["close"]
