from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from premium_indicators import (
    MODE_MOMENTUM,
    MODE_NORMAL,
    MODE_PENDING,
    MODE_TWO_POLLS,
    REQUIRED_CONFIRMATIONS,
    IndicatorDataError,
    anti_chop_status,
    calculate_confirmations,
    completed_candles,
    confirmation_status,
    continuation_status,
    ema,
    macd,
    trigger_ema_ok,
    trigger_ema_position,
    wilder_rsi,
)


IST = ZoneInfo("Asia/Kolkata")
SESSION_START = datetime(2026, 10, 5, 9, 15, tzinfo=IST)
# Sideways: the 8 bars before the trigger span 95-106 (~10.9% of mid); red 103/106/95/98.
SIDEWAYS_DAY = [
    (99, 101, 98, 100), (100, 102, 99, 101), (101, 103, 100, 102), (102, 104, 101, 103),
    (103, 104, 100, 101), (101, 105, 99, 104), (104, 106, 101, 103), (103, 106, 95, 98),
    (98, 110, 97, 109),
]
# Trending: the 8 bars before the trigger span 84-110 (~26.8%); red 105/110/98/100.
NORMAL_DAY = [
    (85, 88, 84, 87), (87, 91, 86, 90), (90, 94, 89, 93), (93, 97, 92, 96), (96, 100, 95, 99),
    (99, 103, 98, 102), (102, 106, 101, 105), (105, 110, 98, 100), (100, 113, 99, 112),
]


def build(today, volume=None):
    """Previous-session warmup + today's bars, without volume by default."""
    candles = []
    for index in range(38):
        start = datetime(2026, 10, 2, 9, 15, tzinfo=IST) + timedelta(minutes=10 * index)
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
    now = SESSION_START + timedelta(minutes=10 * len(today), seconds=5)
    return candles, now


def bars(highs_lows, start=SESSION_START):
    return [
        {"_timestamp_epoch": (start + timedelta(minutes=10 * index)).timestamp(), "high": high, "low": low}
        for index, (high, low) in enumerate(highs_lows)
    ]


def test_known_ema_wilder_rsi_and_macd_fixtures():
    assert ema([1, 2, 3, 4, 5], 3) == [None, None, 2.0, 3.0, 4.0]
    assert wilder_rsi(list(range(15))) == [None] * 14 + [100.0]
    assert wilder_rsi([5] * 15)[-1] == 50.0
    assert wilder_rsi(list(range(15, 0, -1)))[-1] == 0.0
    line, signal = macd(list(range(1, 35)))
    assert line[24] is None and line[25] == pytest.approx(7.0)
    assert signal[32] is None and signal[33] == pytest.approx(7.0)


@pytest.mark.parametrize("today", [NORMAL_DAY, SIDEWAYS_DAY])
def test_all_three_gates_pass_without_volume_vwap_or_psar(today):
    candles, now = build(today)
    result = calculate_confirmations(candles, now)
    assert REQUIRED_CONFIRMATIONS == (
        "trigger_above_or_straddles_ema9", "macd_above_signal", "rsi14_above_25_and_rising",
    )
    assert result["passed"] == {name: True for name in REQUIRED_CONFIRMATIONS}
    assert result["ready"]
    assert not {"vwap", "psar_0_02_0_2"} & set(result["values"])
    assert "vwap_as_of" not in result
    assert result["evidence_mode"] == "completed"
    assert result["trigger_evidence"] == "completed_ohlc"
    assert result["indicator_as_of"] == "2026-10-05T10:35:00+05:30"
    assert result["previous_indicator_as_of"] == "2026-10-05T10:25:00+05:30"
    # Volume (missing, zero or real) never changes the gates.
    assert calculate_confirmations(build(today, volume=0)[0], now)["values"] == result["values"]
    assert calculate_confirmations(build(today, volume=500)[0], now)["values"] == result["values"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("rsi14", 25.0),            # threshold equality fails
        ("rsi14", 20.0),
        ("rsi14_previous", 60.0),   # flat RSI is not rising
        ("rsi14_previous", 65.0),   # falling RSI
        ("macd_signal9", 2.0),      # MACD equality fails
        ("macd_signal9", 3.0),
        ("ema9", 121.0),            # trigger wholly below EMA9
    ],
)
def test_each_confirmation_is_strict_and_mandatory(field, value):
    values = {
        "premium": 100.0, "trigger_low": 95.0, "trigger_high": 102.0, "ema9": 80.0,
        "rsi14": 60.0, "rsi14_previous": 55.0, "macd12_26": 2.0, "macd_signal9": 1.0,
    }
    assert all(confirmation_status(values).values())
    values[field] = value
    assert not all(confirmation_status(values).values())


def test_rsi_just_above_25_and_rising_passes():
    values = {
        "premium": 100.0, "trigger_low": 95.0, "trigger_high": 102.0, "ema9": 80.0,
        "rsi14": 25.0001, "rsi14_previous": 25.0, "macd12_26": 2.0, "macd_signal9": 1.0,
    }
    assert confirmation_status(values)["rsi14_above_25_and_rising"]


@pytest.mark.parametrize(
    ("price", "low", "high", "ema_value", "position", "ok"),
    [
        (105, 102, 106, 100, "above", True),
        (105, 99, 106, 100, "straddle", True),
        (99.5, 99, 101, 100, "straddle", True),     # price below EMA but candle straddles: allowed
        (100, 100, 101, 100, "straddle", True),     # low touching EMA is straddling
        (99, 97, 100, 100, "straddle", True),       # high touching EMA is straddling
        (99, 97, 99.5, 100, "below", False),        # wholly below EMA: invalid
    ],
)
def test_trigger_ema_above_straddle_below(price, low, high, ema_value, position, ok):
    assert trigger_ema_position(price, low, high, ema_value) == position
    assert trigger_ema_ok(price, low, high, ema_value) is ok


@pytest.mark.parametrize(
    "evidence",
    [(None, 99, 101, 100), (100, None, 101, 100), (100, 99, None, 100), (100, 99, 101, None),
     (float("nan"), 99, 101, 100), (102, 99, 101, 100), (98, 99, 101, 100)],
)
def test_missing_or_inconsistent_trigger_evidence_fails_closed(evidence):
    with pytest.raises(IndicatorDataError, match="missing_trigger_evidence"):
        trigger_ema_position(*evidence)


@pytest.mark.parametrize(
    ("red", "expected"),
    [((106, 108, 104.5, 105), "above"), ((103, 106, 95, 98), "straddle"), ((97, 97.5, 93, 94), "below")],
)
def test_reference_red_candle_may_sit_anywhere_relative_to_ema(red, expected):
    today = list(SIDEWAYS_DAY)
    today[7] = red
    candles, now = build(today)
    closed = completed_candles(candles, now)
    red_index = len(closed) - 2
    red_ema = ema([c["close"] for c in closed], 9)[red_index]
    placement = (
        "above" if red[2] > red_ema else "below" if red[1] < red_ema else "straddle"
    )
    assert placement == expected
    result = calculate_confirmations(candles, now)
    assert result["passed"]["trigger_above_or_straddles_ema9"] is True


def test_completed_trigger_wholly_below_ema_fails():
    today = list(NORMAL_DAY)
    today[-1] = (85, 86, 80, 81)
    candles, now = build(today)
    result = calculate_confirmations(candles, now)
    assert result["ema9_position"] == "below"
    assert result["passed"]["trigger_above_or_straddles_ema9"] is False
    assert not result["ready"]


def test_live_snapshot_is_pure_and_uses_observed_tick_extremes():
    candles, _ = build(SIDEWAYS_DAY[:-1])
    now = SESSION_START + timedelta(minutes=80, seconds=40)
    before = [dict(c) for c in candles]
    provisional = {
        "timestamp": (SESSION_START + timedelta(minutes=80)).timestamp(),
        "open": 106.5, "high": 107.5, "low": 106.5, "close": 107,
        "observed_at": now.timestamp(),
    }
    first = calculate_confirmations(candles, now, provisional)
    second = calculate_confirmations(candles, now, provisional)
    assert first == second
    assert candles == before and len(candles) == 46
    assert first["evidence_mode"] == "live_provisional_ltp"
    assert first["trigger_evidence"] == "live_observed_ticks"
    assert first["values"]["premium"] == 107
    assert first["values"]["trigger_low"] == 106.5 and first["values"]["trigger_high"] == 107.5
    assert first["previous_indicator_as_of"] == "2026-10-05T10:25:00+05:30"
    assert first["live_quote_as_of"] == "2026-10-05T10:35:40+05:30"
    assert first["anti_chop"]["window_end"] == "2026-10-05T10:35:00+05:30"


def test_live_trigger_straddle_passes_and_wholly_below_fails():
    candles, _ = build(SIDEWAYS_DAY[:-1])
    now = SESSION_START + timedelta(minutes=80, seconds=40)
    base = {"timestamp": (SESSION_START + timedelta(minutes=80)).timestamp(), "observed_at": now.timestamp()}
    ema_now = calculate_confirmations(
        candles, now, {**base, "open": 100, "high": 100, "low": 100, "close": 100}
    )["values"]["ema9"]
    straddle = calculate_confirmations(
        candles, now, {**base, "open": ema_now - 1, "high": ema_now + 1, "low": ema_now - 1, "close": ema_now - 0.5}
    )
    assert straddle["ema9_position"] == "straddle"
    assert straddle["passed"]["trigger_above_or_straddles_ema9"]
    below = calculate_confirmations(
        candles, now, {**base, "open": 90, "high": 90.5, "low": 89, "close": 90}
    )
    assert below["ema9_position"] == "below"
    assert not below["passed"]["trigger_above_or_straddles_ema9"]


def test_invalid_future_or_stale_bucket_provisional_live_candle_fails_closed():
    candles, now = build(SIDEWAYS_DAY[:-1])
    provisional = {
        "timestamp": (SESSION_START + timedelta(minutes=80)).timestamp(),
        "open": 107, "high": 108, "low": 106, "close": float("nan"),
        "observed_at": now.timestamp(),
    }
    with pytest.raises(IndicatorDataError, match="invalid_provisional_candle"):
        calculate_confirmations(candles, now, provisional)
    provisional["close"] = 107
    provisional["timestamp"] = (now + timedelta(seconds=1)).timestamp()
    with pytest.raises(IndicatorDataError, match="invalid_provisional_candle"):
        calculate_confirmations(candles, now, provisional)
    provisional["timestamp"] = (SESSION_START + timedelta(minutes=70)).timestamp()  # already completed bar
    with pytest.raises(IndicatorDataError, match="invalid_provisional_candle"):
        calculate_confirmations(candles, now, provisional)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda candle: candle.pop("close"),
        lambda candle: candle.update(high=float("inf")),
        lambda candle: candle.update(low=0),
    ],
)
def test_invalid_or_missing_ohlc_fails_closed(mutate):
    candles, now = build(NORMAL_DAY)
    mutate(candles[-1])
    with pytest.raises(IndicatorDataError, match="invalid_ohlc"):
        calculate_confirmations(candles, now)


@pytest.mark.parametrize("volume", [None, float("nan"), -1, "bad", 0])
def test_missing_or_invalid_volume_does_not_block(volume):
    candles, now = build(NORMAL_DAY)
    for candle in candles:
        candle["volume"] = volume
    assert calculate_confirmations(candles, now)["ready"]


def test_stale_and_inadequate_warmup_fail_closed():
    candles, now = build(NORMAL_DAY)
    with pytest.raises(IndicatorDataError, match="stale_candles"):
        calculate_confirmations(candles[:-3], now)
    with pytest.raises(IndicatorDataError, match="insufficient_history"):
        calculate_confirmations(candles[-33:], now)


def test_candles_are_deduplicated_ordered_and_future_bars_excluded():
    candles, now = build(NORMAL_DAY)
    duplicate = dict(candles[-1], close=111.5, high=113)
    future = dict(candles[-1], timestamp=(now + timedelta(minutes=20)).isoformat())
    normalized = completed_candles([candles[-2], duplicate, candles[-3], candles[-1], future], now)
    assert [candle["timestamp"] for candle in normalized] == sorted(
        candle["timestamp"] for candle in normalized
    )
    assert len(normalized) == 3
    assert normalized[-1]["close"] == candles[-1]["close"]


# --------------------------------------------------------------- Option A anti-chop
def test_user_fixture_90_to_110_around_100_is_exactly_20_percent_sideways():
    window = bars([(110, 100), (105, 95), (104, 90), (108, 99), (102, 96), (106, 98), (103, 97), (101, 99)])
    trigger = SESSION_START + timedelta(minutes=80)
    status = anti_chop_status(window, trigger.timestamp())
    assert status["window_high"] == 110 and status["window_low"] == 90
    assert status["mid_price"] == 100
    assert status["range_fraction"] == 0.2
    assert status["sideways"] is True          # inclusive boundary


def test_range_above_20_percent_is_normal_and_just_below_is_sideways():
    trigger = (SESSION_START + timedelta(minutes=80)).timestamp()
    wide = bars([(110, 100)] * 7 + [(110, 89.9)])
    assert anti_chop_status(wide, trigger)["sideways"] is False
    narrow = bars([(110, 100)] * 7 + [(110, 90.1)])
    assert anti_chop_status(narrow, trigger)["sideways"] is True


def test_anti_chop_uses_only_eight_bars_before_trigger_and_fails_closed():
    trigger = (SESSION_START + timedelta(minutes=80)).timestamp()
    # A huge earlier bar outside the 8-bar window and a trigger/later bar are ignored.
    history = bars([(500, 1)] + [(105, 95)] * 8 + [(900, 1)], start=SESSION_START - timedelta(minutes=10))
    status = anti_chop_status(history, trigger)
    assert status["bars"] == 8 and status["window_high"] == 105 and status["window_low"] == 95
    with pytest.raises(IndicatorDataError, match="insufficient_anti_chop_history"):
        anti_chop_status(bars([(105, 95)] * 7, start=SESSION_START + timedelta(minutes=10)), trigger)
    gapped = bars([(105, 95)] * 8)
    for bar in gapped[:4]:
        bar["_timestamp_epoch"] -= 3600
    with pytest.raises(IndicatorDataError, match="anti_chop_session_gap"):
        anti_chop_status(gapped, trigger)
    with pytest.raises(IndicatorDataError, match="invalid_anti_chop_range"):
        anti_chop_status(bars([(0, 0)] * 8), trigger)


def test_previous_session_bars_are_not_bridged_for_anti_chop():
    candles, now = build(NORMAL_DAY[:5])  # only 4 same-session bars precede the trigger
    with pytest.raises(IndicatorDataError, match="insufficient_anti_chop_history|anti_chop_session_gap"):
        calculate_confirmations(candles, now)


def test_completed_fixture_classification():
    assert calculate_confirmations(*build(SIDEWAYS_DAY))["anti_chop"]["sideways"] is True
    assert calculate_confirmations(*build(NORMAL_DAY))["anti_chop"]["sideways"] is False


# --------------------------------------------------------------- continuation
def test_normal_mode_needs_only_strict_base_breakout():
    status = continuation_status(sideways=False, price=100.05, red_high=100, initial_stop=90)
    assert status["qualified"] and status["mode"] == MODE_NORMAL
    equal = continuation_status(sideways=False, price=100, red_high=100, initial_stop=90, live_polls=5)
    assert not equal["qualified"] and equal["reason"] == "no_breakout"


def test_sideways_two_distinct_polls_or_0_3r_are_or_alternatives():
    one = continuation_status(sideways=True, price=101, red_high=100, initial_stop=90, live_polls=1)
    assert not one["qualified"] and one["mode"] == MODE_PENDING
    assert one["r"] == 10 and one["momentum_threshold"] == 103
    two = continuation_status(sideways=True, price=101, red_high=100, initial_stop=90, live_polls=2)
    assert two["qualified"] and two["mode"] == MODE_TWO_POLLS
    at_threshold = continuation_status(sideways=True, price=103, red_high=100, initial_stop=90)
    assert at_threshold["qualified"] and at_threshold["mode"] == MODE_MOMENTUM
    below = continuation_status(sideways=True, price=102.99, red_high=100, initial_stop=90)
    assert not below["qualified"]


def test_momentum_branch_still_requires_base_breakout_and_valid_r():
    no_base = continuation_status(sideways=True, price=100, red_high=100, initial_stop=90, live_polls=3)
    assert not no_base["qualified"]
    invalid_r = continuation_status(sideways=True, price=150, red_high=100, initial_stop=100)
    assert not invalid_r["qualified"] and invalid_r["r"] is None
    assert invalid_r["reason"] == "awaiting_polls_invalid_r"
    # Two genuine polls remain an alternative even when R is invalid.
    assert continuation_status(sideways=True, price=150, red_high=100, initial_stop=100, live_polls=2)["qualified"]


def test_completed_candle_momentum_uses_actual_close_not_wick():
    wick = continuation_status(
        sideways=True, price=104, red_high=100, initial_stop=90, continuation_price=101
    )
    assert not wick["qualified"]
    held = continuation_status(
        sideways=True, price=104, red_high=100, initial_stop=90, continuation_price=103
    )
    assert held["qualified"] and held["mode"] == MODE_MOMENTUM


def test_completed_sideways_fixture_is_pending_without_live_polls():
    candles, now = build(SIDEWAYS_DAY)
    result = calculate_confirmations(candles, now)
    assert result["ready"] and result["anti_chop"]["sideways"]
    # Red 103/106/95/98: stop 90.25, R 15.75, 0.3R level 110.725; close 109 is below it.
    status = continuation_status(
        sideways=True, price=110, red_high=106, initial_stop=90.25, continuation_price=109
    )
    assert status["momentum_threshold"] == pytest.approx(110.725)
    assert not status["qualified"]
