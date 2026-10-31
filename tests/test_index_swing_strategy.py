import json
import sqlite3
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from index_swing_strategy import (
    INDEX_SYMBOLS, IST, IndexSwingStrategy, aggregate_five_minutes, spot_setups, valid_entry,
)
from lifecycle_charts import LifecycleChartService
from live_runtime import LiveRoute
from premium_indicators import IndicatorDataError, completed_candles
from runtime_services import QueueConsumerWorker
from screener_premium import PremiumScreener
from signal_lifecycle import SignalLifecycle
from telegram_handler import format_option_breakout_alert

NOW = datetime(2026, 10, 5, 10, 31, 5, tzinfo=IST)


def bar(stamp, open_, high, low, close, volume=100):
    return dict(timestamp=stamp.timestamp(), open=open_, high=high, low=low,
                close=close, volume=volume)


def history():
    start = NOW.replace(hour=9, minute=30, second=0)
    premiums = [bar(start + timedelta(minutes=5 * n), 72, 75, 70, 71) for n in range(12)]
    premiums[4].update(high=90)
    premiums[-2].update(open=66, high=67, low=60, close=62)
    premiums[-1].update(open=62, high=70, low=60, close=68, volume=200)
    spot = [
        bar(NOW.replace(day=1, hour=15, minute=20, second=0), 120, 130, 100, 120),
        bar(NOW.replace(hour=9, minute=15, second=0), 115, 120, 110, 115),
        bar(NOW.replace(hour=10, minute=15, second=0), 109, 112, 106, 107),
        bar(NOW.replace(hour=10, minute=20, second=0), 107, 108, 100, 102),
        bar(NOW.replace(hour=10, minute=25, second=0), 102, 112, 101, 110),
    ]
    one = [bar(NOW.replace(minute=30, second=0), 68, 70, 68, 69, 150)]
    quote = {"price": 69, "bid": 68.95, "ask": 69.05, "trade_timestamp": NOW.timestamp()}
    return spot, premiums, one, quote


def reflected(bars):
    return [{**c, "open": 200 - c["open"], "close": 200 - c["close"],
             "high": 200 - c["low"], "low": 200 - c["high"]} for c in bars]


def make_signal(symbol="NIFTY", side="CE", now=NOW):
    spot, premiums, one, quote = history()
    if side == "PE":
        spot = reflected(spot)
    engine = IndexSwingStrategy()
    reference = engine.prepare(symbol, side, spot, premiums, NOW)
    signal = engine.evaluate(symbol, side, reference, one, quote, NOW)
    if now != NOW:
        delta = (now - NOW).total_seconds()
        for field in ("breakout_timestamp", "first_crossing_timestamp", "confirmation_end",
                      "one_minute_start", "reference_timestamp"):
            signal[field] += delta
        signal["detected_at"] = now.isoformat()
    return signal


def incoming(symbol="NIFTY", side="CE", now=NOW, key="setup-1"):
    signal = make_signal(symbol, side, now)
    metadata = {**signal, "timeframe": "5min", "strike_band": "ITM+1", "strike": 23050,
                "security_id": {"NIFTY": 101, "BANKNIFTY": 102, "SENSEX": 103}[symbol],
                "exchange_segment": "BSE_FNO" if symbol == "SENSEX" else "NSE_FNO",
                "instrument_type": "OPTIDX", "lot_size": 75, "tick_size": .05,
                "expiry": "27OCT2026", "option_symbol": symbol + "-" + side}
    return {"symbol": symbol, "category": "index_options", "idempotency_key": key,
            "metadata": metadata, "strategy": "premium_screener"}


@pytest.mark.parametrize("symbol", sorted(INDEX_SYMBOLS))
@pytest.mark.parametrize("side", ["CE", "PE"])
def test_reversal_requires_both_spot_and_option_confirmation(symbol, side):
    signal = make_signal(symbol, side)
    assert signal["alerts_only"] is True
    assert signal["stop_loss"] == 60
    assert signal["target"] == 90
    assert signal["spot_direction"] == ("bullish" if side == "CE" else "bearish")
    assert valid_entry(signal, NOW)


def opening_bars(shallow=False):
    start = NOW.replace(hour=9, minute=15, second=0)
    return [
        bar(start, 100, 105, 95, 101),
        bar(start + timedelta(minutes=5), 102, 113, 101, 112),
        bar(start + timedelta(minutes=10), 112, 112, 110 if shallow else 105, 111 if shallow else 108),
        bar(start + timedelta(minutes=15), 111 if shallow else 108, 115, 107, 114),
    ]


@pytest.mark.parametrize("shallow,expected", [
    (False, "opening_range_retest"), (True, "shallow_pullback_continuation"),
])
@pytest.mark.parametrize("side", ["CE", "PE"])
def test_opening_setups_are_alternatives_not_all_required(shallow, expected, side):
    raw = opening_bars(shallow)
    if side == "PE":
        raw = reflected(raw)
    now = NOW.replace(hour=9, minute=35)
    context = spot_setups(completed_candles(raw, now, timeframe_seconds=300), side, now)
    assert expected in context["setups"]


def test_no_pause_means_no_momentum_chase_and_missing_opening_is_not_substituted():
    now = NOW.replace(hour=9, minute=35)
    raw = opening_bars(True)
    raw[2].update(open=110, close=111)
    with pytest.raises(IndicatorDataError, match="index_no_setup"):
        spot_setups(completed_candles(raw, now, timeframe_seconds=300), "CE", now)
    with pytest.raises(IndicatorDataError, match="index_no_setup"):
        spot_setups(completed_candles(opening_bars()[1:], now, timeframe_seconds=300), "CE", now)


@pytest.mark.parametrize("mutation,reason", [
    (lambda s, p: p[-1].update(close=66), "index_premium_confirmation"),
    (lambda s, p: p[-1].update(volume=20), "index_volume_confirmation"),
    (lambda s, p: p[-1].update(volume=None), "index_volume_confirmation"),
    (lambda s, p: s[-1].update(close=103), "index_spot_direction"),
    (lambda s, p: p[-1].update(timestamp=NOW.timestamp()), "index_insufficient_history"),
    (lambda s, p: s[-1].update(timestamp=NOW.timestamp()), "index_confirmation_stale"),
])
def test_missing_or_unconfirmed_evidence_blocks(mutation, reason):
    spot, premium, _, _ = history()
    mutation(spot, premium)
    with pytest.raises(IndicatorDataError, match=reason):
        IndexSwingStrategy().prepare("NIFTY", "CE", spot, premium, NOW)


@pytest.mark.parametrize("mutation", [
    lambda one, quote: one[-1].update(timestamp=NOW.replace(minute=29, second=0).timestamp()),
    lambda one, quote: one[-1].update(close=68, open=69),
    lambda one, quote: quote.update(trade_timestamp=NOW.timestamp() - 11),
    lambda one, quote: quote.update(trade_timestamp=NOW.timestamp() + 1),
    lambda one, quote: quote.update(trade_timestamp=None),
    lambda one, quote: quote.update(bid=60),
    lambda one, quote: quote.update(ask=None),
    lambda one, quote: quote.update(price=80),
    lambda one, quote: quote.update(price=68),
])
def test_one_minute_must_be_later_fresh_green_and_not_chased(mutation):
    spot, premium, one, quote = history()
    engine = IndexSwingStrategy()
    reference = engine.prepare("NIFTY", "CE", spot, premium, NOW)
    mutation(one, quote)
    with pytest.raises(IndicatorDataError):
        engine.evaluate("NIFTY", "CE", reference, one, quote, NOW)


def test_future_swing_is_not_used_and_no_target_is_invented():
    spot, premium, _, _ = history()
    premium[4]["high"] = 75
    premium.append(bar(NOW.replace(minute=35), 70, 100, 60, 90))
    with pytest.raises(IndicatorDataError, match="index_no_swing_target"):
        IndexSwingStrategy().prepare("NIFTY", "CE", spot, premium, NOW)


def test_second_later_one_minute_confirmation_is_allowed_after_settling_delay():
    spot, premium, one, current_quote = history()
    later = NOW + timedelta(minutes=1)
    one.append(bar(later.replace(second=0) - timedelta(minutes=1), 69, 71, 68.5, 70))
    current_quote.update(price=70, bid=69.95, ask=70.05, trade_timestamp=later.timestamp())
    engine = IndexSwingStrategy()
    reference = engine.prepare("NIFTY", "CE", spot, premium, later)
    assert engine.evaluate("NIFTY", "CE", reference, one, current_quote, later)


def test_risk_room_and_chase_thresholds_are_measured():
    signal = make_signal()
    signal["target"] = 82.5
    assert valid_entry(signal, NOW)  # Exactly 1.5R.
    signal["target"] = 82.49
    assert not valid_entry(signal, NOW)
    signal.update(reference_low=65, target=90, breakout_price=70)
    assert valid_entry(signal, NOW)  # Exactly the 25%-of-risk chase allowance.
    signal["breakout_price"] = 70.01
    assert not valid_entry(signal, NOW)
    signal.update(breakout_price=69, reference_low=58.64, target=100)
    assert not valid_entry(signal, NOW)  # More than 15% initial risk.


def test_enabled_trial_suppresses_runtime_auto_even_with_old_live_flags(monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.setenv("INDEX_SWING_STRATEGY_ENABLED", "true")
    monkeypatch.setenv("NIFTY_LIVE_ENABLED", "true")
    monkeypatch.setenv("AUTO_TRADING_ENABLED", "true")
    import main
    assert main._load_runtime_config()["nifty_live"]["auto"] is False


def test_aggregation_requires_all_five_closed_minutes():
    start = NOW.replace(minute=30, second=0)
    raw = [bar(start + timedelta(minutes=n), 70, 75, 68, 72) for n in range(5)]
    assert aggregate_five_minutes(raw, start + timedelta(minutes=5))[0]["low"] == 68
    assert aggregate_five_minutes(raw[:-1], start + timedelta(minutes=5)) == []
    assert aggregate_five_minutes(raw, start + timedelta(minutes=4)) == []


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


def test_lifecycle_preserves_strategy_levels_and_lock_across_restart(lifecycle):
    service, clock, sent = lifecycle
    position = service.open_once(incoming())
    assert (position["entry"], position["stop_loss"], position["target"]) == (69, 60, 90)
    assert service.open_once(incoming(side="PE", key="other-side")) is None
    restarted = SignalLifecycle(service.db_path, clock=lambda: clock[0])
    assert restarted.is_open("NIFTY")
    assert restarted.open_once(incoming(key="different")) is None
    assert service.open_once(incoming("BANKNIFTY", key="bank")) is not None
    assert service.open_once(incoming("SENSEX", key="sensex")) is not None
    service.dispatch_notifications()
    assert all(category == "index_options" for category, _ in sent)
    assert "Support held/reclaimed" in sent[0][1]
    assert "Alerts only" in sent[0][1]


def quote(price, now, security=101):
    return {("NSE_FNO", security): {"price": price, "trade_timestamp": now.timestamp()}}


def test_breakeven_and_five_minute_lows_only_tighten_and_persist(lifecycle):
    service, clock, _ = lifecycle
    service.open_once(incoming())
    start = NOW.replace(minute=32, second=0)
    raw = [bar(start + timedelta(minutes=n), 71, 74, 70, 73) for n in range(2)]
    clock[0] = NOW.replace(minute=34, second=5)
    service.observe_quotes(quote(73, clock[0]), clock[0])
    service.observe_index_candles(101, "NSE_FNO", raw, clock[0])
    assert service.open_positions()[0]["stop_loss"] == 69
    # A complete five-minute candle is used only after its close.
    start = NOW.replace(minute=35, second=0)
    raw = [bar(start + timedelta(minutes=n), 75, 80, 74, 78) for n in range(5)]
    clock[0] = NOW.replace(minute=40, second=5)
    service.observe_quotes(quote(78, clock[0]), clock[0])
    service.observe_index_candles(101, "NSE_FNO", raw, clock[0])
    position = service.open_positions()[0]
    assert position["stop_loss"] == 73.95
    assert position["target"] == 90
    service.observe_index_candles(101, "NSE_FNO", raw, clock[0])
    assert service.open_positions()[0]["trailing_step"] == 2
    restarted = SignalLifecycle(service.db_path, clock=lambda: clock[0])
    assert restarted.open_positions()[0]["stop_loss"] == 73.95
    clock[0] = NOW.replace(minute=45, second=5)
    service.observe_quotes(quote(76, clock[0]), clock[0])
    lower = [bar(start + timedelta(minutes=n + 5), 73, 78, 71, 76) for n in range(5)]
    service.observe_index_candles(101, "NSE_FNO", lower, clock[0])
    assert service.open_positions()[0]["stop_loss"] == 73.95


def test_incomplete_candles_and_stale_quotes_cannot_raise_stop(lifecycle):
    service, clock, _ = lifecycle
    service.open_once(incoming())
    start = NOW.replace(minute=35, second=0)
    incomplete = [bar(start + timedelta(minutes=n), 75, 80, 74, 78) for n in range(4)]
    clock[0] = NOW.replace(minute=40, second=5)
    # No fresh observation: no protection update despite profitable candles.
    service.observe_index_candles(101, "NSE_FNO", incomplete, clock[0])
    assert service.open_positions()[0]["stop_loss"] == 60
    service.observe_quotes(quote(78, clock[0]), clock[0])
    service.observe_index_candles(101, "NSE_FNO", incomplete, clock[0])
    # Breakeven is eligible, but the incomplete five-minute low is not.
    assert service.open_positions()[0]["stop_loss"] == 69


def test_stop_gap_uses_observed_price_and_profitable_stop_is_a_win(lifecycle):
    service, clock, _ = lifecycle
    service.open_once(incoming())
    start = NOW.replace(minute=35, second=0)
    raw = [bar(start + timedelta(minutes=n), 75, 80, 74, 78) for n in range(5)]
    clock[0] = NOW.replace(minute=40, second=5)
    service.observe_quotes(quote(78, clock[0]), clock[0])
    service.observe_index_candles(101, "NSE_FNO", raw, clock[0])
    clock[0] += timedelta(seconds=2)
    service.observe_quotes(quote(72, clock[0]), clock[0])
    with sqlite3.connect(service.db_path) as db:
        closed = json.loads(db.execute("SELECT data FROM lifecycle_signals").fetchone()[0])
    assert closed["exit_premium"] == 72
    assert closed["pnl"] == 225
    assert SignalLifecycle._outcomes([closed], 0, clock[0].timestamp())["winrate"] == "100.00%"
    assert not service.is_open("NIFTY")


def test_target_closes_full_signal_and_releases_lock(lifecycle):
    service, clock, _ = lifecycle
    service.open_once(incoming())
    clock[0] += timedelta(seconds=2)
    assert service.observe_quotes(quote(90, clock[0]), clock[0]) == 1
    assert not service.is_open("NIFTY")
    assert service.status()["target"] == 1


def test_maintenance_protects_open_contract_once_per_minute_and_squareoffs(lifecycle):
    service, clock, _ = lifecycle
    service.open_once(incoming())
    clock[0] += timedelta(seconds=2)
    dm = MagicMock()
    dm.fetch_quotes.side_effect = lambda _: quote(75, clock[0])
    start = NOW.replace(minute=32, second=0)
    dm.fetch_contract_candles.return_value = [
        bar(start + timedelta(minutes=n), 71, 76, 70, 75) for n in range(2)]
    clock[0] = NOW.replace(minute=34, second=5)
    service.maintenance(dm, clock[0])
    assert service.open_positions()[0]["stop_loss"] == 69
    clock[0] += timedelta(seconds=2)
    service.maintenance(dm, clock[0])
    assert dm.fetch_contract_candles.call_count == 1
    clock[0] = NOW.replace(hour=15, minute=25)
    service.maintenance(dm, clock[0])
    assert not service.is_open("NIFTY")
    assert service.status()["session_close"] == 1
    assert service.status()["reports"] == 2  # Equity index and stock channels.


def test_daily_cap_and_stop_cooldown_are_durable(lifecycle):
    service, clock, _ = lifecycle
    for n in range(3):
        assert service.open_once(incoming(now=clock[0], key=f"setup-{n}")) is not None
        clock[0] += timedelta(seconds=2)
        service.observe_quotes(quote(90, clock[0]), clock[0])
        clock[0] += timedelta(minutes=15)
    restarted = SignalLifecycle(service.db_path, clock=lambda: clock[0])
    assert restarted.open_once(incoming(now=clock[0], key="fourth")) is None


def test_stop_cooldown_blocks_both_sides(lifecycle):
    service, clock, _ = lifecycle
    service.open_once(incoming())
    clock[0] += timedelta(seconds=2)
    service.observe_quotes(quote(59, clock[0]), clock[0])
    clock[0] += timedelta(minutes=1)
    assert service.open_once(incoming(side="PE", now=clock[0], key="new")) is None
    clock[0] += timedelta(minutes=10)
    assert service.open_once(incoming(side="PE", now=clock[0], key="new")) is not None


def test_scanner_routes_only_three_indices_and_opens_durably(tmp_path, monkeypatch):
    monkeypatch.setenv("INDEX_SWING_STRATEGY_ENABLED", "true")
    service = SignalLifecycle(tmp_path / "signals.db", clock=lambda: NOW)
    instrument = SimpleNamespace(symbol="NIFTY", category="index_options", exchange="NSE")
    stock = SimpleNamespace(symbol="RELIANCE", category="nifty50_stock_options", exchange="NSE")
    commodity = SimpleNamespace(symbol="GOLD", category="commodity_options", exchange="MCX")
    dm = MagicMock()
    dm.get_instruments.return_value = {"index_options": [instrument],
                                      "nifty50_stock_options": [stock],
                                      "commodity_options": [commodity]}
    dm.resolve_underlying.return_value = SimpleNamespace(
        security_id=13, exchange_segment="IDX_I", instrument_type="INDEX")
    spot, premium, one, current_quote = history()
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
    scanner.fetcher.fetch_option_candles.side_effect = lambda c, interval, now: premium if interval == "5min" else one
    assert scanner._uses_index_swing(instrument)
    assert not scanner._uses_index_swing(stock)
    assert not scanner._uses_index_swing(commodity)
    assert scanner._refresh_instrument(instrument, "10min", NOW, spot=110) == 1
    payload = callback.call_args.args[0]
    assert payload["metadata"]["alerts_only"] is True
    assert payload["metadata"]["timeframe"] == "5min"
    assert payload["stop_loss"] == 60
    assert payload["target_price"] == 90
    assert service.is_open("NIFTY")
    assert scanner._refresh_instrument(instrument, "10min", NOW, spot=110) == 0
    scanner.run_once(NOW.replace(minute=32))
    assert callback.call_count == 1


def test_chart_uses_five_minutes_without_changing_other_categories(tmp_path):
    provider = MagicMock(return_value=[])
    charts = LifecycleChartService(tmp_path / "charts.db", MagicMock(), provider, enabled=True)
    position = {
        "id": "test", "opened_at": NOW.timestamp(), "observed_entry": 69,
        "entry": 69, "stop_loss": 60, "target": 90, "index_swing": True,
        "security_id": 101, "exchange_segment": "NSE_FNO", "instrument_type": "OPTIDX",
        "metadata": {"timeframe": "5min"},
    }
    overlay = SignalLifecycle._chart_overlay(position, "OPEN")
    snapshot, _ = charts._fetch_snapshot(overlay)
    assert provider.call_args.args[-1] == 5
    assert snapshot["request"]["interval_minutes"] == 5
    assert charts._verify_snapshot(snapshot, overlay)
    legacy = {**overlay, "interval_minutes": 10}
    assert not charts._verify_snapshot(snapshot, legacy)
    charts._fetch_snapshot(legacy)
    assert provider.call_args.args[-1] == 10


def test_formatter_explains_zone_and_confirmation():
    metadata = incoming()["metadata"]
    message = format_option_breakout_alert(metadata)
    assert "Support held/reclaimed" in message
    assert "5m option close" in message
    assert "Alerts only" in message
