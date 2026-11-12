import json
import sqlite3
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from atm_options_fetcher import BAND_ATM
from index_swing_strategy import (
    INDEX_SYMBOLS, IST, IndexSwingStrategy, detect_patterns, spot_setups, valid_entry,
)
from live_runtime import LiveRoute
from premium_indicators import IndicatorDataError, completed_candles
from runtime_services import QueueConsumerWorker
from screener_premium import PremiumScreener
from signal_lifecycle import SignalLifecycle

NOW = datetime(2026, 11, 3, 10, 31, 5, tzinfo=IST)


def bar(stamp, open_, high, low, close, volume=100):
    return dict(timestamp=stamp.timestamp(), open=open_, high=high, low=low,
                close=close, volume=volume)


def history():
    start = NOW.replace(hour=9, minute=15, second=0)
    spot = [
        bar(start, 100, 105, 95, 101),
        bar(start + timedelta(minutes=5), 102, 113, 101, 112),
        bar(start + timedelta(minutes=10), 112, 114, 110, 113),
        bar(start + timedelta(minutes=15), 113, 116, 112, 115),
        bar(start + timedelta(minutes=20), 115, 117, 113, 116),
        bar(start + timedelta(minutes=25), 116, 118, 114, 117),
        bar(start + timedelta(minutes=30), 117, 119, 115, 118),
        bar(start + timedelta(minutes=35), 118, 121, 117, 120),
        bar(start + timedelta(minutes=40), 120, 122, 118, 121),
        bar(start + timedelta(minutes=45), 121, 124, 120, 123),
        bar(start + timedelta(minutes=50), 123, 125, 121, 124),
        bar(start + timedelta(minutes=55), 124, 127, 123, 126),
        bar(start + timedelta(minutes=60), 126, 128, 124, 127),
        bar(start + timedelta(minutes=65), 127, 130, 125, 129),
        bar(start + timedelta(minutes=70), 129, 131, 127, 130),
        bar(start + timedelta(minutes=75), 130, 132, 128, 131),
    ]
    premiums = [bar(start + timedelta(minutes=5 * n), 72, 75, 70, 71)
                for n in range(15)]
    premiums[5].update(low=60)
    premiums[8].update(high=100)
    one = [
        bar(NOW.replace(minute=29, second=0), 70, 73, 60.2, 69),
        bar(NOW.replace(minute=30, second=0), 69, 75, 68.5, 74),
    ]
    quote = {"price": 74.5, "bid": 74.45, "ask": 74.55, "trade_timestamp": NOW.timestamp()}
    return spot, premiums, one, quote


def reflected(bars):
    return [{**c, "open": 300 - c["open"], "close": 300 - c["close"],
             "high": 300 - c["low"], "low": 300 - c["high"]} for c in bars]


def make_signal(symbol="NIFTY", side="CE", now=NOW):
    spot, premiums, one, quote = history()
    if side == "PE":
        spot = reflected(spot)
    engine = IndexSwingStrategy()
    reference = engine.prepare(symbol, side, spot, premiums, now)
    signal = engine.evaluate(symbol, side, reference, one, quote, now)
    return signal


def incoming(symbol="NIFTY", side="CE", now=NOW, key="setup-1"):
    signal = make_signal(symbol, side, now)
    metadata = {**signal, "timeframe": "5min", "strike_band": BAND_ATM, "strike": 23050,
                "security_id": {"NIFTY": 101, "BANKNIFTY": 102, "SENSEX": 103}[symbol],
                "exchange_segment": "BSE_FNO" if symbol == "SENSEX" else "NSE_FNO",
                "instrument_type": "OPTIDX", "lot_size": 75, "tick_size": .05,
                "expiry": "27NOV2026", "option_symbol": symbol + "-" + side}
    return {"symbol": symbol, "category": "index_options", "idempotency_key": key,
            "metadata": metadata, "strategy": "premium_screener"}


@pytest.mark.parametrize("symbol", sorted(INDEX_SYMBOLS))
@pytest.mark.parametrize("side", ["CE", "PE"])
def test_index_entry_uses_only_premium_support_and_recovery(symbol, side):
    signal = make_signal(symbol, side)
    assert signal["alerts_only"] is True
    assert signal["strike_band"] == "ATM"
    assert signal["stop_loss"] == 60
    assert signal["target"] == 100
    assert signal["setups"] == ["premium_support_retest"]
    assert signal["premium_support"] == 60
    assert "spot_direction" not in signal
    assert signal["reference_high"] == 73
    assert signal["one_minute_close"] == 74
    assert valid_entry(signal, NOW)


def test_recovery_can_finish_with_latest_five_minute_bar():
    _, premium, one, quote = history()
    now = NOW - timedelta(minutes=1)
    one = [{**item, "timestamp": item["timestamp"] - 60} for item in one]
    quote["trade_timestamp"] = now.timestamp()
    engine = IndexSwingStrategy()
    reference = engine.prepare("NIFTY", "CE", [], premium, now)
    signal = engine.evaluate("NIFTY", "CE", reference, one, quote, now)
    assert signal["confirmation_end"] == signal["one_minute_start"] + 60
    assert valid_entry(signal, now)


def test_previous_minute_can_be_green():
    _, premium, one, quote = history()
    one[-2]["open"] = 68
    engine = IndexSwingStrategy()
    reference = engine.prepare("NIFTY", "CE", [], premium, NOW)
    signal = engine.evaluate("NIFTY", "CE", reference, one, quote, NOW)
    assert valid_entry(signal, NOW)


def premium_bars(values):
    start = NOW.replace(hour=9, minute=15, second=0)
    return [bar(start + timedelta(minutes=5 * i), *value)
            for i, value in enumerate(values)]


@pytest.mark.parametrize("side", ["CE", "PE"])
def test_user_w_example_enters_near_47_without_waiting_for_60(side):
    premium = premium_bars([
        (70, 72, 65, 66), (66, 67, 55, 56), (56, 57, 47, 49),
        (49, 56, 49, 55), (55, 60, 53, 58), (58, 59, 52, 54),
        (54, 55, 49, 50),
    ])
    # Fill the current history without a five-minute green breakout.
    while len(premium) < 15:
        stamp = datetime.fromtimestamp(premium[-1]["timestamp"], IST) + timedelta(minutes=5)
        premium.append(bar(stamp, 50, 51, 48, 49))
    one = [
        bar(NOW.replace(minute=29, second=0), 49, 49.5, 47, 47.5),
        bar(NOW.replace(minute=30, second=0), 47.5, 50, 47.4, 49.8),
    ]
    quote_ = {"price": 49.7, "bid": 49.65, "ask": 49.75, "trade_timestamp": NOW.timestamp()}
    engine = IndexSwingStrategy()
    reference = engine.prepare("NIFTY", side, [{"invalid": "spot ignored"}], premium, NOW)
    signal = engine.evaluate("NIFTY", side, reference, one, quote_, NOW)
    assert signal["premium_support"] == 47
    assert signal["entry"] == 49.7  # Fresh price can be below the 1m close, not its trigger.
    assert signal["target"] == 60
    assert signal["stop_loss"] == 47
    assert signal["one_minute_close"] < 60


@pytest.mark.parametrize("side", ["CE", "PE"])
def test_user_average_reclaim_uses_confirmed_lows_and_repeated_resistance(side):
    premium = premium_bars([
        (105, 110, 102, 108), (108, 120, 106, 115), (115, 116, 100, 103),
        (103, 105, 90, 93), (93, 110, 93, 108), (108, 120, 106, 116),
        (116, 117, 103, 106), (106, 108, 100, 102), (102, 112, 101, 110),
        (110, 120, 109, 115), (115, 116, 102, 105),
    ])
    while len(premium) < 15:
        stamp = datetime.fromtimestamp(premium[-1]["timestamp"], IST) + timedelta(minutes=5)
        premium.append(bar(stamp, 105, 108, 102, 104))
    one = [
        bar(NOW.replace(minute=29, second=0), 96, 96.5, 94, 94.5),
        bar(NOW.replace(minute=30, second=0), 94.5, 98, 94.4, 97),
    ]
    quote_ = {"price": 97, "bid": 96.95, "ask": 97.05, "trade_timestamp": NOW.timestamp()}
    engine = IndexSwingStrategy()
    reference = engine.prepare("NIFTY", side, [], premium, NOW)
    signal = engine.evaluate("NIFTY", side, reference, one, quote_, NOW)
    assert signal["setups"] == ["premium_average_reclaim"]
    assert signal["pullback_lows"] == [90, 100]
    assert signal["premium_support"] == 95
    assert signal["stop_loss"] == 90
    assert signal["target"] == 120


@pytest.mark.parametrize("mutation", [
    lambda s: s.update(support_known_at=NOW.timestamp() + 1),
    lambda s: s.update(previous_minute_start=s["one_minute_start"] - 120),
    lambda s: s.update(retest_low=70),
    lambda s: s.update(retest_low=50),
    lambda s: s.update(one_minute_open=s["one_minute_close"]),
    lambda s: s.update(premium_confirmation_low=59),
    lambda s: s.update(confirmation_end=NOW.timestamp() - 400),
])
def test_durable_premium_gate_rejects_missing_or_impossible_confirmation(mutation):
    signal = make_signal()
    mutation(signal)
    assert not valid_entry(signal, NOW)


def test_support_requires_known_rebound_and_labels_previous_session_separately():
    _, premium, _, _ = history()
    engine = IndexSwingStrategy()
    with pytest.raises(IndicatorDataError, match="index_premium_support_missing"):
        engine.prepare("NIFTY", "CE", [], premium[:6], NOW.replace(hour=9, minute=45))
    yesterday = [{**item, "timestamp": item["timestamp"] - 86400} for item in premium]
    today_flat = premium_bars([(70, 75, 69, 71)] * 15)
    reference = engine.prepare("NIFTY", "CE", [], yesterday + today_flat, NOW)
    assert reference["candidates"]
    assert all(item["setup"] == "premium_previous_session_recovery"
               for item in reference["candidates"])


def test_support_explicitly_fails_after_a_completed_close_breaks_it():
    _, premium, _, _ = history()
    premium[-1].update(open=65, low=50, close=55)
    with pytest.raises(IndicatorDataError, match="index_premium_support_missing"):
        IndexSwingStrategy().prepare("NIFTY", "CE", [], premium, NOW)


def test_only_completed_spot_patterns_are_reported():
    start = NOW.replace(hour=9, minute=15, second=0)
    points = [
        (100, 103, 99, 102), (102, 105, 98, 100), (100, 110, 100, 106),
        (106, 107, 99, 103), (103, 106, 98.1, 104), (104, 112, 100, 110),
        (110, 114, 105, 113),
    ]
    bars = [bar(start + timedelta(minutes=5 * i), *values) for i, values in enumerate(points)]
    patterns = detect_patterns(completed_candles(bars, NOW, timeframe_seconds=300), 5)
    assert any(item["name"] == "double_bottom" and item["side"] == "CE" for item in patterns)
    inverse = reflected(bars)
    patterns = detect_patterns(completed_candles(inverse, NOW, timeframe_seconds=300), 5)
    assert any(item["name"] == "double_top" and item["side"] == "PE" for item in patterns)


@pytest.mark.parametrize("bars,expected,side", [
    ([
        (10, 12, 9, 11), (12, 15, 11, 14), (14, 14, 10, 11),
        (11, 20, 10.5, 18), (12, 13, 10.1, 12), (12, 15.01, 11, 14),
        (14, 14, 8, 9),
    ], "head_and_shoulders", "PE"),
    ([
        (10, 12, 9, 11), (9, 11, 8.5, 10), (10, 12, 9, 11),
        (8, 11, 6, 7), (10, 12.2, 9, 11), (9, 11, 8.51, 9),
        (11, 14, 10, 13),
    ], "inverse_head_and_shoulders", "CE"),
])
def test_head_and_shoulders_patterns_require_neckline_break(bars, expected, side):
    start = NOW.replace(hour=9, minute=15, second=0)
    raw = [bar(start + timedelta(minutes=5 * i), *values) for i, values in enumerate(bars)]
    patterns = detect_patterns(completed_candles(raw, NOW, timeframe_seconds=300), 5)
    assert any(item["name"] == expected and item["side"] == side for item in patterns)


@pytest.mark.parametrize("mutation,reason", [
    (lambda spot, premium, one, quote: one[-1].update(close=72), "index_recovery_close_missing"),
    (lambda spot, premium, one, quote: one[-2].update(low=68),
     "index_premium_retest_missing"),
    (lambda spot, premium, one, quote: quote.update(trade_timestamp=NOW.timestamp() - 11),
     "index_quote_stale"),
    (lambda spot, premium, one, quote: quote.update(bid=60), "index_spread"),
])
def test_pullback_or_quote_failures_block_entry(mutation, reason):
    spot, premium, one, quote = history()
    mutation(spot, premium, one, quote)
    engine = IndexSwingStrategy()
    reference = engine.prepare("NIFTY", "CE", spot, premium, NOW)
    with pytest.raises(IndicatorDataError, match=reason):
        engine.evaluate("NIFTY", "CE", reference, one, quote, NOW)


def test_no_opening_range_and_no_other_setup_is_rejected():
    spot, _, _, _ = history()
    spot = spot[2:]
    with pytest.raises(IndicatorDataError, match="index_no_setup"):
        spot_setups(completed_candles(spot, NOW, timeframe_seconds=300), "CE", NOW)


def test_initial_risk_and_target_must_be_valid():
    signal = make_signal()
    signal.update(reference_low=80)
    assert not valid_entry(signal, NOW)
    signal = make_signal()
    signal["target"] = signal["breakout_price"]
    assert not valid_entry(signal, NOW)


def test_enabled_trial_suppresses_runtime_auto_even_with_old_live_flags(monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.setenv("INDEX_SWING_STRATEGY_ENABLED", "true")
    monkeypatch.setenv("NIFTY_LIVE_ENABLED", "true")
    monkeypatch.setenv("AUTO_TRADING_ENABLED", "true")
    import main
    assert main._load_runtime_config()["nifty_live"]["auto"] is False


def test_queue_and_live_route_cannot_execute_even_with_live_flags():
    signal = incoming()
    live = MagicMock()
    orders = MagicMock()
    worker = QueueConsumerWorker(
        MagicMock(), orders, MagicMock(), {"nifty_live": {"enabled": True, "auto": True}},
        MagicMock(), live_route=live,
    )
    assert worker._execute_signal(signal)["status"] == "signal_only"
    live.propose.assert_not_called()
    orders.execute_order.assert_not_called()
    execution = MagicMock(enabled=True)
    route = LiveRoute(execution, MagicMock())
    assert not route.targets(signal)
    assert route.propose(signal)["status"] == "rejected"
    execution.propose.assert_not_called()


@pytest.fixture
def lifecycle(tmp_path):
    clock = [NOW]
    sent = []
    service = SignalLifecycle(
        tmp_path / "signals.db", clock=lambda: clock[0],
        notify=lambda category, message: sent.append((category, message)) or True,
    )
    return service, clock, sent


def quote(price, now, security=101):
    return {("NSE_FNO", security): {"price": price, "trade_timestamp": now.timestamp()}}


def test_unlimited_independent_signals_can_remain_open_and_persist(lifecycle):
    service, clock, sent = lifecycle
    first = service.open_once(incoming(key="setup-1"))
    assert first is not None
    with service._connection() as db:
        db.execute("DROP INDEX lifecycle_open_underlying")
        db.execute("CREATE UNIQUE INDEX lifecycle_open_underlying "
                   "ON lifecycle_signals(underlying) WHERE status='OPEN'")
    second_time = NOW + timedelta(seconds=2)
    restarted = SignalLifecycle(service.db_path, clock=lambda: second_time)
    second = restarted.open_once(incoming(now=second_time, key="setup-2"))
    assert second is not None
    assert len(restarted.open_positions()) == 2
    assert service.open_once(incoming(now=second_time, key="setup-2")) is None
    service.dispatch_notifications()
    assert all(category == "index_options" for category, _ in sent)


def test_compounded_twenty_percent_stop_ladder_is_advisory(lifecycle):
    service, clock, _ = lifecycle
    signal = incoming()
    signal["metadata"]["target"] = 300
    position = service.open_once(signal)
    assert position["stop_loss"] == 60
    clock[0] += timedelta(seconds=2)
    service.observe_quotes(quote(89.4, clock[0]), clock[0])
    position = service.open_positions()[0]
    assert position["trailing_step"] == 1
    assert position["stop_loss"] == 84.95
    clock[0] += timedelta(seconds=2)
    service.observe_quotes(quote(107.28, clock[0]), clock[0])
    position = service.open_positions()[0]
    assert position["trailing_step"] == 2
    assert position["stop_loss"] == 101.95
    clock[0] += timedelta(seconds=2)
    service.observe_quotes(quote(100, clock[0]), clock[0])
    assert not service.is_open("NIFTY")


def test_lifecycle_rejects_non_atm_index_contracts(lifecycle):
    service, _, _ = lifecycle
    signal = incoming()
    signal["metadata"]["strike_band"] = "ITM+1"
    assert service.open_once(signal) is None


def test_lifecycle_accepts_a_support_reclaim_after_previous_minute_was_below_stop(lifecycle):
    service, _, _ = lifecycle
    signal = incoming()
    signal["metadata"].update(
        reference_high=59.9, breakout_price=61.5,
        one_minute_open=59.8, one_minute_close=61, retest_low=59.8,
    )
    position = service.open_once(signal)
    assert position is not None
    assert position["stop_loss"] == 60
    assert position["entry"] == 61.5


def test_scanner_routes_only_three_indices_and_uses_atm_contracts(tmp_path, monkeypatch):
    monkeypatch.setenv("INDEX_SWING_STRATEGY_ENABLED", "true")
    service = SignalLifecycle(tmp_path / "signals.db", clock=lambda: NOW)
    instrument = SimpleNamespace(symbol="NIFTY", category="index_options", exchange="NSE")
    stock = SimpleNamespace(symbol="RELIANCE", category="nifty50_stock_options", exchange="NSE")
    dm = MagicMock()
    dm.get_instruments.return_value = {"index_options": [instrument],
                                      "nifty50_stock_options": [stock],
                                      "commodity_options": []}
    dm.resolve_underlying.return_value = SimpleNamespace(
        security_id=13, exchange_segment="IDX_I", instrument_type="INDEX")
    spot, premium, one, current_quote = history()
    premium[-2].update(open=72, high=73, low=60.2, close=69)
    premium[-1].update(open=69, high=75, low=68.5, close=74)
    dm.fetch_contract_candles.return_value = spot
    dm.fetch_quotes.return_value = {("NSE_FNO", 101): current_quote}
    callback = MagicMock(return_value=True)
    scanner = PremiumScreener(
        dm, MagicMock(), MagicMock(), signal_lifecycle=service, signal_callback=callback,
        alert_state_file=str(tmp_path / "alerts.json"), clock=lambda: NOW,
    )
    scanner.fetcher = MagicMock()
    contract = incoming()["metadata"]
    scanner.fetcher.resolve_strike_band.side_effect = lambda i, p, side, **kw: [contract] if side == "CE" else []
    scanner.fetcher.fetch_option_candles.side_effect = lambda c, interval, now, **kw: premium if interval == "5min" else one
    assert scanner._uses_index_swing(instrument)
    assert not scanner._uses_index_swing(stock)
    assert scanner._refresh_instrument(instrument, "10min", NOW, spot=130) == 1
    assert scanner.fetcher.resolve_strike_band.call_args_list[0].kwargs["bands"] == (BAND_ATM,)
    payload = callback.call_args.args[0]
    assert payload["metadata"]["strike_band"] == BAND_ATM
    assert service.is_open("NIFTY")
    evaluation = next(item for item in scanner.status()["recent_index_evaluations"]
                      if item.get("route") == "support_5m_recovery")
    assert evaluation["result"] == "ACCEPTED"
    dm.fetch_contract_candles.assert_not_called()
    assert "spot_setup" not in evaluation
    scanner_policy = scanner.status()["index_swing_strategy"]
    assert scanner_policy["spot_confirmation_required"] is False
    assert scanner_policy["entry_policy"] == "premium_structure_v3"
    assert scanner._refresh_instrument(instrument, "10min", NOW, spot=130) == 0
    assert any(item.get("route") == "support_5m_recovery"
               and item["result"] == "QUALIFIED_NOT_ACCEPTED"
               for item in scanner.status()["recent_index_evaluations"])
    assert len(service.open_positions()) == 1
    assert callback.call_count == 1
