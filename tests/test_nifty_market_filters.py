from datetime import datetime, timedelta
from collections import OrderedDict
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from nifty_market_filters import IST, NiftyMarketFilter, market_context
from premium_indicators import IndicatorDataError, completed_candles


NOW = datetime(2026, 10, 16, 11, 1, tzinfo=IST)


def spot_candles(direction=1, volume=100):
    prior = datetime(2026, 10, 15, 9, 15, tzinfo=IST)
    current = NOW.replace(hour=9, minute=15)
    times = [prior + timedelta(minutes=15 * i) for i in range(25)]
    times += [current + timedelta(minutes=15 * i) for i in range(7)]
    result = []
    for index, stamp in enumerate(times):
        price = 25000 + direction * index * 10
        result.append(dict(timestamp=stamp.timestamp(), open=price, high=price + 2,
                           low=price - 2, close=price, volume=volume))
    return result


def gate(monkeypatch, *, direction=True, sr=False):
    monkeypatch.setenv("NIFTY_DIRECTION_FILTER_ENABLED", str(direction))
    monkeypatch.setenv("NIFTY_SR_FILTER_ENABLED", str(sr))
    instance = NiftyMarketFilter(MagicMock())
    instance._candles = spot_candles()
    return instance


@pytest.mark.parametrize("direction,allowed,blocked", [(1, "CE", "PE"), (-1, "PE", "CE")])
def test_direction_regimes(monkeypatch, direction, allowed, blocked):
    instance = gate(monkeypatch)
    instance._candles = spot_candles(direction)
    assert instance.rejection(allowed, {}, NOW) is None
    assert instance.rejection(blocked, {}, NOW) == "direction_filter_reject"


def test_neutral_policies(monkeypatch):
    instance = gate(monkeypatch)
    instance._candles = spot_candles(0)
    for side in ("CE", "PE"):
        assert instance.rejection(side, {}, NOW) == "direction_filter_reject"
    instance.neutral_policy = "stronger"
    assert instance.rejection("CE", {"breakout_price": 100.4, "reference_high": 100}, NOW)
    assert instance.rejection("PE", {"breakout_price": 100.6, "reference_high": 100}, NOW) is None


@pytest.mark.parametrize("side,direction", [("CE", 1), ("PE", -1)])
def test_sr_near_previous_day_level_and_broken_level(monkeypatch, side, direction):
    instance = gate(monkeypatch, direction=False, sr=True)
    bars = spot_candles(direction)
    spot = bars[-1]["close"]
    for bar in bars[:25]:
        if side == "CE":
            bar["high"] = spot + 10
        else:
            bar["low"] = spot - 10
    instance._candles = bars
    assert instance.rejection(side, {}, NOW) == "sr_filter_reject"
    instance.buffer_pct = 0
    assert instance.rejection(side, {}, NOW) is None


def test_sr_swing_and_pivot_levels():
    bars = spot_candles(0)
    bars[-3]["high"] += 50
    bars[-3]["low"] -= 50
    context = market_context(bars, NOW)
    assert 25052 in context["resistances"]
    assert 24948 in context["supports"]
    assert 25000 in context["resistances"]
    assert 25000 in context["supports"]


@pytest.mark.parametrize("volume,kind", [(100, "vwap"), (0, "twap"), (None, "twap")])
def test_session_average_and_no_future_candles(volume, kind):
    bars = spot_candles(volume=volume)
    expected = market_context(bars, NOW)
    bars.append(dict(timestamp=NOW.timestamp(), open=30000, high=30000, low=30000, close=30000))
    assert market_context(bars, NOW) == expected
    assert expected["average_kind"] == kind
    assert expected["as_of"] <= NOW.timestamp()


def test_vwap_uses_session_only():
    bars = spot_candles()
    bars[-1]["volume"] = 1000
    context = market_context(bars, NOW)
    session = bars[25:]
    expected = sum(bar["close"] * bar["volume"] for bar in session) / sum(bar["volume"] for bar in session)
    assert context["session_average"] == pytest.approx(expected)


@pytest.mark.parametrize("mode", ["missing", "stale", "invalid", "previous_day"])
def test_enabled_gates_fail_closed(monkeypatch, mode):
    instance = gate(monkeypatch, sr=True)
    if mode == "missing":
        instance._candles = []
    elif mode == "stale":
        instance._candles = instance._candles[:25]
    elif mode == "invalid":
        instance._candles[-1]["close"] = float("nan")
    else:
        instance._candles = instance._candles[25:]
        instance.direction_enabled = False
    assert instance.rejection("CE", {}, NOW) in {"direction_filter_reject", "sr_filter_reject"}


def test_underlying_cache_and_failure_retry(monkeypatch):
    instance = gate(monkeypatch)
    manager = instance.data_manager
    manager.resolve_underlying.return_value = SimpleNamespace(
        security_id=13, exchange_segment="IDX_I", instrument_type="INDEX")
    manager.fetch_contract_candles.return_value = spot_candles()
    instance.refresh(NOW)
    instance.refresh(NOW + timedelta(seconds=10))
    assert manager.fetch_contract_candles.call_count == 1
    args, kwargs = manager.fetch_contract_candles.call_args
    assert args == (13, "IDX_I", "INDEX", "2026-10-15", "2026-10-16", 15)
    assert kwargs["now"] == NOW.timestamp()
    manager.fetch_contract_candles.side_effect = RuntimeError("offline")
    instance.refresh(NOW + timedelta(minutes=15))
    assert instance._candles == []
    instance.refresh(NOW + timedelta(minutes=16))
    assert manager.fetch_contract_candles.call_count == 2
    instance.refresh(NOW + timedelta(minutes=17))
    assert manager.fetch_contract_candles.call_count == 3


def test_disabled_makes_no_requests(monkeypatch):
    instance = gate(monkeypatch, direction=False)
    instance.refresh(NOW)
    instance.data_manager.resolve_underlying.assert_not_called()
    assert instance.rejection("PE", {}, NOW) is None


def test_invalid_config_is_conservative(monkeypatch):
    monkeypatch.setenv("NIFTY_SR_BUFFER_PCT", "nan")
    monkeypatch.setenv("NIFTY_DIRECTION_HTF_MINUTES", "10")
    monkeypatch.setenv("NIFTY_DIRECTION_NEUTRAL_POLICY", "anything")
    instance = gate(monkeypatch)
    assert instance.buffer_pct == 0.10
    assert instance.minutes == 15
    assert instance.neutral_policy == "block"


def test_stale_completion_rejected():
    with pytest.raises(IndicatorDataError, match="underlying_stale"):
        market_context(spot_candles(), NOW + timedelta(minutes=35))


def test_custom_timeframe_requires_complete_bar():
    bars = [dict(timestamp=NOW.timestamp() - 600, open=100, high=101, low=99, close=100)]
    assert len(completed_candles(bars, NOW)) == 1
    assert len(completed_candles(bars, NOW, timeframe_seconds=300)) == 1
    assert completed_candles(bars, NOW, timeframe_seconds=900) == []


def test_wrong_underlying_provenance_blocks_without_candle_request(monkeypatch):
    instance = gate(monkeypatch)
    instance.data_manager.resolve_underlying.return_value = SimpleNamespace(
        security_id=123, exchange_segment="NSE_FNO", instrument_type="FUTIDX")
    instance.refresh(NOW)
    instance.data_manager.fetch_contract_candles.assert_not_called()
    assert instance.rejection("CE", {}, NOW) == "direction_filter_reject"


def test_empty_fetch_retries_after_backoff(monkeypatch):
    instance = gate(monkeypatch)
    instance.data_manager.resolve_underlying.return_value = SimpleNamespace(
        security_id=13, exchange_segment="IDX_I", instrument_type="INDEX")
    instance.data_manager.fetch_contract_candles.return_value = []
    instance.refresh(NOW)
    instance.refresh(NOW + timedelta(seconds=119))
    assert instance.data_manager.fetch_contract_candles.call_count == 1
    instance.refresh(NOW + timedelta(seconds=120))
    assert instance.data_manager.fetch_contract_candles.call_count == 2


def test_delayed_latest_bar_does_not_cache_for_whole_bucket(monkeypatch):
    instance = gate(monkeypatch)
    instance.data_manager.resolve_underlying.return_value = SimpleNamespace(
        security_id=13, exchange_segment="IDX_I", instrument_type="INDEX")
    instance.data_manager.fetch_contract_candles.return_value = spot_candles()[:-1]
    instance.refresh(NOW)
    instance.refresh(NOW + timedelta(seconds=119))
    assert instance.data_manager.fetch_contract_candles.call_count == 1
    instance.data_manager.fetch_contract_candles.return_value = spot_candles()
    instance.refresh(NOW + timedelta(seconds=120))
    assert instance.data_manager.fetch_contract_candles.call_count == 2
    assert instance.rejection("CE", {}, NOW + timedelta(seconds=120)) is None


def test_delayed_retry_refreshes_real_shared_candle_cache(monkeypatch):
    from data_manager import DataManager

    monkeypatch.setattr("data_manager._apply_rate_limit", lambda: None)
    manager = DataManager.__new__(DataManager)
    manager._contract_cache = OrderedDict()
    manager._contract_cache_lock = threading.RLock()
    manager.resolve_underlying = MagicMock(return_value=SimpleNamespace(
        security_id=13, exchange_segment="IDX_I", instrument_type="INDEX"))
    manager._fetch_dhan_intraday_data = MagicMock(side_effect=[
        spot_candles()[:-1], spot_candles()])
    instance = gate(monkeypatch)
    instance.data_manager = manager
    instance.refresh(NOW)
    instance.refresh(NOW + timedelta(seconds=120))
    assert manager._fetch_dhan_intraday_data.call_count == 2
    assert instance.rejection("CE", {}, NOW + timedelta(seconds=120)) is None
    result = manager.fetch_contract_candles(
        13, "IDX_I", "INDEX", "2026-10-15", "2026-10-16", 15,
        now=(NOW + timedelta(seconds=125)).timestamp())
    assert len(result) == len(spot_candles())
    assert manager._fetch_dhan_intraday_data.call_count == 2
