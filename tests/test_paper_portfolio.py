import json
import multiprocessing
from datetime import datetime, time as market_time
from zoneinfo import ZoneInfo

import pytest

from paper_portfolio import PaperPortfolio

IST = ZoneInfo("Asia/Kolkata")


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 5, 10, tzinfo=IST).timestamp()

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def signal(identifier=1, **changes):
    metadata = {
        "security_id": identifier, "exchange_segment": "NSE_FNO",
        "option_symbol": f"NIFTY-{identifier}-CE", "lot_size": 50,
        "tick_size": .05, "stop_loss": 90, "timeframe": "5min",
        "category": "index_options",
    }
    metadata.update(changes.pop("metadata", {}))
    return {"signal_id": str(identifier), "entry_price": 100,
            "metadata": metadata, **changes}


def quote(clock, price=100, **changes):
    return {"price": price, "timestamp": clock(), **changes}


@pytest.fixture
def engine(tmp_path):
    clock = Clock()
    events = []
    portfolio = PaperPortfolio(tmp_path / "paper.db", clock=clock, notify=events.append)
    return portfolio, clock, events


def open_position(engine, identifier=1, **changes):
    portfolio, clock, _ = engine
    order = portfolio.submit(signal(identifier, **changes), quote(clock))
    return portfolio.action("approve_limit", order["id"], actor="alice")


def test_initial_capital_once_and_persisted_dedupe(engine):
    portfolio, clock, _ = engine
    order = open_position(engine)
    assert order["status"] == "filled"
    reopened = PaperPortfolio(portfolio.db_path, clock=clock)
    assert reopened.snapshot()["account"]["cash"] == 495000
    assert reopened.submit(signal(1))["id"] == order["id"]
    assert reopened.record_update(123)
    assert not portfolio.record_update(123)
    assert reopened.snapshot()["account"]["mode"] == "PRACTICE"


@pytest.mark.parametrize("field,value", [
    ("lot_size", 0), ("lot_size", -1), ("lot_size", 1.5), ("lot_size", True),
    ("lot_size", "50"), ("tick_size", 0), ("tick_size", float("nan")),
    ("tick_size", None), ("stop_loss", 100),
    ("exchange_segment", "UNKNOWN"), ("timeframe", "forever"),
])
def test_invalid_metadata(engine, field, value):
    portfolio, _, _ = engine
    result = portfolio.submit(signal(metadata={field: value}))
    assert result["status"] == "rejected"
    assert not portfolio.snapshot()["pending"]


def test_missing_tick_size(engine):
    portfolio, _, _ = engine
    value = signal()
    del value["metadata"]["tick_size"]
    assert portfolio.submit(value)["status"] == "rejected"


def test_integral_contract_float_lot_normalizes_to_exact_units(engine):
    portfolio, clock, _ = engine
    request = portfolio.submit(signal(metadata={"lot_size": 65.0}), quote(clock))
    assert request["status"] == "awaiting_approval"
    result = portfolio.action("approve_limit", request["id"])
    assert result["quantity"] == 65 and isinstance(result["lot_size"], int)


def test_new_signals_require_fresh_executable_quote(engine):
    portfolio, clock, _ = engine
    assert portfolio.submit(signal())["reason"] == "no_price"
    assert portfolio.submit(signal(2), quote(clock, timestamp=clock() - 11))["reason"] == "no_price"
    assert not portfolio.snapshot()["pending"]
    assert portfolio.snapshot()["account"]["reserved_cash"] == 0


def test_signal_age_bounds_approval_window_and_idempotency_precedes_signal_id(engine):
    portfolio, clock, _ = engine
    timestamp = datetime.fromtimestamp(clock() - 50, IST).isoformat()
    request = portfolio.submit(signal(
        timestamp=timestamp, idempotency_key="stable-key"), quote(clock))
    assert request["expires_at"] == clock() + 10
    clock.advance(10)
    portfolio.tick({})
    duplicate = portfolio.submit(signal(
        2, timestamp=timestamp, idempotency_key="stable-key"), quote(clock))
    assert duplicate["id"] == request["id"] and duplicate["status"] == "expired"
    assert portfolio.submit(signal(
        3, timestamp=timestamp), quote(clock))["reason"] == "stale_signal"


def test_signal_timings_include_queue_delay_and_keep_request_timings_distinct(engine):
    portfolio, clock, _ = engine
    timestamp = datetime.fromtimestamp(clock() - 10, IST).isoformat()
    request = portfolio.submit(signal(timestamp=timestamp), quote(clock, 101))
    assert request["signal_to_submit_seconds"] == 10
    clock.advance(2)
    approved = portfolio.action("approve_limit", request["id"])
    assert approved["signal_to_approval_seconds"] == 12
    assert approved["request_to_approval_seconds"] == 2
    clock.advance(5)
    portfolio.tick({("NSE_FNO", 1): quote(clock, 101)})
    position = portfolio.snapshot()["positions"][0]
    assert position["signal_to_fill_seconds"] == 17
    assert position["request_to_fill_seconds"] == 7
    assert position["order_to_fill_seconds"] == 5


def test_short_entry_is_rejected(engine):
    portfolio, clock, _ = engine
    assert portfolio.submit(signal(side="SELL"), quote(clock))["status"] == "rejected"


def test_reserved_slots_and_cash(engine):
    portfolio, clock, _ = engine
    requests = [portfolio.submit(signal(i), quote(clock)) for i in range(1, 7)]
    assert all(o["status"] == "awaiting_approval" for o in requests[:5])
    assert requests[5]["reason"] == "position_limit"
    assert portfolio.snapshot()["account"]["reserved_cash"] == 25250
    portfolio.action("reject", requests[0]["id"], actor="operator")
    assert portfolio.submit(signal(7), quote(clock))["status"] == "awaiting_approval"
    costly = signal(8, entry_price=10000, metadata={"stop_loss": 9000, "lot_size": 100})
    portfolio.action("reject", requests[1]["id"])
    assert portfolio.submit(costly, quote(clock, 10000))["reason"] == "insufficient_cash"


def test_approval_expiry_and_idempotence(engine):
    portfolio, clock, events = engine
    request = portfolio.submit(signal(), quote(clock))
    clock.advance(60)
    result = portfolio.action("approve_market", request["id"], actor=99)
    assert result["status"] == "expired"
    assert portfolio.snapshot()["account"]["reserved_cash"] == 0
    assert portfolio.action("approve_market", request["id"])["status"] == "expired"
    assert sum(e["type"] == "order_expired" for e in events) == 1
    assert any(a["actor"] == "99" for a in portfolio.snapshot()["audit"])


def test_limit_ask_evidence_then_five_second_fallback(engine):
    portfolio, clock, _ = engine
    request = portfolio.submit(signal(), quote(clock, 99, ask=102, bid=98))
    assert portfolio.action("approve_limit", request["id"])["status"] == "pending"
    clock.advance(5)
    portfolio.tick({("NSE_FNO", 1): quote(clock, 99, ask=102.01, bid=98)})
    result = portfolio.snapshot()["positions"][0]
    assert result["entry_price"] == 103.05
    assert result["quantity"] == 50
    assert result["quote_source"] == "ASK"
    assert result["fill_model"] == "adverse_1pct"
    assert result["approval_to_order_seconds"] >= 0
    assert result["order_to_fill_seconds"] == 5


def test_limit_evidence_takes_priority_over_elapsed_fallback(engine):
    portfolio, clock, _ = engine
    request = portfolio.submit(signal(), quote(clock, 101))
    portfolio.action("approve_limit", request["id"])
    clock.advance(6)
    portfolio.tick({("NSE_FNO", 1): quote(clock, 99)})
    position = portfolio.snapshot()["positions"][0]
    assert position["entry_price"] == 99
    assert position["fill_model"] == "limit"


def test_limit_fallback_requires_quote_evidence_after_five_second_wait(engine):
    portfolio, clock, _ = engine
    original = quote(clock, 101)
    request = portfolio.submit(signal(metadata={"stop_loss": 95}), original)
    portfolio.action("approve_limit", request["id"])
    clock.advance(6)
    # The initial quote is still within the ten-second freshness limit, but
    # cannot serve as new evidence for an adverse fallback after the wait.
    portfolio.tick({("NSE_FNO", 1): original})
    assert not portfolio.snapshot()["positions"]
    assert portfolio.snapshot()["pending"][0]["status"] == "pending"
    portfolio.tick({("NSE_FNO", 1): quote(clock, 101)})
    position = portfolio.snapshot()["positions"][0]
    assert position["entry_price"] == 102.05
    assert position["risk_points"] == 7.05
    assert position["target_1"] == 109.1 and position["target_2"] == 116.15


def test_market_slippage_recomputes_targets_from_actual_fill_and_stop(engine):
    portfolio, clock, events = engine
    request = portfolio.submit(signal(metadata={"stop_loss": 95}), quote(clock))
    assert request["target_1"] == 105 and request["target_2"] == 110
    result = portfolio.action("approve_market", request["id"])
    assert result["fill_price"] == 101
    assert result["risk_points"] == 6
    assert result["risk_inr"] == 300
    assert result["target_1"] == 107 and result["target_2"] == 113
    portfolio.tick({("NSE_FNO", 1): quote(clock, 105)})
    assert not portfolio.snapshot()["positions"][0]["t1_reached"]
    portfolio.tick({("NSE_FNO", 1): quote(clock, 107)})
    assert portfolio.snapshot()["positions"][0]["stop_loss"] == 101
    assert sum(event["type"] == "target_1" for event in events) == 1
    portfolio.tick({("NSE_FNO", 1): quote(clock, 113)})
    assert not portfolio.snapshot()["positions"]
    assert portfolio.snapshot()["closed_positions"][0]["exit_reason"] == "target_2"


@pytest.mark.parametrize("change", [
    {"timestamp": 1}, {"timestamp": float("nan")}, {"price": 0},
    {"price": float("inf")}, {"bid": 105, "ask": 100}, {"ask": -1},
    {"timestamp": 99999999999},
])
def test_invalid_or_stale_quotes_never_fill(engine, change):
    portfolio, clock, _ = engine
    request = portfolio.submit(signal(), quote(clock, **change))
    assert request["reason"] == "no_price"
    assert portfolio.action("approve_market", request["id"])["status"] == "rejected"
    clock.advance(6)
    portfolio.tick({("NSE_FNO", 1): quote(clock, **change)})
    assert not portfolio.snapshot()["positions"]


def test_market_rounding_bid_exit_and_disclosed_ltp(engine):
    portfolio, clock, _ = engine
    request = portfolio.submit(signal(), quote(clock, 100.01))
    result = portfolio.action("approve_market", request["id"])
    assert result["fill_price"] == 101.05
    assert result["quote_source"] == "LTP"
    portfolio.tick({("NSE_FNO", 1): quote(clock, 105, bid=104.03, ask=105.5)})
    portfolio.action("close", request["id"], actor="alice")
    snapshot = portfolio.snapshot()
    assert not snapshot["positions"]
    assert snapshot["executions"][-1]["price"] == 102.95
    assert snapshot["executions"][-1]["quote_source"] == "BID"
    assert snapshot["closed_positions"][0]["exit_reason"] == "manual_close"


def test_sell_limit_uses_bid_side_evidence_and_persists_filled_order(engine):
    portfolio, clock, _ = engine
    entry = open_position(engine)
    portfolio.tick({("NSE_FNO", 1): quote(clock, 104, bid=101, ask=105)})
    result = portfolio.action("close", entry["id"], order_type="LIMIT", limit_price=102)
    assert result["status"] == "open"
    snapshot = portfolio.snapshot()
    exit_order = snapshot["pending_exit_orders"][0]
    assert exit_order["side"] == "SELL" and exit_order["order_type"] == "LIMIT"
    assert exit_order["limit_price"] == 102 and exit_order["evidence"] == 101
    assert exit_order["quote_source"] == "BID" and not exit_order["approval_required"]
    assert snapshot["positions"][0]["exit_order_id"] == exit_order["id"]
    assert snapshot["account"]["cash"] == 495000
    assert snapshot["account"]["available_slots"] == 4
    assert not any(execution["side"] == "SELL" for execution in snapshot["executions"])
    clock.advance(2)
    portfolio.tick({("NSE_FNO", 1): quote(clock, 105, bid=102.02, ask=106)})
    snapshot = portfolio.snapshot()
    assert snapshot["pending_exit_orders"] == [] and snapshot["positions"] == []
    assert snapshot["exit_orders"][0]["id"] == exit_order["id"]
    assert snapshot["exit_orders"][0]["status"] == "filled"
    assert snapshot["exit_orders"][0]["fill_price"] == 102
    assert snapshot["executions"][-1]["fill_model"] == "limit"
    assert snapshot["executions"][-1]["exit_order_id"] == exit_order["id"]
    assert snapshot["account"]["cash"] == 500100


def test_sell_limit_discloses_ltp_when_no_bid_and_never_applies_adverse_on_evidence(engine):
    portfolio, clock, _ = engine
    entry = open_position(engine)
    portfolio.action("close", entry["id"], order_type="LIMIT", limit_price=100)
    snapshot = portfolio.snapshot()
    assert snapshot["exit_orders"][0]["quote_source"] == "LTP"
    assert snapshot["exit_orders"][0]["fill_model"] == "limit"
    assert snapshot["executions"][-1]["price"] == 100
    assert snapshot["account"]["cash"] == 500000


def test_sell_limit_fallback_waits_for_new_quote_after_five_seconds(engine):
    portfolio, clock, _ = engine
    entry = open_position(engine)
    old_quote = quote(clock)
    portfolio.action("close", entry["id"], order_type="LIMIT", limit_price=105)
    exit_id = portfolio.snapshot()["pending_exit_orders"][0]["id"]
    clock.advance(6)
    portfolio.tick({("NSE_FNO", 1): old_quote})
    assert portfolio.snapshot()["pending_exit_orders"][0]["id"] == exit_id
    assert len(portfolio.snapshot()["positions"]) == 1
    portfolio.tick({("NSE_FNO", 1): quote(clock, 101)})
    snapshot = portfolio.snapshot()
    assert not snapshot["positions"]
    assert snapshot["exit_orders"][0]["id"] == exit_id
    assert snapshot["exit_orders"][0]["fill_price"] == 99.95
    assert snapshot["exit_orders"][0]["fill_model"] == "adverse_1pct"
    assert snapshot["exit_orders"][0]["quote_timestamp"] == clock()
    portfolio.tick({("NSE_FNO", 1): quote(clock)})
    portfolio.action("close", entry["id"])
    assert sum(e["side"] == "SELL" for e in portfolio.snapshot()["executions"]) == 1


def test_sell_order_outage_restart_preserves_trigger_and_pending_position(engine):
    portfolio, clock, _ = engine
    entry = open_position(engine)
    clock.advance(11)
    portfolio.action("close", entry["id"], order_type="LIMIT", limit_price=105)
    pending = portfolio.snapshot()["pending_exit_orders"][0]
    assert pending["quote"] is None and pending["trigger_time"] == clock()
    reopened = PaperPortfolio(portfolio.db_path, clock=clock)
    clock.advance(5)
    reopened.tick({})
    snapshot = reopened.snapshot()
    assert snapshot["pending_exit_orders"][0]["created_at"] == pending["created_at"]
    assert snapshot["account"]["cash"] == 495000 and len(snapshot["positions"]) == 1
    reopened.tick({("NSE_FNO", 1): quote(clock, 109, bid=108, ask=110)})
    snapshot = reopened.snapshot()
    assert snapshot["exit_orders"][0]["id"] == pending["id"]
    assert snapshot["exit_orders"][0]["fill_price"] == 108
    assert snapshot["exit_orders"][0]["fill_model"] == "limit"
    assert snapshot["account"]["available_slots"] == 5


def test_protective_sell_market_order_waits_through_calendar_outage_restart(engine):
    portfolio, clock, _ = engine
    open_position(engine)
    portfolio.calendar = lambda exchange, now: False
    portfolio.tick({("NSE_FNO", 1): quote(clock, 89)})
    pending = portfolio.snapshot()["pending_exit_orders"][0]
    assert pending["order_type"] == "MARKET" and pending["reason"] == "initial_stop"
    reopened = PaperPortfolio(portfolio.db_path, clock=clock, calendar=lambda exchange, now: False)
    reopened.tick({})
    assert reopened.snapshot()["pending_exit_orders"][0]["id"] == pending["id"]
    assert reopened.snapshot()["account"]["cash"] == 495000
    reopened.calendar = lambda exchange, now: True
    clock.advance(1)
    reopened.tick({("NSE_FNO", 1): quote(clock, 91)})
    snapshot = reopened.snapshot()
    assert snapshot["exit_orders"][0]["status"] == "filled"
    assert snapshot["exit_orders"][0]["fill_model"] == "adverse_1pct"
    assert snapshot["exit_orders"][0]["fill_price"] == 90.05
    assert snapshot["closed_positions"][0]["exit_reason"] == "initial_stop"


@pytest.mark.parametrize("trigger,reason", [
    ("risk", "daily_loss_limit"), ("cutoff", "time_cutoff"), ("stop", "initial_stop"),
    ("target", "target_2"),
])
def test_protection_escalates_existing_sell_limit_without_duplicate_order(engine, trigger, reason):
    portfolio, clock, _ = engine
    entry = open_position(engine)
    portfolio.action("close", entry["id"], order_type="LIMIT", limit_price=105)
    exit_id = portfolio.snapshot()["pending_exit_orders"][0]["id"]
    if trigger == "risk":
        portfolio.action("limits", loss_limit=100)
        price = 97
    elif trigger == "cutoff":
        clock.now = datetime(2026, 10, 5, 15, 25, tzinfo=IST).timestamp()
        price = 100
    elif trigger == "target":
        price = 120
    else:
        price = 89
    portfolio.tick({("NSE_FNO", 1): quote(clock, price)})
    snapshot = portfolio.snapshot()
    assert len(snapshot["exit_orders"]) == 1
    assert snapshot["exit_orders"][0]["id"] == exit_id
    assert snapshot["exit_orders"][0]["order_type"] == "MARKET"
    assert snapshot["exit_orders"][0]["status"] == "filled"
    assert snapshot["closed_positions"][0]["exit_reason"] == reason
    assert sum(e["side"] == "SELL" for e in snapshot["executions"]) == 1
    assert snapshot["daily"]["filled"] == 1
    assert snapshot["account"]["remaining_daily_entries"] == 19
    audit = next(json.loads(row["data"]) for row in snapshot["audit"]
                 if row["action"] == "exit_escalated")
    assert audit["old"] == {"order_type": "LIMIT", "limit_price": 105, "reason": "manual_close"}
    assert audit["new"] == {"order_type": "MARKET", "limit_price": None, "reason": reason}
    assert audit["outcome"] == "escalated"


def test_pending_manual_market_exit_reprioritizes_stop_reason_in_same_order(engine):
    portfolio, clock, _ = engine
    entry = open_position(engine)
    clock.advance(11)
    portfolio.action("close", entry["id"])
    exit_id = portfolio.snapshot()["pending_exit_orders"][0]["id"]
    clock.advance(1)
    portfolio.tick({("NSE_FNO", 1): quote(clock, 89)})
    snapshot = portfolio.snapshot()
    assert len(snapshot["exit_orders"]) == 1
    assert snapshot["exit_orders"][0]["id"] == exit_id
    assert snapshot["exit_orders"][0]["reason"] == "initial_stop"
    assert snapshot["closed_positions"][0]["exit_reason"] == "initial_stop"
    audit = next(json.loads(row["data"]) for row in snapshot["audit"]
                 if row["action"] == "exit_escalated")
    assert audit["old"]["order_type"] == audit["new"]["order_type"] == "MARKET"
    assert audit["old"]["reason"] == "manual_close" and audit["new"]["reason"] == "initial_stop"


@pytest.mark.parametrize("kwargs", [
    {"order_type": "OTHER"}, {"order_type": "LIMIT"}, {"order_type": "LIMIT", "limit_price": 0},
])
def test_invalid_manual_exit_order_does_not_change_position(engine, kwargs):
    portfolio, _, _ = engine
    entry = open_position(engine)
    assert portfolio.action("close", entry["id"], **kwargs)["status"] == "rejected"
    snapshot = portfolio.snapshot()
    assert not snapshot["exit_orders"]
    assert snapshot["positions"][0]["exit_reason"] is None


def test_t1_once_keeps_one_lot_t2_closes(engine):
    portfolio, clock, events = engine
    order = open_position(engine)
    for price in (110, 110, 115):
        portfolio.tick({("NSE_FNO", 1): quote(clock, price)})
    position = portfolio.snapshot()["positions"][0]
    assert position["quantity"] == 50
    assert position["stop_loss"] == 100
    assert sum(e["type"] == "target_1" for e in events) == 1
    portfolio.tick({("NSE_FNO", 1): quote(clock, 120)})
    assert portfolio.snapshot()["closed_positions"][0]["exit_reason"] == "target_2"
    assert portfolio.snapshot()["counts"]["target_1"] == 1
    assert portfolio.snapshot()["counts"]["target_2"] == 1
    assert portfolio.action("close", order["id"])["reason"] == "position_not_found"


@pytest.mark.parametrize("prices,reason", [
    ([89], "initial_stop"), ([110, 99], "breakeven_stop"),
])
def test_distinct_stop_reasons(engine, prices, reason):
    portfolio, clock, _ = engine
    open_position(engine)
    for price in prices:
        portfolio.tick({("NSE_FNO", 1): quote(clock, price)})
    assert portfolio.snapshot()["closed_positions"][0]["exit_reason"] == reason
    assert portfolio.snapshot()["counts"][reason] == 1


def test_trailing_completed_same_timeframe_no_future_and_breached_low(engine):
    portfolio, clock, _ = engine
    open_position(engine)
    start = clock()
    clock.advance(300)
    key = ("NSE_FNO", 1, "5min")
    portfolio.tick({("NSE_FNO", 1): quote(clock, 105)}, {
        key: [{"timestamp": start, "low": 102}, {"timestamp": clock(), "low": 109}]})
    assert portfolio.snapshot()["positions"][0]["stop_loss"] == 102
    clock.advance(300)
    portfolio.tick({("NSE_FNO", 1): quote(clock, 103)}, {
        key: {"timestamp": start + 300, "low": 104}})
    assert portfolio.snapshot()["closed_positions"][0]["exit_reason"] == "trailing_stop"
    assert portfolio.snapshot()["closed_positions"][0]["stop_loss"] == 102


def test_existing_stop_precedes_completed_candle_breach(engine):
    portfolio, clock, _ = engine
    open_position(engine)
    start = clock()
    clock.advance(300)
    portfolio.tick({("NSE_FNO", 1): quote(clock, 89)}, {
        ("NSE_FNO", 1): {"timestamp": start, "low": 100}})
    closed = portfolio.snapshot()["closed_positions"][0]
    assert closed["exit_reason"] == "initial_stop" and closed["stop_loss"] == 90


def test_capital_valuation_unrealized_realized_and_reason_counts(engine):
    portfolio, clock, _ = engine
    request = open_position(engine)
    portfolio.tick({("NSE_FNO", 1): quote(clock, 105)})
    snapshot = portfolio.snapshot()
    assert snapshot["account"]["premium_committed"] == 5000
    assert snapshot["account"]["marked_position_value"] == 5250
    assert snapshot["account"]["unrealized"] == 250
    assert snapshot["account"]["unrealized_daily"] == 250
    assert snapshot["positions"][0]["unrealized_pnl"] == 250
    portfolio.action("close", request["id"])
    snapshot = portfolio.snapshot()
    assert snapshot["account"]["premium_committed"] == 0
    assert snapshot["account"]["marked_position_value"] == 0
    assert snapshot["account"]["realized_daily"] == pytest.approx(197.5)
    assert snapshot["daily"]["realized_daily"] == pytest.approx(197.5)
    assert snapshot["counts"]["manual_close"] == 1
    assert snapshot["counts"]["closed"] == 1


def test_explicit_capacity_one_lot_intent_and_summary_accounting(engine):
    portfolio, clock, events = engine
    request = portfolio.submit(signal(), quote(clock))
    assert request["lots"] == 1
    assert request["risk_points"] == 10 and request["risk_inr"] == 500
    account = portfolio.snapshot()["account"]
    assert account["max_open_positions"] == 5 and account["max_daily_entries"] == 20
    assert account["available_slots"] == 4 and account["remaining_daily_entries"] == 19
    portfolio.action("approve_limit", request["id"])
    pending = portfolio.submit(signal(2), quote(clock))
    portfolio.action("modify", pending["id"], limit_price=102, stop_loss=91)
    modified = portfolio.snapshot()["pending"][0]
    assert modified["lots"] == 1 and modified["risk_points"] == 11
    assert modified["risk_inr"] == 550
    snapshot = portfolio.snapshot()
    assert snapshot["account"]["available_slots"] == 3
    assert snapshot["account"]["remaining_daily_entries"] == 18
    assert snapshot["daily"]["remaining_daily_entries"] == 18
    clock.now = datetime(2026, 10, 5, 15, 25, tzinfo=IST).timestamp()
    portfolio.tick({("NSE_FNO", 1): quote(clock, 105)})
    interim = next(event for event in events if event["type"] == "day_summary_interim")
    assert interim["initial_capital"] == 500000
    assert interim["premium_committed"] == 0 and interim["marked_position_value"] == 0
    assert interim["realized_daily"] == pytest.approx(197.5)
    assert interim["unrealized_daily"] == 0 and interim["unrealized"] == 0


def test_candle_stop_persists_without_quote_until_fresh_exit(engine):
    portfolio, clock, _ = engine
    open_position(engine)
    start = clock()
    clock.advance(300)
    portfolio.tick({}, {("NSE_FNO", 1, "5min"): {"timestamp": start, "low": 103}})
    assert portfolio.snapshot()["positions"][0]["stop_loss"] == 90
    assert portfolio.snapshot()["positions"][0]["pending_trailing_low"] == 103
    portfolio.tick({("NSE_FNO", 1): quote(clock, 102)})
    assert portfolio.snapshot()["executions"][-1]["reason"] == "trailing_stop"


def test_market_data_candle_end_alias_rejects_future_candles(engine):
    portfolio, clock, _ = engine
    open_position(engine)
    start = clock()
    clock.advance(300)
    portfolio.tick({}, {("NSE_FNO", 1, "5min"): {
        "timestamp": start, "end": clock() + 1, "low": 105}})
    assert portfolio.snapshot()["positions"][0]["stop_loss"] == 90
    clock.advance(1)
    portfolio.tick({}, {("NSE_FNO", 1, "5min"): {
        "timestamp": start, "end": clock(), "low": 105}})
    assert portfolio.snapshot()["positions"][0]["pending_trailing_low"] == 105


def test_scanner_hyphenated_timeframe_and_instrument_metadata(engine):
    portfolio, clock, _ = engine
    open_position(engine, metadata={"timeframe": "10-min", "instrument_type": "OPTSTK"})
    contract = portfolio.contracts()[0]
    assert contract["timeframe"] == "10-min" and contract["instrument_type"] == "OPTSTK"
    start = clock()
    clock.advance(600)
    portfolio.tick({("NSE_FNO", 1): quote(clock, 104)}, {
        ("NSE_FNO", 1): {"timestamp": start, "end": clock(), "low": 105}})
    assert portfolio.snapshot()["closed_positions"][0]["exit_reason"] == "trailing_stop"
    with pytest.raises(ValueError):
        portfolio._timeframe_seconds("-10-min")


def test_previous_completed_candle_before_entry_can_tighten_stop(engine):
    portfolio, clock, _ = engine
    open_position(engine)
    portfolio.tick({("NSE_FNO", 1): quote(clock, 104)}, {
        ("NSE_FNO", 1): {"timestamp": clock() - 600, "end_timestamp": clock() - 300, "low": 102}})
    position = portfolio.snapshot()["positions"][0]
    assert position["stop_loss"] == 102 and position["stop_kind"] == "trailing_stop"


def test_candle_starting_before_entry_and_finishing_after_is_not_skipped(engine):
    portfolio, clock, _ = engine
    clock.advance(30)
    open_position(engine)
    start = clock() - 30
    clock.advance(270)
    portfolio.tick({("NSE_FNO", 1): quote(clock, 104)}, {
        ("NSE_FNO", 1): {"timestamp": start, "end_timestamp": clock(), "low": 102}})
    assert portfolio.snapshot()["positions"][0]["stop_loss"] == 102


def test_gap_to_t2_reports_t1_milestone_first(engine):
    portfolio, clock, events = engine
    open_position(engine)
    portfolio.tick({("NSE_FNO", 1): quote(clock, 125)})
    kinds = [event["type"] for event in events]
    assert kinds.count("target_1") == 1
    assert kinds.index("target_1") < kinds.index("exit_filled")


@pytest.mark.parametrize("limit,price,reason", [
    ({"profit_limit": 1000}, 120, "daily_profit_limit"),
    ({"loss_limit": 500}, 89, "daily_loss_limit"),
])
def test_risk_latch_cancels_entries_and_cannot_be_cleared(engine, limit, price, reason):
    portfolio, clock, _ = engine
    open_position(engine)
    pending = portfolio.submit(signal(2), quote(clock))
    portfolio.action("limits", **limit)
    portfolio.tick({("NSE_FNO", 1): quote(clock, price)})
    snapshot = portfolio.snapshot()
    assert snapshot["daily"]["risk_latched"]
    assert snapshot["daily"]["risk_reason"] == reason
    assert snapshot["closed_positions"][0]["exit_reason"] == reason
    assert snapshot["counts"]["risk"] == 1
    assert next(o for o in snapshot["orders"] if o["id"] == pending["id"])["status"] == "cancelled"
    assert not snapshot["positions"]
    portfolio.action("limits", profit_limit=999999, loss_limit=999999)
    assert portfolio.submit(signal(3))["reason"] == reason


def test_protective_exit_waits_for_real_quote_and_cutoff_summaries(engine):
    portfolio, clock, events = engine
    open_position(engine)
    clock.now = datetime(2026, 10, 5, 15, 25, tzinfo=IST).timestamp()
    portfolio.tick({})
    snapshot = portfolio.snapshot()
    assert snapshot["positions"][0]["exit_reason"] == "time_cutoff"
    assert snapshot["account"]["cash"] == 495000
    assert sum(e["type"] == "day_summary_interim" for e in events) == 1
    interim = next(e for e in events if e["type"] == "day_summary_interim")
    assert interim["valuation_status"] == "marked_unresolved"
    assert len(interim["unresolved_exits"]) == 1
    assert len(interim["pending_exit_orders"]) == 1
    assert interim["pending_exit_orders"][0]["side"] == "SELL"
    assert not any(e["type"] == "day_summary_final" for e in events)
    portfolio.tick({})
    assert sum(e["type"] == "day_summary_interim" for e in events) == 1
    portfolio.tick({("NSE_FNO", 1): quote(clock, 101)})
    assert not portfolio.snapshot()["positions"]
    clock.now = datetime(2026, 10, 5, 23, tzinfo=IST).timestamp()
    portfolio.tick({})
    assert sum(e["type"] == "day_summary_final" for e in events) == 1
    final = next(e for e in events if e["type"] == "day_summary_final")
    assert len(final["trade_log"]) == 2
    assert final["valuation_status"] == "settled"
    assert portfolio.snapshot()["closed_positions"][0]["exit_reason"] == "time_cutoff"


def test_equity_interim_before_commodity_final_even_when_exit_unresolved(engine):
    portfolio, clock, events = engine
    open_position(engine)
    commodity = {"category": "commodity_options", "exchange_segment": "MCX_COMM"}
    request = portfolio.submit(signal(2, metadata=commodity), quote(clock))
    portfolio.action("approve_limit", request["id"])
    clock.now = datetime(2026, 10, 5, 15, 25, tzinfo=IST).timestamp()
    portfolio.tick({("NSE_FNO", 1): quote(clock)})
    interim = [event for event in events if event["type"] == "day_summary_interim"]
    assert len(interim) == 1 and len(interim[0]["remaining_positions"]) == 1
    assert interim[0]["unresolved_exits"] == []
    assert not any(event["type"] == "day_summary_final" for event in events)
    clock.now = datetime(2026, 10, 5, 23, tzinfo=IST).timestamp()
    portfolio.tick({})
    final = [event for event in events if event["type"] == "day_summary_final"]
    assert len(final) == 1 and len(final[0]["unresolved_exits"]) == 1
    assert final[0]["valuation_status"] == "marked_unresolved"
    portfolio.tick({("MCX_COMM", 2): quote(clock)})
    portfolio.tick({})
    assert sum(event["type"] == "day_summary_interim" for event in events) == 1
    assert sum(event["type"] == "day_summary_final" for event in events) == 1


def test_final_waits_for_eligible_commodity_session_even_before_first_commodity_trade(engine):
    portfolio, clock, events = engine
    open_position(engine)
    clock.now = datetime(2026, 10, 5, 15, 25, tzinfo=IST).timestamp()
    portfolio.tick({("NSE_FNO", 1): quote(clock)})
    assert sum(event["type"] == "day_summary_interim" for event in events) == 1
    assert not any(event["type"] == "day_summary_final" for event in events)
    clock.now = datetime(2026, 10, 5, 17, tzinfo=IST).timestamp()
    commodity = {"category": "commodity_options", "exchange_segment": "MCX_COMM"}
    request = portfolio.submit(signal(2, metadata=commodity), quote(clock))
    assert portfolio.action("approve_limit", request["id"])["status"] == "filled"
    clock.now = datetime(2026, 10, 5, 23, tzinfo=IST).timestamp()
    portfolio.tick({("MCX_COMM", 2): quote(clock)})
    portfolio.tick({})
    finals = [event for event in events if event["type"] == "day_summary_final"]
    assert len(finals) == 1
    commodity_log = [entry for entry in finals[0]["trade_log"] if entry["security_id"] == 2]
    assert [entry["side"] for entry in commodity_log] == ["BUY", "SELL"]


def test_closed_commodity_calendar_permits_final_at_equity_cutoff(tmp_path):
    class EquityOnlyCalendar:
        @staticmethod
        def session_windows(exchange, day):
            return () if exchange == "MCX" else ((market_time(9, 15), market_time(15, 30)),)

        def is_exchange_open(self, exchange, now):
            return any(start <= now.time().replace(tzinfo=None) <= end
                       for start, end in self.session_windows(exchange, now.date()))
    clock = Clock()
    events = []
    portfolio = PaperPortfolio(
        tmp_path / "calendar.db", clock=clock, calendar=EquityOnlyCalendar(), notify=events.append)
    request = portfolio.submit(signal(), quote(clock))
    portfolio.action("approve_limit", request["id"])
    clock.now = datetime(2026, 10, 5, 15, 25, tzinfo=IST).timestamp()
    portfolio.tick({("NSE_FNO", 1): quote(clock)})
    assert sum(event["type"] == "day_summary_final" for event in events) == 1


def test_no_trade_day_final_also_waits_for_last_eligible_segment(engine):
    portfolio, clock, events = engine
    clock.now = datetime(2026, 10, 5, 15, 25, tzinfo=IST).timestamp()
    portfolio.tick({})
    assert sum(event["type"] == "day_summary_interim" for event in events) == 1
    assert not any(event["type"] == "day_summary_final" for event in events)
    clock.now = datetime(2026, 10, 5, 23, tzinfo=IST).timestamp()
    portfolio.tick({})
    final = next(event for event in events if event["type"] == "day_summary_final")
    assert final["trade_log"] == []


def test_calendar_commodity_cutoff_and_missed_old_cutoff(engine):
    portfolio, clock, _ = engine
    value = signal(metadata={"category": "commodity_options", "exchange_segment": "MCX_COMM"})
    request = portfolio.submit(value, quote(clock))
    assert portfolio.action("approve_limit", request["id"])["status"] == "filled"
    clock.now = datetime(2026, 10, 5, 15, 26, tzinfo=IST).timestamp()
    portfolio.tick({("MCX_COMM", 1): quote(clock)})
    assert portfolio.snapshot()["positions"][0]["exit_reason"] is None
    clock.now = datetime(2026, 10, 6, 10, tzinfo=IST).timestamp()
    portfolio.tick({("MCX_COMM", 1): quote(clock)})
    assert portfolio.snapshot()["closed_positions"][0]["exit_reason"] == "time_cutoff"
    closed = PaperPortfolio(portfolio.db_path + ".closed", clock=clock,
                            calendar=lambda exchange, now: False)
    assert closed.submit(signal(), quote(clock))["reason"] == "exchange_closed"


def test_early_mcx_session_close_cancels_entries_exits_and_summarizes(tmp_path):
    clock = Clock()
    clock.now = datetime(2026, 1, 1, 16, tzinfo=IST).timestamp()
    events = []
    portfolio = PaperPortfolio(tmp_path / "early.db", clock=clock, notify=events.append)
    commodity = {"category": "commodity_options", "exchange_segment": "MCX_COMM"}
    request = portfolio.submit(signal(metadata=commodity), quote(clock))
    portfolio.action("approve_limit", request["id"])
    clock.now = datetime(2026, 1, 1, 16, 54, 50, tzinfo=IST).timestamp()
    pending = portfolio.submit(signal(2, metadata=commodity), quote(clock))
    clock.advance(10)
    portfolio.tick({("MCX_COMM", 1): quote(clock)})
    snapshot = portfolio.snapshot()
    assert not snapshot["positions"]
    assert snapshot["closed_positions"][0]["exit_reason"] == "time_cutoff"
    assert snapshot["closed_positions"][0]["exit_price"] == 99
    assert next(o for o in snapshot["orders"] if o["id"] == pending["id"])["reason"] == "time_cutoff"
    assert any(event["type"] == "day_summary_final" for event in events)


def test_rollover_equity_does_not_add_historical_realized(engine):
    portfolio, clock, _ = engine
    open_position(engine)
    portfolio.tick({("NSE_FNO", 1): quote(clock, 105)})
    portfolio.action("close", portfolio.snapshot()["positions"][0]["id"])
    previous_equity = portfolio.snapshot()["account"]["equity"]
    clock.advance(86400)
    snapshot = portfolio.snapshot()
    assert snapshot["daily"]["opening"] == previous_equity
    assert snapshot["daily"]["pnl"] == 0
    assert snapshot["daily"]["realized"] == 0


def test_carried_position_daily_realized_uses_prior_close_mark(engine):
    portfolio, clock, _ = engine
    open_position(engine)
    portfolio.tick({("NSE_FNO", 1): quote(clock, 108)})
    prior = portfolio.snapshot()["account"]["equity"]
    clock.advance(86400)
    portfolio.tick({("NSE_FNO", 1): quote(clock, 109)})
    snapshot = portfolio.snapshot()
    assert snapshot["daily"]["opening"] == prior
    assert snapshot["daily"]["realized"] == pytest.approx((107.9 - 108) * 50)
    assert snapshot["daily"]["pnl"] == pytest.approx((107.9 - 108) * 50)


def test_fill_rechecks_cash_after_market_jump(engine):
    portfolio, clock, _ = engine
    request = portfolio.submit(signal(metadata={"lot_size": 4000}), quote(clock))
    clock.advance(1)
    portfolio.tick({("NSE_FNO", 1): quote(clock, 200)})
    result = portfolio.action("approve_market", request["id"])
    assert result["reason"] == "insufficient_cash_at_fill"
    assert not portfolio.snapshot()["positions"]
    assert portfolio.snapshot()["account"]["reserved_cash"] == 0


def test_repeated_approval_does_not_restart_fallback_timer(engine):
    portfolio, clock, _ = engine
    request = portfolio.submit(signal(), quote(clock, 101))
    portfolio.action("approve_limit", request["id"])
    clock.advance(4)
    portfolio.action("approve_limit", request["id"])
    clock.advance(1)
    portfolio.tick({("NSE_FNO", 1): quote(clock, 101)})
    assert portfolio.snapshot()["positions"][0]["entry_price"] == 102.05


def test_snapshot_in_notification_is_safe(tmp_path):
    clock = Clock()
    snapshots = []
    portfolio = PaperPortfolio(tmp_path / "callback.db", clock=clock)
    portfolio.notify = lambda event: snapshots.append(portfolio.snapshot())
    portfolio.submit(signal(), quote(clock))
    assert len(snapshots) == 1
    assert len(snapshots[0]["pending"]) == 1


def test_delivery_measurements_and_namespaced_update_ids(engine):
    portfolio, clock, events = engine
    portfolio.submit(signal(), quote(clock))
    event_id = events[0]["event_id"]
    portfolio.record_delivery(event_id, 12.5, True)
    portfolio.record_delivery(event_id, 0, False)
    snapshot = portfolio.snapshot()
    assert [row["duration_ms"] for row in snapshot["deliveries"]] == [12.5, 0]
    assert [row["success"] for row in snapshot["deliveries"]] == [1, 0]
    assert any(row["action"] == "notification_delivery" for row in snapshot["audit"])
    assert portfolio.record_update("telegram:update:123")
    assert portfolio.record_update("telegram:callback:123")
    assert not portfolio.record_update("telegram:update:123")
    with pytest.raises(ValueError):
        portfolio.record_delivery(event_id, float("nan"), True)


def test_daily_filled_plus_reserved_cap(engine):
    portfolio, clock, _ = engine
    portfolio.action("limits", profit_limit=1000000, loss_limit=1000000)
    for i in range(1, 21):
        order = open_position(engine, i)
        assert order["status"] == "filled"
        portfolio.action("close", order["id"])
    assert portfolio.submit(signal(21))["reason"] == "daily_entry_limit"
    assert portfolio.snapshot()["daily"]["filled"] == 20


def test_modify_mode_contracts_and_metrics(engine):
    portfolio, clock, _ = engine
    request = portfolio.submit(signal(), quote(clock))
    assert portfolio.contracts() == [
        {"security_id": 1, "exchange_segment": "NSE_FNO", "timeframe": "5min",
         "instrument_type": "OPTIDX", "option_symbol": "NIFTY-1-CE"}]
    result = portfolio.action("modify", request["id"], limit_price=101.02, stop_loss=92)
    assert result["limit_price"] == 101.05
    assert result["reserved_cash"] == 5105
    portfolio.action("mode", enabled=False)
    assert not portfolio.contracts()
    assert portfolio.submit(signal(2))["reason"] == "practice_disabled"
    portfolio.action("mode", enabled=True, approval_required=False)
    assert portfolio.submit(signal(3), quote(clock))["status"] == "filled"
    assert any(m["name"] == "submit" and m["count"] >= 3 for m in portfolio.snapshot()["metrics"])


def test_outbox_retries_after_restart(tmp_path):
    clock = Clock()
    path = tmp_path / "outbox.db"
    def failed(event):
        raise RuntimeError("offline")
    portfolio = PaperPortfolio(path, clock=clock, notify=failed)
    portfolio.submit(signal(), quote(clock))
    events = []
    reopened = PaperPortfolio(path, clock=clock, notify=events.append)
    assert events[0]["type"] == "order_awaiting_approval"
    reopened.snapshot()
    assert len(events) == 1


def test_manual_delivery_worker_never_blocks_portfolio_transactions(engine):
    portfolio, clock, events = engine
    portfolio.auto_deliver = False
    request = portfolio.submit(signal(), quote(clock))
    assert request["status"] == "awaiting_approval" and not events
    portfolio.snapshot()
    assert not events
    portfolio.deliver_notifications()
    assert len(events) == 1 and events[0]["id"] == request["id"]
    portfolio.deliver_notifications()
    assert len(events) == 1


def test_delivery_telemetry_cannot_flush_outbox_recursively(engine):
    portfolio, clock, events = engine
    portfolio.auto_deliver = False
    portfolio.submit(signal(), quote(clock))
    portfolio.auto_deliver = True
    portfolio.record_delivery("external-event", 1, True)
    assert events == []
    portfolio.deliver_notifications()
    assert len(events) == 1


def test_real_control_schema_actions_delivery_metrics_and_false_retry(engine, monkeypatch):
    from paper_telegram import PaperTelegramControl
    portfolio, clock, _ = engine
    control = PaperTelegramControl(
        portfolio, object(), allowed_user_ids=[7], chat_id=-10, webhook_secret="s" * 32)
    monkeypatch.setattr(control, "_api", lambda method, payload: False)
    portfolio.notify = control.send_event
    request = portfolio.submit(signal(), quote(clock))
    assert portfolio.snapshot()["deliveries"][-1]["success"] == 0
    sent = []
    monkeypatch.setattr(control, "_api", lambda method, payload: sent.append(payload) or True)
    portfolio.tick({})
    assert sent[-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == (
        f"paper:{request['id']}:approve_limit")
    update = {"update_id": 1, "message": {
        "from": {"id": 7}, "chat": {"id": -10},
        "text": f"/approve {request['id']} limit"}}
    response, status = control.handle_update(update, "s" * 32)
    assert status == 200 and response["result"]["status"] == "filled"
    update["update_id"] = 2
    update["message"]["text"] = "/mode approval off"
    response, status = control.handle_update(update, "s" * 32)
    assert status == 200
    account = portfolio.snapshot()["account"]
    assert account["enabled"] and not account["approval_required"]
    assert portfolio.snapshot()["deliveries"][-1]["success"] == 1


def test_real_control_denies_unconfigured_chat_and_none_payload(engine, monkeypatch):
    from types import SimpleNamespace
    from paper_telegram import PaperTelegramControl
    portfolio, _, _ = engine
    monkeypatch.delenv("CHANNEL_TRADE_CONTROL_ID", raising=False)
    handler = SimpleNamespace(_get_bot_for_category=lambda category: ("token", -10))
    control = PaperTelegramControl(
        portfolio, handler, allowed_user_ids=[7], webhook_secret="s" * 32)
    assert control.handle_update(None, "s" * 32)[1] == 400
    update = {"update_id": 1, "message": {
        "from": {"id": 7}, "chat": {"id": -10}, "text": "/portfolio"}}
    assert control.handle_update(update, "s" * 32)[1] == 403
    assert control.chat_id is None


def test_real_runtime_worker_uses_engine_contract_quote_candle_schema(engine):
    from types import SimpleNamespace
    from runtime_services import PaperPortfolioWorker
    portfolio, clock, _ = engine
    request = portfolio.submit(signal(), quote(clock, 101))
    portfolio.action("approve_limit", request["id"])
    requests = []
    data = SimpleNamespace(
        fetch_quotes=lambda requested: requests.append(requested) or {
            ("NSE_FNO", 1): quote(clock, 99)},
        fetch_paper_candles=lambda contract: [])
    worker = PaperPortfolioWorker(portfolio, data)
    worker.run_once()
    assert requests == [{"NSE_FNO": [1]}]
    assert portfolio.snapshot()["positions"][0]["entry_price"] == 99
    clock.advance(300)
    data.fetch_quotes = lambda requested: {("NSE_FNO", 1): quote(clock, 98)}
    data.fetch_paper_candles = lambda contract: [{
        "timestamp": clock() - 300, "end": clock(), "low": 98.5}]
    worker.run_once()
    assert portfolio.snapshot()["closed_positions"][0]["exit_reason"] == "trailing_stop"


def test_reports_default_today_with_explicit_historical_day_and_timings(engine):
    portfolio, clock, _ = engine
    request = open_position(engine, metadata={"instrument_type": "OPTSTK"})
    assert portfolio.contracts()[0]["instrument_type"] == "OPTSTK"
    clock.advance(11)
    portfolio.action("close", request["id"])
    clock.advance(4)
    portfolio.tick({("NSE_FNO", 1): quote(clock)})
    snapshot = portfolio.snapshot()
    position = snapshot["closed_positions"][0]
    assert position["protection_latency_seconds"] == 4
    assert position["submit_duration_seconds"] >= 0
    assert position["approval_duration_seconds"] >= 0
    assert position["approval_to_order_seconds"] >= 0
    assert position["order_to_fill_seconds"] == 0
    assert position["fill_duration_seconds"] >= 0
    assert position["exit_fill_duration_seconds"] >= 0
    previous_day = snapshot["daily"]["day"]
    clock.advance(86400)
    today = portfolio.snapshot()
    assert today["orders"] == [] and today["closed_positions"] == [] and today["executions"] == []
    history = portfolio.snapshot(day=previous_day)
    assert len(history["orders"]) == 1 and len(history["executions"]) == 2
    assert len(history["closed_positions"]) == 1
    empty_history = portfolio.snapshot(day="2026-10-04")
    assert empty_history["executions"] == [] and empty_history["orders"] == []
    assert not empty_history["daily"]["recorded"]
    with pytest.raises(ValueError):
        portfolio.snapshot(day="bad")


@pytest.mark.parametrize("action,kwargs", [
    ("approve_limit", {}), ("approve_market", {}), ("reject", {}),
    ("modify", {"limit_price": 102, "stop_loss": 91}),
    ("mode", {"enabled": False}), ("limits", {"profit_limit": 20000, "loss_limit": 9000}),
    ("close", {}),
])
def test_action_audits_atomically_include_old_new_and_actual_result(engine, action, kwargs):
    portfolio, clock, _ = engine
    request = portfolio.submit(signal(), quote(clock))
    if action == "close":
        portfolio.action("approve_limit", request["id"])
    result = portfolio.action(action, request_id=request["id"], actor=42, **kwargs)
    record = [row for row in portfolio.snapshot()["audit"]
              if row["action"] == action and row["actor"] == "42"][-1]
    data = json.loads(record["data"])
    assert record["timestamp"] == clock()
    assert data["requested"] == kwargs and data["result"] == result
    assert data["outcome"] == result["status"] and data["reason"]
    assert data["old"] != data["new"]
    if action == "modify":
        assert data["old"]["order"]["limit_price"] == 100
        assert data["new"]["order"]["limit_price"] == 102
    if action == "limits":
        assert data["old"]["account"]["profit_limit"] == 30000
        assert data["new"]["account"]["profit_limit"] == 20000
    immutable = record["data"]
    portfolio.action("limits", profit_limit=25000)
    assert next(row for row in portfolio.snapshot()["audit"]
                if row["id"] == record["id"])["data"] == immutable


def test_rejected_modification_audit_contains_unchanged_state_and_failure(engine):
    portfolio, clock, _ = engine
    request = portfolio.submit(signal(), quote(clock))
    result = portfolio.action("modify", request["id"], actor=9, stop_loss=110)
    record = next(row for row in portfolio.snapshot()["audit"]
                  if row["action"] == "modify" and row["actor"] == "9")
    data = json.loads(record["data"])
    assert data["old"] == data["new"]
    assert data["result"] == result and data["outcome"] == "rejected"
    assert data["reason"] == "stop_loss must be below entry"


def _concurrent_submit(path, now, identifier, queue):
    portfolio = PaperPortfolio(path, clock=lambda: now)
    queue.put(portfolio.submit(signal(identifier), {"price": 100, "timestamp": now})["status"])


def _concurrent_close(path, now, position_id, queue):
    portfolio = PaperPortfolio(path, clock=lambda: now)
    queue.put(portfolio.action("close", position_id)["status"])


def test_cross_process_slot_reservation_is_atomic(tmp_path):
    path = str(tmp_path / "atomic.db")
    clock = Clock()
    PaperPortfolio(path, clock=clock)
    context = multiprocessing.get_context("fork")
    queue = context.Queue()
    workers = [context.Process(target=_concurrent_submit, args=(path, clock(), i, queue))
               for i in range(1, 9)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=15)
        assert worker.exitcode == 0
    statuses = [queue.get(timeout=2) for _ in workers]
    assert statuses.count("awaiting_approval") == 5
    snapshot = PaperPortfolio(path, clock=clock).snapshot()
    assert len(snapshot["pending"]) == 5
    assert snapshot["account"]["cash"] == 500000


def test_cross_process_signal_idempotence(tmp_path):
    path = str(tmp_path / "idempotence.db")
    clock = Clock()
    PaperPortfolio(path, clock=clock)
    context = multiprocessing.get_context("fork")
    queue = context.Queue()
    workers = [context.Process(target=_concurrent_submit, args=(path, clock(), 1, queue))
               for _ in range(4)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=15)
        assert worker.exitcode == 0
    assert all(queue.get(timeout=2) == "awaiting_approval" for _ in workers)
    assert len(PaperPortfolio(path, clock=clock).snapshot()["pending"]) == 1


def test_cross_process_sell_fill_cash_update_is_atomic_and_idempotent(engine):
    portfolio, clock, _ = engine
    entry = open_position(engine)
    context = multiprocessing.get_context("fork")
    queue = context.Queue()
    workers = [context.Process(
        target=_concurrent_close, args=(portfolio.db_path, clock(), entry["id"], queue))
        for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=15)
        assert worker.exitcode == 0
    assert sorted(queue.get(timeout=2) for _ in workers) == ["closed", "rejected"]
    snapshot = portfolio.snapshot()
    assert len(snapshot["exit_orders"]) == 1 and snapshot["exit_orders"][0]["status"] == "filled"
    assert sum(e["side"] == "SELL" for e in snapshot["executions"]) == 1
    assert snapshot["account"]["cash"] == 499950
