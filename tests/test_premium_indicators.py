from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from premium_indicators import (
    IndicatorDataError,
    calculate_confirmations,
    completed_candles,
    confirmation_status,
    ema,
    macd,
    parabolic_sar,
    wilder_rsi,
)


IST = ZoneInfo("Asia/Kolkata")
INSTRUMENT = SimpleNamespace(symbol="NIFTY", category="index_options")
NOW = datetime(2026, 10, 5, 10, 31, tzinfo=IST)


def passing_candles():
    candles = []
    for index in range(38):
        start = datetime(2026, 10, 2, 9, 15, tzinfo=IST) + timedelta(minutes=10 * index)
        close = 80 + index * 0.25 + (0.15 if index % 3 else -0.1)
        open_price = close - 0.1 if index % 4 else close + 0.08
        candles.append({
            "timestamp": start.isoformat(), "open": open_price, "high": max(open_price, close) + 0.4,
            "low": min(open_price, close) - 0.4, "close": close, "volume": 100 + index,
        })
    for stamp, open_price, high, low, close in (
        ("09:15", 99, 101, 98, 100), ("09:25", 100, 102, 99, 101),
        ("09:35", 101, 103, 100, 102), ("09:45", 102, 104, 101, 103),
        ("09:55", 103, 104, 100, 101), ("10:05", 100, 110, 90, 98),
        ("10:15", 98, 113, 97, 112),
    ):
        hour, minute = map(int, stamp.split(":"))
        start = datetime(2026, 10, 5, hour, minute, tzinfo=IST)
        candles.append({
            "timestamp": start.isoformat(), "open": open_price, "high": high, "low": low,
            "close": close, "volume": 100,
        })
    return candles


def test_known_ema_wilder_rsi_macd_and_psar_fixtures():
    assert ema([1, 2, 3, 4, 5], 3) == [None, None, 2.0, 3.0, 4.0]
    assert wilder_rsi(list(range(15))) == [None] * 14 + [100.0]
    assert wilder_rsi([5] * 15)[-1] == 50.0
    assert wilder_rsi(list(range(15, 0, -1)))[-1] == 0.0
    line, signal = macd(list(range(1, 35)))
    assert line[24] is None and line[25] == pytest.approx(7.0)
    assert signal[32] is None and signal[33] == pytest.approx(7.0)
    psar = parabolic_sar([10, 12, 13, 12, 11, 8], [8, 9, 10, 9, 7, 6])
    assert psar == [None, 8, 8, pytest.approx(8.2), 13, pytest.approx(12.88)]


def test_all_five_confirmations_pass_and_vwap_uses_only_today_session_volume():
    candles = passing_candles()
    expected = calculate_confirmations(candles, NOW, INSTRUMENT)
    for candle in candles[:-7]:
        candle["volume"] = 1e12
    actual = calculate_confirmations(candles, NOW, INSTRUMENT)
    assert actual["passed"] == {
        "above_vwap": True,
        "rsi14_above_30_and_rising": True,
        "above_ema9": True,
        "macd_above_signal": True,
        "psar_below_premium": True,
    }
    assert actual["values"] == expected["values"]
    assert actual["vwap_volume"] == 700
    assert actual["vwap_as_of"] == "2026-10-05T10:25:00+05:30"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("vwap", 100.0),
        ("rsi14", 30.0),
        ("rsi14_previous", 60.0),
        ("rsi14_previous", 65.0),
        ("ema9", 100.0),
        ("macd_signal9", 2.0),
        ("psar_0_02_0_2", 100.0),
    ],
)
def test_each_confirmation_is_strict_and_mandatory(field, value):
    values = {
        "premium": 100.0, "vwap": 90.0, "rsi14": 60.0, "rsi14_previous": 55.0,
        "ema9": 80.0, "macd12_26": 2.0, "macd_signal9": 1.0, "psar_0_02_0_2": 90.0,
    }
    values[field] = value
    assert not all(confirmation_status(values).values())


def test_live_snapshot_is_pure_and_keeps_vwap_on_real_completed_volume():
    candles = passing_candles()
    provisional = {
        "timestamp": datetime(2026, 10, 5, 10, 25, tzinfo=IST).timestamp(),
        "open": 112, "high": 114, "low": 111, "close": 113,
    }
    first = calculate_confirmations(candles, NOW, INSTRUMENT, provisional)
    second = calculate_confirmations(candles, NOW, INSTRUMENT, provisional)
    assert first == second
    assert first["evidence_mode"] == "live_provisional_ltp"
    assert first["values"]["premium"] == 113
    assert first["values"]["rsi14"] > first["values"]["rsi14_previous"]
    assert first["vwap_volume"] == 700
    assert len(candles) == 45


@pytest.mark.parametrize(
    "mutate",
    [
        lambda candle: candle.pop("volume"),
        lambda candle: candle.update(volume=float("nan")),
        lambda candle: candle.update(high=float("inf")),
        lambda candle: candle.update(volume=-1),
    ],
)
def test_invalid_or_missing_ohlcv_fails_closed(mutate):
    candles = passing_candles()
    mutate(candles[-1])
    with pytest.raises(IndicatorDataError):
        calculate_confirmations(candles, NOW, INSTRUMENT)


def test_zero_session_volume_and_inadequate_warmup_fail_closed():
    candles = passing_candles()
    for candle in candles[-7:]:
        candle["volume"] = 0
    with pytest.raises(IndicatorDataError, match="invalid_ohlcv"):
        calculate_confirmations(candles, NOW, INSTRUMENT)
    with pytest.raises(IndicatorDataError, match="stale_candles"):
        calculate_confirmations(passing_candles()[:-7], NOW, INSTRUMENT)
    with pytest.raises(IndicatorDataError, match="insufficient_history"):
        calculate_confirmations(passing_candles()[-33:], NOW, INSTRUMENT)


def test_candles_are_deduplicated_ordered_and_future_bars_excluded():
    candles = passing_candles()
    duplicate = dict(candles[-1], close=111.5, high=113)
    future = dict(candles[-1], timestamp=(NOW + timedelta(minutes=20)).isoformat())
    normalized = completed_candles([candles[-2], duplicate, candles[-3], candles[-1], future], NOW)
    assert [candle["timestamp"] for candle in normalized] == sorted(
        candle["timestamp"] for candle in normalized
    )
    assert len(normalized) == 3
    assert normalized[-1]["close"] == candles[-1]["close"]


def test_zero_current_session_volume_fails_even_with_prior_day_volume():
    candles = passing_candles()
    for candle in candles[-7:]:
        candle["volume"] = 0
    with pytest.raises(IndicatorDataError, match="invalid_ohlcv"):
        calculate_confirmations(candles, NOW, INSTRUMENT)
