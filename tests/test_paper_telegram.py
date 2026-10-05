from unittest.mock import Mock
from pathlib import Path
from uuid import uuid4
from datetime import datetime
from zoneinfo import ZoneInfo
import sqlite3

import pytest

from paper_telegram import PaperTelegramControl, format_portfolio


SECRET = "a" * 40


class Engine:
    def __init__(self):
        self.updates = set()
        self.action = Mock(return_value={"ok": True})
        self.snapshot = Mock(return_value={"pending": [], "trades": []})

    def record_update(self, update_id):
        if update_id in self.updates:
            return False
        self.updates.add(update_id)
        return True


@pytest.fixture
def control(monkeypatch):
    monkeypatch.delenv("CHANNEL_TRADE_CONTROL_ID", raising=False)
    handler = Mock()
    handler._get_bot_for_category.return_value = ("private-token", -100)
    handler._http_session.return_value.post.return_value.status_code = 200
    handler._http_session.return_value.post.return_value.json.return_value = {"ok": True}
    return PaperTelegramControl(Engine(), handler, [42], -100, SECRET)


def update(text="/portfolio", update_id=1):
    return {"update_id": update_id,
            "message": {"text": text, "from": {"id": 42},
                        "chat": {"id": -100}}}


def callback(data="paper:s1:approve_limit", update_id=1, callback_id="cb1"):
    return {"update_id": update_id, "callback_query": {
        "id": callback_id, "from": {"id": 42}, "data": data,
        "message": {"chat": {"id": -100}, "from": {"id": 999, "is_bot": True}},
    }}


@pytest.mark.parametrize("secret", [None, "", "wrong", 1, "é" * 40])
def test_reject_bad_secret(control, secret):
    assert control.handle_update(update(), secret)[1] == 403
    assert not control.engine.updates
    control.engine.snapshot.assert_not_called()


def test_secret_uses_constant_time_comparison(control, monkeypatch):
    compare = Mock(return_value=True)
    monkeypatch.setattr("paper_telegram.hmac.compare_digest", compare)
    assert control.handle_update(update(), SECRET)[1] == 200
    compare.assert_called_once_with(SECRET.encode(), SECRET.encode())


@pytest.mark.parametrize("item", [None, [], {}, {"update_id": True},
                                  {"update_id": 1, "callback_query": []}])
def test_invalid_update(control, item):
    assert control.handle_update(item, SECRET)[1] == 400
    control.engine.action.assert_not_called()


def test_missing_configuration_denies(monkeypatch, control):
    monkeypatch.delenv("PAPER_TELEGRAM_ALLOWED_USER_IDS", raising=False)
    monkeypatch.delenv("PAPER_TELEGRAM_WEBHOOK_SECRET", raising=False)
    locked = PaperTelegramControl(control.engine, control.telegram_handler)
    assert locked.allowed_user_ids == set()
    assert locked.handle_update(update(), SECRET)[1] == 403
    locked.webhook_secret = "x" * 31
    assert locked.handle_update(update(), "x" * 31)[1] == 403


def test_missing_chat_denies_even_with_valid_secret_and_allowlist(monkeypatch, control):
    monkeypatch.delenv("CHANNEL_TRADE_CONTROL_ID", raising=False)
    locked = PaperTelegramControl(control.engine, control.telegram_handler,
                                  allowed_user_ids=[42], webhook_secret=SECRET)
    assert locked.chat_id is None
    assert locked.handle_update(update(), SECRET)[1] == 403
    assert not locked.send_event({"type": "order_awaiting_approval", "id": "s1"})
    control.telegram_handler._http_session.return_value.post.assert_not_called()


def test_environment_configuration(monkeypatch, control):
    monkeypatch.setenv("PAPER_TELEGRAM_ALLOWED_USER_IDS", "42, 99 invalid -5 1.5")
    monkeypatch.setenv("CHANNEL_TRADE_CONTROL_ID", "-200")
    monkeypatch.setenv("PAPER_TELEGRAM_WEBHOOK_SECRET", SECRET)
    configured = PaperTelegramControl(control.engine, control.telegram_handler)
    assert configured.allowed_user_ids == {42, 99}
    assert configured.chat_id == -200
    assert configured.handle_update(update(), SECRET)[1] == 403


@pytest.mark.parametrize("is_callback", [True, False])
@pytest.mark.parametrize("violation", ["user", "chat", "bot", "forward", "sender_chat"])
def test_authorization_before_dedup(control, is_callback, violation):
    item = callback() if is_callback else update("/approve s1")
    source = item["callback_query"] if is_callback else item["message"]
    message = source["message"] if is_callback else source
    if violation == "user":
        source["from"]["id"] = 7
    elif violation == "chat":
        message["chat"]["id"] = -200
    elif violation == "bot":
        source["from"]["is_bot"] = True
    elif violation == "forward":
        message["forward_origin"] = {}
    else:
        message["sender_chat"] = {"id": -100}
        if is_callback:
            assert control.handle_update(item, SECRET)[1] == 200
            control.engine.action.assert_called_once()
            return
    assert control.handle_update(item, SECRET)[1] == 403
    assert not control.engine.updates
    control.engine.action.assert_not_called()


def test_authorized_callback_on_channel_post_with_sender_chat(control):
    item = callback()
    item["callback_query"]["message"].update(
        chat={"id": -100, "type": "channel"},
        sender_chat={"id": -100, "type": "channel", "title": "Paper control"},
    )
    assert control.handle_update(item, SECRET)[1] == 200
    control.engine.action.assert_called_once_with(
        "approve_limit", request_id="s1", actor=42,
        telegram_request_id="telegram:callback:cb1")


def test_forwarded_channel_callback_still_denied(control):
    item = callback()
    item["callback_query"]["message"].update(
        sender_chat={"id": -100, "type": "channel"},
        forward_origin={"type": "channel", "chat": {"id": -200}},
    )
    assert control.handle_update(item, SECRET)[1] == 403
    control.engine.action.assert_not_called()
    assert not control.engine.updates


@pytest.mark.parametrize("text,action,kwargs", [
    ("/mode approval on", "mode", {"enabled": True, "approval_required": True}),
    ("/mode approval off", "mode", {"enabled": True, "approval_required": False}),
    ("/approve s1", "approve_limit", {"request_id": "s1"}),
    ("/approve s1 market", "approve_market", {"request_id": "s1"}),
    ("/approve s1 limit", "approve_limit", {"request_id": "s1"}),
    ("/modify s1 100 90", "modify", {"request_id": "s1", "limit_price": 100,
                                  "stop_loss": 90}),
    ("/modify s1 100", "modify", {"request_id": "s1", "limit_price": 100}),
    ("/reject s1", "reject", {"request_id": "s1"}),
    ("/close p1", "close", {"request_id": "p1"}),
    ("/close p1 market", "close", {"request_id": "p1", "order_type": "MARKET"}),
    ("/close p1 limit 110", "close", {"request_id": "p1", "order_type": "LIMIT",
                                    "limit_price": 110}),
    ("/limits 500 200", "limits", {"profit_limit": 500, "loss_limit": 200}),
])
def test_commands(control, text, action, kwargs):
    assert control.handle_update(update(text), SECRET)[1] == 200
    expected = dict(kwargs)
    target_id = expected.pop("request_id", None)
    control.engine.action.assert_called_once_with(
        action, request_id=target_id, actor=42,
        telegram_request_id="telegram:update:1", **expected)


@pytest.mark.parametrize("command", ["/pending", "/portfolio", "/trades",
                                      "/orders", "/positions"])
def test_snapshot_commands(control, command):
    assert control.handle_update(update(command), SECRET)[1] == 200
    control.engine.snapshot.assert_called_once()
    control.engine.action.assert_not_called()


def test_dated_trade_report(control):
    body, status = control.handle_update(update("/trades 2026-10-01"), SECRET)
    assert status == 200
    control.engine.snapshot.assert_called_once_with(day="2026-10-01")
    assert body["result"]["trades"] == []


@pytest.mark.parametrize("day", ["20261001", "2026-02-30", "yesterday", "2026-1-1"])
def test_invalid_report_date(control, day):
    assert control.handle_update(update(f"/trades {day}"), SECRET)[1] == 400
    control.engine.snapshot.assert_not_called()


def test_positions_report_has_account_daily_and_positions_only(control):
    control.engine.snapshot.return_value = {
        "account": {"equity": 500000}, "daily": {"pnl": 0},
        "positions": [{"id": "p1"}], "orders": [{"id": "s1"}]}
    body, status = control.handle_update(update("/positions"), SECRET)
    assert status == 200
    assert set(body["result"]) == {"account", "daily", "positions"}


def test_approval_toggle_preserves_disabled_portfolio(control):
    control.engine.snapshot.return_value = {"account": {"enabled": False}}
    assert control.handle_update(update("/mode approval on"), SECRET)[1] == 200
    control.engine.action.assert_called_once_with(
        "mode", request_id=None, actor=42, telegram_request_id="telegram:update:1",
        enabled=False, approval_required=True)


@pytest.mark.parametrize("text", ["/mode on", "/mode approval maybe",
                                 "/approve s1 live", "/modify s1 nan",
                                 "/modify s1 inf", "/modify s1 -1",
                                 "/limits 10 0", "/close", "/close p1 limit",
                                 "/close p1 limit nan", "/close p1 limit 0",
                                 "/close p1 limit -1", "/close p1 market 100",
                                 "/close p1 invalid", "/unknown"])
def test_bad_commands(control, text):
    assert control.handle_update(update(text), SECRET)[1] == 400
    control.engine.action.assert_not_called()


def test_persistent_dedup_callbacks_and_updates(control):
    assert control.handle_update(callback(), SECRET)[1] == 200
    assert control.handle_update(callback(), SECRET)[0]["duplicate"]
    assert control.handle_update(callback(update_id=2), SECRET)[0]["duplicate"]
    control.engine.action.assert_called_once_with(
        "approve_limit", request_id="s1", actor=42,
        telegram_request_id="telegram:callback:cb1")
    other = PaperTelegramControl(control.engine, control.telegram_handler, [42], -100, SECRET)
    assert other.handle_update(callback(update_id=3), SECRET)[0]["duplicate"]


def test_modify_callback_only_instructs(control):
    body, status = control.handle_update(callback("paper:s1:modify"), SECRET)
    assert status == 200
    assert "/modify s1" in body["result"]["instruction"]
    control.engine.action.assert_not_called()


def test_inline_callback_without_configured_chat_denied(control):
    item = callback()
    del item["callback_query"]["message"]
    assert control.handle_update(item, SECRET)[1] == 403
    control.engine.action.assert_not_called()


def test_update_and_callback_id_namespaces_do_not_collide(control):
    assert control.handle_update(update("/reject s2"), SECRET)[1] == 200
    assert control.handle_update(callback(update_id=2, callback_id="1"), SECRET)[1] == 200
    assert control.engine.action.call_count == 2


@pytest.mark.parametrize("data", ["paper:s1:close", "live:s1:reject", "paper::reject",
                                 "paper:" + "x" * 70 + ":reject", 5])
def test_bad_callbacks(control, data):
    assert control.handle_update(callback(data), SECRET)[1] == 400
    control.engine.action.assert_not_called()


def test_stale_callback_engine_result(control):
    control.engine.action.return_value = {"ok": False, "error": "Already rejected"}
    body, status = control.handle_update(callback(), SECRET)
    assert status == 200
    assert body["result"]["ok"] is False


def test_pending_buttons_and_transport(control):
    assert control.send_event({"type": "order_awaiting_approval", "payload": {
        "id": "s1", "symbol": "<unsafe>", "reason": "a & b"}})
    call = control.telegram_handler._http_session.return_value.post.call_args
    assert call.args[0].endswith("/sendMessage")
    assert call.kwargs["timeout"] == 10
    payload = call.kwargs["json"]
    assert payload["chat_id"] == -100
    assert "&lt;unsafe&gt;" in payload["text"]
    assert "a &amp; b" in payload["text"]
    buttons = payload["reply_markup"]["inline_keyboard"]
    assert [b["text"] for row in buttons for b in row] == [
        "Approve Limit", "Approve Market", "Modify", "Reject"]
    assert all(len(b["callback_data"].encode()) <= 64 for row in buttons for b in row)
    assert control.last_delivery["success"]
    assert control.last_delivery["duration_ms"] >= 0


def test_oversize_callback_identifier(control):
    assert control._buttons("é" * 40) is None


@pytest.mark.parametrize("kind,fields,points,inr", [
    ("entry_filled", {"entry_price": 100}, "0.0", "0"),
    ("exit_filled", {"entry_price": 100, "exit_price": 110, "realized_pnl": 500},
     "10.0", "500"),
])
def test_engine_fill_messages_include_points_and_inr(control, kind, fields, points, inr):
    event = {"type": kind, "id": "p1", **fields}
    assert control.send_event(event)
    text = control.telegram_handler._http_session.return_value.post.call_args.kwargs["json"]["text"]
    assert f"P&amp;L points: {points}" in text
    assert f"P&amp;L INR: {inr}" in text
    assert "pnl_points" not in event


@pytest.mark.parametrize("failure", ["http", "json", "exception", "routing"])
def test_delivery_failure_retryable_no_secret_logs(control, failure, caplog):
    response = control.telegram_handler._http_session.return_value.post.return_value
    if failure == "http":
        response.status_code = 500
    elif failure == "json":
        response.json.return_value = {"ok": False}
    elif failure == "routing":
        control.telegram_handler._get_bot_for_category.return_value = ("private-token", -200)
    else:
        control.telegram_handler._http_session.return_value.post.side_effect = RuntimeError(
            "private-token secret-url")
    assert not control.send_event({"type": "closed", "pnl_points": 10, "pnl_inr": 500})
    assert control.last_delivery["success"] is False
    assert "private-token" not in caplog.text


def test_full_trade_log_safe_chunking():
    snapshot = {"marks": {"<symbol>": 100}, "trades": [
        {"id": i, "reason": "<&>" * 600, "pnl_inr": i} for i in range(20)]}
    chunks = format_portfolio(snapshot)
    assert len(chunks) > 20
    assert all(len(chunk) < 4096 for chunk in chunks)
    assert "&lt;symbol&gt;" in chunks[0]
    assert "trades: 20." in "\n".join(chunks)
    assert all("<" not in chunk for chunk in chunks)


def test_internal_failure_is_redacted(control):
    control.engine.record_update = Mock(side_effect=RuntimeError("sensitive"))
    body, status = control.handle_update(update(), SECRET)
    assert status == 503
    assert "sensitive" not in str(body)


def test_value_error_details_are_never_returned_sent_or_logged(control, caplog):
    sensitive = "private-token stack trace internal database path"
    control.engine.record_update = Mock(side_effect=ValueError(sensitive))
    body, status = control.handle_update(update(), SECRET)
    assert status == 400
    assert body == {"ok": False, "error": "Invalid paper command or request"}
    payload = control.telegram_handler._http_session.return_value.post.call_args.kwargs["json"]
    assert payload["text"] == "Invalid paper command or request"
    assert sensitive not in caplog.text


def test_partial_event_failure_returns_false_for_outbox_retry(control):
    response = Mock(status_code=200)
    response.json.return_value = {"ok": True}
    control.telegram_handler._http_session.return_value.post.side_effect = [
        response, RuntimeError("transport failure")]
    assert not control.send_event({"type": "portfolio", "data": {
        "trades": [{"reason": "<&>" * 1000} for _ in range(3)]}})
    assert control.last_delivery["success"] is False


def test_engine_event_and_trade_snapshot_schema(control):
    assert control.send_event({"event_id": "event1", "type": "order_awaiting_approval",
                               "id": "s1", "status": "awaiting_approval"})
    payload = control.telegram_handler._http_session.return_value.post.call_args.kwargs["json"]
    assert payload["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == (
        "paper:s1:approve_limit")
    assert control.last_delivery["event_id"] == "event1"
    control.engine.snapshot.return_value = {
        "executions": [{"id": "execution-last", "pnl_points": 10}],
        "closed_positions": [{"id": "position-last", "pnl_inr": 500}]}
    assert control.handle_update(update("/trades"), SECRET)[1] == 200
    payload = control.telegram_handler._http_session.return_value.post.call_args.kwargs["json"]
    assert "execution-last" in payload["text"]
    assert "position-last" in payload["text"]
    assert "P&amp;L points" in payload["text"]
    assert "P&amp;L INR" in payload["text"]


def test_pending_exit_orders_are_information_only(control):
    control.engine.snapshot.return_value = {
        "pending": [], "pending_exit_orders": [
            {"id": "exit1", "position_id": "p1", "order_type": "LIMIT",
             "limit_price": 110, "status": "pending"}]}
    body, status = control.handle_update(update("/pending"), SECRET)
    assert status == 200
    assert body["result"]["pending_exit_orders"][0]["id"] == "exit1"
    payload = control.telegram_handler._http_session.return_value.post.call_args.kwargs["json"]
    assert "exit1" in payload["text"]
    assert "no approval" in payload["text"]
    assert "reply_markup" not in payload
    control.engine.action.assert_not_called()


@pytest.mark.parametrize("kind", ["exit_order_pending", "exit_pending", "pending"])
def test_exit_pending_event_never_has_approval_buttons(control, kind):
    assert control.send_event({"type": kind, "id": "exit1", "side": "SELL"})
    payload = control.telegram_handler._http_session.return_value.post.call_args.kwargs["json"]
    assert "reply_markup" not in payload


def test_auto_entry_pending_event_never_requests_approval(control):
    assert control.send_event({"type": "order_pending", "id": "entry1",
                               "side": "BUY", "approval_required": False,
                               "status": "pending"})
    payload = control.telegram_handler._http_session.return_value.post.call_args.kwargs["json"]
    assert "reply_markup" not in payload


def test_explicit_approval_event_for_sell_never_has_approval_buttons(control):
    assert control.send_event({"type": "approval_request", "id": "exit1", "side": "SELL"})
    payload = control.telegram_handler._http_session.return_value.post.call_args.kwargs["json"]
    assert "reply_markup" not in payload


def test_pending_command_only_buttons_for_awaiting_entry(control):
    control.engine.snapshot.return_value = {"pending": [
        {"id": "entry1", "status": "awaiting_approval", "side": "BUY"},
        {"id": "entry2", "status": "pending", "side": "BUY", "approval_required": False},
    ], "pending_exit_orders": [{"id": "exit1", "side": "SELL", "status": "pending"}]}
    assert control.handle_update(update("/pending"), SECRET)[1] == 200
    posts = control.telegram_handler._http_session.return_value.post.call_args_list
    with_buttons = [call.kwargs["json"] for call in posts if "reply_markup" in call.kwargs["json"]]
    assert len(with_buttons) == 1
    assert with_buttons[0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == (
        "paper:entry1:approve_limit")


def test_orders_report_includes_entries_and_exits(control):
    control.engine.snapshot.return_value = {
        "orders": [{"id": "entry1"}], "exit_orders": [{"id": "exit1"}]}
    body, status = control.handle_update(update("/orders"), SECRET)
    assert status == 200
    assert body["result"] == {
        "orders": [{"id": "entry1"}], "exit_orders": [{"id": "exit1"}]}
    payload = control.telegram_handler._http_session.return_value.post.call_args.kwargs["json"]
    assert "entry1" in payload["text"]
    assert "exit1" in payload["text"]


def test_optional_delivery_measurement_hook(control):
    control.engine.record_delivery = Mock()
    assert control.send_event({"event_id": "e1", "type": "entry_filled",
                               "id": "p1", "entry_price": 100})
    control.engine.record_delivery.assert_called_once_with(**control.last_delivery)
    assert control.last_delivery["event_id"] == "e1"


def test_optional_delivery_hook_failure_does_not_mask_sent_message(control):
    control.engine.record_delivery = Mock(side_effect=RuntimeError("private"))
    assert control.send_event({"event_id": "e1", "type": "order_pending", "id": "s1"})


def test_real_engine_persistent_limits_and_cross_instance_dedup(control):
    from paper_portfolio import PaperPortfolio

    path = Path(__file__).parent / f".paper-telegram-test-{uuid4().hex}.sqlite"
    try:
        engine = PaperPortfolio(path, calendar=lambda *_: True)
        first = PaperTelegramControl(engine, control.telegram_handler, [42], -100, SECRET)
        body, status = first.handle_update(update("/limits 900 300"), SECRET)
        assert status == 200
        assert body["result"]["profit_limit"] == 900
        assert body["result"]["loss_limit"] == 300
        reopened = PaperPortfolio(path, calendar=lambda *_: True)
        second = PaperTelegramControl(reopened, control.telegram_handler, [42], -100, SECRET)
        assert second.handle_update(update("/limits 1 1"), SECRET)[0]["duplicate"]
        assert reopened.snapshot()["account"]["profit_limit"] == 900
        enabled = reopened.snapshot()["account"]["enabled"]
        assert second.handle_update(update("/mode approval off", 2), SECRET)[1] == 200
        account = reopened.snapshot()["account"]
        assert account["enabled"] == enabled
        assert not account["approval_required"]
    finally:
        for suffix in ("", "-wal", "-shm"):
            Path(str(path) + suffix).unlink(missing_ok=True)


def test_real_engine_notify_assigned_after_init_and_approval_callback(control):
    from paper_portfolio import PaperPortfolio

    path = Path(__file__).parent / f".paper-telegram-test-{uuid4().hex}.sqlite"
    now = datetime(2026, 10, 5, 10, tzinfo=ZoneInfo("Asia/Kolkata")).timestamp()
    try:
        engine = PaperPortfolio(path, clock=lambda: now)
        webhook = PaperTelegramControl(engine, control.telegram_handler, [42], -100, SECRET)
        engine.notify = webhook.send_event
        order = engine.submit({
            "signal_id": "s1", "entry_price": 100,
            "metadata": {"security_id": 1, "exchange_segment": "NSE_FNO",
                         "option_symbol": "NIFTY-1-CE", "lot_size": 50,
                         "tick_size": .05, "stop_loss": 90, "timeframe": "5min",
                         "category": "index_options"},
        }, {"price": 100, "timestamp": now})
        assert order["status"] == "awaiting_approval"
        posts = control.telegram_handler._http_session.return_value.post.call_args_list
        assert any("reply_markup" in call.kwargs["json"] for call in posts)
        body, status = webhook.handle_update(
            callback(f"paper:{order['id']}:approve_limit"), SECRET)
        assert status == 200
        assert body["result"]["status"] == "filled", body
        assert len(engine.snapshot()["positions"]) == 1
        assert webhook.last_delivery["success"]
        assert engine.snapshot()["deliveries"]
        assert webhook.handle_update(callback(update_id=2), SECRET)[0]["duplicate"]
        assert len(engine.snapshot()["positions"]) == 1
        today, status = webhook.handle_update(update("/trades 2026-10-05", 3), SECRET)
        assert status == 200
        assert len(today["result"]["trades"]) == 1
        prior, status = webhook.handle_update(update("/trades 2026-10-04", 4), SECRET)
        assert status == 200
        assert prior["result"]["trades"] == []
        now += 86400
        engine.tick({})
        archived, status = webhook.handle_update(update("/trades 2026-10-05", 5), SECRET)
        assert status == 200
        assert len(archived["result"]["trades"]) == 1
        current, status = webhook.handle_update(update("/trades", 6), SECRET)
        assert status == 200
        assert current["result"]["trades"] == []
    finally:
        for suffix in ("", "-wal", "-shm"):
            Path(str(path) + suffix).unlink(missing_ok=True)


def test_real_engine_false_delivery_remains_in_outbox_for_retry(control):
    from paper_portfolio import PaperPortfolio

    path = Path(__file__).parent / f".paper-telegram-test-{uuid4().hex}.sqlite"
    now = datetime(2026, 10, 5, 10, tzinfo=ZoneInfo("Asia/Kolkata")).timestamp()
    try:
        engine = PaperPortfolio(path, clock=lambda: now)
        webhook = PaperTelegramControl(engine, control.telegram_handler, [42], -100, SECRET)
        engine.notify = webhook.send_event
        response = control.telegram_handler._http_session.return_value.post.return_value
        response.status_code = 500
        order = engine.submit({
            "signal_id": "s1", "entry_price": 100,
            "metadata": {"security_id": 1, "exchange_segment": "NSE_FNO",
                         "option_symbol": "NIFTY-1-CE", "lot_size": 50,
                         "tick_size": .05, "stop_loss": 90, "timeframe": "5min",
                         "category": "index_options"},
        }, {"price": 100, "timestamp": now})
        assert order["status"] == "awaiting_approval"
        with sqlite3.connect(path) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM paper_outbox WHERE delivered=0").fetchone()[0] == 1
        response.status_code = 200
        engine.tick({})
        with sqlite3.connect(path) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM paper_outbox WHERE delivered=0").fetchone()[0] == 0
        assert any(not attempt["success"] for attempt in engine.snapshot()["deliveries"])
        assert any(attempt["success"] for attempt in engine.snapshot()["deliveries"])
    finally:
        for suffix in ("", "-wal", "-shm"):
            Path(str(path) + suffix).unlink(missing_ok=True)
