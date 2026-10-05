from unittest.mock import MagicMock

import pytest
import requests

from paper_telegram import PaperTelegramController


@pytest.fixture
def controller():
    engine = MagicMock()
    engine.get.return_value = {"id": "T1", "status": "OPEN", "trailing": False}
    engine.modify.return_value = {"id": "T1", "status": "REQUESTED"}
    engine.approve.return_value = {"id": "T1", "status": "OPEN"}
    engine.reject.return_value = {"id": "T1", "status": "REJECTED"}
    engine.exit.return_value = {"id": "T1", "status": "CLOSED"}
    engine.configure.return_value = {"approval_required": True}
    engine.trades.return_value = []
    engine.portfolio.return_value = {"positions": []}
    engine.squareoff.return_value = []
    session = MagicMock()
    session.post.return_value.json.return_value = {"ok": True, "result": []}
    return PaperTelegramController(engine, "test-placeholder", -100, [7], session=session)


def message(text, actor=7, chat=-100, update_id=None):
    update = {"message": {"from": {"id": actor}, "chat": {"id": chat}, "text": text}}
    if update_id is not None:
        update["update_id"] = update_id
    return update


def callback(action, actor=7, chat=-100, update_id=None):
    update = {"callback_query": {
        "id": "cb1", "from": {"id": actor}, "data": f"paper:T1:{action}",
        "message": {"from": {"id": 7}, "chat": {"id": chat}},
    }}
    if update_id is not None:
        update["update_id"] = update_id
    return update


@pytest.mark.parametrize("update", [
    message("/squareoff", actor=8), message("/squareoff", chat=-200),
    message("/squareoff", actor=True), {"channel_post": {"text": "/squareoff"}},
    callback("approve_market", actor=8), callback("approve_market", chat=-200),
    {"callback_query": {"id": "cb1", "data": "paper:T1:approve_market",
                        "message": {"from": {"id": 7}, "chat": {"id": -100}}}},
])
def test_unauthorized_updates_never_reach_engine(controller, update):
    assert controller.handle_update(update) is False
    assert controller.engine.mock_calls == []


@pytest.mark.parametrize("env", ["", "7,invalid", "0", "-7", "7,", "7.0"])
def test_authorization_env_fails_closed(monkeypatch, env):
    monkeypatch.setenv("TELEGRAM_AUTHORIZED_USER_IDS", env)
    engine = MagicMock()
    control = PaperTelegramController(engine, "test-placeholder", -100)
    assert not control.authorized_user_ids
    assert control.handle_update(message("/squareoff")) is False
    assert not engine.mock_calls


def test_authorization_env_parsed(monkeypatch):
    monkeypatch.setenv("TELEGRAM_AUTHORIZED_USER_IDS", "7, 8")
    control = PaperTelegramController(MagicMock(), "test-placeholder", -100)
    assert control.authorized_user_ids == {7, 8}


@pytest.mark.parametrize("text,method,args,kwargs", [
    ("/mode approval off", "configure", ({"approval_required": False},), {"actor": 7}),
    ("/mode approval on", "configure", ({"approval_required": True},), {"actor": 7}),
    ("/risk pnl +30000 -10000", "configure",
     ({"profit_cap": 30000.0, "loss_cap": 10000.0},), {"actor": 7}),
    ("/limits max_open 5 max_day20", "configure",
     ({"max_open_positions": 5, "max_daily_trades": 20},), {"actor": 7}),
    ("/limits max_open 5 max_day 20", "configure",
     ({"max_open_positions": 5, "max_daily_trades": 20},), {"actor": 7}),
    ("/cutoff index15:25 commodity23:00", "configure",
     ({"index_cutoff": "15:25", "commodity_cutoff": "23:00"},), {"actor": 7}),
    ("/approve T1", "approve", ("T1", "limit"), {"actor": 7}),
    ("/approve@paper_bot T1 market", "approve", ("T1", "market"), {"actor": 7}),
    ("/exit T1 limit 100", "exit", ("T1",),
     {"order_type": "limit", "price": 100.0, "actor": 7}),
    ("/exit T1 market", "exit", ("T1",),
     {"order_type": "market", "price": None, "actor": 7}),
    ("/squareoff", "squareoff", (), {"reason": "EMERGENCY", "actor": 7}),
])
def test_command_routing(controller, text, method, args, kwargs):
    assert controller.handle_update(message(text))
    getattr(controller.engine, method).assert_called_once_with(*args, **kwargs)
    payload = controller.session.post.call_args.kwargs["json"]
    assert payload["text"].startswith("Paper ")
    assert "parse_mode" not in payload


def test_pending_includes_requests_and_approved_fills(controller):
    controller.engine.trades.side_effect = [
        [{"id": "T1", "status": "REQUESTED"}],
        [{"id": "T2", "status": "ENTRY_PENDING"}],
    ]
    assert controller.handle_update(message("/pending"))
    assert [call.kwargs["status"] for call in controller.engine.trades.call_args_list] == [
        "REQUESTED", "ENTRY_PENDING"
    ]
    text = controller.session.post.call_args.kwargs["json"]["text"]
    assert "T1" in text and "T2" in text


def test_positions_exposes_ids_details_guidance_and_controls(controller):
    controller.engine.trades.side_effect = [
        [{"id": "T1", "status": "OPEN", "stop_loss": 90}],
        [{"trade_id": "T2", "status": "EXIT_PENDING", "exit_limit": 120}],
    ]
    assert controller.handle_update(message("/positions"))
    controller.engine.portfolio.assert_called_once_with()
    assert [call.kwargs["status"] for call in controller.engine.trades.call_args_list] == [
        "OPEN", "EXIT_PENDING"
    ]
    payload = controller.session.post.call_args.kwargs["json"]
    assert "/modify T1 key=value" in payload["text"]
    assert "/exit T2 limit PRICE" in payload["text"]
    assert '"stop_loss": 90' in payload["text"]
    assert '"exit_limit": 120' in payload["text"]
    rows = payload["reply_markup"]["inline_keyboard"]
    assert rows[0][0]["callback_data"] == "paper:T1:exit_market"
    assert rows[1][1]["callback_data"] == "paper:T2:exit_limit"
    assert rows[1][2]["callback_data"] == "paper:T2:trailing"


@pytest.mark.parametrize("text", [
    "/mode approval maybe", "/risk pnl nan -10", "/risk pnl 10 inf",
    "/risk pnl -1 -10", "/risk pnl 10 10", "/limits max_open 0 max_day20",
    "/limits max_open 1.5 max_day20", "/cutoff index24:25 commodity23:00",
    "/cutoff index15:60 commodity23:00", "/approve T1 live",
    "/exit T1 limit", "/exit T1 market 10", "/exit T1 limit nan",
    "/exit T1 limit -1", "/modify T1 actor=99", "/modify T1 trailing_enabled=maybe",
    "/modify T1 entry_order_type=live", "/modify T1 stop_loss=inf",
    "/modify T1 target1=0", "/modify T1 target1=10 target1=11",
    "/modify T1 stop_loss", "/squareoff actor=99", "/approve T1 market actor=99",
])
def test_invalid_commands_have_no_effect(controller, text):
    assert controller.handle_update(message(text))
    assert not controller.engine.mock_calls


@pytest.mark.parametrize("status", ["REQUESTED", "PENDING", "pending_approval"])
def test_modify_requires_final_confirmation(controller, status):
    controller.engine.modify.return_value = {"id": "T1", "status": status}
    assert controller.handle_update(message(
        "/modify T1 entry_order_type=limit entry_price_requested=100 "
        "stop_loss=90 target1=120 target2=140 trailing_enabled=on"
    ))
    controller.engine.modify.assert_called_once_with("T1", {
        "entry_order_type": "limit", "entry": 100.0,
        "stop_loss": 90.0, "target_1": 120.0, "target_2": 140.0,
        "trailing": True,
    }, actor=7)
    controller.engine.approve.assert_not_called()
    payloads = [call.kwargs["json"] for call in controller.session.post.call_args_list]
    confirmation = next(p for p in payloads if "reply_markup" in p)
    assert "Final confirmation" in confirmation["text"]
    assert confirmation["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "paper:T1:approve_limit"


@pytest.mark.parametrize("action,method,args,kwargs", [
    ("approve_limit", "approve", ("T1", "limit"), {"actor": 7}),
    ("approve_market", "approve", ("T1", "market"), {"actor": 7}),
    ("reject", "reject", ("T1",), {"actor": 7}),
    ("exit_market", "exit", ("T1",), {"order_type": "market", "actor": 7}),
])
def test_callbacks(controller, action, method, args, kwargs):
    assert controller.handle_update(callback(action))
    getattr(controller.engine, method).assert_called_once_with(*args, **kwargs)
    assert any(call.args[0].endswith("/answerCallbackQuery")
               for call in controller.session.post.call_args_list)


@pytest.mark.parametrize("action,guidance", [
    ("modify", "/modify T1 key=value"), ("exit_limit", "/exit T1 limit PRICE"),
])
def test_callback_guidance_does_not_execute(controller, action, guidance):
    assert controller.handle_update(callback(action))
    assert not controller.engine.mock_calls
    assert guidance in controller.session.post.call_args.kwargs["json"]["text"]


def test_trailing_toggle(controller):
    controller.engine.modify.return_value = {"id": "T1", "status": "OPEN", "trailing": True}
    assert controller.handle_update(callback("trailing"))
    controller.engine.modify.assert_called_once_with("T1", {"trailing": True}, actor=7)
    controller.engine.approve.assert_not_called()


@pytest.mark.parametrize("data", ["paper:T1:live", "paper:T1:approve_market:99",
                                  "trade:T1:approve_market", "paper::approve_market", None])
def test_malformed_callbacks(controller, data):
    update = callback("approve_market")
    update["callback_query"]["data"] = data
    assert controller.handle_update(update)
    assert not controller.engine.mock_calls


def test_duplicate_updates_execute_once(controller):
    update = callback("approve_market", update_id=10)
    assert controller.handle_update(update)
    assert not controller.handle_update(update)
    controller.engine.approve.assert_called_once()


def test_poll_drops_initial_backlog_and_preserves_future_actions(controller):
    response = controller.session.post.return_value
    response.json.side_effect = [
        {"ok": True, "result": [message("/squareoff", update_id=9)]},
        {"ok": True, "result": [message("/approve T1", update_id=10)]},
        {"ok": True, "result": True},
        {"ok": True, "result": [message("/approve T1", update_id=10)]},
    ]
    assert controller.poll_once() == 0
    controller.engine.squareoff.assert_not_called()
    assert controller.offset == 10
    assert controller.poll_once() == 1
    assert controller.poll_once() == 0
    controller.engine.approve.assert_called_once_with("T1", "limit", actor=7)
    polls = [call.kwargs["json"] for call in controller.session.post.call_args_list
             if call.args[0].endswith("/getUpdates")]
    assert [poll["offset"] for poll in polls] == [-1, 10, 11]
    assert polls[1]["timeout"] == 20


def test_poll_empty_startup_and_callback(controller):
    response = controller.session.post.return_value
    response.json.side_effect = [
        {"ok": True, "result": []},
        {"ok": True, "result": [callback("exit_market", update_id=3)]},
        {"ok": True, "result": True},
        {"ok": True, "result": True},
    ]
    assert controller.poll_once() == 0
    assert controller.poll_once() == 1
    controller.engine.exit.assert_called_once_with("T1", order_type="market", actor=7)
    assert controller.offset == 4


@pytest.mark.parametrize("result", [{"ok": False, "description": "test-placeholder"},
                                    {"ok": True, "result": "invalid"}, None])
def test_invalid_poll_response_is_safe(controller, result):
    controller.session.post.return_value.json.return_value = result
    with pytest.raises(RuntimeError, match="^Telegram unavailable$"):
        controller.poll_once()
    assert not controller._initialized
    assert not controller.engine.mock_calls


def test_api_failure_redacts_token_and_does_not_initialize(controller):
    controller.session.post.side_effect = requests.RequestException(
        "https://api.telegram.org/bottest-placeholder/getUpdates"
    )
    with pytest.raises(RuntimeError, match="^Telegram unavailable$"):
        controller.poll_once()
    assert not controller._initialized
    assert not controller.notify("safe text")


def test_engine_error_not_exposed(controller):
    controller.engine.approve.side_effect = RuntimeError("test-placeholder secret details")
    assert controller.handle_update(message("/approve T1"))
    text = controller.session.post.call_args.kwargs["json"]["text"]
    assert "test-placeholder" not in text
    assert "secret" not in text


def test_notify_uses_plain_text_and_buttons(controller):
    assert controller.notify("<unsafe> *markup*", [[{"text": "Exit", "callback_data": "paper:T1:exit_market"}]])
    payload = controller.session.post.call_args.kwargs["json"]
    assert payload["text"] == "<unsafe> *markup*"
    assert "parse_mode" not in payload
    assert payload["chat_id"] == "-100"


def test_long_messages_split_and_buttons_stay_with_final_chunk(controller):
    text = "📊" * 4500
    buttons = [[{"text": "Exit", "callback_data": "paper:T1:exit_market"}]]
    assert controller.notify(text, buttons)
    payloads = [call.kwargs["json"] for call in controller.session.post.call_args_list]
    assert "".join(payload["text"] for payload in payloads) == text
    assert all(len(payload["text"].encode("utf-16-le")) // 2 <= 4096 for payload in payloads)
    assert all("reply_markup" not in payload for payload in payloads[:-1])
    assert payloads[-1]["reply_markup"]["inline_keyboard"] == buttons


def test_non_command_ignored(controller):
    assert not controller.handle_update(message("hello"))
    assert not controller.engine.mock_calls
    controller.session.post.assert_not_called()


def test_real_engine_field_mapping_and_confirmation(controller):
    from datetime import datetime, timezone

    from paper_portfolio import PaperPortfolio

    now = datetime(2026, 1, 5, 9, tzinfo=timezone.utc)
    engine = PaperPortfolio(
        ":memory:", clock=lambda: now,
        quote_provider=lambda trade: {"price": 100, "timestamp": now},
    )
    controller.engine = engine
    try:
        trade = engine.submit({
            "symbol": "NIFTY", "entry": 100, "stop_loss": 90,
            "target1": 120, "target2": 140,
            "metadata": {"option_symbol": "NIFTY-TEST-CE", "security_id": 123,
                         "exchange_segment": "NSE_FNO", "lot_size": 25},
        })
        trade_id = trade["id"]
        assert controller.handle_update(message("/risk pnl +20000 -5000"))
        assert engine.config["profit_cap"] == 20000
        assert engine.config["loss_cap"] == 5000
        assert controller.handle_update(message("/limits max_open 4 max_day10"))
        assert engine.config["max_open_positions"] == 4
        assert engine.config["max_daily_trades"] == 10
        assert controller.handle_update(message(
            f"/modify {trade_id} entry_price_requested=101 target1=125 "
            "target2=145 trailing_enabled=on"
        ))
        modified = engine.get(trade_id)
        assert modified["status"] == "REQUESTED"
        assert modified["entry"] == 101
        assert modified["target_1"] == 125
        assert modified["target_2"] == 145
        assert modified["trailing"] is True
        payload = controller.session.post.call_args.kwargs["json"]
        assert "Final confirmation" in payload["text"]
        assert "reply_markup" in payload
        assert controller.handle_update(message("/pending"))
        assert trade_id in controller.session.post.call_args.kwargs["json"]["text"]
        assert controller.handle_update(message(f"/approve {trade_id} market"))
        assert engine.get(trade_id)["status"] == "OPEN"
        assert controller.handle_update(message(f"/exit {trade_id} market"))
        assert engine.get(trade_id)["status"] == "CLOSED"
    finally:
        engine.close()
