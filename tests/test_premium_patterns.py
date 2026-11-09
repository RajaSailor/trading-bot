from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from atm_options_fetcher import ATMOptionsFetcher
from index_swing_strategy import IST, IndexSwingStrategy, describe_setup, valid_entry
from lifecycle_charts import LifecycleChartService
from option_chart import ChartRenderError, render_premium_chart
from premium_indicators import IndicatorDataError, completed_candles
from premium_patterns import (
    PATTERN_NAMES, PATTERN_POLICY, PATTERN_TIMEFRAMES, aggregate_minutes,
    bullish_patterns, scan_patterns,
)
from screener_premium import PremiumScreener
from signal_lifecycle import SignalLifecycle


SHAPES = {
    "double_bottom": [55, 47, 54, 60, 54, 47, 54, 55, 61],
    "triple_bottom": [55, 47, 54, 60, 54, 47, 54, 60, 54, 47, 54, 55, 61],
    "inverse_head_and_shoulders": [100, 90, 99, 110, 95, 80, 95, 110, 99, 90, 100, 105, 112],
    "ascending_triangle": [95, 110, 100, 90, 100, 110, 103, 95, 103, 110, 105, 106, 112],
    "falling_wedge": [120, 130, 115, 100, 110, 125, 110, 98, 110, 120, 105, 96, 103, 105, 116],
    "bullish_flag": [80, 90, 110, 120, 118, 116, 117, 118, 116, 114, 115, 116, 114, 112, 113, 114, 118],
}


def pattern_history(name="double_bottom", minutes=1):
    values = SHAPES[name]
    per_day = 375 // minutes
    days = []
    day = datetime(2026, 11, 3, 9, 15, tzinfo=IST)
    while len(days) * per_day < len(values):
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    days.reverse()
    frames, base = [], []
    for index, value in enumerate(values):
        start = days[index // per_day] + timedelta(minutes=(index % per_day) * minutes)
        open_ = values[index - 1] if index == len(values) - 1 else value
        frame = {"timestamp": start.timestamp(), "open": open_, "high": max(open_, value) + .2,
                 "low": min(open_, value) - .2, "close": value}
        frames.append(frame)
        for offset in range(minutes):
            base.append({**frame, "timestamp": start.timestamp() + offset * 60,
                         "open": open_ if offset == 0 else value})
    now = datetime.fromtimestamp(frames[-1]["timestamp"], IST) + timedelta(minutes=minutes, seconds=5)
    quote = {"price": values[-1], "bid": values[-1] - .05, "ask": values[-1] + .05,
             "trade_timestamp": now.timestamp()}
    return frames, base, now, quote


@pytest.mark.parametrize("name", PATTERN_NAMES)
@pytest.mark.parametrize("minutes", PATTERN_TIMEFRAMES)
def test_six_confirmed_patterns_on_all_nine_timeframes(name, minutes):
    frames, base, now, _ = pattern_history(name, minutes)
    aggregated = aggregate_minutes(base, minutes, now)
    assert len(aggregated) == len(frames)
    assert [bar["close"] for bar in aggregated] == [bar["close"] for bar in frames]
    patterns = bullish_patterns(aggregated, minutes)
    match = next(item for item in patterns if item["name"] == name)
    assert match["direction"] == "bullish_premium"
    assert match["confirmed_end"] <= now.timestamp()
    assert match["stop"] < frames[-1]["close"] < match["target"]
    assert match["points"] and match["lines"]


@pytest.mark.parametrize("side", ["CE", "PE"])
@pytest.mark.parametrize("minutes", PATTERN_TIMEFRAMES)
def test_pattern_entry_is_independent_of_support_recovery(side, minutes):
    _, base, now, quote = pattern_history(minutes=minutes)
    signals, diagnostics = IndexSwingStrategy().evaluate_patterns("NIFTY", side, base, quote, now)
    signal = next(item for item in signals if item["premium_pattern"]["timeframe"] == minutes)
    assert signal["signal"] == ("CALL" if side == "CE" else "PUT")
    assert signal["entry_policy"] == PATTERN_POLICY
    assert "premium_support" not in signal
    assert "one_minute_start" not in signal
    assert valid_entry(signal, now)
    assert f"{minutes}m premium chart" in describe_setup(signal)
    assert any(item["timeframe"] == minutes and item["status"] == "CONFIRMED"
               for item in diagnostics)


def test_only_completed_fresh_breakout_not_forming_or_continuation():
    frames, _, now, _ = pattern_history()
    completed = completed_candles(frames, now, timeframe_seconds=60)
    assert bullish_patterns(completed, 1)
    assert not bullish_patterns(completed[:-1], 1)
    forming = deepcopy(completed)
    forming[-1].update(close=59, high=61)
    assert not bullish_patterns(forming, 1)
    continuing = completed + [{**completed[-1], "timestamp": now.timestamp(),
                              "_timestamp_epoch": completed[-1]["_timestamp_epoch"] + 60}]
    assert not bullish_patterns(continuing, 1)
    stale, _ = scan_patterns(frames, now + timedelta(minutes=2))
    assert not stale


@pytest.mark.parametrize("name", PATTERN_NAMES)
def test_each_pattern_rejects_unconfirmed_and_bearish_closes(name):
    frames, _, now, _ = pattern_history(name)
    bars = completed_candles(frames, now, timeframe_seconds=60)
    assert not any(item["name"] == name for item in bullish_patterns(bars[:-1], 1))
    bars[-1]["open"] = bars[-1]["close"] + .1
    bars[-1]["high"] = max(bars[-1]["high"], bars[-1]["open"])
    assert not bullish_patterns(bars, 1)


def test_missing_full_bucket_cannot_manufacture_a_pattern():
    _, base, now, _ = pattern_history(minutes=5)
    del base[10]
    hits, diagnostics = scan_patterns(base, now)
    assert not any(hit["pattern"]["timeframe"] == 5 for hit in hits)
    five = next(item for item in diagnostics if item["timeframe"] == 5)
    assert five["gap_excluded_bars"] > 0
    assert five["status"] == "INSUFFICIENT_HISTORY"


def test_aggregation_never_uses_incomplete_tail_overnight_or_missing_minutes():
    _, base, now, _ = pattern_history(minutes=240)
    bars = aggregate_minutes(base, 240, now)
    assert len(bars) == len(SHAPES["double_bottom"])
    assert all(datetime.fromtimestamp(item["timestamp"], IST).strftime("%H:%M") == "09:15"
               for item in bars)
    missing = base[:-1]
    assert len(aggregate_minutes(missing, 240, now)) == len(bars) - 1
    assert len(aggregate_minutes(base, 240, now - timedelta(seconds=6))) == len(bars) - 1
    _, short, short_now, _ = pattern_history()
    _, diagnostics = scan_patterns(short, short_now)
    assert next(item for item in diagnostics if item["timeframe"] == 240)["status"] == "INSUFFICIENT_HISTORY"


@pytest.mark.parametrize("mutation", [
    lambda s: s["premium_pattern"].update(name="double_top"),
    lambda s: s["premium_pattern"].update(timeframe=4),
    lambda s: s["premium_pattern"].update(boundary=1),
    lambda s: s.update(reference_low=1),
    lambda s: s.update(target=1000),
    lambda s: s.update(timeframe="5min"),
    lambda s: s.update(chart_candles=[]),
    lambda s: s.update(alerts_only=False),
    lambda s: s.update(breakout_price=60),
])
def test_pattern_durable_admission_rejects_tampered_evidence(mutation):
    _, base, now, quote = pattern_history()
    signals, _ = IndexSwingStrategy().evaluate_patterns("NIFTY", "CE", base, quote, now)
    signal = signals[0]
    mutation(signal)
    assert not valid_entry(signal, now)


def test_exact_contract_previous_session_support_can_recover_at_open():
    day = datetime(2026, 11, 2, 9, 15, tzinfo=IST)
    prior = [
        {"timestamp": (day + timedelta(minutes=5 * i)).timestamp(),
         "open": value, "high": value + 2, "low": value - 2, "close": value}
        for i, value in enumerate([175, 162, 175, 190, 180])
    ]
    now = datetime(2026, 11, 3, 9, 18, 5, tzinfo=IST)
    one = [
        {"timestamp": now.replace(minute=16, second=0).timestamp(),
         "open": 163, "high": 164, "low": 160, "close": 161},
        {"timestamp": now.replace(minute=17, second=0).timestamp(),
         "open": 161, "high": 166, "low": 160.5, "close": 165},
    ]
    quote = {"price": 165, "bid": 164.95, "ask": 165.05, "trade_timestamp": now.timestamp()}
    engine = IndexSwingStrategy()
    reference = engine.prepare("NIFTY", "CE", [], prior, now)
    signal = engine.evaluate("NIFTY", "CE", reference, one, quote, now)
    assert signal["setups"] == ["premium_previous_session_recovery"]
    assert signal["premium_support"] == signal["stop_loss"] == 160
    assert signal["target"] == 192
    assert signal["premium_reference_levels"] == [
        {"price": 160, "label": "Previous session low"},
        {"price": 192, "label": "Previous session high"},
    ]
    assert signal["previous_session_date"] == "2026-11-02"
    assert valid_entry(signal, now)
    assert "Previous-session" in describe_setup(signal)
    signal["previous_session_date"] = "2026-10-30"
    assert not valid_entry(signal, now)
    with pytest.raises(IndicatorDataError, match="index_insufficient_history"):
        engine.prepare("NIFTY", "CE", [], [], now)


def test_fetcher_history_is_bounded_and_same_contract():
    dm = MagicMock()
    dm.fetch_contract_candles.return_value = []
    contract = {"security_id": 101, "exchange_segment": "NSE_FNO",
                "instrument_type": "OPTIDX", "option_symbol": "NIFTY-CE"}
    ATMOptionsFetcher(dm).fetch_option_candles(
        contract, "1min", datetime(2026, 11, 3, tzinfo=IST), lookback_sessions=10,
    )
    args = dm.fetch_contract_candles.call_args.kwargs
    assert args["security_id"] == 101 and args["interval"] == 1
    assert args["from_date"] < "2026-11-02"
    with pytest.raises(ValueError):
        ATMOptionsFetcher(dm).fetch_option_candles(contract, lookback_sessions=11)


def test_extended_history_is_cached_separately_and_current_data_takes_precedence():
    dm = MagicMock()
    _, base, now, _ = pattern_history()
    current = base[-2:]
    historical = base[:-1]
    dm.fetch_contract_candles.side_effect = lambda **kw: (
        current if kw["to_date"] == now.date().isoformat() else historical
    )
    contract = {"security_id": 101, "exchange_segment": "NSE_FNO",
                "instrument_type": "OPTIDX", "option_symbol": "NIFTY-CE"}
    fetcher = ATMOptionsFetcher(dm)
    fetcher.fetch_option_candles(contract, "1min", now, lookback_sessions=10)
    assert dm.fetch_contract_candles.call_count == 2
    current[-2] = {**current[-2], "close": 55.1}
    merged = fetcher.fetch_option_candles(contract, "1min", now, lookback_sessions=10)
    assert dm.fetch_contract_candles.call_count == 3
    validated = completed_candles(merged, now, timeframe_seconds=60)
    assert validated[-2]["close"] == 55.1
    other_contract = {**contract, "security_id": 102, "option_symbol": "NIFTY-PE"}
    fetcher.fetch_option_candles(other_contract, "1min", now, lookback_sessions=10)
    assert dm.fetch_contract_candles.call_count == 5


@pytest.mark.parametrize("mutation,reason", [
    (lambda quote, now: quote.update(trade_timestamp=now.timestamp() - 11), "index_quote_stale"),
    (lambda quote, now: quote.update(bid=40), "index_spread"),
])
def test_pattern_route_retains_source_quote_safety(mutation, reason):
    _, base, now, quote = pattern_history()
    mutation(quote, now)
    with pytest.raises(IndicatorDataError, match=reason):
        IndexSwingStrategy().evaluate_patterns("NIFTY", "CE", base, quote, now)


def scanner_with_pattern(tmp_path, monkeypatch):
    monkeypatch.setenv("INDEX_SWING_STRATEGY_ENABLED", "true")
    _, base, now, quote = pattern_history(minutes=5)
    instrument = SimpleNamespace(symbol="NIFTY", category="index_options", exchange="NSE")
    contract = {"security_id": 101, "exchange_segment": "NSE_FNO", "instrument_type": "OPTIDX",
                "option_symbol": "NIFTY-CE", "option_type": "CE", "strike": 23000,
                "strike_band": "ATM", "expiry": "27NOV2026", "tick_size": .05, "lot_size": 75}
    service = SignalLifecycle(tmp_path / "signals.db", clock=lambda: now)
    callback = MagicMock(return_value=True)
    dm = MagicMock()
    dm.get_instruments.return_value = {"index_options": [instrument]}
    dm.fetch_quotes.return_value = {("NSE_FNO", 101): quote}
    scanner = PremiumScreener(dm, MagicMock(), MagicMock(), signal_lifecycle=service,
                             signal_callback=callback, clock=lambda: now,
                             alert_state_file=str(tmp_path / "alerts.json"))
    scanner.fetcher = MagicMock()
    scanner.fetcher.resolve_strike_band.side_effect = lambda i, p, side, **kw: [contract] if side == "CE" else []
    scanner.fetcher.fetch_option_candles.side_effect = lambda c, interval, n, **kw: [] if interval == "5min" else base
    return scanner, instrument, service, callback, now


def test_scanner_pattern_survives_failed_support_and_deduplicates(tmp_path, monkeypatch):
    scanner, instrument, service, callback, now = scanner_with_pattern(tmp_path, monkeypatch)
    assert scanner._refresh_index_instrument(instrument, now, 23000) >= 1
    first_count = callback.call_count
    assert first_count == len(service.open_positions())
    assert all(p["metadata"]["entry_policy"] == PATTERN_POLICY for p in service.open_positions())
    assert all("premium_pattern" in call.args[0]["metadata"] for call in callback.call_args_list)
    scanner._refresh_index_instrument(instrument, now, 23000)
    assert callback.call_count == first_count
    assert len(service.open_positions()) == first_count
    status = scanner.status()
    assert status["index_swing_strategy"]["pattern_timeframes_minutes"] == list(PATTERN_TIMEFRAMES)
    assert any(item.get("pattern_timeframes") for item in status["recent_index_evaluations"])


def test_support_alert_is_first_without_blocking_independent_pattern(tmp_path, monkeypatch):
    scanner, instrument, service, callback, now = scanner_with_pattern(tmp_path, monkeypatch)
    frames, base, _, _ = pattern_history(minutes=5)
    base[-2].update(open=60.4, low=60, high=60.8, close=60.5)
    base[-1].update(open=60.5, low=60.3, high=61.2, close=61)
    prior = [{**frame, "timestamp": frame["timestamp"] - 86400,
              "open": 75, "high": 80, "low": 70, "close": 74} for frame in frames]
    prior[2]["low"] = 60
    prior[5]["high"] = 100
    scanner.fetcher.fetch_option_candles.side_effect = lambda c, interval, n, **kw: (
        prior + frames if interval == "5min" else base
    )
    assert scanner._refresh_index_instrument(instrument, now, 23000) >= 2
    policies = [call.args[0]["metadata"]["entry_policy"] for call in callback.call_args_list]
    assert policies[0] == "premium_support_recovery_v2"
    assert PATTERN_POLICY in policies[1:]
    assert len(service.open_positions()) == callback.call_count
    scanner._refresh_index_instrument(instrument, now, 23000)
    assert len(service.open_positions()) == len(policies)


@pytest.mark.parametrize("minutes", PATTERN_TIMEFRAMES)
def test_pattern_lifecycle_freezes_timeframe_and_keeps_execution_disabled(tmp_path, minutes):
    from runtime_services import QueueConsumerWorker
    _, base, now, quote = pattern_history(minutes=minutes)
    signals, _ = IndexSwingStrategy().evaluate_patterns("NIFTY", "PE", base, quote, now)
    signal = next(s for s in signals if s["timeframe"] == f"{minutes}min")
    contract = {"security_id": 101, "exchange_segment": "NSE_FNO", "instrument_type": "OPTIDX",
                "option_symbol": "NIFTY-PE", "option_type": "PE", "strike": 23000,
                "strike_band": "ATM", "expiry": "27NOV2026", "tick_size": .05, "lot_size": 75}
    instrument = SimpleNamespace(symbol="NIFTY", category="index_options")
    signal["idempotency_key"] = PremiumScreener._signal_key("index_options", {**signal, **contract})
    payload = PremiumScreener._build_queue_payload(instrument, signal, "index_options",
                                                  "premium_screener", contract)
    service = SignalLifecycle(tmp_path / "signals.db", clock=lambda: now)
    position = service.open_once(payload)
    assert position is not None
    overlay = service._chart_overlay(position, "OPEN")
    assert overlay["interval_minutes"] == minutes
    assert overlay["premium_pattern"] == signal["premium_pattern"]
    assert overlay["chart_candles"] == signal["chart_candles"]
    caption = service._signal_message(position, "OPEN")
    assert "Independent pattern" in caption
    assert len(caption.encode("utf-16-le")) // 2 <= 1024
    orders, live = MagicMock(), MagicMock()
    worker = QueueConsumerWorker(MagicMock(), orders, MagicMock(),
                                 {"nifty_live": {"enabled": True, "auto": True}},
                                 MagicMock(), live_route=live)
    assert worker._execute_signal(payload)["status"] == "signal_only"
    orders.execute_order.assert_not_called()
    live.propose.assert_not_called()


@pytest.mark.parametrize("name", PATTERN_NAMES)
def test_all_pattern_geometry_is_drawn(name):
    from matplotlib.figure import Figure
    frames, base, now, quote = pattern_history(name)
    signals, _ = IndexSwingStrategy().evaluate_patterns("NIFTY", "CE", base, quote, now)
    signal = next(item for item in signals if item["premium_pattern"]["name"] == name)
    saved = []
    original = Figure.savefig

    def capture(figure, *args, **kwargs):
        saved.append(figure.axes[0])
        return original(figure, *args, **kwargs)

    with patch.object(Figure, "savefig", capture):
        render_premium_chart(frames, {**signal, "event": "OPEN", "event_time": now.timestamp(),
                                     "interval_minutes": 1, "stop": signal["stop_loss"]})
    assert name.replace("_", " ") in saved[0].get_legend_handles_labels()[1]
    for line in signal["premium_pattern"]["lines"]:
        assert line["label"] in saved[0].get_legend_handles_labels()[1]


def test_pattern_chart_draws_structure_boundaries_and_correct_timeframe(tmp_path):
    from matplotlib.figure import Figure
    frames, base, now, quote = pattern_history(minutes=240)
    signals, _ = IndexSwingStrategy().evaluate_patterns("NIFTY", "PE", base, quote, now)
    signal = next(s for s in signals if s["timeframe"] == "240min")
    overlay = {
        **signal, "event": "OPEN", "event_time": now.timestamp(),
        "stop": signal["stop_loss"], "interval_minutes": 240, "option_symbol": "NIFTY-PE",
        "security_id": 101, "exchange_segment": "NSE_FNO", "instrument_type": "OPTIDX",
        "window": 80,
    }
    saved = []
    original = Figure.savefig

    def capture(figure, *args, **kwargs):
        saved.append(figure.axes[0])
        return original(figure, *args, **kwargs)

    with patch.object(Figure, "savefig", capture):
        png = render_premium_chart(frames, overlay)
    assert png.startswith(b"\x89PNG")
    assert "double bottom" in saved[0].get_legend_handles_labels()[1]
    assert "Neckline / breakout" in saved[0].get_legend_handles_labels()[1]
    assert "240min" in saved[0].get_title()
    provider = MagicMock()
    service = LifecycleChartService(tmp_path / "charts.db", MagicMock(), provider)
    snapshot, error = service._fetch_snapshot(overlay)
    assert error is None and service._verify_snapshot(snapshot, overlay)
    assert snapshot["request"]["interval_minutes"] == 240
    provider.assert_not_called()
    assert "240m" in service._caption(overlay)
    overlay["interval_minutes"] = 5
    with pytest.raises(ChartRenderError, match="pattern_timeframe_mismatch"):
        service._fetch_snapshot(overlay)


def test_pattern_photo_delivery_uses_frozen_chart_despite_small_global_window(tmp_path):
    values = SHAPES["bullish_flag"]
    expanded = [left + (right - left) * offset / 3
                for left, right in zip(values[:-2], values[1:-1]) for offset in range(3)] + values[-2:]
    with patch.dict(SHAPES, {"bullish_flag": expanded}):
        _, base, now, quote = pattern_history("bullish_flag", 5)
    signals, _ = IndexSwingStrategy().evaluate_patterns("NIFTY", "CE", base, quote, now)
    signal = next(s for s in signals if s["premium_pattern"]["name"] == "bullish_flag")
    overlay = {
        **signal, "event": "OPEN", "event_time": now.timestamp(), "interval_minutes": 5,
        "stop": signal["stop_loss"], "option_symbol": "NIFTY-CE",
        "security_id": 101, "exchange_segment": "NSE_FNO", "instrument_type": "OPTIDX",
    }
    sender = MagicMock(return_value={"ok": True, "message_id": 900})
    sender.resolve_chat_id.return_value = -1001
    provider = MagicMock()
    charts = LifecycleChartService(tmp_path / "charts.db", sender, provider,
                                   clock=lambda: now.timestamp(), window=20)
    with charts._connect() as db:
        result = charts.deliver_event(db, {
            "job_id": "pattern:OPEN", "signal_id": "pattern", "event": "OPEN",
            "category": "index_options", "overlay": overlay,
        }, "Confirmed pattern chart")
        assert result["ok"] is True
        assert db.execute("SELECT status FROM lifecycle_chart_jobs").fetchone()[0] == "SENT"
    provider.assert_not_called()
    assert sender.call_args.args[1].startswith(b"\x89PNG")


def test_pattern_exit_chart_resamples_one_minute_contract_history(tmp_path):
    frames, base, now, quote = pattern_history(minutes=3)
    signals, _ = IndexSwingStrategy().evaluate_patterns("NIFTY", "CE", base, quote, now)
    signal = next(s for s in signals if s["timeframe"] == "3min")
    overlay = {**signal, "event": "STOP", "event_time": now.timestamp(),
               "interval_minutes": 3, "stop": signal["stop_loss"],
               "security_id": 101, "exchange_segment": "NSE_FNO", "instrument_type": "OPTIDX"}
    provider = MagicMock(return_value=base)
    charts = LifecycleChartService(tmp_path / "charts.db", MagicMock(), provider)
    snapshot, error = charts._fetch_snapshot(overlay)
    assert error is None and charts._verify_snapshot(snapshot, overlay)
    assert provider.call_args.args[-1] == 1
    assert [bar["close"] for bar in snapshot["candles"]] == [bar["close"] for bar in frames]
