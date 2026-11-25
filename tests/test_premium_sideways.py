from datetime import timedelta

import pytest

from premium_sideways import rejection_reason
from copy import deepcopy

from premium_structure import evaluate_structure
from signal_lifecycle import SignalLifecycle
from tests.test_premium_structure import (
    ASSETS, OPEN, asset_payload, bars, make_live_scanner, signal_for, structure_history,
)


@pytest.mark.parametrize("count", [2, 3, 4, 5, 7, 12])
def test_sideways_period_has_no_fixed_five_candle_requirement(count):
    five = bars([(100, 110, 90, 100)] * count)
    now = OPEN + timedelta(minutes=5 * count, seconds=20)
    assert rejection_reason(five, [], 105, now.timestamp(), now) == "sideways_range"


def test_band_uses_previous_candle_midpoint_not_current_candle():
    five = bars([(410, 430, 403, 415), (415, 456, 400, 440)])
    now = OPEN + timedelta(minutes=10, seconds=20)
    assert rejection_reason(five, [], 450, now.timestamp(), now) == "sideways_range"


@pytest.mark.parametrize("close", [94, 106])
def test_exact_band_edges_are_still_sideways(close):
    five = bars([(100, 110, 90, 100), (100, 110, 90, close)])
    now = OPEN + timedelta(minutes=10, seconds=20)
    assert rejection_reason(five, [], 107, now.timestamp(), now) == "sideways_range"


def test_closed_breakout_requires_later_one_minute_close_above_its_high():
    five = bars([(100, 110, 90, 100), (100, 110, 90, 100),
                 (100, 120, 99, 107)])
    now = OPEN + timedelta(minutes=16, seconds=5)
    assert rejection_reason(five, [], 121, now.timestamp(), now) == (
        "sideways_one_minute_confirmation_missing")
    one = bars([(107, 121, 106, 120)], 1, OPEN + timedelta(minutes=15))
    assert rejection_reason(five, one, 121, now.timestamp(), now) == (
        "sideways_breakout_high_not_cleared")
    one[-1]["close"] = 121
    assert rejection_reason(five, one, 121, now.timestamp(), now) is None
    assert rejection_reason(five, one, 120, now.timestamp(), now) == (
        "sideways_breakout_high_not_cleared")


def test_forming_five_minute_breakout_cannot_unlock_range():
    five = bars([(100, 110, 90, 100), (100, 110, 90, 100),
                 (100, 120, 99, 107)])
    now = OPEN + timedelta(minutes=14, seconds=59)
    assert rejection_reason(five, [], 121, now.timestamp(), now) == "sideways_range"


def test_downward_break_does_not_unlock_buys():
    five = bars([(100, 110, 90, 100), (100, 110, 90, 100),
                 (100, 101, 80, 85)])
    now = OPEN + timedelta(minutes=15, seconds=20)
    assert rejection_reason(five, [], 100, now.timestamp(), now) == "sideways_range"


def test_wick_above_band_without_close_does_not_unlock_buys():
    five = bars([(100, 110, 90, 100), (100, 120, 90, 105)])
    now = OPEN + timedelta(minutes=10, seconds=20)
    assert rejection_reason(five, [], 121, now.timestamp(), now) == "sideways_range"


def test_downward_escape_can_recover_with_confirmed_upward_break():
    five = bars([(100, 110, 90, 100), (100, 110, 90, 100),
                 (100, 101, 80, 85), (85, 100, 84, 97)])
    now = OPEN + timedelta(minutes=21, seconds=5)
    one = bars([(97, 102, 96, 101)], 1, OPEN + timedelta(minutes=20))
    assert rejection_reason(five, one, 101, now.timestamp(), now) is None


def test_candle_gap_does_not_link_sideways_period_across_sessions():
    five = bars([(100, 110, 90, 100), (100, 110, 90, 100)])
    five += bars([(110, 115, 109, 114)], start=OPEN + timedelta(days=1))
    now = OPEN + timedelta(days=1, minutes=5, seconds=20)
    assert rejection_reason(five, [], 116, now.timestamp(), now) is None


def test_trending_candles_do_not_require_sideways_breakout_confirmation():
    five = bars([(100, 102, 98, 100), (110, 115, 109, 114)])
    now = OPEN + timedelta(minutes=10, seconds=20)
    assert rejection_reason(five, [], 116, now.timestamp(), now) is None


@pytest.mark.parametrize("asset", ASSETS)
@pytest.mark.parametrize("side", ["CE", "PE"])
def test_real_lifecycle_blocks_in_range_retests_across_assets_and_restarts(tmp_path, asset, side):
    symbol, category, _, _ = asset
    signal, now, _ = signal_for("order_block_retest", symbol, side, category)
    payload, _ = asset_payload(signal, *asset)
    path = tmp_path / "signals.db"
    for _ in range(2):
        service = SignalLifecycle(path, clock=lambda: now)
        assert service.open_once(deepcopy(payload)) is None
        assert not service.open_positions()
        assert service.status()["pending_notifications"] == 0


@pytest.mark.parametrize("asset", ASSETS)
@pytest.mark.parametrize("side", ["CE", "PE"])
def test_real_compression_entry_at_next_minute_open_without_minute_close(tmp_path, asset, side):
    symbol, category, _, _ = asset
    five, _, now, _ = structure_history("compression_breakout")
    one = []
    quote = {"price": 86, "bid": 85.95, "ask": 86.05, "trade_timestamp": now.timestamp()}
    signals, _, _ = evaluate_structure(symbol, side, five, one, quote, now, .05, category)
    signal = next(item for item in signals if item["setups"] == ["compression_breakout"])
    payload, _ = asset_payload(signal, *asset)
    service = SignalLifecycle(tmp_path / "signals.db", clock=lambda: now)
    position = service.open_once(payload)
    assert position is not None
    assert position["entry"] == 86
    assert position["initial_stop_loss"] == 76
    assert service.status()["pending_notifications"] == 1


def test_real_scanner_suppresses_inside_range_alert(tmp_path, monkeypatch):
    scanner, service, data, now, quote = make_live_scanner(
        tmp_path, monkeypatch, "order_block_retest")
    data.fetch_quotes.return_value = {("NSE_FNO", 101): quote}
    assert scanner.live_check_once(now) == 0
    assert not service.open_positions()


@pytest.mark.parametrize("minutes", [5, 10, 15, 30, 60, 240])
@pytest.mark.parametrize("name", ["double_bottom", "triple_bottom"])
@pytest.mark.parametrize("symbol", ["NIFTY", "SENSEX"])
def test_real_pattern_neckline_confirmation_replaces_universal_range_gate(tmp_path, minutes, name, symbol):
    from index_swing_strategy import IndexSwingStrategy
    from tests.test_premium_patterns import entry_risk_history, pattern_history

    _, base, now, quote = pattern_history(name=name, minutes=minutes)
    signals, _ = IndexSwingStrategy().evaluate_patterns(
        symbol, "PE", base, quote, now,
        five_candles=entry_risk_history(now, quote["price"]),
    )
    signal = next(item for item in signals if item["timeframe"] == f"{minutes}min")
    asset = next(asset for asset in ASSETS if asset[0] == symbol)
    payload, _ = asset_payload(signal, *asset)
    assert payload["metadata"]["entry_one_minute_candles"]
    assert len(payload["metadata"]["entry_one_minute_candles"]) <= 10
    service = SignalLifecycle(tmp_path / "signals.db", clock=lambda: now)
    assert service.open_once(payload) is not None
    assert len(service.open_positions()) == 1
