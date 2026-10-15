"""NIFTY auto Super Order flow with a mocked simulation adapter (no broker I/O)."""

import copy

import pytest

from live_broker import LiveBlocked, LiveBroker
from live_execution import LiveExecution
from live_runtime import LiveRoute, LiveWorker, format_live_event
from tests.test_live_broker import FakeAdapter
from tests.test_live_execution import signal

CUTOFF = 1791351000.0 + 4 * 3600 + 25 * 60  # 2026-10-07 15:25 IST


class SuperAdapter(FakeAdapter):
    super_order_simulation = True
    product_type = "INTRADAY"

    def __init__(self):
        super().__init__()
        self.modifies = []
        self.fail_modify = False

    def modify_target(self, order_id, price):
        self.modifies.append((order_id, price))
        if self.fail_modify:
            raise TimeoutError("ambiguous modify")
        return {"order_id": order_id}

    def report_super(self, item, *, filled, status="FILLED", wire="TRADED", exit_qty=0, exit_price=None,
                     legs=None, actual=None, stop=None, target=None, trailing=0):
        self.sequence += 1
        self.orders[item["correlation_id"]] = {
            "order_id": "SO1", "security_id": item["security_id"], "side": "BUY",
            "quantity": item["quantity"], "filled_quantity": filled, "status": status,
            "wire_status": wire, "average_price": 100, "fill_timestamp": self.now,
            "order_type": "LIMIT", "limit_price": item["limit_price"],
            "protection": "DHAN_SUPER_ORDER", "stop_order_type": "MARKET", "target_order_type": "LIMIT",
            "legs": {
                "STOP_LOSS_LEG": {"status": (legs or {}).get("STOP_LOSS_LEG", "PENDING"),
                                  "price": stop if stop is not None else item["stop_loss"],
                                  "trailing_jump": trailing},
                "TARGET_LEG": {"status": (legs or {}).get("TARGET_LEG", "PENDING"),
                               "price": target if target is not None else item["target_price"],
                               "trailing_jump": 0},
            },
            "exit_fills": {"quantity": exit_qty, "average_price": exit_price,
                           "fill_timestamp": self.now if exit_qty else None,
                           "legs_triggered": sorted(name for name, state in (legs or {}).items()
                                                    if state == "TRADED")},
        }
        if actual is not None:
            self.positions[str(item["security_id"])] = actual


@pytest.fixture
def adapter():
    return SuperAdapter()


@pytest.fixture
def engine(tmp_path, adapter):
    broker = LiveBroker(adapter, allow_simulation=True, clock=lambda: adapter.now)
    return LiveExecution(tmp_path / "auto.sqlite", broker, enabled=True, practice=False, auto=True,
                         auto_mode=True, clock=lambda: adapter.now)


def outbox(engine):
    events = []
    engine.deliver_notifications(lambda _id, event: events.append(event) or True)
    return events


def test_auto_signal_submits_one_super_order_with_fixed_2r_target(engine, adapter):
    item = engine.propose(signal(adapter))
    assert item["status"] == "PENDING" and len(adapter.writes) == 1
    order = adapter.writes[0]
    assert order["protection"] == "DHAN_SUPER_ORDER" and order["order_type"] == "LIMIT"
    assert order["stop_order_type"] == "MARKET" and order["target_order_type"] == "LIMIT"
    assert order["quantity"] == 65 and order["limit_price"] == 99
    assert order["stop_loss"] == 85.5 and order["target_price"] == 126.0
    assert order["trailing_jump"] == 0 and order["product_type"] == "INTRADAY"
    assert outbox(engine) == []  # acknowledgement is never an execution message
    again = engine.propose(signal(adapter))
    assert again["id"] == item["id"] and len(adapter.writes) == 1


def test_lifecycle_tick_rounded_policy_is_accepted(engine, adapter):
    payload = signal(adapter, key="bad", breakout=100.02)
    payload["metadata"].update(_lifecycle_managed=True, entry_price=100.05, stop_loss=85.5, target=140)
    with pytest.raises(LiveBlocked, match="2R policy"):
        engine.propose(payload)
    payload = signal(adapter, key="unflagged", breakout=100.02)
    payload["metadata"].update(entry_price=100.05, stop_loss=85.5)
    with pytest.raises(LiveBlocked, match="reference-derived stop"):
        engine.propose(payload)
    payload = signal(adapter, breakout=100.02)
    payload["metadata"].update(_lifecycle_managed=True, entry_price=100.05, stop_loss=85.5, target=129.15)
    item = engine.propose(payload)
    assert item["limit_price"] == 100.05 and item["target_price"] == 129.15
    assert len(adapter.writes) == 1


def test_market_is_only_accepted_for_the_super_order_stop_leg(engine, adapter):
    for field in ("order_type", "entry_order_type", "target_order_type"):
        payload = signal(adapter, key=field)
        payload["metadata"][field] = "MARKET"
        with pytest.raises(LiveBlocked, match="LIMIT only"):
            engine.propose(payload)
    with pytest.raises(LiveBlocked, match="LIMIT only"):
        engine.broker.limit_only({"side": "SELL", "stop_order_type": "MARKET", "protection": "DHAN_SUPER_ORDER"})
    assert not adapter.writes


def test_fill_gated_entry_and_target_exit_messages_with_gross_pnl(engine, adapter):
    item = engine.propose(signal(adapter))
    adapter.report_super(item, filled=65, actual=65)
    engine.reconcile()
    events = outbox(engine)
    assert [e["kind"] for e in events] == ["entry_executed"]
    text = format_live_event(events[0])
    assert "ENTRY EXECUTED" in text and "65 of 65" in text and "₹100.00" in text
    adapter.report_super(item, filled=65, wire="CLOSED", exit_qty=40, exit_price=126,
                         legs={"TARGET_LEG": "PART_TRADED"}, actual=25)
    engine.reconcile()
    assert outbox(engine) == []  # partial exit: no EXIT EXECUTED claim yet
    adapter.report_super(item, filled=65, wire="CLOSED", exit_qty=65, exit_price=126,
                         legs={"TARGET_LEG": "TRADED", "STOP_LOSS_LEG": "CANCELLED"}, actual=0)
    engine.reconcile()
    events = outbox(engine)
    assert [e["kind"] for e in events] == ["exit_executed"]
    exit_text = format_live_event(events[0])
    assert "TARGET LEG FILLED" in exit_text and "Gross P&L: ₹1,690.00" in exit_text
    assert "before brokerage" in exit_text
    status = engine.status()
    assert status["limits"]["managed_open_positions"] == 0
    assert status["halted"] is False


def test_stop_leg_exit_reason_and_day_cap(engine, adapter):
    first = engine.propose(signal(adapter, "one"))
    adapter.report_super(first, filled=65, actual=65)
    engine.reconcile()
    with pytest.raises(LiveBlocked, match="one open"):
        engine.propose(signal(adapter, "blocked", breakout=130))
    adapter.report_super(first, filled=65, wire="CLOSED", exit_qty=65, exit_price=85,
                         legs={"STOP_LOSS_LEG": "TRADED", "TARGET_LEG": "CANCELLED"}, actual=0)
    engine.reconcile()
    exit_event = [e for e in outbox(engine) if e["kind"] == "exit_executed"][0]
    assert exit_event["proposal"]["reason"] == "stop_loss"
    assert exit_event["proposal"]["realized_pnl"] == (85 - 100) * 65
    second = engine.propose(signal(adapter, "two", breakout=130))
    adapter.report_super(second, filled=65, wire="CLOSED", exit_qty=65, exit_price=140,
                         legs={"TARGET_LEG": "TRADED"}, actual=0)
    engine.reconcile()
    with pytest.raises(LiveBlocked, match="two-entry"):
        engine.propose(signal(adapter, "three", breakout=200))
    assert len(adapter.writes) == 2


def test_insufficient_funds_skips_submission_with_deduplicated_operational_alert(engine, adapter):
    adapter.available = 10
    for key in ("a", "b", "c"):
        with pytest.raises(LiveBlocked, match="insufficient funds"):
            engine.propose(signal(adapter, key))
    events = outbox(engine)
    assert [e["kind"] for e in events] == ["operational_alert"]
    text = format_live_event(events[0])
    assert "NOT an order execution" in text and "No order was sent" in text
    assert not adapter.writes
    assert engine.status()["counters"]["funds_insufficient"] == 1


def test_margin_shortfall_evidence_blocks_entry(engine, adapter):
    adapter.margin = lambda order: adapter.evidence(required=6500, insufficient=100)
    with pytest.raises(LiveBlocked, match="insufficient funds"):
        engine.propose(signal(adapter))
    assert not adapter.writes


def test_ambiguous_write_is_never_retried_and_alerts_once(engine, adapter, tmp_path):
    adapter.fail_write = True
    item = engine.propose(signal(adapter))
    assert item["status"] == "UNKNOWN" and len(adapter.writes) == 1
    restarted = LiveExecution(engine.db_path, engine.broker, enabled=True, practice=False, auto=True,
                              auto_mode=True, clock=lambda: adapter.now)
    with pytest.raises(LiveBlocked):
        restarted.propose(signal(adapter, "next", breakout=130))
    assert len(adapter.writes) == 1
    assert [e["code"] for e in outbox(restarted)] == ["write_outcome_unknown"]
    adapter.report_super(item, filled=65, actual=65)  # appears later via reconciliation only
    restarted.reconcile()
    assert restarted.status()["account"]["write_busy"] is None
    assert [e["kind"] for e in outbox(restarted)] == ["entry_executed"]


def test_definite_rejection_resolves_only_after_later_snapshot(engine, adapter):
    class Rejected(LiveBlocked):
        definite_rejection = True
        category = "order_rejected"

    def place(order):
        adapter.writes.append(order)
        raise Rejected("dhan order_rejected error DH-906")
    adapter.place = place
    item = engine.propose(signal(adapter))
    assert item["status"] == "UNKNOWN"
    adapter.sequence += 1
    engine.reconcile()
    assert engine.status()["intents"][0]["status"] == "UNKNOWN"  # too early
    adapter.now += 61
    adapter.sequence += 1
    engine.reconcile()
    status = engine.status()
    assert status["intents"][0]["status"] == "REJECTED"
    assert status["account"]["write_busy"] is None
    assert len(adapter.writes) == 1


def test_partial_entry_is_not_cancelled_and_blocks_entries(engine, adapter):
    item = engine.propose(signal(adapter))
    adapter.report_super(item, filled=20, status="PENDING", wire="PART_TRADED", actual=20)
    adapter.now += 120
    engine.manage()
    assert adapter.cancels == []
    codes = [e.get("code") for e in outbox(engine)]
    assert "partial_entry" in codes
    with pytest.raises(LiveBlocked):
        engine.propose(signal(adapter, "next", breakout=130))


def test_unfilled_entry_times_out_with_entry_leg_cancel(engine, adapter):
    item = engine.propose(signal(adapter))
    adapter.report_super(item, filled=0, status="PENDING", wire="PENDING")
    adapter.now += 61
    engine.manage()
    assert adapter.cancels == ["1"] or adapter.cancels == ["SO1"]
    engine.manage()
    assert len(adapter.cancels) == 1


def test_manual_leg_edit_and_trailing_halt_entries(engine, adapter):
    item = engine.propose(signal(adapter))
    adapter.report_super(item, filled=65, actual=65, stop=90)
    engine.reconcile()
    status = engine.status()
    assert status["halted"] and "changed outside" in status["halt_reason"]
    assert "manual_leg_edit" in [e.get("code") for e in outbox(engine)]


def test_manual_exit_with_active_legs_halts_without_exit_claim(engine, adapter):
    item = engine.propose(signal(adapter))
    adapter.report_super(item, filled=65, actual=65)
    engine.reconcile()
    outbox(engine)
    adapter.report_super(item, filled=65, actual=0)  # flat, legs still PENDING, no leg fills
    engine.reconcile()
    events = outbox(engine)
    kinds = [e["kind"] for e in events]
    assert "exit_executed" not in kinds
    codes = {e.get("code") for e in events}
    assert {"external_reduction", "orphan_exit_legs"} <= codes
    assert engine.status()["halted"] is True


def test_time_exit_moves_target_leg_to_limit_bid_and_never_market(engine, adapter):
    item = engine.propose(signal(adapter))
    adapter.report_super(item, filled=65, actual=65)
    engine.reconcile()
    outbox(engine)
    adapter.now = CUTOFF + 1
    adapter.price = 110.03
    engine.manage()
    assert adapter.modifies == [("SO1", 110.0)]
    adapter.report_super(item, filled=65, actual=65, target=110.0)
    engine.reconcile()
    assert engine.status()["halted"] is False  # our own requested target price is not a manual edit
    adapter.report_super(item, filled=65, wire="CLOSED", exit_qty=65, exit_price=110,
                         legs={"TARGET_LEG": "TRADED", "STOP_LOSS_LEG": "CANCELLED"}, target=110.0, actual=0)
    engine.reconcile()
    exit_event = [e for e in outbox(engine) if e["kind"] == "exit_executed"][0]
    assert exit_event["proposal"]["reason"] == "time_exit_1525"
    assert all(order.get("order_type") == "LIMIT" for order in adapter.writes)


def test_unfilled_time_exit_stays_open_with_manual_action_alert(engine, adapter):
    engine.time_exit_attempts = 2
    item = engine.propose(signal(adapter))
    adapter.report_super(item, filled=65, actual=65)
    engine.reconcile()
    outbox(engine)
    adapter.now = CUTOFF
    for _ in range(4):
        engine.manage()
        adapter.now += 31
    assert len(adapter.modifies) == 2
    status = engine.status()
    assert status["limits"]["managed_open_positions"] == 1  # never falsely closed
    alerts = [e for e in outbox(engine) if e.get("code") == "time_exit_unfilled"]
    assert len(alerts) == 1 and "MANUAL ACTION REQUIRED" in alerts[0]["message"]


def test_no_entries_after_cutoff(engine, adapter):
    adapter.now = CUTOFF
    payload = signal(adapter)
    with pytest.raises(LiveBlocked, match="15:25"):
        engine.propose(payload)


@pytest.mark.parametrize("category", ["nifty50_stock_options", "commodity_options"])
def test_non_index_categories_are_excluded_from_live_route(engine, adapter, category):
    payload = signal(adapter)
    payload.update(category=category, _scanner_origin=True)
    payload["metadata"].update(underlying="NIFTY")
    assert LiveRoute(engine, None).propose(payload)["status"] == "rejected"
    assert not adapter.writes


def test_crash_after_fill_does_not_duplicate_notifications(engine, adapter):
    item = engine.propose(signal(adapter))
    adapter.report_super(item, filled=65, actual=65)
    engine.reconcile()
    restarted = LiveExecution(engine.db_path, engine.broker, enabled=True, practice=False, auto=True,
                              auto_mode=True, clock=lambda: adapter.now)
    restarted.reconcile()
    assert [e["kind"] for e in outbox(restarted)] == ["entry_executed"]
    assert outbox(restarted) == []


def test_live_worker_delivers_only_formatted_trade_control_messages(engine, adapter):
    sent = []
    route = LiveRoute(engine, None)
    worker = LiveWorker(route, lambda text: sent.append(text) or True)
    item = engine.propose(signal(adapter))
    worker.run_once()
    assert sent == []
    adapter.report_super(item, filled=65, actual=65)
    worker.run_once()
    assert len(sent) == 1 and sent[0].startswith("✅ NIFTY LIVE ENTRY EXECUTED")
    failing = LiveWorker(route, lambda text: False)
    adapter.report_super(item, filled=65, wire="CLOSED", exit_qty=65, exit_price=126,
                         legs={"TARGET_LEG": "TRADED"}, actual=0)
    failing.run_once()
    worker.run_once()  # undelivered message retried at-least-once
    assert len(sent) == 2 and "EXIT EXECUTED" in sent[1]
    assert copy.deepcopy(worker.status())["running"] is False
