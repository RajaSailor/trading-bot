"""Persistent position controls and real Telegram channel workflows."""

import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from decimal import InvalidOperation
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

from paper_portfolio import PaperPortfolio
from paper_telegram import PaperTelegramControl

SECRET = "a" * 40


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 5, 10, tzinfo=ZoneInfo("Asia/Kolkata")).timestamp()

    def __call__(self):
        return self.now


@pytest.fixture
def engine(tmp_path):
    clock = Clock()
    events = []
    portfolio = PaperPortfolio(tmp_path / "paper.db", clock=clock, notify=events.append)
    return portfolio, clock, events


def opened(engine, identifier=1):
    portfolio, clock, _ = engine
    order = portfolio.submit({
        "signal_id": str(identifier), "entry_price": 100,
        "metadata": {"security_id": identifier, "exchange_segment": "NSE_FNO",
                     "option_symbol": f"NIFTY-{identifier}-CE", "lot_size": 50,
                     "tick_size": .05, "stop_loss": 90, "timeframe": "5min",
                     "category": "index_options"},
    }, {"price": 100, "timestamp": clock()})
    portfolio.action("approve_limit", order["id"], actor="alice")
    return next(p for p in portfolio.snapshot()["positions"] if p["id"] == order["id"])


def control(portfolio, identifier, action, **kwargs):
    return portfolio.position_control(identifier, action, "alice", -100, **kwargs)


def evidence(portfolio, clock, price, identifier=1):
    with portfolio._transaction("quote") as db:
        portfolio._store_quote(db, ("NSE_FNO", identifier),
                               {"price": price, "timestamp": clock()})


def confirm(portfolio, preview):
    return control(portfolio, None, "confirm", token=preview["token"])


def test_persisted_input_preview_confirm_and_duplicate(engine):
    portfolio, clock, _ = engine
    position = opened(engine)
    start = control(portfolio, position["id"], "sl", version=0)
    assert re.fullmatch("[0-9a-f]{16}", start["token"])
    assert start["stage"] == "input" and start["old"] == 90 and start["value"] is None
    assert start["expires_at"] == clock() + 300
    portfolio = PaperPortfolio(portfolio.db_path, clock=clock)
    for char in "95.0":
        result = control(portfolio, None, "digit", token=start["token"], value=char)
        assert result["ok"]
    preview = control(portfolio, None, "preview", token=start["token"])
    assert preview["stage"] == "confirm" and preview["value"] == 95
    committed = confirm(portfolio, preview)
    assert committed["stage"] == "committed" and committed["position"]["stop_loss"] == 95
    assert committed["position"]["control_version"] == 1
    assert not confirm(portfolio, preview)["ok"]
    assert len(portfolio.snapshot()["executions"]) == 1


@pytest.mark.parametrize("action,value", [
    ("sl", 95), ("target_1", 111), ("target_2", 125), ("trailing", False),
    ("exit_market", None), ("exit_limit", 105),
])
def test_each_control_preview_and_commit(engine, action, value):
    portfolio, _, _ = engine
    position = opened(engine)
    preview = control(portfolio, position["id"], action, value=value)
    assert preview["ok"] and preview["stage"] == "confirm"
    result = confirm(portfolio, preview)
    assert result["ok"] and result["stage"] == "committed"
    assert result["position"]["id"] == position["id"]
    assert result["value"] == value
    if action.startswith("exit"):
        snapshot = portfolio.snapshot()
        assert len(snapshot["exit_orders"]) == 1
        assert snapshot["exit_orders"][0]["order_type"] == (
            "MARKET" if action == "exit_market" else "LIMIT")
    else:
        field = "stop_loss" if action == "sl" else "trailing_enabled" if action == "trailing" else action
        assert result["position"][field] == value


def test_token_bindings_cancel_and_fixed_expiry(engine):
    portfolio, clock, _ = engine
    position = opened(engine)
    start = control(portfolio, position["id"], "sl")
    token = start["token"]
    assert portfolio.position_control(None, "input", "bob", -100, token=token, value=95)["reason"] == "actor_mismatch"
    assert portfolio.position_control(None, "input", "alice", -101, token=token, value=95)["reason"] == "chat_mismatch"
    assert control(portfolio, "wrong", "input", token=token, value=95)["reason"] == "position_mismatch"
    assert control(portfolio, None, "target_2", token=token, value=125)["reason"] == "action_mismatch"
    cancelled = control(portfolio, None, "cancel", token=token)
    assert cancelled["ok"] and cancelled["stage"] == "cancelled"
    assert not confirm(portfolio, start)["ok"]
    start = control(portfolio, position["id"], "sl", value=95)
    clock.now += 300
    assert confirm(portfolio, start)["reason"] == "workflow_expired"


@pytest.mark.parametrize("value", [0, -1, True, "nan", "inf", 95.01, 89.95, 100])
def test_sl_rejects_invalid_loosening_or_mark_crossing(engine, value):
    portfolio, _, _ = engine
    position = opened(engine)
    assert not control(portfolio, position["id"], "sl", value=value)["ok"]
    assert portfolio.snapshot()["positions"][0]["stop_loss"] == 90


def test_above_entry_stop_uses_current_mark_and_revalidates(engine):
    portfolio, clock, _ = engine
    position = opened(engine)
    evidence(portfolio, clock, 108)
    preview = control(portfolio, position["id"], "sl", value=105)
    assert preview["ok"] and preview["position"]["mark_price"] == 108
    evidence(portfolio, clock, 104)
    assert confirm(portfolio, preview)["reason"] == "sl_must_be_below_mark"
    evidence(portfolio, clock, 108)
    committed = confirm(portfolio, preview)
    assert committed["position"]["stop_loss"] == 105
    assert committed["position"]["stop_source"] == "manual"


@pytest.mark.parametrize("action,value", [
    ("target_1", 100), ("target_1", 120), ("target_2", 110), ("target_2", 110.01),
])
def test_target_ordering_and_tick_alignment(engine, action, value):
    portfolio, _, _ = engine
    position = opened(engine)
    assert not control(portfolio, position["id"], action, value=value)["ok"]


def test_target_future_mark_and_reached_t1(engine):
    portfolio, clock, events = engine
    position = opened(engine)
    preview = control(portfolio, position["id"], "target_1", value=108)
    evidence(portfolio, clock, 109)
    assert confirm(portfolio, preview)["reason"] == "target_must_be_above_mark"
    portfolio.tick({("NSE_FNO", 1): {"price": 111, "timestamp": clock()}})
    assert control(portfolio, position["id"], "target_1")["reason"] == "target_1_already_reached"
    event = next(e for e in events if e["type"] == "target_1")
    assert event["entry_price"] == 100 and event["t1_reached"] and event["control_version"] == 1
    assert control(portfolio, position["id"], "target_2", value=125)["ok"]


def test_marks_do_not_invalidate_but_controls_do(engine):
    portfolio, clock, _ = engine
    position = opened(engine)
    first = control(portfolio, position["id"], "sl", value=95)
    second = control(portfolio, position["id"], "target_2", value=125)
    portfolio.tick({("NSE_FNO", 1): {"price": 104, "timestamp": clock()}})
    assert portfolio.snapshot()["positions"][0]["control_version"] == 0
    assert confirm(portfolio, first)["ok"]
    assert confirm(portfolio, second)["reason"] == "version_mismatch"
    assert control(portfolio, position["id"], "refresh", version=999)["ok"]


@pytest.mark.parametrize("action", ["target_1", "target_2"])
def test_same_valued_default_target_becoming_custom_invalidates_preview(engine, action):
    portfolio, _, _ = engine
    position = opened(engine)
    outstanding = control(portfolio, position["id"], "sl", value=95)
    preview = control(portfolio, position["id"], action, value=position[action])
    committed = confirm(portfolio, preview)
    assert committed["ok"] and committed["position"][action] == position[action]
    assert committed["position"][f"custom_{action}"]
    assert committed["position"]["control_version"] == 1
    assert confirm(portfolio, outstanding)["reason"] == "version_mismatch"


def test_freshness_and_confirm_trigger_stop_before_edit(engine):
    portfolio, clock, _ = engine
    position = opened(engine)
    preview = control(portfolio, position["id"], "sl", value=95)
    clock.now += 11
    assert confirm(portfolio, preview)["reason"] == "no_fresh_quote"
    assert control(portfolio, position["id"], "target_2")["reason"] == "no_fresh_quote"
    evidence(portfolio, clock, 89)
    rejected = confirm(portfolio, preview)
    assert rejected["reason"] == "position_exiting"
    assert rejected["position"]["status"] == "closed"
    assert rejected["position"]["exit_reason"] == "initial_stop"
    assert len(portfolio.snapshot()["executions"]) == 2


@pytest.mark.parametrize("reason", ["risk", "cutoff", "disabled", "closed"])
def test_start_and_confirm_revalidate_runtime_state(engine, reason):
    portfolio, clock, _ = engine
    position = opened(engine)
    preview = control(portfolio, position["id"], "sl", value=95)
    if reason == "risk":
        with portfolio._transaction("limits") as db:
            db.execute("UPDATE paper_account SET profit_limit=100 WHERE id=1")
        evidence(portfolio, clock, 105)
    elif reason == "cutoff":
        clock.now = datetime(2026, 10, 5, 15, 25, tzinfo=ZoneInfo("Asia/Kolkata")).timestamp()
        evidence(portfolio, clock, 100)
    elif reason == "disabled":
        portfolio.action("mode", enabled=False)
    else:
        portfolio.calendar = lambda *_: False
    assert not confirm(portfolio, preview)["ok"]
    assert not control(portfolio, position["id"], "sl")["ok"]
    if reason in ("risk", "cutoff"):
        assert portfolio.snapshot()["closed_positions"][0]["status"] == "closed"


def test_trailing_off_freezes_and_t1_still_lifts_stop(engine):
    portfolio, clock, events = engine
    position = opened(engine)
    assert confirm(portfolio, control(portfolio, position["id"], "trailing", value=False))["ok"]
    candle = {"timestamp": clock() - 300, "end_timestamp": clock(), "low": 107}
    portfolio.tick({("NSE_FNO", 1): {"price": 111, "timestamp": clock()}},
                   {("NSE_FNO", 1): [candle]})
    position = portfolio.snapshot()["positions"][0]
    assert position["stop_loss"] == 100 and position["t1_reached"]
    with portfolio._transaction("pending") as db:
        position["pending_trailing_low"] = 115
        portfolio._save_position(db, position)
    enabled = confirm(portfolio, control(portfolio, position["id"], "trailing", value=True))
    assert enabled["ok"] and "pending_trailing_low" not in enabled["position"]
    portfolio.tick({("NSE_FNO", 1): {"price": 111, "timestamp": clock()}},
                   {("NSE_FNO", 1): [candle]})
    position = portfolio.snapshot()["positions"][0]
    assert position["stop_loss"] == 107
    event = next(e for e in events if e["type"] == "stop_tightened")
    assert event["entry_price"] == 100 and event["control_version"] == position["control_version"]


def test_trailing_ignores_incomplete_candle(engine):
    portfolio, clock, _ = engine
    position = opened(engine)
    portfolio.tick({("NSE_FNO", 1): {"price": 109, "timestamp": clock()}}, {
        ("NSE_FNO", 1): [{"timestamp": clock() - 100, "low": 108, "completed": False}],
    })
    assert portfolio.snapshot()["positions"][0]["stop_loss"] == position["stop_loss"]


@pytest.mark.parametrize("trigger,price", [("stop", 89), ("target", 121), ("risk", 105), ("cutoff", 100)])
def test_protection_overrides_limit_without_wait(engine, trigger, price):
    portfolio, clock, _ = engine
    position = opened(engine)
    result = confirm(portfolio, control(portfolio, position["id"], "exit_limit", value=130))
    assert result["ok"] and result["position"]["status"] == "open"
    if trigger == "risk":
        with portfolio._transaction("limits") as db:
            db.execute("UPDATE paper_account SET profit_limit=100 WHERE id=1")
    if trigger == "cutoff":
        clock.now = datetime(2026, 10, 5, 15, 25, tzinfo=ZoneInfo("Asia/Kolkata")).timestamp()
    portfolio.tick({("NSE_FNO", 1): {"price": price, "timestamp": clock()}})
    snapshot = portfolio.snapshot()
    assert snapshot["closed_positions"][0]["status"] == "closed"
    assert snapshot["exit_orders"][0]["order_type"] == "MARKET"
    assert len(snapshot["exit_orders"]) == 1 and len(snapshot["executions"]) == 2


def test_limit_fallback_and_atomic_duplicate_confirmation(engine):
    portfolio, clock, _ = engine
    position = opened(engine)
    preview = control(portfolio, position["id"], "exit_limit", value=105)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: confirm(portfolio, preview), range(2)))
    assert sum(result["ok"] for result in results) == 1
    clock.now += 5
    portfolio.tick({("NSE_FNO", 1): {"price": 100, "timestamp": clock()}})
    snapshot = portfolio.snapshot()
    assert snapshot["closed_positions"][0]["exit_price"] == 99
    assert len(snapshot["exit_orders"]) == 1 and len(snapshot["executions"]) == 2


def test_snapshot_identity_audits_and_message_persistence(engine):
    portfolio, clock, _ = engine
    first, second = opened(engine), opened(engine, 2)
    preview = control(portfolio, second["id"], "sl", value=95)
    assert confirm(portfolio, preview)["position"]["id"] == second["id"]
    assert portfolio.snapshot()["positions"][0]["stop_loss"] == first["stop_loss"]
    control(portfolio, second["id"], "sl", value=94)
    portfolio.audit_control_attempt("alice", {
        "action": "telegram_control", "position_id": second["id"],
        "old": None, "new": None, "outcome": "rejected", "raw_secret": "not-stored",
    })
    with portfolio._connection() as db:
        audits = [dict(row) for row in db.execute("SELECT * FROM paper_audit WHERE action='position_control'")]
    assert all(audit["actor"] == "alice" for audit in audits)
    assert any(json.loads(audit["data"])["outcome"] == "committed" for audit in audits)
    assert any(json.loads(audit["data"])["outcome"] == "sl_cannot_loosen" for audit in audits)
    rejected = next(json.loads(audit["data"]) for audit in audits
                    if json.loads(audit["data"])["outcome"] == "sl_cannot_loosen")
    assert rejected["old"] == 95 and rejected["new"] == 94
    assert "raw_secret" not in audits[-1]["data"]
    portfolio.remember_control_message(second["id"], -100, 7)
    portfolio.remember_control_message(second["id"], -100, 7)
    portfolio = PaperPortfolio(portfolio.db_path, clock=clock)
    assert len(portfolio.control_messages(second["id"])) == 1
    portfolio.disable_control_messages(second["id"])
    assert not portfolio.control_messages(second["id"])


def test_digit_buffer_bounds_and_direct_input(engine):
    portfolio, _, _ = engine
    position = opened(engine)
    start = control(portfolio, position["id"], "sl")
    for _ in range(16):
        assert control(portfolio, None, "digit", token=start["token"], value="9")["ok"]
    assert control(portfolio, None, "digit", token=start["token"], value="9")["reason"] == "input_too_long"
    assert control(portfolio, None, "digit", token=start["token"], value="back")["input"] == "9" * 15
    for value in ("", "12", "x"):
        assert not control(portfolio, None, "digit", token=start["token"], value=value)["ok"]
    preview = control(portfolio, None, "input", token=start["token"], value=95)
    assert preview["stage"] == "confirm" and confirm(portfolio, preview)["ok"]


@pytest.mark.parametrize("action", ["input", "preview"])
def test_invalid_price_text_is_not_exposed_or_audited(engine, action):
    portfolio, _, _ = engine
    position = opened(engine)
    start = control(portfolio, position["id"], "sl")
    result = control(portfolio, None, action, token=start["token"], value="private-token")
    assert result["reason"] == "invalid_price"
    assert "private-token" not in json.dumps(result)
    with portfolio._connection() as db:
        records = [row["data"] for row in db.execute("SELECT data FROM paper_audit")]
        records += [row["data"] for row in db.execute("SELECT data FROM paper_position_controls")]
    assert all("private-token" not in record for record in records)


@pytest.mark.parametrize("exception", [ValueError, TypeError, InvalidOperation])
@pytest.mark.parametrize("action", ["input", "confirm"])
def test_numeric_exception_details_never_escape_controls(engine, monkeypatch, exception, action):
    import paper_portfolio

    portfolio, _, _ = engine
    position = opened(engine)
    start = control(portfolio, position["id"], "sl",
                    **({"value": 95} if action == "confirm" else {}))
    original = paper_portfolio._number
    def failing_number(value):
        if value == 95:
            raise exception("private-token secret stack trace")
        return original(value)
    monkeypatch.setattr(paper_portfolio, "_number", failing_number)
    result = control(portfolio, None, action, token=start["token"],
                     **({"value": 95} if action == "input" else {}))
    assert result["reason"] == "invalid_price"
    assert "private-token" not in json.dumps(result)
    with portfolio._connection() as db:
        records = [row["data"] for row in db.execute("SELECT data FROM paper_audit")]
    assert all("private-token" not in record for record in records)


def test_real_telegram_command_input_and_confirm(engine, monkeypatch):
    monkeypatch.delenv("CHANNEL_TRADE_CONTROL_ID", raising=False)
    portfolio, _, _ = engine
    position = opened(engine)
    handler = Mock()
    handler._get_bot_for_category.return_value = ("test-token", -100)
    handler._http_session.return_value.post.return_value.status_code = 200
    handler._http_session.return_value.post.return_value.json.return_value = {
        "ok": True, "result": {"message_id": 7},
    }
    adapter = PaperTelegramControl(portfolio, handler, [42], -100, "a" * 40)
    def send(text, update_id):
        return adapter.handle_update({
            "update_id": update_id, "message": {
                "text": text, "from": {"id": 42}, "chat": {"id": -100}},
        }, "a" * 40)
    assert send(f"/sl {position['id']}", 1)[1] == 200
    with portfolio._connection() as db:
        workflow = json.loads(db.execute("SELECT data FROM paper_position_controls").fetchone()["data"])
    assert send(f"/input {workflow['token']} 95", 2)[1] == 200
    assert send(f"/confirm {workflow['token']}", 3)[1] == 200
    assert portfolio.snapshot()["positions"][0]["stop_loss"] == 95


def telegram(portfolio, chat_id=-100, handler=None):
    handler = handler or Mock()
    handler._get_bot_for_category.return_value = ("test-token", chat_id)
    handler._http_session.return_value.post.return_value.status_code = 200
    handler._http_session.return_value.post.return_value.json.return_value = {
        "ok": True, "result": {"message_id": 7},
    }
    adapter = PaperTelegramControl(portfolio, handler, [42, 43], chat_id, SECRET)
    portfolio.notify = adapter.send_event
    return adapter


def channel_click(adapter, data, update_id, actor=42, chat_id=None):
    return adapter.handle_update({
        "update_id": update_id, "callback_query": {
            "id": f"channel-{update_id}", "from": {"id": actor}, "data": data,
            "message": {"message_id": 7, "chat": {"id": chat_id or adapter.chat_id,
                                                "type": "channel"},
                        "sender_chat": {"id": adapter.chat_id},
                        "from": {"id": 999, "is_bot": True}},
        },
    }, SECRET)


def position_button(adapter, position, code):
    return next(button["callback_data"]
                for row in adapter._position_buttons(position)["inline_keyboard"]
                for button in row if button["callback_data"].endswith(":" + code))


def test_channel_open_buttons_keypad_bound_restart_and_consumed(engine):
    portfolio, clock, _ = engine
    position = opened(engine)
    adapter = telegram(portfolio)
    adapter._send_position(position)
    assert portfolio.control_messages(position["id"])
    assert channel_click(adapter, position_button(adapter, position, "sl"), 1, actor=99)[1] == 403
    body, status = channel_click(adapter, position_button(adapter, position, "sl"), 2)
    assert status == 200 and body["result"]["stage"] == "input"
    token = body["result"]["token"]
    assert channel_click(adapter, f"pc:{token}:d9", 3)[0]["result"]["input"] == "9"
    assert channel_click(adapter, f"pc:{token}:d5", 4)[0]["result"]["input"] == "95"
    assert channel_click(adapter, f"pc:{token}:preview", 5, actor=43)[0]["result"]["reason"] == "actor_mismatch"
    other = telegram(PaperPortfolio(portfolio.db_path, clock=clock), chat_id=-101)
    assert channel_click(other, f"pc:{token}:preview", 6)[0]["result"]["reason"] == "chat_mismatch"
    portfolio = PaperPortfolio(portfolio.db_path, clock=clock)
    adapter = telegram(portfolio, handler=adapter.telegram_handler)
    preview = channel_click(adapter, f"pc:{token}:preview", 7)[0]["result"]
    assert preview["value"] == 95 and preview["stage"] == "confirm"
    assert portfolio.snapshot()["positions"][0]["stop_loss"] == 90
    committed = channel_click(adapter, f"pc:{token}:confirm", 8)[0]["result"]
    assert committed["stage"] == "committed" and committed["position"]["stop_loss"] == 95
    assert channel_click(adapter, f"pc:{token}:confirm", 8)[0]["duplicate"]
    assert channel_click(adapter, f"pc:{token}:confirm", 9)[0]["result"]["reason"] == "workflow_consumed"
    assert len(portfolio.snapshot()["executions"]) == 1
    with portfolio._connection() as db:
        audit = json.loads(db.execute(
            "SELECT data FROM paper_audit WHERE actor='42' AND action='position_control' "
            "ORDER BY id DESC LIMIT 1").fetchone()["data"])
    assert audit["outcome"] == "workflow_consumed"


def test_channel_market_exit_requires_confirm_and_closed_refresh_rejected(engine):
    portfolio, _, _ = engine
    position = opened(engine)
    adapter = telegram(portfolio)
    preview = channel_click(adapter, position_button(adapter, position, "em"), 1)[0]["result"]
    assert preview["stage"] == "confirm"
    assert len(portfolio.snapshot()["executions"]) == 1
    assert portfolio.snapshot()["positions"][0]["status"] == "open"
    committed = channel_click(adapter, f"pc:{preview['token']}:confirm", 2)[0]["result"]
    assert committed["position"]["status"] == "closed"
    assert len(portfolio.snapshot()["executions"]) == 2
    refreshed = channel_click(adapter, position_button(adapter, position, "rf"), 3)[0]["result"]
    assert not refreshed["ok"] and refreshed["reason"] == "position_closed"
    assert not portfolio.control_messages(position["id"])


def test_channel_limit_exit_keypad_fallback(engine):
    portfolio, clock, _ = engine
    position = opened(engine)
    adapter = telegram(portfolio)
    start = channel_click(adapter, position_button(adapter, position, "el"), 1)[0]["result"]
    token = start["token"]
    for index, char in enumerate("105", 2):
        assert channel_click(adapter, f"pc:{token}:d{char}", index)[0]["result"]["ok"]
    assert channel_click(adapter, f"pc:{token}:preview", 5)[0]["result"]["value"] == 105
    result = channel_click(adapter, f"pc:{token}:confirm", 6)[0]["result"]
    assert result["stage"] == "committed" and result["position"]["status"] == "open"
    clock.now += 4
    portfolio.tick({("NSE_FNO", 1): {"price": 100, "timestamp": clock()}})
    assert len(portfolio.snapshot()["executions"]) == 1
    clock.now += 1
    portfolio.tick({("NSE_FNO", 1): {"price": 100, "timestamp": clock()}})
    assert portfolio.snapshot()["closed_positions"][0]["exit_price"] == 99
    assert len(portfolio.snapshot()["exit_orders"]) == 1


@pytest.mark.parametrize("trigger,price", [("stop", 89), ("target", 121), ("risk", 105), ("cutoff", 100)])
def test_channel_pending_workflow_never_blocks_protection(engine, trigger, price):
    portfolio, clock, _ = engine
    position = opened(engine)
    adapter = telegram(portfolio)
    start = channel_click(adapter, position_button(adapter, position, "sl"), 1)[0]["result"]
    if trigger == "risk":
        with portfolio._transaction("limits") as db:
            db.execute("UPDATE paper_account SET profit_limit=100 WHERE id=1")
    if trigger == "cutoff":
        clock.now = datetime(2026, 10, 5, 15, 25, tzinfo=ZoneInfo("Asia/Kolkata")).timestamp()
    portfolio.tick({("NSE_FNO", 1): {"price": price, "timestamp": clock()}})
    snapshot = portfolio.snapshot()
    assert snapshot["closed_positions"][0]["status"] == "closed"
    assert len(snapshot["executions"]) == 2
    assert not portfolio.control_messages(position["id"])
    assert not channel_click(adapter, f"pc:{start['token']}:confirm", 2)[0]["result"]["ok"]


def test_channel_custom_targets_t1_breakeven_and_complete_event(engine):
    portfolio, clock, _ = engine
    position = opened(engine)
    adapter = telegram(portfolio)
    start = channel_click(adapter, position_button(adapter, position, "t1"), 1)[0]["result"]
    preview = portfolio.position_control(None, "input", 42, -100, token=start["token"], value=108)
    result = channel_click(adapter, f"pc:{preview['token']}:confirm", 2)[0]["result"]
    assert result["position"]["custom_target_1"] and result["position"]["target_1"] == 108
    portfolio.tick({("NSE_FNO", 1): {"price": 109, "timestamp": clock()}})
    position = portfolio.snapshot()["positions"][0]
    assert position["stop_loss"] == 100 and position["t1_reached"]
    rejected = channel_click(adapter, position_button(adapter, position, "t1"), 3)[0]["result"]
    assert rejected["reason"] == "target_1_already_reached"
    start = channel_click(adapter, position_button(adapter, position, "t2"), 4)[0]["result"]
    preview = portfolio.position_control(None, "input", 42, -100, token=start["token"], value=125)
    result = channel_click(adapter, f"pc:{preview['token']}:confirm", 5)[0]["result"]
    assert result["position"]["custom_target_2"] and result["position"]["target_2"] == 125
    with portfolio._connection() as db:
        events = [json.loads(row["data"]) for row in db.execute("SELECT data FROM paper_outbox")]
    event = next(event for event in events if event["type"] == "target_1")
    assert event["custom_target_1"] and event["entry_price"] == 100 and event["stop_loss"] == 100
    assert event["mark_timestamp"] == clock()
    posts = adapter.telegram_handler._http_session.return_value.post.call_args_list
    assert any("#TARGET1" in call.kwargs["json"].get("text", "") for call in posts)
