import copy
import json
import sqlite3
from datetime import datetime
from types import SimpleNamespace

import pytest

from dhan_super_order import DhanSuperOrderAdapter, IST, tick_price
from live_broker import LiveBlocked, LiveBroker
from live_execution import AutoSuperExecution
from live_runtime import LiveRoute, build_live_route
from tests.test_live_broker import FakeAdapter
from tests.test_live_execution import signal


class SuperAdapter(FakeAdapter, DhanSuperOrderAdapter):
    simulation = False
    def __init__(self):
        FakeAdapter.__init__(self)
        self.blockers = []
        self.super_book, self.trade_book = {}, {}
        self.modifications = []
        self.fund_reads, self.margin_reads = 0, 0
        self.fail_modify = False
        self.contract["underlying_kind"] = "INDEX"

    def funds(self):
        self.fund_reads += 1
        return FakeAdapter.funds(self)

    def margin(self, order):
        self.margin_reads += 1
        return FakeAdapter.margin(self, order)

    def snapshot(self):
        return self.evidence(
            authoritative=True, sequence=self.sequence, orders=copy.deepcopy(self.orders),
            super_orders=copy.deepcopy(self.super_book), trades=copy.deepcopy(self.trade_book),
            positions=copy.deepcopy(self.positions),
            position_details={s: {"quantity": q, "product_type": "CNC",
                                  "exchange_segment": "NSE_FNO"} for s, q in self.positions.items() if q})

    def place(self, order):
        if self.before_place:
            self.before_place(order)
        self.writes.append(copy.deepcopy(order))
        if self.fail_write:
            raise TimeoutError()
        return {"order_id": str(len(self.writes))}

    def modify_protection(self, order_id, leg_name, price):
        self.modifications.append((order_id, leg_name, price))
        if self.fail_modify:
            raise TimeoutError()
        return {"order_id": order_id}

    def report_entry(self, item, quantity=65, price=102, status="FILLED"):
        self.sequence += 1
        write = next(w for w in reversed(self.writes) if w["id"] == item["id"])
        correlation = write["correlation_id"]
        order_id = str(self.writes.index(write) + 1)
        record = {
            "order_id": order_id, "security_id": item["security_id"], "side": "BUY",
            "quantity": 65, "filled_quantity": quantity, "status": status,
            "product_type": "CNC", "exchange_segment": "NSE_FNO",
        }
        self.orders[order_id] = {**record, "correlation_id": correlation}
        previous = self.super_book.get(correlation, {}).get("filled_quantity", 0)
        if quantity > previous:
            self.trade_book[f"E{order_id}:{quantity}"] = {
                "id": f"E{order_id}:{quantity}", "order_id": order_id, "security_id": item["security_id"],
                "side": "BUY", "quantity": quantity - previous, "price": price,
                "product_type": "CNC", "exchange_segment": "NSE_FNO", "timestamp": self.now}
        legs = {name: {"order_id": order_id + suffix, "status": "PENDING", "triggered_quantity": 0,
                       "price": leg_price}
                for name, suffix, leg_price in (
                    ("TARGET_LEG", "-T", write["target_price"]),
                    ("STOP_LOSS_LEG", "-S", write["initial_stop_loss"]))}
        self.super_book[correlation] = {**record, "legs": legs}
        if quantity:
            self.positions[item["security_id"]] = quantity

    def confirm_modify(self):
        order_id, name, price = self.modifications[-1]
        for record in self.super_book.values():
            if record["order_id"] == order_id:
                record["legs"][name]["price"] = price
        self.sequence += 1

    def exit(self, item, price=110):
        self.sequence += 1
        record = next(s for s in self.super_book.values() if s["order_id"] == item["order_id"])
        child_id = record["order_id"] + "-T"
        quantity = record["filled_quantity"]
        self.orders[child_id] = {
            "order_id": child_id, "correlation_id": None, "side": "SELL",
            "security_id": item["security_id"], "quantity": quantity,
            "filled_quantity": quantity, "status": "FILLED",
            "product_type": "CNC", "exchange_segment": "NSE_FNO"}
        self.trade_book["X" + child_id] = {
            "id": "X" + child_id, "order_id": child_id, "side": "SELL",
            "security_id": item["security_id"], "quantity": quantity, "price": price,
            "timestamp": self.now, "product_type": "CNC", "exchange_segment": "NSE_FNO"}
        record["status"] = "CLOSED"
        record["legs"]["TARGET_LEG"].update(status="FILLED", triggered_quantity=quantity)
        record["legs"]["STOP_LOSS_LEG"]["status"] = "CANCELLED"
        self.positions = {}


@pytest.fixture
def adapter():
    return SuperAdapter()


@pytest.fixture
def engine(tmp_path, adapter):
    broker = LiveBroker(adapter, allow_production=True, clock=lambda: adapter.now)
    return AutoSuperExecution(tmp_path / "super.sqlite", broker, enabled=True,
                              practice=False, auto=True, clock=lambda: adapter.now)


def scanner(adapter, key="first"):
    item = signal(adapter, key)
    item["_scanner_origin"] = True
    item["metadata"].pop("indicator_confirmations")
    if key != "first":
        offset = (sum(ord(char) for char in key) % 19 + 1) * 600
        item["metadata"]["reference_timestamp"] -= offset
    return item


def submitted(engine, adapter, key="first"):
    return engine.propose(scanner(adapter, key))


def filled(engine, adapter, *, price=102, quantity=65, status="FILLED"):
    item = submitted(engine, adapter)
    adapter.report_entry(item, quantity, price, status)
    engine.reconcile()
    return engine.status()["intents"][-1]


@pytest.mark.parametrize("flags", [
    {"enabled": False}, {"practice": True}, {"auto": False},
])
def test_default_off_guards(engine, adapter, flags):
    for key, value in flags.items():
        setattr(engine, key, value)
    with pytest.raises(LiveBlocked):
        submitted(engine, adapter)
    assert not adapter.writes


@pytest.mark.parametrize("side,strike", [("CE", 24950), ("PE", 25050)])
def test_auto_one_lot_cnc_super_limit_without_indicators(engine, adapter, side, strike):
    adapter.contract.update(option_type=side, strike=strike)
    def before_place(order):
        with sqlite3.connect(engine.db_path) as db:
            item = json.loads(db.execute("SELECT data FROM live_intents").fetchone()[0])
            assert item["status"] == "UNKNOWN"
            assert item["attempts"][0]["status"] == "UNKNOWN"
    adapter.before_place = before_place
    item = submitted(engine, adapter)
    order = adapter.writes[0]
    assert (order["side"], order["quantity"], order["product_type"], order["order_type"]) == (
        "BUY", 65, "CNC", "LIMIT")
    assert order["initial_stop_loss"] == 85.5
    assert order["target_price"] == 126
    assert item["status"] == "PENDING" and item["filled_quantity"] == 0
    assert item["capital_context"]["available_before"] == 100000
    assert engine.deliver_notifications(lambda *_: pytest.fail("no acknowledgment alerts")) == 0
    with pytest.raises(LiveBlocked, match="no approval"):
        engine.approve(item["id"], 1, 7, -9)


@pytest.mark.parametrize("change", [
    {"lots": 2}, {"quantity": 130}, {"underlying_kind": "STOCK"}, {"side": "SELL"},
    {"underlying": "BANKNIFTY"}, {"exchange_segment": "BSE_FNO"}, {"instrument_type": "FUTIDX"},
    {"breakout_timestamp": 1}, {"detected_at": 1}, {"breakout_price": 99},
])
def test_whitelist_freshness_and_one_lot(engine, adapter, change):
    item = scanner(adapter)
    item["metadata"].update(change)
    with pytest.raises(LiveBlocked):
        engine.propose(item)
    assert not adapter.writes


def test_stop_is_tick_rounded_from_reference_low_not_supplied_stop(engine, adapter):
    item = scanner(adapter)
    item["metadata"].update(reference_low=90.03, stop_loss=2)
    order = engine.propose(item)
    assert order["initial_stop_loss"] == 85.5


@pytest.mark.parametrize("balance", [0, -1, True, float("nan"), 1])
def test_fresh_positive_available_funds_required(engine, adapter, balance):
    adapter.available = balance
    with pytest.raises(LiveBlocked):
        submitted(engine, adapter)
    assert not adapter.writes
    assert adapter.fund_reads > 0


def test_unknown_placement_restart_missing_order_never_resends(engine, adapter):
    adapter.fail_write = True
    item = submitted(engine, adapter)
    restarted = AutoSuperExecution(engine.db_path, engine.broker, enabled=True,
                                    practice=False, auto=True, clock=lambda: adapter.now)
    adapter.sequence += 1
    restarted.tick()
    assert restarted.status()["account"]["write_busy"] == item["id"]
    assert len(adapter.writes) == 1
    assert restarted.propose(scanner(adapter))["id"] == item["id"]
    with pytest.raises(LiveBlocked):
        restarted.propose(scanner(adapter, "new"))
    assert len(adapter.writes) == 1


def test_only_reconciled_zero_fill_rejection_gets_one_market_fallback(engine, adapter):
    item = submitted(engine, adapter)
    adapter.report_entry(item, 0, status="REJECTED")
    engine.tick()
    assert len(adapter.writes) == 2
    assert adapter.writes[-1]["order_type"] == "MARKET"
    assert adapter.writes[0]["correlation_id"] != adapter.writes[-1]["correlation_id"]
    latest = engine.status()["intents"][0]
    adapter.report_entry(latest, 0, status="REJECTED")
    engine.tick()
    assert len(adapter.writes) == 2
    assert len(engine.status()["intents"]) == 1


def test_unknown_market_fallback_does_not_use_old_rejection_as_ack(engine, adapter):
    item = submitted(engine, adapter)
    adapter.report_entry(item, 0, status="REJECTED")
    adapter.fail_write = True
    engine.tick()
    adapter.sequence += 1
    engine.tick()
    state = engine.status()
    assert state["intents"][0]["status"] == "UNKNOWN"
    assert state["account"]["write_busy"] == item["id"]
    assert len(adapter.writes) == 2


@pytest.mark.parametrize("quantity,status", [(0, "PENDING"), (10, "REJECTED"), (0, "CANCELLED")])
def test_no_fallback_for_pending_partial_or_cancelled(engine, adapter, quantity, status):
    item = submitted(engine, adapter)
    adapter.report_entry(item, quantity, status=status)
    engine.tick()
    assert len(adapter.writes) == 1
    if quantity:
        assert engine.status()["intents"][0]["entry_day"]


def test_entry_fill_target_rebased_to_actual_weighted_fill_and_modify_confirmed(engine, adapter):
    item = filled(engine, adapter, price=102)
    assert item["target_price"] == 135
    engine.tick()
    assert adapter.modifications == [(item["order_id"], "TARGET_LEG", 135)]
    assert engine.status()["intents"][0]["pending_modify"]
    engine.tick()
    assert len(adapter.modifications) == 1
    adapter.confirm_modify()
    engine.tick()
    assert "pending_modify" not in engine.status()["intents"][0]


def test_partial_fill_averages_change_without_extra_daily_intent_or_entry_alert(engine, adapter):
    item = filled(engine, adapter, price=100, quantity=10, status="PENDING")
    day = item["entry_day"]
    adapter.report_entry(item, 65, price=102)
    engine.reconcile()
    item = engine.status()["intents"][0]
    average = (10 * 100 + 55 * 102) / 65
    assert item["average_price"] == average
    assert item["target_price"] == tick_price(average + 2 * (average - 85.5), .05, up=True)
    assert item["entry_day"] == day
    events = []
    engine.deliver_notifications(lambda _, e: events.append(e))
    assert [e["kind"] for e in events] == ["entry_executed"]


def test_step_trail_5_then_5_monotonic_and_no_local_sell(engine, adapter):
    item = filled(engine, adapter, price=99)
    adapter.price = 103.99
    engine.tick()
    assert not adapter.modifications
    for mark, expected in [(104, 99), (109, 104), (114, 109)]:
        adapter.price = mark
        engine.tick()
        assert adapter.modifications[-1] == (item["order_id"], "STOP_LOSS_LEG", expected)
        adapter.confirm_modify()
        engine.tick()
        assert engine.status()["intents"][0]["stop_loss"] == expected
    count = len(adapter.modifications)
    adapter.price = 100
    engine.tick()
    assert len(adapter.modifications) == count
    assert len(adapter.writes) == 1 and adapter.writes[0]["side"] == "BUY"
    with pytest.raises(LiveBlocked):
        engine.request_exit(item["id"])


def test_modify_timeout_never_resends_and_book_confirmation_recovers(engine, adapter):
    item = filled(engine, adapter, price=99)
    adapter.price, adapter.fail_modify = 104, True
    engine.tick()
    engine.tick()
    assert len(adapter.modifications) == 1
    assert engine.status()["intents"][0]["stop_loss"] == 85.5
    adapter.confirm_modify()
    engine.tick()
    assert engine.status()["intents"][0]["stop_loss"] == 99


def test_broker_exit_lifecycle_outbox_gross_pnl_context_and_retry(engine, adapter):
    item = filled(engine, adapter, price=102)
    adapter.exit(item, price=110)
    engine.tick()
    saved = engine.status()["intents"][0]
    assert saved["status"] == "CLOSED" and saved["owned_quantity"] == 0
    events = []
    assert engine.deliver_notifications(lambda *_: False) == 0
    assert engine.deliver_notifications(lambda _, e: events.append(e)) == 2
    assert [e["kind"] for e in events] == ["entry_executed", "exit_executed"]
    exit_data = events[-1]["proposal"]
    assert exit_data["pnl_points"] == 8
    assert exit_data["gross_pnl"] == 520
    assert exit_data["pnl_percent"] == pytest.approx(8 / 102 * 100)
    assert exit_data["capital_context"]["available_before"] == 100000
    assert exit_data["pnl_basis"] == "gross_before_charges"
    assert exit_data["exit_average_price"] == 110 and exit_data["realized_pnl"] == 520
    assert exit_data["entry_time"] == exit_data["first_fill_timestamp"]
    assert exit_data["exit_time"] == exit_data["exit_timestamp"]
    assert exit_data["available_funds"] == 100000
    assert engine.deliver_notifications(lambda *_: pytest.fail("already delivered")) == 0
    assert len(adapter.writes) == 1


def test_two_first_filled_logical_entries_per_day_and_funds_refetched(engine, adapter):
    for key in ("first", "second"):
        candidate = scanner(adapter, key)
        if key == "second":
            candidate["metadata"]["breakout_price"] = 110
            adapter.price = 110
        item = engine.propose(candidate)
        adapter.report_entry(item)
        engine.reconcile()
        item = next(i for i in engine.status()["intents"] if i["id"] == item["id"])
        adapter.exit(item)
        engine.tick()
    assert len(adapter.writes) == 2 and adapter.margin_reads >= 2
    candidate = scanner(adapter, "third")
    candidate["metadata"]["breakout_price"] = 120
    adapter.price = 120
    with pytest.raises(LiveBlocked, match="two first-filled"):
        engine.propose(candidate)
    assert len(adapter.writes) == 2
    assert engine._day(adapter.now) == "2026-10-07"


@pytest.mark.parametrize("change", ["manual_add", "manual_reduce", "external_order", "missing_previous_day"])
def test_external_and_uncertain_ownership_blocks_entries_and_modification(engine, adapter, change):
    item = filled(engine, adapter)
    if change == "manual_add":
        adapter.positions[item["security_id"]] += 1
    elif change == "manual_reduce":
        adapter.positions[item["security_id"]] -= 1
    elif change == "external_order":
        adapter.orders["external"] = {
            "order_id": "external", "correlation_id": None, "side": "SELL",
            "security_id": item["security_id"], "quantity": 1,
            "filled_quantity": 0, "status": "PENDING"}
    else:
        adapter.super_book = {}
    adapter.sequence += 1
    engine.tick()
    with pytest.raises(LiveBlocked):
        submitted(engine, adapter, "next")
    assert not adapter.modifications and len(adapter.writes) == 1


def test_pending_exit_broker_oco_not_closed_cannot_unlock_next_position(engine, adapter):
    item = filled(engine, adapter)
    adapter.exit(item)
    record = adapter.super_book[item["correlation_id"]]
    record["legs"]["STOP_LOSS_LEG"]["status"] = "PENDING"
    engine.reconcile()
    assert engine.status()["intents"][0]["status"] != "CLOSED"
    with pytest.raises(LiveBlocked):
        submitted(engine, adapter, "second")


def test_route_tick_delivers_only_lifecycle_and_auto_factory(monkeypatch, tmp_path, engine, adapter):
    item = filled(engine, adapter)
    events = []
    route = LiveRoute(engine, SimpleNamespace(secret="", allowed_actors=set(), allowed_chats=set()),
                      notifier=lambda _, e: events.append(e))
    assert route.tick()["notifications_delivered"] == 1
    assert [e["kind"] for e in events] == ["entry_executed"]
    monkeypatch.setattr("dhan_super_order.DhanSuperOrderAdapter", lambda: adapter)
    # Factory isinstance boundary imports the patched name, so inspect creation
    # without invoking readiness until the class is restored.
    configured = build_live_route(
        {"enabled": True, "practice": False, "auto": True, "db_path": str(tmp_path / "factory.db")},
        tmp_path / "paper.db")
    assert isinstance(configured.execution, AutoSuperExecution)
    assert configured.execution.broker.adapter is adapter


def test_first_fill_ist_day_not_signal_submission_day(engine, adapter):
    adapter.now = datetime(2026, 10, 7, 23, 59, 59, tzinfo=IST).timestamp()
    item = submitted(engine, adapter)
    adapter.now += 2
    adapter.report_entry(item)
    engine.reconcile()
    assert engine.status()["intents"][0]["entry_day"] == "2026-10-08"
    assert engine._day(adapter.now - 2) == "2026-10-07"


def test_expired_rejected_limit_does_not_get_delayed_market_retry(engine, adapter):
    item = submitted(engine, adapter)
    adapter.now += 11
    adapter.report_entry(item, 0, status="REJECTED")
    engine.tick()
    assert len(adapter.writes) == 1
    assert engine.status()["intents"][0]["status"] == "INVALIDATED"


def test_guard_flip_cannot_modify_protective_orders(engine, adapter):
    filled(engine, adapter, price=99)
    engine.auto = False
    adapter.price = 104
    with pytest.raises(LiveBlocked):
        engine.tick()
    assert not adapter.modifications


def test_target_crossed_before_rebase_retains_broker_protection_no_ordinary_sell(engine, adapter):
    filled(engine, adapter)
    adapter.price = 140
    result = engine.tick()
    assert result["blockers"]
    assert not adapter.modifications and len(adapter.writes) == 1


def test_legacy_live_database_does_not_claim_super_order_ownership(engine, adapter):
    with engine._db() as db:
        engine._save(db, {"id": "legacy", "signal_key": "legacy", "status": "AWAITING",
                          "side": "BUY", "filled_quantity": 0, "owned_quantity": 0})
    restarted = AutoSuperExecution(engine.db_path, engine.broker, enabled=True,
                                    practice=False, auto=True, clock=lambda: adapter.now)
    with pytest.raises(LiveBlocked, match="legacy live state"):
        restarted.tick()
    with pytest.raises(LiveBlocked, match="legacy live state"):
        submitted(restarted, adapter)
    assert not adapter.writes


def test_triggered_nested_leg_requires_actual_child_fill_to_complete_exit(engine, adapter):
    item = filled(engine, adapter)
    adapter.exit(item)
    adapter.super_book[item["correlation_id"]]["legs"]["TARGET_LEG"]["status"] = "PENDING"
    engine.reconcile()
    assert engine.status()["intents"][0]["status"] == "CLOSED"


def test_parent_single_argument_notify_callback_receives_idle_lifecycle(engine, adapter):
    filled(engine, adapter)
    route = LiveRoute(engine, None)
    events = []
    route.notify = lambda event: events.append(event)
    assert route.tick()["notifications_delivered"] == 1
    assert events[0]["kind"] == "entry_executed"
    assert events[0]["event_id"] > 0
    assert events[0]["proposal"]["option_symbol"] == adapter.contract["security_id"]


@pytest.mark.parametrize("payload", [
    {"update_id": 1, "message": {"text": "/live_status"}},
    {"update_id": 2, "message": {"text": "/live_limit proposal 1 100"}},
    {"update_id": 3, "callback_query": {"data": "live:proposal:1:approve"}},
])
def test_automatic_live_telegram_endpoint_blocks_all_controls(engine, payload):
    control = SimpleNamespace(
        handle_update=lambda *_: pytest.fail("automatic mode must never dispatch legacy controls"))
    result, status = LiveRoute(engine, control).handle_update(payload, "secret")
    assert status == 403 and result["ok"] is False and result["status"] == "blocked"


def acted_and_closed(engine, adapter):
    item = filled(engine, adapter)
    adapter.exit(item)
    engine.tick()
    return engine.status()["intents"][0]


def trigger_candidate(adapter, price, key="next"):
    candidate = scanner(adapter, key)
    candidate["metadata"].update(breakout_price=price, reference_high=price - 1,
                                  reference_low=price - 10)
    adapter.price = price
    return candidate


@pytest.mark.parametrize("price,blocked", [
    (94.999, False), (95, True), (100, True), (105, True), (105.001, False),
])
def test_execution_anti_chop_inclusive_decimal_boundaries(engine, adapter, price, blocked):
    acted_and_closed(engine, adapter)
    candidate = trigger_candidate(adapter, price)
    if blocked:
        with pytest.raises(LiveBlocked, match="inclusive"):
            engine.propose(candidate)
        assert len(adapter.writes) == 1
    else:
        engine.propose(candidate)
        assert len(adapter.writes) == 2


def test_acted_trigger_survives_telegram_failure_restart_and_day_rollover(engine, adapter):
    acted_and_closed(engine, adapter)
    assert engine.deliver_notifications(lambda *_: False) == 0
    adapter.now += 86400
    adapter.contract["expiry"] = "2026-10-15"
    restarted = AutoSuperExecution(engine.db_path, engine.broker, enabled=True,
                                    practice=False, auto=True, clock=lambda: adapter.now)
    with pytest.raises(LiveBlocked, match="inclusive"):
        restarted.propose(trigger_candidate(adapter, 105))
    assert restarted.status()["account"]["last_acted_trigger_prices"] == {
        "index_options:NIFTY:CE": 100}
    assert len(adapter.writes) == 1


def test_trigger_band_shared_across_strikes_and_expiries_but_separate_by_side(engine, adapter):
    acted_and_closed(engine, adapter)
    adapter.spot = 25070
    adapter.contract.update(strike=25000, expiry="2026-10-15")
    with pytest.raises(LiveBlocked, match="inclusive"):
        engine.propose(trigger_candidate(adapter, 100))
    adapter.contract.update(option_type="PE", strike=25100)
    engine.propose(trigger_candidate(adapter, 100, "second"))
    assert len(adapter.writes) == 2


def test_reference_dedup_independent_of_signal_id_and_outside_trigger_band(engine, adapter):
    original = acted_and_closed(engine, adapter)
    restarted = AutoSuperExecution(engine.db_path, engine.broker, enabled=True,
                                    practice=False, auto=True, clock=lambda: adapter.now)
    candidate = scanner(adapter, "new")
    candidate["metadata"].update(reference_timestamp=original["reference_timestamp"], breakout_price=106)
    adapter.price = 106
    result = restarted.propose(candidate)
    assert result["id"] == original["id"]
    assert len(adapter.writes) == 1
    with restarted._db() as db:
        assert db.execute("SELECT count(*) FROM live_references").fetchone()[0] == 1


def test_zero_fill_cancelled_reference_consumed_without_successful_acted_price(engine, adapter):
    original = submitted(engine, adapter)
    adapter.report_entry(original, 0, status="CANCELLED")
    engine.tick()
    candidate = scanner(adapter, "new")
    candidate["metadata"]["reference_timestamp"] = original["reference_timestamp"]
    assert engine.propose(candidate)["id"] == original["id"]
    assert not engine.status()["account"].get("last_acted_trigger_prices")
    engine.propose(trigger_candidate(adapter, 101, "second"))
    assert len(adapter.writes) == 2
