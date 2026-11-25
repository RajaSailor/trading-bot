from copy import deepcopy
from datetime import timedelta
import json
from unittest.mock import patch

import pytest

from index_swing_strategy import valid_entry
from premium_indicators import IndicatorDataError
from premium_risk_filter import minimum_distance_stop
from premium_structure import (
    bos_candidate, compression_candidate, evaluate_structure, prepare_zones, support_candidate,
)
from signal_lifecycle import SignalLifecycle
from tests.test_premium_structure import ASSETS, OPEN, asset_payload, bars, bos_history, structure_history


def support_history():
    five = bars([
        (145, 150, 140, 145), (160, 180, 150, 165), (130, 140, 120, 125),
        (100, 105, 90, 95), (105, 120, 100, 115), (105, 110, 97, 100),
        (95, 102, 90, 91), (91, 98, 90.5, 92.7),
    ])
    now = OPEN + timedelta(minutes=40, seconds=5)
    quote = {"price": 92.7, "bid": 92.65, "ask": 92.75, "trade_timestamp": now.timestamp()}
    return five, now, quote


def fvg_history():
    five = bars([
        (130, 140, 125, 135), (140, 180, 130, 150), (110, 120, 90, 100),
        (115, 130, 105, 120), (110, 125, 100, 115), (115, 140, 108, 135),
        (125, 135, 115, 130), (132, 158, 130, 156), (150, 160, 145, 158),
    ])
    one = bars([(150, 151, 140, 143), (143, 151, 142, 150)], 1,
               OPEN + timedelta(minutes=45))
    now = OPEN + timedelta(minutes=47, seconds=5)
    quote = {"price": 150, "bid": 149.95, "ask": 150.05, "trade_timestamp": now.timestamp()}
    return five, one, now, quote


@pytest.mark.parametrize("entry,low,tick,expected", [
    (94.5, 90, .05, 89.75), (92.7, 90.5, .05, 88.05),
    (100, 95, .05, 95), (100, 80, .05, 80),
])
def test_minimum_stop_distance_widens_only_close_stops(entry, low, tick, expected):
    assert minimum_distance_stop(entry, low, tick) == expected


@pytest.mark.parametrize("low", [0, -1, 100, 101, float("nan")])
def test_stop_floor_does_not_manufacture_valid_candle_stop(low):
    with pytest.raises(ValueError):
        minimum_distance_stop(100, low, .05)


@pytest.mark.parametrize("asset", ASSETS)
@pytest.mark.parametrize("side", ["CE", "PE"])
def test_support_second_touch_three_percent_close_and_two_targets(tmp_path, asset, side):
    five, now, quote = support_history()
    symbol, category, segment, _ = asset
    signals, _, _ = evaluate_structure(symbol, side, five, [], quote, now, .05, category)
    signal = next(item for item in signals if item["setups"] == ["support_5m_recovery"])
    assert valid_entry(signal, now)
    assert signal["targets"] == [120, 180]
    assert signal["target"] == 180
    assert signal["stop_loss"] == 88.05
    # Recovery is below both the previous high and the neckline.
    assert signal["entry"] < five[-2]["high"] < 120
    payload, _ = asset_payload(signal, *asset)
    service = SignalLifecycle(tmp_path / "signals.db", clock=lambda: now)
    position = service.open_once(payload)
    assert position is not None
    assert position["targets"] == [120, 180]
    assert service._chart_overlay(position, "OPEN")["targets"] == [120, 180]
    assert "T1: 120.00 | T2: 180.00" in service._signal_message(position, "OPEN")
    key = (segment, 101)
    later = now + timedelta(seconds=1)
    assert service.observe_quotes({key: {"price": 121, "trade_timestamp": later.timestamp()}}, later) == 0
    assert service.open_positions()[0]["t1_reached"] is True
    assert "exit_premium" not in service.open_positions()[0]
    service = SignalLifecycle(service.db_path, clock=lambda: later)
    later += timedelta(seconds=1)
    service.observe_quotes({key: {"price": 122, "trade_timestamp": later.timestamp()}}, later)
    with service._connection() as db:
        assert db.execute("SELECT COUNT(*) FROM lifecycle_outbox WHERE event_id LIKE '%:T1'").fetchone()[0] == 1
        message = db.execute("SELECT message FROM lifecycle_outbox WHERE event_id LIKE '%:T1'").fetchone()[0]
    assert "No exit booked at T1" in message
    assert "P&amp;L" not in message and "Exit premium" not in message
    later += timedelta(seconds=1)
    assert service.observe_quotes({key: {"price": 181, "trade_timestamp": later.timestamp()}}, later) == 1
    assert not service.open_positions()


def test_t1_does_not_prevent_later_stop_or_inflate_realized_advisory_pnl(tmp_path):
    five, now, quote = support_history()
    signals, _, _ = evaluate_structure("NIFTY", "CE", five, [], quote, now, .05)
    signal = next(item for item in signals if item["setups"] == ["support_5m_recovery"])
    service = SignalLifecycle(tmp_path / "signals.db", clock=lambda: now)
    assert service.open_once(asset_payload(signal, *ASSETS[0])[0])
    key = ("NSE_FNO", 101)
    for offset, price in [(1, 121), (2, 87)]:
        moment = now + timedelta(seconds=offset)
        service.observe_quotes({key: {"price": price, "trade_timestamp": moment.timestamp()}}, moment)
    with service._connection() as db:
        position = json.loads(db.execute("SELECT data FROM lifecycle_signals").fetchone()[0])
    assert position["status"] == "STOP" and position["t1_reached"] is True
    assert position["pnl"] == -285


@pytest.mark.parametrize("mutation", ["below_threshold", "no_second_touch", "failed_support", "replay"])
def test_support_requires_fresh_recovery_after_separated_second_touch(mutation):
    five, now, _ = support_history()
    if mutation == "below_threshold":
        five[-1]["close"] = 92.69
    elif mutation == "no_second_touch":
        five[-2].update(low=100, open=101, close=101)
        five[-1].update(low=100, open=101, close=102, high=103)
    elif mutation == "failed_support":
        five[-2].update(low=88, close=89)
    else:
        five[-2]["close"] = 93
    with pytest.raises(IndicatorDataError, match="support_5m_retest_missing"):
        support_candidate(five, None, now)


@pytest.mark.parametrize("asset", ASSETS)
@pytest.mark.parametrize("side", ["CE", "PE"])
def test_fvg_requires_bullish_structure_touch_and_later_one_minute_recovery(tmp_path, asset, side):
    five, one, now, quote = fvg_history()
    symbol, category, _, _ = asset
    signals, _, _ = evaluate_structure(symbol, side, five, one, quote, now, .05, category)
    signal = next(item for item in signals if item["setups"] == ["fvg_retest"])
    assert signal["entry"] > signal["premium_zone"]["high"]
    assert signal["stop_loss"] == 142.5 and signal["target"] == 180
    assert valid_entry(signal, now)
    service = SignalLifecycle(tmp_path / "signals.db", clock=lambda: now)
    assert service.open_once(asset_payload(signal, *asset)[0]) is not None


@pytest.mark.parametrize("mutation,reason", [
    ("touch_only", "index_fvg_confirmation_missing"),
    ("same_candle_recovery", "index_fvg_confirmation_missing"),
    ("below_gap", "index_fvg_support_failed"),
    ("lower_low", "index_fvg_bullish_structure_missing"),
    ("stale", "index_fvg_confirmation_stale"),
])
def test_fvg_rejects_unconfirmed_failed_or_countertrend_retests(mutation, reason):
    five, one, now, quote = fvg_history()
    if mutation == "touch_only":
        one[-1]["close"] = 144
    elif mutation == "same_candle_recovery":
        one = one[:1]
        one[0]["close"] = 150
    elif mutation == "below_gap":
        one[0].update(low=132, close=134)
    elif mutation == "lower_low":
        five[4]["low"] = 85
    else:
        now += timedelta(seconds=71)
        quote["trade_timestamp"] = now.timestamp()
    _, diagnostics, _ = evaluate_structure("NIFTY", "PE", five, one, quote, now, .05)
    assert next(item for item in diagnostics if item["route"] == "fvg_retest")["reason"] == reason


def test_fvg_remains_available_after_closed_retest_but_not_failed_support():
    five, _, now, _ = fvg_history()
    five += bars([(150, 151, 140, 150)], start=OPEN + timedelta(minutes=45))
    later = OPEN + timedelta(minutes=50, seconds=5)
    assert any(zone["route"] == "fvg_retest" and zone["low"] == 135
               for zone in prepare_zones(five, later))
    five[-1].update(low=132, close=134)
    assert not any(zone["route"] == "fvg_retest" and zone["low"] == 135
                   for zone in prepare_zones(five, later))


def test_compression_midpoint_is_fixed_and_not_recalculated_from_breakout():
    five, one, now, _ = structure_history()
    candidate = compression_candidate(five, one, now)
    assert candidate["midpoint"] == 74.5 and candidate["range_candles"] == 2
    assert five[-1]["low"] > candidate["midpoint"]
    five[-1]["high"] = 100
    assert compression_candidate(five, [], now)["midpoint"] == 74.5
    five[-1]["low"] = 74.5
    with pytest.raises(IndicatorDataError, match="range_missing"):
        compression_candidate(five, [], now)


def test_bos_is_not_a_live_quote_reclaim_without_completed_minute_confirmation():
    five, one, now = bos_history()
    candidate = bos_candidate(five, one, now, .05, 130.2, now.timestamp())
    assert candidate["structure_break"] == "bullish_choch"
    with pytest.raises(IndicatorDataError, match="rejection_reclaim_missing"):
        bos_candidate(five, [], now, .05, 130.2, now.timestamp(), [129.9, now.timestamp() - 1])
    one[0]["close"] = candidate["boundary"]
    with pytest.raises(IndicatorDataError, match="rejection_reclaim_missing"):
        bos_candidate(five, one, now, .05, 130.2, now.timestamp())


def test_target_evidence_cannot_be_changed_to_invent_a_second_target(tmp_path):
    five, now, quote = support_history()
    signals, _, _ = evaluate_structure("NIFTY", "CE", five, [], quote, now, .05)
    signal = deepcopy(next(item for item in signals if item["setups"] == ["support_5m_recovery"]))
    signal["targets"] = [120, 200]
    assert not valid_entry(signal, now)


def test_off_tick_primary_high_does_not_hide_farther_second_target():
    five, now, quote = support_history()
    five[4]["high"] = 120.03
    signals, _, _ = evaluate_structure("NIFTY", "CE", five, [], quote, now, .05)
    signal = next(item for item in signals if item["setups"] == ["support_5m_recovery"])
    assert signal["targets"] == [120, 180]
    assert valid_entry(signal, now)


def test_missing_higher_swing_uses_single_target_exit(tmp_path):
    five, now, quote = support_history()
    signals, _, _ = evaluate_structure("NIFTY", "CE", five[2:], [], quote, now, .05)
    signal = next(item for item in signals if item["setups"] == ["support_5m_recovery"])
    assert signal["targets"] == [120] and signal["target"] == 120
    service = SignalLifecycle(tmp_path / "signals.db", clock=lambda: now)
    assert service.open_once(asset_payload(signal, *ASSETS[0])[0])
    later = now + timedelta(seconds=1)
    assert service.observe_quotes(
        {("NSE_FNO", 101): {"price": 121, "trade_timestamp": later.timestamp()}}, later) == 1
    with service._connection() as db:
        assert db.execute("SELECT COUNT(*) FROM lifecycle_outbox WHERE event_id LIKE '%:T1'").fetchone()[0] == 0


def test_farther_target_cannot_rescue_insufficient_reward_to_primary(tmp_path, caplog):
    five, now, quote = support_history()
    quote.update(price=114, bid=113.95, ask=114.05)
    signals, _, _ = evaluate_structure("NIFTY", "CE", five, [], quote, now, .05)
    signal = next(item for item in signals if item["setups"] == ["support_5m_recovery"])
    assert signal["targets"] == [120, 180]
    service = SignalLifecycle(tmp_path / "signals.db", clock=lambda: now)
    with caplog.at_level("INFO", logger="signal_lifecycle"):
        assert service.open_once(asset_payload(signal, *ASSETS[0])[0]) is None
    assert "target_distance_below_10_percent" in caplog.text


def test_pattern_reaches_real_scanner_and_lifecycle_without_mocked_range_gate(tmp_path, monkeypatch):
    from tests.test_premium_patterns import scanner_with_pattern

    scanner, instrument, service, callback, now = scanner_with_pattern(tmp_path, monkeypatch)
    assert scanner._refresh_index_instrument(instrument, now, 23000) >= 1
    metadata = callback.call_args.args[0]["metadata"]
    assert metadata["entry_one_minute_candles"]
    assert len(service.open_positions()) == 1
    assert service.open_positions()[0]["entry_strategy"] == "double_bottom"


def test_live_fvg_watcher_can_confirm_above_zone_without_entering_on_touch(tmp_path, monkeypatch):
    from tests.test_premium_structure import make_live_scanner

    scanner, service, data, _, _ = make_live_scanner(tmp_path, monkeypatch, "order_block_retest")
    five, one, now, quote = fvg_history()
    scanner._index_zone_states[("NSE_FNO", 101)].update(
        five=five, one=one, zones=prepare_zones(five, now), prepared_at=now.timestamp(),
    )
    service.clock = lambda: now
    data.fetch_quotes.return_value = {("NSE_FNO", 101): quote}
    assert scanner.live_check_once(now) == 1
    assert service.open_positions()[0]["entry_strategy"] == "fvg_retest"


@pytest.mark.parametrize("route", ["support_5m_recovery", "compression_breakout"])
def test_quote_must_follow_completed_confirmation(route):
    if route == "support_5m_recovery":
        five, now, quote = support_history()
        one = []
    else:
        five, one, now, quote = structure_history()
    quote["trade_timestamp"] = now.timestamp() - 6
    _, diagnostics, _ = evaluate_structure("NIFTY", "CE", five, one, quote, now, .05)
    assert next(item for item in diagnostics if item["route"] == route)["reason"] == (
        "index_confirmation_after_quote")


def test_two_support_targets_are_both_drawn_on_premium_chart():
    from matplotlib.figure import Figure
    from option_chart import render_premium_chart

    five, now, _ = support_history()
    figures = []
    original = Figure.savefig

    def capture(figure, *args, **kwargs):
        figures.append(figure)
        return original(figure, *args, **kwargs)

    with patch.object(Figure, "savefig", capture):
        assert render_premium_chart(five, {
            "event": "OPEN", "event_time": now.timestamp(), "entry": 92.7,
            "stop": 88.05, "target": 180, "targets": [120, 180], "interval_minutes": 5,
        }).startswith(b"\x89PNG")
    labels = figures[0].axes[0].get_legend_handles_labels()[1]
    assert "Target 180.00" in labels and "T1 120.00 (T2 180.00)" in labels
