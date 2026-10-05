from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import json
import sqlite3

import pytest

from paper_portfolio import PaperPortfolio


class Market:
    def __init__(self):
        self.now = datetime(2026, 10, 5, 10, tzinfo=ZoneInfo("Asia/Kolkata"))
        self.price = 100
        self.age = 0
        self.available = True

    def clock(self):
        return self.now

    def quote(self, trade):
        if not self.available:
            return None
        return {"price": self.price, "timestamp": self.now - timedelta(seconds=self.age)}

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


def signal(**changes):
    result = {
        "symbol": "NIFTY", "entry": 100, "stop_loss": 90,
        "target_1": 110, "target_2": 120,
        "metadata": {"option_symbol": "NIFTY26OCT25000CE", "security_id": 123,
                     "exchange_segment": "NSE_FNO", "lot_size": 100},
    }
    result.update(changes)
    return result


@pytest.fixture
def setup(tmp_path):
    market = Market()
    messages = []
    engine = PaperPortfolio(
        tmp_path / "paper.sqlite", quote_provider=market.quote, clock=market.clock,
        notify=lambda message, buttons=None: messages.append((message, buttons)))
    yield engine, market, messages
    engine.close()


def opened(engine, payload=None):
    request = engine.submit(payload or signal())
    return engine.approve(request["id"], "market", actor="trader")


def test_initial_capital_and_contract_metadata(setup):
    engine, _, messages = setup
    assert engine.portfolio()["equity"] == 500000
    trade = opened(engine)
    assert trade["quantity"] == trade["lot_size"] == 100
    assert trade["lots"] == 1
    assert engine.portfolio()["cash"] == 490000
    assert engine.portfolio()["premium_in_use"] == 10000
    assert "PAPER REQUEST" in messages[0][0]
    callbacks = messages[0][1]["inline_keyboard"][0]
    assert callbacks[0]["callback_data"] == f"paper:{trade['id']}:approve_limit"
    assert any("PAPER OPEN" in message for message, _ in messages)


@pytest.mark.parametrize("field", ["option_symbol", "security_id", "exchange_segment", "lot_size"])
def test_missing_metadata_is_never_invented(setup, field):
    engine, _, _ = setup
    payload = signal()
    del payload["metadata"][field]
    with pytest.raises(ValueError, match=field):
        engine.submit(payload)
    assert engine.trades() == []


@pytest.mark.parametrize("category,exchange,expected", [
    ("index_options", "NSE_FNO", "index"),
    ("nifty50_stock_options", "NSE_FNO", "stock"),
    ("commodity_options", "MCX_COMM", "commodity"),
])
def test_screener_option_categories_normalize_for_runtime_candles(setup, category, exchange, expected):
    engine, _, _ = setup
    payload = signal()
    payload["metadata"].update(category=category, exchange_segment=exchange)
    trade = engine.submit(payload)
    assert trade["category"] == trade["segment"] == expected


def test_invalid_or_mismatched_option_segment_refused(setup):
    engine, _, _ = setup
    for category in ("crypto", "commodity_options"):
        payload = signal()
        payload["metadata"]["category"] = category
        with pytest.raises(ValueError):
            engine.submit(payload)


@pytest.mark.parametrize("price", [0, -1, float("nan"), float("inf")])
def test_invalid_quote_never_fills(setup, price):
    engine, market, _ = setup
    market.price = price
    trade = opened(engine)
    assert trade["status"] == "ENTRY_PENDING"
    assert trade["entry_price"] is None


def test_stale_missing_naive_and_future_quotes_are_refused(setup):
    engine, market, _ = setup
    market.age = 11
    trade = opened(engine)
    assert trade["status"] == "ENTRY_PENDING"
    market.available = False
    engine.tick()
    assert engine.get(trade["id"])["entry_price"] is None
    for timestamp in (market.now.replace(tzinfo=None), market.now + timedelta(seconds=1)):
        engine.quote_provider = lambda trade: {"price": 100, "timestamp": timestamp}
        engine.tick()
        assert engine.get(trade["id"])["status"] == "ENTRY_PENDING"
    engine.quote_provider = market.quote
    market.available, market.age = True, 0
    engine.tick()
    assert engine.get(trade["id"])["status"] == "OPEN"


@pytest.mark.parametrize("timestamp_kind", ["iso", "epoch"])
def test_supported_quote_timestamps(setup, timestamp_kind):
    engine, market, _ = setup
    timestamp = market.now.isoformat() if timestamp_kind == "iso" else market.now.timestamp()
    engine.quote_provider = lambda trade: {"price": 100, "timestamp": timestamp}
    assert opened(engine)["status"] == "OPEN"


def test_approval_expiry_and_rejection(setup):
    engine, market, _ = setup
    trade = engine.submit(signal())
    market.advance(60)
    assert engine.approve(trade["id"], "market", "late")["status"] == "EXPIRED"
    trade = engine.submit(signal())
    engine.reject(trade["id"], "admin")
    with pytest.raises(ValueError):
        engine.approve(trade["id"], "market", "admin")
    trade = engine.submit(signal())
    market.advance(60)
    engine.tick()
    assert engine.get(trade["id"])["status"] == "EXPIRED"


def test_latency_uses_original_signal_time(setup):
    engine, market, _ = setup
    trade = engine.submit(signal(timestamp=(market.now - timedelta(seconds=2)).isoformat()))
    market.advance(3)
    trade = engine.approve(trade["id"], "market", "admin")
    assert trade["signal_to_approval_ms"] == 5000
    assert trade["signal_to_fill_ms"] == 5000
    assert trade["approval_to_fill_ms"] == 0


@pytest.mark.parametrize("kind", ["aware_iso", "epoch", "datetime"])
def test_latency_prefers_signal_detected_at_and_persists_pipeline_fields(setup, kind):
    engine, market, _ = setup
    detected = market.now - timedelta(seconds=4)
    value = (detected.isoformat() if kind == "aware_iso" else detected.timestamp()
             if kind == "epoch" else detected)
    trade = engine.submit(signal(signal_detected_at=value, timestamp=market.now.isoformat()))
    market.advance(2)
    trade = engine.approve(trade["id"], "market")
    assert trade["signal_to_approval_ms"] == trade["signal_to_fill_ms"] == 6000
    assert trade["order_sent"] == trade["order_sent_at"]
    assert trade["approval_to_order_sent_ms"] == 0
    assert trade["order_sent_to_fill_ms"] == 0
    assert engine.get(trade["id"])["total_latency_ms"] == 6000



def test_approval_timeout_can_be_injected_from_environment(tmp_path, monkeypatch):
    market = Market()
    monkeypatch.setenv("PAPER_APPROVAL_TIMEOUT_SECONDS", "15")
    engine = PaperPortfolio(tmp_path / "paper.sqlite", clock=market.clock,
                            quote_provider=market.quote)
    try:
        assert engine.config["approval_timeout"] == 15
        trade = engine.submit(signal())
        market.advance(15)
        engine.tick()
        assert engine.get(trade["id"])["status"] == "EXPIRED"
    finally:
        engine.close()


def test_five_second_limit_fallback_both_sides(setup):
    engine, market, _ = setup
    market.price = 102
    trade = engine.submit(signal())
    engine.approve(trade["id"], "limit", "admin")
    market.advance(4)
    engine.tick()
    assert engine.get(trade["id"])["status"] == "ENTRY_PENDING"
    market.advance(1)
    market.available = False
    engine.tick()
    assert engine.get(trade["id"])["status"] == "ENTRY_PENDING"
    market.available = True
    engine.tick()
    trade = engine.get(trade["id"])
    assert trade["entry_price"] == pytest.approx(103.02)
    assert trade["entry_fallback"]
    assert trade["approval_to_fill_ms"] == 5000
    assert trade["approval_to_order_sent_ms"] == 0
    assert trade["sent_to_fill_ms"] == 5000
    assert trade["total_latency_ms"] == 5000
    assert trade["entry_price_requested"] == trade["requested_price"] == 100
    assert trade["entry_price_filled"] == trade["filled_price"] == pytest.approx(103.02)
    engine.exit(trade["id"], "limit", 105, actor="admin")
    market.advance(4)
    engine.tick()
    assert engine.get(trade["id"])["status"] == "EXIT_PENDING"
    market.advance(1)
    engine.tick()
    assert engine.get(trade["id"])["exit_price"] == pytest.approx(100.98)
    assert engine.get(trade["id"])["exit_latency_ms"] == 5000


def test_limit_execution_uses_quote_not_requested_price(setup):
    engine, market, _ = setup
    market.price = 99
    trade = engine.submit(signal())
    trade = engine.approve(trade["id"], "limit", "admin")
    assert trade["entry_price"] == 99
    market.price = 105
    trade = engine.exit(trade["id"], "limit", price=104)
    assert trade["exit_price"] == 105


def test_target1_breakeven_keeps_one_lot_then_target2_closes(setup):
    engine, market, messages = setup
    trade = opened(engine)
    market.price = 110
    engine.tick()
    trade = engine.get(trade["id"])
    assert trade["status"] == "OPEN" and trade["target1_hit"]
    assert trade["quantity"] == 100 and trade["stop_loss"] == 100
    assert trade["target1_hit_count"] == engine.portfolio()["target1_hit_count"] == 1
    assert engine.portfolio()["unrealized_pnl"] == 1000
    assert any("TARGET1" in message for message, _ in messages)
    market.price = 120
    engine.tick()
    trade = engine.get(trade["id"])
    assert trade["status"] == "CLOSED" and trade["exit_reason"] == "TARGET2"
    assert trade["realized_pnl"] == 2000
    assert engine.portfolio()["equity"] == 502000


@pytest.mark.parametrize("price,reason", [(89, "STOP_LOSS"), (120, "TARGET2")])
def test_pending_manual_limit_exit_keeps_protection_before_timeout(setup, price, reason):
    engine, market, _ = setup
    trade = opened(engine)
    engine.exit(trade["id"], "limit", price=130)
    market.advance(1)
    market.price = price
    engine.tick()
    trade = engine.get(trade["id"])
    assert trade["status"] == "CLOSED"
    assert trade["exit_reason"] == reason
    assert trade["exit_price"] == price
    assert not trade["exit_fallback"]


def test_pending_limit_exit_still_updates_target1_and_trailing(setup):
    engine, market, _ = setup
    trade = opened(engine)
    engine.exit(trade["id"], "limit", price=130)
    market.advance(1)
    market.price = 110
    engine.tick()
    trade = engine.get(trade["id"])
    assert trade["status"] == "EXIT_PENDING"
    assert trade["target1_hit"] and trade["stop_loss"] == 100
    market.price = 99
    engine.tick()
    assert engine.get(trade["id"])["exit_reason"] == "STOP_LOSS"


def test_tick_fetches_each_option_contract_once(setup):
    engine, market, _ = setup
    for _ in range(5):
        opened(engine)
    calls = []

    def quote(trade):
        calls.append((trade["exchange_segment"], trade["security_id"]))
        return market.quote(trade)

    engine.quote_provider = quote
    engine.tick()
    assert len(calls) == 1
    market.price = 120
    calls.clear()
    engine.tick()
    assert len(calls) == 1
    assert engine.portfolio()["open_positions"] == 0


def test_tick_cached_quote_cannot_fill_after_source_becomes_stale(setup):
    engine, market, _ = setup
    trade = opened(engine)
    source_time = market.now
    calls = []

    def quotes(trade):
        calls.append(market.now)
        return {"price": 89, "timestamp": source_time}

    def slow_candle(trade):
        market.advance(11)
        return None

    engine.quote_provider = quotes
    engine.candle_provider = slow_candle
    engine.tick()
    trade = engine.get(trade["id"])
    assert len(calls) == 2
    assert trade["status"] == "EXIT_PENDING"
    assert trade["exit_reason"] == "STOP_LOSS"
    assert trade["exit_price"] is None


@pytest.mark.parametrize("side", ["entry", "exit"])
def test_tick_crossing_limit_deadline_refetches_post_deadline_quote(setup, side):
    engine, market, _ = setup
    existing = opened(engine)
    market.price = 102
    if side == "entry":
        request = engine.submit(signal())
        trade = engine.approve(request["id"], "limit")
        request_time = trade["approved_at"]
    else:
        trade = engine.exit(existing["id"], "limit", price=130)
        request_time = trade["exit_requested_at"]
    market.advance(4.9)
    calls = []

    def quotes(trade):
        calls.append(market.now)
        if side == "entry" and len(calls) == 1:
            market.advance(0.3)
        return {"price": 102 if len(calls) == 1 else 103, "timestamp": market.now}

    def slow_candle(trade):
        if side == "exit":
            market.advance(0.3)
        return None

    engine.quote_provider = quotes
    engine.candle_provider = slow_candle
    engine.tick()
    result = engine.get(trade["id"])
    assert len(calls) == 2
    assert calls[0] < datetime.fromisoformat(request_time) + timedelta(seconds=5)
    assert calls[1] >= datetime.fromisoformat(request_time) + timedelta(seconds=5)
    if side == "entry":
        assert result["entry_price"] == pytest.approx(104.03)
        assert result["entry_fallback"]
    else:
        assert result["exit_price"] == pytest.approx(101.97)
        assert result["exit_fallback"]


def test_breakeven_stop_and_pending_exit_retry(setup):
    engine, market, _ = setup
    trade = opened(engine)
    market.price = 110
    engine.tick()
    market.available = False
    trade = engine.exit(trade["id"])
    assert trade["status"] == "EXIT_PENDING" and trade["exit_price"] is None
    market.price, market.available = 100, True
    engine.tick()
    assert engine.get(trade["id"])["realized_pnl"] == 0


def test_trailing_only_uses_completed_candle_and_only_tightens(setup):
    engine, market, _ = setup
    trade = opened(engine)
    engine.modify(trade["id"], {"trailing": True, "previous_candle_low": 95,
                               "previous_candle_timestamp": market.now.isoformat()}, "admin")
    assert engine.get(trade["id"])["stop_loss"] == 90
    engine.modify(trade["id"], {
        "previous_candle_timestamp": (market.now - timedelta(minutes=1)).isoformat()}, "admin")
    assert engine.get(trade["id"])["stop_loss"] == 95
    engine.modify(trade["id"], {"previous_candle_low": 91}, "admin")
    engine.tick()
    assert engine.get(trade["id"])["stop_loss"] == 95
    with pytest.raises(ValueError, match="tighten"):
        engine.modify(trade["id"], {"stop_loss": 94}, "admin")
    market.price = 94
    engine.tick()
    assert engine.get(trade["id"])["exit_reason"] == "STOP_LOSS"


def test_quote_provider_completed_candles_drive_trailing(setup):
    engine, market, _ = setup
    trade = opened(engine)
    engine.modify(trade["id"], {"trailing": True}, "admin")
    candle = {"previous_candle_low": 95,
              "previous_candle_timestamp": market.now - timedelta(minutes=1)}
    engine.quote_provider = lambda trade: {**market.quote(trade), **candle}
    engine.tick()
    assert engine.get(trade["id"])["stop_loss"] == 95
    candle["previous_candle_low"] = 99
    candle["previous_candle_timestamp"] = market.now
    engine.tick()
    assert engine.get(trade["id"])["stop_loss"] == 95
    candle["previous_candle_timestamp"] = market.now - timedelta(minutes=2)
    engine.tick()
    assert engine.get(trade["id"])["stop_loss"] == 95


def test_invalid_optional_candle_does_not_invalidate_real_quote(setup):
    engine, market, _ = setup
    engine.quote_provider = lambda trade: {
        **market.quote(trade), "previous_candle_low": "invalid",
        "previous_candle_timestamp": "invalid"}
    assert opened(engine)["status"] == "OPEN"


def test_injected_candle_provider_checks_ten_minute_completion(setup):
    engine, market, _ = setup
    candle = {"low": 96, "timestamp": market.now - timedelta(minutes=5)}
    engine.candle_provider = lambda trade: candle
    trade = opened(engine)
    assert trade["trade_id"] == trade["id"]
    assert trade["segment"] == "index"
    engine.modify(trade["id"], {"trailing": True}, "admin")
    engine.tick()
    assert engine.get(trade["id"])["stop_loss"] == 90
    candle["timestamp"] = market.now - timedelta(minutes=11)
    engine.tick()
    assert engine.get(trade["id"])["stop_loss"] == 96
    candle.update(low=98, timestamp=market.now - timedelta(days=1))
    engine.tick()
    assert engine.get(trade["id"])["stop_loss"] == 96


def test_concurrent_approvals_cannot_exceed_five_positions(setup):
    engine, _, _ = setup
    requests = [engine.submit(signal()) for _ in range(12)]

    def approve(trade):
        try:
            return engine.approve(trade["id"], "market", "concurrent")["status"]
        except ValueError:
            return "REFUSED"

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(approve, requests))
    assert results.count("OPEN") == 5
    assert engine.portfolio()["open_positions"] == 5


def test_maximum_twenty_filled_entries_per_day(setup):
    engine, _, _ = setup
    for _ in range(20):
        trade = opened(engine)
        engine.exit(trade["id"])
    assert engine.portfolio()["daily_trades"] == 20
    with pytest.raises(ValueError, match="daily trades"):
        engine.submit(signal())


@pytest.mark.parametrize("price,reason", [(400, "DAILY_PROFIT_CAP"), (0.01, "DAILY_LOSS_CAP")])
def test_combined_realized_unrealized_caps_sticky_and_persistent(setup, price, reason):
    engine, market, _ = setup
    if reason == "DAILY_LOSS_CAP":
        opened(engine)
        opened(engine)
    else:
        opened(engine, signal(target_1=600, target_2=700))
    market.price = price
    engine.tick()
    p = engine.portfolio()
    assert p["halted"] and p["halt_reason"] == reason
    assert p["open_positions"] == 0
    market.price = 100
    with pytest.raises(ValueError, match="halted"):
        engine.submit(signal())
    assert engine.portfolio()["halted"]


def test_refused_entry_commits_risk_halt(setup):
    engine, market, _ = setup
    opened(engine, signal(target_1=600, target_2=700))
    market.price = 400
    with pytest.raises(ValueError, match="halted"):
        engine.submit(signal())
    assert engine.portfolio()["halted"]
    assert engine.trades("CLOSED")[0]["exit_reason"] == "DAILY_PROFIT_CAP"


def test_realized_plus_unrealized_loss_cap(setup):
    engine, market, _ = setup
    first = opened(engine)
    market.price = 50
    engine.exit(first["id"])
    assert engine.portfolio()["daily_pnl"] == -5000
    market.price = 100
    opened(engine)
    market.price = 50
    engine.tick()
    assert engine.portfolio()["halt_reason"] == "DAILY_LOSS_CAP"
    assert engine.portfolio()["daily_pnl"] == -10000


def test_risk_requires_fresh_marks_before_new_entries(setup):
    engine, market, _ = setup
    opened(engine)
    market.advance(11)
    market.available = False
    with pytest.raises(ValueError, match="fresh quotes"):
        engine.submit(signal())
    market.available = True
    assert engine.submit(signal())["status"] == "REQUESTED"



@pytest.mark.parametrize("key,value", [
    ("lots", 2), ("max_open_positions", 0), ("max_daily_trades", 1.5),
    ("profit_cap", -1), ("loss_cap", 0), ("initial_capital", 500001),
    ("limit_timeout", 6), ("quote_max_age", float("nan")),
])
def test_configuration_requires_valid_limits_and_preserves_one_lot(setup, key, value):
    engine, _, _ = setup
    with pytest.raises(ValueError):
        engine.configure({key: value}, "admin")


def test_config_changes_reapply_risk(setup):
    engine, market, _ = setup
    opened(engine)
    market.price = 105
    engine.configure({"profit_cap": 500}, "admin")
    assert engine.portfolio()["halted"]


def test_refused_commands_are_audited_with_actor_and_original_state(setup):
    engine, _, _ = setup
    trade = opened(engine)
    with pytest.raises(ValueError):
        engine.modify(trade["id"], {"stop_loss": 89}, actor="alice")
    row = engine._conn.execute(
        "SELECT actor,old_value,new_value FROM paper_audit WHERE action='MODIFY_REFUSED'"
    ).fetchone()
    assert row["actor"] == "alice"
    assert json.loads(row["old_value"])["stop_loss"] == 90
    assert "tighten" in json.loads(row["new_value"])["error"]
    assert engine.get(trade["id"])["stop_loss"] == 90


def test_runtime_configurable_cutoffs_and_compatibility_aliases(setup):
    engine, market, _ = setup
    request = engine.submit(signal())
    assert request["entry_price_requested"] == 100
    assert request["lifecycle_state"] == "pending_approval"
    assert engine.trades("pending_approval")[0]["id"] == request["id"]
    request = engine.modify(request["id"], {"trailing_enabled": True}, "admin")
    assert request["trailing_enabled"] and request["status"] == "REQUESTED"
    trade = engine.approve(request["id"], "market")
    engine.configure({"index_cutoff": "10:01", "max_open_trades": 4}, "admin")
    assert engine.config["max_open_positions"] == 4
    market.advance(60)
    market.available = False
    engine.tick()
    assert engine.trades("open")[0]["status"] == "EXIT_PENDING"
    market.available = True
    engine.tick()
    assert engine.get(trade["id"])["status"] == "CLOSED"


def test_modify_desired_entry_order_type_requires_final_approval(setup):
    engine, _, _ = setup
    trade = engine.submit(signal())
    assert trade["entry_order_type"] == "limit"
    trade = engine.modify(trade["id"], {"entry_order_type": "market"}, "admin")
    assert trade["status"] == "REQUESTED"
    assert trade["entry_order_type"] == "market"
    trade = engine.approve(trade["id"], actor="admin")
    assert trade["status"] == "OPEN"
    with pytest.raises(ValueError):
        engine.modify(trade["id"], {"entry_order_type": "limit"}, "admin")


def test_approval_mode_off_auto_approves_paper_only_with_risk_limits(setup):
    engine, market, messages = setup
    engine.configure({"approval_required": False, "max_open_positions": 1}, "admin")
    market.available = False
    trade = engine.submit(signal())
    assert trade["status"] == "ENTRY_PENDING"
    assert trade["approved_by"] == "SYSTEM"
    assert any("QUOTE ALERT" in message for message, _ in messages)
    market.available = True
    engine.tick()
    assert engine.get(trade["id"])["status"] == "OPEN"
    with pytest.raises(ValueError, match="maximum open"):
        engine.submit(signal())
    engine.exit(trade["id"])
    engine.configure({"approval_required": True}, "admin")
    assert engine.submit(signal())["status"] == "REQUESTED"


def test_runtime_limits_can_change_beyond_defaults(setup):
    engine, _, _ = setup
    config = engine.configure({
        "max_open_positions": 6, "max_daily_trades": 21,
        "profit_cap": 40000, "loss_cap": 15000}, "admin")
    assert config["max_open_positions"] == 6
    for _ in range(6):
        assert opened(engine)["status"] == "OPEN"
    with pytest.raises(ValueError, match="maximum open"):
        opened(engine)


def test_missing_quote_alerts_are_durable_and_rate_limited(setup):
    engine, market, messages = setup
    market.available = False
    trade = opened(engine)
    assert trade["status"] == "ENTRY_PENDING"
    assert len([message for message, _ in messages if "QUOTE ALERT" in message]) == 1
    engine.tick()
    assert len([message for message, _ in messages if "QUOTE ALERT" in message]) == 1
    market.advance(60)
    engine.tick()
    assert len([message for message, _ in messages if "QUOTE ALERT" in message]) == 2
    assert engine._conn.execute(
        "SELECT COUNT(*) FROM paper_audit WHERE action='QUOTE_UNAVAILABLE'").fetchone()[0] == 2


def test_limit_timeout_uses_adverse_fallback_even_when_price_crosses(setup):
    engine, market, _ = setup
    market.price = 102
    trade = engine.submit(signal())
    engine.approve(trade["id"], "limit")
    market.advance(5)
    market.price = 99
    engine.tick()
    assert engine.get(trade["id"])["entry_price"] == pytest.approx(99.99)
    engine.exit(trade["id"], "limit", price=105)
    market.advance(5)
    market.price = 106
    engine.tick()
    assert engine.get(trade["id"])["exit_price"] == pytest.approx(104.94)


def test_default_trailing_and_target1_enable_trailing(setup):
    engine, market, _ = setup
    trade = opened(engine)
    assert trade["trailing"]
    engine.modify(trade["id"], {"trailing": False}, "admin")
    market.price = 110
    engine.tick()
    trade = engine.get(trade["id"])
    assert trade["target1_hit_at"] and trade["trailing"]


def test_daily_snapshot_complete_summary(setup):
    engine, market, _ = setup
    trade = opened(engine)
    market.price = 105
    engine.exit(trade["id"])
    row = engine._conn.execute("SELECT * FROM paper_daily_snapshots").fetchone()
    assert row["date"] == market.now.date().isoformat()
    assert row["opening_balance"] == 500000
    assert row["closing_balance"] == 500500
    assert row["total_trades"] == row["winning_trades"] == 1
    assert row["losing_trades"] == row["open_positions"] == 0
    assert json.loads(row["payload"])["equity"] == 500500
    portfolio = json.loads(engine._conn.execute(
        "SELECT payload FROM paper_portfolio_state WHERE id=1").fetchone()[0])
    assert portfolio["equity"] == 500500
    assert portfolio["daily_wins"] == portfolio["daily_closed_trades"] == 1
    assert portfolio["daily_losses"] == 0
    assert portfolio["daily_exit_reasons"] == {"MANUAL": 1}
    assert portfolio["daily_manual_exits"] == 1


def test_exact_required_lifecycle_portfolio_and_daily_summary_names(setup):
    engine, _, _ = setup
    trade = opened(engine)
    lifecycle = {
        "trade_id", "symbol", "option_symbol", "segment", "lot_size", "lots", "qty",
        "entry_order_type", "entry_price_requested", "entry_price_filled", "entry_time",
        "stop_loss", "target1", "target2", "trailing_enabled", "trailing_value",
        "exit_order_type", "exit_price_requested", "exit_price_filled", "exit_time",
        "exit_reason", "pnl_points", "pnl_rupees", "status", "lifecycle",
        "signal_detected_at", "approval_time", "order_sent", "fill_time",
        "approval_to_order_sent_ms", "order_sent_to_fill_ms", "signal_to_fill_ms",
    }
    assert lifecycle <= set(trade)
    assert trade["qty"] == 100
    assert trade["target1"] == 110 and trade["target2"] == 120
    engine.squareoff(reason="CUSTOM_SESSION", actor="admin")
    state = json.loads(engine._conn.execute(
        "SELECT payload FROM paper_portfolio_state").fetchone()[0])
    assert {"opening_balance", "available_balance", "used_margin", "premium_in_use",
            "realized_pnl", "unrealized_pnl", "net_pnl", "closing_balance"} <= set(state)
    daily = json.loads(engine._conn.execute(
        "SELECT payload FROM paper_daily_snapshots").fetchone()[0])
    required = {"date", "opening_balance", "closing_balance", "total_trades",
                "open_positions_count", "target1_hit_count", "target2_hit_count",
                "stop_loss_hit_count", "manual_exit_count", "squareoff_exit_count",
                "realized_pnl", "unrealized_pnl", "net_pnl"}
    assert required <= set(daily)
    assert daily["squareoff_exit_count"] == 1


@pytest.mark.parametrize("segment,hour,minute", [
    ("NSE_FNO", 15, 25), ("BSE_FNO", 15, 25), ("MCX_COMM", 23, 0),
])
def test_segment_cutoff_cancels_requests_and_closes_positions(setup, segment, hour, minute):
    engine, market, _ = setup
    payload = signal()
    payload["metadata"]["exchange_segment"] = segment
    trade = opened(engine, payload)
    pending = engine.submit(payload)
    market.now = market.now.replace(hour=hour, minute=minute)
    market.available = False
    engine.tick()
    assert engine.get(trade["id"])["status"] == "EXIT_PENDING"
    assert engine.get(pending["id"])["status"] == "EXPIRED"
    market.available = True
    engine.tick()
    assert engine.get(trade["id"])["exit_reason"] == "SESSION_CUTOFF"
    assert engine.get(trade["id"])["status"] == "CLOSED"
    with pytest.raises(ValueError, match="cutoff"):
        engine.submit(payload)


def test_commodity_remains_open_after_equity_cutoff(setup):
    engine, market, _ = setup
    payload = signal()
    payload["metadata"]["exchange_segment"] = "MCX_COMM"
    trade = opened(engine, payload)
    market.now = market.now.replace(hour=16)
    engine.tick()
    assert engine.get(trade["id"])["status"] == "OPEN"


def test_next_day_squareoff_and_daily_reset(setup):
    engine, market, _ = setup
    trade = opened(engine)
    market.now += timedelta(days=1)
    market.available = False
    engine.tick()
    assert engine.get(trade["id"])["status"] == "EXIT_PENDING"
    with pytest.raises(ValueError, match="overdue"):
        engine.submit(signal())
    market.available = True
    engine.tick()
    assert engine.get(trade["id"])["status"] == "CLOSED"
    assert engine.portfolio()["daily_trades"] == 0
    assert opened(engine)["status"] == "OPEN"


def test_emergency_squareoff_cancels_pending_and_retries_unquoted_exits(setup):
    engine, market, _ = setup
    trade = opened(engine)
    request = engine.submit(signal())
    market.available = False
    engine.squareoff(actor="operator")
    assert engine.get(trade["id"])["status"] == "EXIT_PENDING"
    assert engine.get(request["id"])["status"] == "CANCELLED"
    market.available = True
    engine.tick()
    assert engine.get(trade["id"])["exit_reason"] == "EMERGENCY"


def test_durable_config_daily_snapshot_audit_and_restart(tmp_path):
    path = tmp_path / "paper.sqlite"
    market = Market()
    engine = PaperPortfolio(path, quote_provider=market.quote, clock=market.clock)
    trade = opened(engine)
    engine.modify(trade["id"], {"stop_loss": 95}, "alice")
    engine.configure({"approval_timeout": 30}, "alice")
    market.price = 105
    engine.exit(trade["id"], actor="alice")
    engine.close()
    engine = PaperPortfolio(path, quote_provider=market.quote, clock=market.clock)
    try:
        assert engine.config["approval_timeout"] == 30
        assert engine.get(trade["id"])["status"] == "CLOSED"
        assert engine.portfolio()["equity"] == 500500
        with sqlite3.connect(path) as db:
            row = db.execute("SELECT equity,trades_count FROM paper_daily_snapshots").fetchone()
            assert row == (500500, 1)
            rows = db.execute(
                "SELECT action,actor,old_value,new_value FROM paper_audit").fetchall()
            actions = {row[0] for row in rows}
            assert {"SUBMIT", "APPROVE", "ENTRY_FILL", "MODIFY",
                    "CONFIGURE", "EXIT_REQUEST", "EXIT_FILL"} <= actions
            modified = next(row for row in rows if row[0] == "MODIFY")
            assert modified[1] == "alice"
            assert json.loads(modified[2])["stop_loss"] == 90
            assert json.loads(modified[3])["stop_loss"] == 95
    finally:
        engine.close()


def test_halt_survives_restart_but_resets_next_ist_day(tmp_path):
    market = Market()
    path = tmp_path / "paper.sqlite"
    engine = PaperPortfolio(path, quote_provider=market.quote, clock=market.clock)
    opened(engine, signal(target_1=600, target_2=700))
    market.price = 400
    engine.tick()
    engine.close()
    engine = PaperPortfolio(path, quote_provider=market.quote, clock=market.clock)
    try:
        assert engine.portfolio()["halted"]
        market.now += timedelta(days=1)
        market.price = 100
        engine.tick()
        assert not engine.portfolio()["halted"]
        assert engine.portfolio()["daily_pnl"] == 0
        assert engine.portfolio()["equity"] == 530000
        assert opened(engine)["status"] == "OPEN"
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT COUNT(*) FROM paper_daily_snapshots").fetchone()[0] == 2
    finally:
        engine.close()


def test_shared_sqlite_database_instances_enforce_global_position_cap(tmp_path):
    market = Market()
    path = tmp_path / "shared.sqlite"
    engines = [PaperPortfolio(path, quote_provider=market.quote, clock=market.clock)
               for _ in range(2)]
    requests = [engines[0].submit(signal()) for _ in range(10)]

    def approve(index):
        try:
            return engines[index % 2].approve(requests[index]["id"], "market")["status"]
        except ValueError:
            return "REFUSED"

    try:
        with ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(approve, range(10)))
        assert results.count("OPEN") == 5
        assert engines[0].portfolio()["open_positions"] == 5
        assert engines[1].portfolio()["open_positions"] == 5
    finally:
        for engine in engines:
            engine.close()


def test_signal_id_deduplicates_concurrent_submissions_and_restart(tmp_path):
    market = Market()
    path = tmp_path / "dedup.sqlite"
    engines = [PaperPortfolio(path, quote_provider=market.quote, clock=market.clock)
               for _ in range(2)]
    payload = signal(signal_id="durable-source-123", action="SELL")
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            trades = list(pool.map(lambda index: engines[index % 2].submit(payload), range(8)))
        assert len({trade["id"] for trade in trades}) == 1
        trade = engines[0].approve(trades[0]["id"], "market")
        assert trade["side"] == "BUY"
        engines[0].exit(trade["id"])
    finally:
        for engine in engines:
            engine.close()
    engine = PaperPortfolio(path, quote_provider=market.quote, clock=market.clock)
    try:
        # Duplicate delivery is returned even when its re-delivered metadata is incomplete.
        duplicate = engine.submit({"signal_id": "durable-source-123"})
        assert duplicate["id"] == trade["id"]
        assert duplicate["status"] == "CLOSED"
        assert len(engine.trades()) == 1
        assert engine.portfolio()["daily_trades"] == 1
    finally:
        engine.close()


def test_notification_failure_does_not_rollback_paper_fills(setup):
    engine, _, _ = setup

    def broken_notify(message, buttons=None):
        raise RuntimeError("delivery failed")

    engine.notify = broken_notify
    assert opened(engine)["status"] == "OPEN"
