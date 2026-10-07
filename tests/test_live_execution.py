import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from live_broker import LiveBlocked, LiveNotSent
from live_execution import LiveExecution
from tests.test_live_broker import adapter, broker


@pytest.fixture
def engine(tmp_path, broker, adapter):
    return LiveExecution(tmp_path / "isolated-live.sqlite", broker,
                         enabled=True, practice=False, auto=True, clock=lambda: adapter.now)


def signal(adapter, key="first", breakout=100):
    adapter.price = breakout
    reference_high = breakout - 1
    return {"signal_id": key, "side": "BUY", "symbol": "NIFTY", "strategy": "premium_screener", "metadata": {
        **adapter.contract, "premium_strategy": True, "entry_price": reference_high,
        "detected_at": adapter.now, "source": "scanner", "trigger": "live_ltp",
        "breakout_timestamp": adapter.now, "reference_timestamp": adapter.now - 600,
        "breakout_price": breakout, "reference_high": reference_high,
        "reference_low": 90, "stop_loss": 85.5,
    }}


def approved(engine, adapter, key="first", breakout=100):
    item = engine.propose(signal(adapter, key, breakout=breakout))
    item = engine.bind(item["id"], 7, -9)
    return engine.approve(item["id"], item["version"], 7, -9)


def filled(engine, adapter, key="first", breakout=100):
    item = approved(engine, adapter, key, breakout=breakout)
    adapter.report(item, 65, "FILLED", actual=65)
    engine.reconcile()
    return engine.status()["intents"][-1]


@pytest.mark.parametrize("flags", [{}, {"enabled": True}, {"enabled": True, "practice": False}])
def test_default_off_three_guards(tmp_path, broker, adapter, flags):
    engine = LiveExecution(tmp_path / "off.sqlite", broker, clock=lambda: adapter.now, **flags)
    with pytest.raises(LiveBlocked):
        engine.propose(signal(adapter))
    assert not adapter.writes


def test_ack_never_fill_and_intent_durable_before_write(engine, adapter):
    def verify(order):
        with sqlite3.connect(engine.db_path) as db:
            text = db.execute("SELECT data FROM live_intents WHERE id=?", (order["id"],)).fetchone()[0]
            assert '"status": "UNKNOWN"' in text
            assert order["correlation_id"] in text
    adapter.before_place = verify
    item = approved(engine, adapter)
    assert item["status"] == "PENDING" and item["filled_quantity"] == 0
    assert len(item["correlation_id"]) <= 30
    assert item["reserved_cash"] == 6500
    assert item["lots"] == 1 and item["quantity"] == adapter.contract["lot_size"]
    assert item["product_type"] == "MARGIN"
    assert len(adapter.writes) == 1
    with pytest.raises(LiveBlocked):
        engine.approve(item["id"], 1, 7, -9)


def test_timeout_survives_restart_never_blind_retry(engine, adapter, broker):
    adapter.fail_write = True
    item = approved(engine, adapter)
    restarted = LiveExecution(engine.db_path, broker, enabled=True, practice=False,
                              auto=True, clock=lambda: adapter.now)
    assert restarted.status()["account"]["write_busy"] == item["id"]
    assert restarted.status()["intents"][0]["reserved_cash"] > 0
    with pytest.raises(LiveBlocked):
        restarted.propose(signal(adapter, "next"))
    with pytest.raises(LiveBlocked):
        restarted.approve(item["id"], 1, 7, -9)
    restarted.reconcile()  # absence is not rejection
    assert restarted.status()["account"]["write_busy"] == item["id"]
    adapter.report(item, 10, "PENDING", actual=10)
    restarted.reconcile()
    assert restarted.status()["intents"][0]["entry_day"]
    assert len(adapter.writes) == 1


def test_version_actor_chat_expiry_binding(engine, adapter):
    item = engine.propose(signal(adapter))
    engine.bind(item["id"], 7, -9)
    for actor, chat in ((8, -9), (7, -8)):
        with pytest.raises(LiveBlocked):
            engine.approve(item["id"], 1, actor, chat)
    with pytest.raises(LiveBlocked, match="exactly one lot"):
        engine.modify(item["id"], 1, 7, -9, lots=3)
    changed = engine.modify(item["id"], 1, 7, -9, order_type="MARKET")
    assert changed["quantity"] == 65 and changed["version"] == 2
    with pytest.raises(LiveBlocked):
        engine.approve(item["id"], 1, 7, -9)
    adapter.now += 121
    with pytest.raises(LiveBlocked):
        engine.approve(item["id"], 2, 7, -9)
    engine.expire()
    assert engine.status()["intents"][0]["reserved_cash"] == 0
    assert not adapter.writes


def test_reject_dedup_and_one_position_reservation(engine, adapter):
    item = engine.propose(signal(adapter))
    assert engine.propose(signal(adapter))["id"] == item["id"]
    with pytest.raises(LiveBlocked, match="one open"):
        engine.propose(signal(adapter, "second"))
    engine.bind(item["id"], 7, -9)
    engine.reject(item["id"], 1, 7, -9)
    assert engine.propose(signal(adapter, "second"))["status"] == "AWAITING"


@pytest.mark.parametrize("breakout,blocked", [
    (95, True), (105, True), (94.95, False), (105.05, False),
])
def test_persisted_inclusive_five_percent_reentry_band(engine, adapter, breakout, blocked):
    entry = filled(engine, adapter, breakout=100)
    adapter.sequence += 1
    adapter.positions["123"] = 0
    engine.reconcile()
    if blocked:
        with pytest.raises(LiveBlocked, match="inclusive five-percent band"):
            engine.propose(signal(adapter, "reentry", breakout=breakout))
    else:
        assert engine.propose(signal(adapter, "reentry", breakout=breakout))["status"] == "AWAITING"


def test_first_partial_counted_once_and_actual_fill_protection(engine, adapter):
    item = approved(engine, adapter)
    adapter.report(item, 10, "PENDING", actual=10, price=110)
    engine.reconcile()
    first = engine.status()["intents"][0]
    assert first["owned_quantity"] == 10 and first["entry_day"]
    assert first["stop_loss"] == pytest.approx(85.5)
    assert first["target_2"] == pytest.approx(159)
    adapter.report(item, 20, "PENDING", actual=20, price=111)
    engine.reconcile()
    assert engine.status()["intents"][0]["entry_day"] == first["entry_day"]
    with pytest.raises(LiveBlocked):
        engine.propose(signal(adapter, "second"))


def test_callback_dedup_and_nonmonotonic_evidence(engine, adapter):
    item = approved(engine, adapter)
    adapter.report(item, 20, "PENDING", actual=20)
    assert engine.callback("c1", {"filled_quantity": 9999})["applied"]
    assert not engine.callback("c1")["applied"]
    assert not engine.reconcile()["applied"]
    adapter.report(item, 10, "PENDING", actual=10)
    with pytest.raises(LiveBlocked, match="nonmonotonic"):
        engine.reconcile()
    assert engine.status()["intents"][0]["filled_quantity"] == 20


def test_cancel_remainder_must_confirm_before_exit(engine, adapter):
    item = approved(engine, adapter)
    adapter.report(item, 10, "PENDING", actual=10)
    assert engine.request_exit(item["id"])["status"] == "CANCEL_AWAITING_RECONCILIATION"
    assert len(adapter.cancels) == 1
    engine.request_exit(item["id"])
    assert len(adapter.cancels) == 1
    adapter.report(item, 12, "CANCELLED", actual=12)
    preview = engine.request_exit(item["id"])
    assert preview["quantity"] == 12 and preview["side"] == "SELL"
    assert len(adapter.writes) == 1


def test_external_reduction_full_exit_and_manual_add_halt(engine, adapter):
    entry = filled(engine, adapter)
    adapter.sequence += 1
    adapter.positions["123"] = 40
    engine.reconcile()
    assert engine.status()["intents"][0]["owned_quantity"] == 40
    adapter.sequence += 1
    adapter.positions["123"] = 50  # re-added after external reduction
    assert engine.reconcile()["halted"]
    adapter.sequence += 1
    adapter.positions["123"] = 0
    engine.reconcile()
    with pytest.raises(LiveBlocked, match="no authoritative"):
        engine.request_exit(entry["id"])


def test_exit_not_blocked_by_kill_switch_or_daily_cap(engine, adapter):
    entry = filled(engine, adapter)
    engine.enabled = False
    engine.auto = False
    engine.practice = True
    exit_item = engine.request_exit(entry["id"])
    engine.bind(exit_item["id"], 7, -9)
    result = engine.approve(exit_item["id"], 1, 7, -9)
    assert result["status"] == "PENDING"
    adapter.report(result, 65, "FILLED", actual=0)
    engine.reconcile()
    assert engine.status()["intents"][0]["owned_quantity"] == 0


def test_two_filled_entries_ist_day_cap(engine, adapter):
    for key, breakout in (("one", 100), ("two", 106)):
        entry = filled(engine, adapter, key, breakout=breakout)
        adapter.sequence += 1
        adapter.positions["123"] = 0  # authoritative external exit
        engine.reconcile()
    with pytest.raises(LiveBlocked, match="two-entry"):
        engine.propose(signal(adapter, "three", breakout=112))
    adapter.now += 86400
    adapter.contract["expiry"] = "2026-10-09"
    assert engine.propose(signal(adapter, "next-day", breakout=118))["status"] == "AWAITING"


def test_outbox_retries_delivery_only_never_orders(engine, adapter):
    approved(engine, adapter)
    assert engine.deliver_notifications(lambda *_: False) == 0
    events = []
    assert engine.deliver_notifications(lambda event_id, event: events.append((event_id, event))) == 2
    assert engine.deliver_notifications(lambda *_: True) == 0
    assert len(adapter.writes) == 1


def test_t1_trailing_and_t2_exit_preview(engine, adapter):
    entry = filled(engine, adapter)
    adapter.price = 125
    assert engine.protection_preview(entry["id"]) is None
    assert engine.status()["intents"][0]["stop_loss"] == pytest.approx(117)
    adapter.price = 140
    preview = engine.protection_preview(entry["id"])
    assert preview["reason"] == "target_2" and preview["side"] == "SELL"
    assert len(adapter.writes) == 1  # preview is never an automatic protective write


def test_unrelated_exposure_never_closed(engine, adapter):
    entry = filled(engine, adapter)
    adapter.sequence += 1
    adapter.positions["unrelated"] = 5
    assert engine.reconcile()["halted"]
    preview = engine.request_exit(entry["id"])
    engine.bind(preview["id"], 7, -9)
    engine.approve(preview["id"], 1, 7, -9)
    assert adapter.writes[-1]["security_id"] == "123"
    assert adapter.writes[-1]["quantity"] == 65


def test_concurrent_reconciliation_before_ack_does_not_erase_fill(engine, adapter, broker):
    other = LiveExecution(engine.db_path, broker, enabled=True, practice=False,
                          auto=True, clock=lambda: adapter.now)
    def reconcile_during_write(item):
        adapter.report(item, 65, "FILLED", actual=65)
        other.reconcile()
    adapter.before_place = reconcile_during_write
    item = approved(engine, adapter)
    assert item["status"] == "FILLED" and item["filled_quantity"] == 65
    assert item["owned_quantity"] == 65 and item["reserved_cash"] == 0


def test_cancel_timeout_not_retried(engine, adapter):
    item = approved(engine, adapter)
    adapter.report(item, 10, "PENDING", actual=10)
    adapter.fail_cancel = True
    engine.request_exit(item["id"])
    engine.request_exit(item["id"])
    assert len(adapter.cancels) == 1
    assert engine.status()["account"]["write_busy"] == item["id"]
    assert len(adapter.writes) == 1


def test_final_order_status_cannot_regress(engine, adapter):
    entry = filled(engine, adapter)
    adapter.report(entry, 65, "PENDING", actual=65)
    with pytest.raises(LiveBlocked, match="regressed"):
        engine.reconcile()


def test_stale_authoritative_snapshots_block_reconciliation(engine, adapter):
    approved(engine, adapter)
    adapter.age = 10
    with pytest.raises(LiveBlocked, match="stale"):
        engine.reconcile()


def test_funds_rechecked_at_approval_not_proposal_only(engine, adapter):
    item = engine.propose(signal(adapter))
    engine.bind(item["id"], 7, -9)
    adapter.available = 1
    with pytest.raises(LiveBlocked, match="funds"):
        engine.approve(item["id"], 1, 7, -9)
    assert engine.status()["intents"][0]["status"] == "AWAITING"
    assert not adapter.writes


def test_scanner_iso_timestamps_supported(engine, adapter):
    payload = signal(adapter)
    for field in ("detected_at", "reference_timestamp", "breakout_timestamp"):
        payload["metadata"][field] = datetime.fromtimestamp(
            payload["metadata"][field], timezone.utc).isoformat()
    assert engine.propose(payload)["initial_stop_loss"] == 85.5


@pytest.mark.parametrize("field,value", [
    ("source", "manual"), ("trigger", "candle_high"), ("breakout_price", 99),
    ("breakout_timestamp", 1791350989.0), ("stop_loss", 90),
    ("reference_timestamp", 1791351001.0), ("premium_strategy", "true"),
])
def test_scanner_bypass_or_stale_evidence_blocked(engine, adapter, field, value):
    payload = signal(adapter)
    payload["metadata"][field] = value
    with pytest.raises(LiveBlocked):
        engine.propose(payload)
    assert not adapter.writes


@pytest.mark.parametrize("field,value", [
    ("source", "manual"), ("security_id", "other"), ("trigger", "candle_high"),
    ("stop_loss", 99), ("underlying", "BANKNIFTY"),
])
def test_metadata_cannot_override_conflicting_top_level(engine, adapter, field, value):
    payload = signal(adapter)
    payload[field] = value
    with pytest.raises(LiveBlocked, match="conflicting"):
        engine.propose(payload)


def test_actual_premium_must_still_break_reference(engine, adapter):
    payload = signal(adapter)
    adapter.price = 99
    with pytest.raises(LiveBlocked, match="current actual"):
        engine.propose(payload)


@pytest.mark.parametrize("lots", [0, 2, True])
def test_live_entry_rejects_anything_other_than_one_lot(engine, adapter, lots):
    payload = signal(adapter)
    payload["metadata"]["lots"] = lots
    with pytest.raises(LiveBlocked, match="exactly one lot"):
        engine.propose(payload)


def test_auto_mode_fails_closed_without_verified_super_order(tmp_path, broker, adapter):
    automatic = LiveExecution(
        tmp_path / "auto.sqlite", broker, enabled=True, practice=False, auto=True,
        auto_mode=True, clock=lambda: adapter.now,
    )
    with pytest.raises(LiveBlocked, match="verified Dhan Super Order adapter"):
        automatic.propose(signal(adapter))
    assert not adapter.writes


def test_actual_fill_risk_keeps_reference_stop_and_derives_r_targets(engine, adapter):
    item = approved(engine, adapter)
    adapter.report(item, 65, "FILLED", actual=65, price=105)
    engine.reconcile()
    position = engine.status()["intents"][0]
    assert position["initial_stop_loss"] == position["stop_loss"] == 85.5
    assert position["risk_points"] == 19.5
    assert position["target_2"] == 144


def test_user_confirmed_stop_and_two_r_target_example(engine, adapter):
    item = approved(engine, adapter)
    adapter.report(item, 65, "FILLED", actual=65, price=100)
    engine.reconcile()
    position = engine.status()["intents"][0]
    assert position["initial_stop_loss"] == 85.5
    assert position["risk_points"] == 14.5
    assert position["target_2"] == 129


def test_scanner_display_expiry_canonicalized_before_master_match(engine, adapter):
    payload = signal(adapter)
    payload["metadata"]["expiry"] = "08OCT2026"
    assert engine.propose(payload)["expiry"] == "2026-10-08"


def test_expiry_display_and_iso_conflicts_blocked(engine, adapter):
    payload = signal(adapter)
    payload["metadata"].update(expiry="08OCT2026", expiry_date="2026-10-15")
    with pytest.raises(LiveBlocked, match="expiry display/date mismatch"):
        engine.propose(payload)


def test_step_trailing_moves_to_breakeven_then_up_five_points(engine, adapter):
    entry = filled(engine, adapter)
    adapter.price = 106.99
    assert engine.protection_preview(entry["id"]) is None
    assert engine.status()["intents"][0]["stop_loss"] == 85.5
    adapter.price = 107
    assert engine.protection_preview(entry["id"]) is None
    assert engine.status()["intents"][0]["stop_loss"] == 102
    adapter.price = 112
    assert engine.protection_preview(entry["id"]) is None
    assert engine.status()["intents"][0]["stop_loss"] == 107
    adapter.price = 117
    assert engine.protection_preview(entry["id"]) is None
    assert engine.status()["intents"][0]["stop_loss"] == 112
    adapter.price = 109
    preview = engine.protection_preview(entry["id"])
    assert preview["reason"] == "trailing_stop"
    assert engine.status()["intents"][0]["stop_loss"] == 112
    assert len(adapter.writes) == 1


def test_lagging_position_snapshot_conservatively_halts_later_catchup(engine, adapter):
    item = approved(engine, adapter)
    adapter.report(item, 65, "FILLED", actual=0)
    engine.reconcile()
    assert engine.status()["intents"][0]["external_reduction"] == 65
    adapter.sequence += 1
    adapter.positions["123"] = 65
    assert engine.reconcile()["halted"]
    with pytest.raises(LiveBlocked):
        engine.propose(signal(adapter, "other"))
    assert len(adapter.writes) == 1


def test_real_scanner_queue_iso_display_expiry_to_mock_execution(engine, adapter):
    from screener_premium import PremiumScreener
    from premium_strategy_engine import PremiumStrategyEngine, TRIGGER_LIVE

    reference_time = datetime.fromtimestamp(adapter.now - 600, timezone.utc).isoformat()
    quote_time = datetime.fromtimestamp(adapter.now, timezone.utc).isoformat()
    reference = {
        "candle": {"open": 170, "close": 165}, "timestamp": reference_time,
        "high": 170.80, "low": 160,
    }
    strategy_signal = PremiumStrategyEngine().build_signal(
        "NIFTY", "CE", "NIFTY50", reference, 171, quote_time, TRIGGER_LIVE)
    strategy_signal.update(premium_strategy=True, detected_at=quote_time,
                           action="BUY", action_text="BUY CALL")
    adapter.price = 171
    option_data = {**adapter.contract, "expiry": "08OCT2026",
                   "premium_ltp": 171, "spot_ltp": adapter.spot, "strike_band": "ITM+1"}
    payload = PremiumScreener._build_queue_payload(
        SimpleNamespace(symbol="NIFTY"), strategy_signal, "NIFTY50",
        "premium_screener", option_data)
    proposal = engine.propose(payload)
    assert proposal["expiry"] == "2026-10-08"
    assert proposal["quantity"] == 65 and proposal["initial_stop_loss"] == 152
    assert proposal["product_type"] == "MARGIN"
    assert proposal["limit_price"] == strategy_signal["entry"] == 170.80
    assert strategy_signal["breakout_price"] == 171 > strategy_signal["reference_high"]
    assert not adapter.writes
    engine.bind(proposal["id"], 7, -9)
    approved_item = engine.approve(proposal["id"], 1, 7, -9)
    assert approved_item["filled_quantity"] == 0
    adapter.report(approved_item, 65, "FILLED", actual=65, price=171.5)
    engine.reconcile()
    assert engine.status()["intents"][0]["target_2"] == 210.5


def test_auto_notifications_include_only_actual_entry_and_exit_with_pnl(engine, adapter):
    entry = approved(engine, adapter)
    engine.auto_mode = True
    adapter.report(entry, 65, "FILLED", actual=65, price=102)
    engine.reconcile()
    engine.auto_mode = False
    exit_preview = engine.request_exit(entry["id"])
    engine.bind(exit_preview["id"], 7, -9)
    exit_order = engine.approve(exit_preview["id"], 1, 7, -9)
    engine.auto_mode = True
    adapter.report(exit_order, 65, "FILLED", actual=0, price=110)
    engine.reconcile()

    sent = []
    assert engine.deliver_notifications(lambda event_id, event: sent.append(event)) == 2
    assert [event["kind"] for event in sent] == ["entry_executed", "exit_executed"]
    assert sent[0]["proposal"]["capital_committed"] == 102 * 65
    assert sent[0]["proposal"]["available_funds"] == 100000
    assert sent[1]["proposal"]["realized_pnl"] == 8 * 65


def test_auto_outbox_suppresses_approval_and_non_execution_events(engine):
    engine.auto_mode = True
    with engine._db() as db:
        for kind in ("approval_requested", "write_unknown", "entry_executed", "exit_executed"):
            engine._event(db, kind, {"kind": kind})
    sent = []
    assert engine.deliver_notifications(lambda event_id, event: sent.append(event["kind"])) == 2
    assert sent == ["entry_executed", "exit_executed"]


def test_breakout_observation_cannot_replace_reference_entry(engine, adapter):
    payload = signal(adapter)
    payload["metadata"]["entry_price"] = payload["metadata"]["breakout_price"]
    with pytest.raises(LiveBlocked, match="invalid actual breakout"):
        engine.propose(payload)


def test_external_partial_exit_after_preview_is_proven_not_sent_and_replaceable(engine, adapter):
    entry = filled(engine, adapter)
    preview = engine.request_exit(entry["id"])
    engine.bind(preview["id"], 7, -9)
    adapter.sequence += 1
    adapter.positions["123"] = 40
    refused = engine.approve(preview["id"], 1, 7, -9)
    assert refused["status"] == "INVALIDATED" and refused["reserved_cash"] == 0
    assert engine.status()["account"]["write_busy"] is None
    assert len(adapter.writes) == 1
    replacement = engine.request_exit(entry["id"])
    assert replacement["quantity"] == 40
    engine.bind(replacement["id"], 7, -9)
    assert engine.approve(replacement["id"], 1, 7, -9)["status"] == "PENDING"
    assert len(adapter.writes) == 2


def test_expired_exit_preview_does_not_block_replacement(engine, adapter):
    entry = filled(engine, adapter)
    old = engine.request_exit(entry["id"])
    adapter.now += 121
    replacement = engine.request_exit(entry["id"])
    assert replacement["id"] != old["id"]
    previous = next(i for i in engine.status()["intents"] if i["id"] == old["id"])
    assert previous["status"] == "EXPIRED"
    assert len(adapter.writes) == 1


def test_sell_post_place_timeout_keeps_unknown_and_blocks_replacement(engine, adapter):
    entry = filled(engine, adapter)
    preview = engine.request_exit(entry["id"])
    engine.bind(preview["id"], 7, -9)
    adapter.fail_write = True
    assert engine.approve(preview["id"], 1, 7, -9)["status"] == "UNKNOWN"
    assert engine.status()["account"]["write_busy"] == preview["id"]
    with pytest.raises(LiveBlocked, match="exit already reserved"):
        engine.request_exit(entry["id"])
    assert len(adapter.writes) == 2


def test_adapter_cannot_claim_not_sent_after_write_boundary(engine, adapter):
    def ambiguous_write(item):
        adapter.writes.append(item)
        raise LiveNotSent("adapter claims refused after boundary")
    adapter.place = ambiguous_write
    item = approved(engine, adapter)
    assert item["status"] == "UNKNOWN"
    assert engine.status()["account"]["write_busy"] == item["id"]
    assert item["reserved_cash"] > 0


def test_known_entry_boundary_refusal_releases_only_proven_unsent_reservation(engine, adapter):
    item = engine.propose(signal(adapter))
    engine.bind(item["id"], 7, -9)
    adapter.positions["unrelated"] = 5
    refused = engine.approve(item["id"], 1, 7, -9)
    assert refused["status"] == "INVALIDATED"
    assert engine.status()["account"]["write_busy"] is None
    assert refused["reserved_cash"] == 0
    assert not adapter.writes


@pytest.mark.parametrize("status,filled_units", [("FILLED", 65), ("PENDING", 0),
                                                ("UNKNOWN", 0), ("CANCELLED", 10)])
def test_unknown_external_buy_activity_blocks_flat_account_proposal(engine, adapter, status, filled_units):
    adapter.orders["external-buy"] = {
        "side": "BUY", "security_id": "123", "quantity": 65,
        "filled_quantity": filled_units, "status": status,
    }
    adapter.positions["123"] = 0
    with pytest.raises(LiveBlocked, match="external BUY"):
        engine.propose(signal(adapter))
    assert not adapter.writes
    assert not engine.status()["intents"]


def test_external_completed_closed_activity_blocks_approval_and_cannot_be_whitelisted_by_signal(engine, adapter):
    payload = signal(adapter)
    payload["metadata"]["known_correlations"] = ["external-buy"]
    item = engine.propose(payload)
    engine.bind(item["id"], 7, -9)
    adapter.orders["external-buy"] = {
        "side": "BUY", "security_id": "123", "quantity": 65,
        "filled_quantity": 65, "status": "FILLED",
    }
    with pytest.raises(LiveBlocked, match="external BUY"):
        engine.approve(item["id"], 1, 7, -9)
    assert not adapter.writes
    assert engine.status()["account"]["write_busy"] is None


def test_external_activity_halts_entries_without_closing_unrelated_and_preserves_owned_exit(engine, adapter):
    entry = filled(engine, adapter)
    adapter.sequence += 1
    adapter.orders["unrelated-external-buy"] = {
        "side": "BUY", "security_id": "unrelated", "quantity": 5,
        "filled_quantity": 5, "status": "FILLED",
    }
    adapter.positions["unrelated"] = 0
    assert engine.reconcile()["halted"]
    assert len(adapter.writes) == 1
    preview = engine.request_exit(entry["id"])
    engine.bind(preview["id"], 7, -9)
    result = engine.approve(preview["id"], 1, 7, -9)
    assert result["status"] == "PENDING"
    assert adapter.writes[-1]["side"] == "SELL"
    assert adapter.writes[-1]["security_id"] == "123"
    assert adapter.writes[-1]["quantity"] == 65


def test_authoritative_unknown_own_order_keeps_write_reservation(engine, adapter):
    adapter.fail_write = True
    item = approved(engine, adapter)
    adapter.report(item, 0, "UNKNOWN", actual=0)
    engine.reconcile()
    assert engine.status()["account"]["write_busy"] == item["id"]
    assert engine.status()["intents"][0]["status"] == "UNKNOWN"
    assert len(adapter.writes) == 1
