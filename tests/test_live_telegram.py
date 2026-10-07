import pytest

from live_telegram import LiveTelegram
from tests.test_live_broker import adapter, broker
from tests.test_live_execution import engine, signal

SECRET = "live-test-webhook-secret-with-32-characters"


@pytest.fixture
def controller(engine):
    return LiveTelegram(engine, secret=SECRET, allowed_actors=[7], allowed_chats=[-9])


def update(item, action="approve", actor=7, chat=-9, version=1, update_id=1):
    return {"update_id": update_id, "callback_query": {
        "id": f"callback-{update_id}",
        "from": {"id": actor, "is_bot": False},
        "message": {"chat": {"id": chat}},
        "data": f"live:{item['id']}:{version}:{action}",
    }}


def card(controller, engine, adapter):
    item = engine.propose(signal(adapter))
    return controller.proposal_card(item, actor=7, chat_id=-9)["proposal"]


@pytest.mark.parametrize("actor,chat,secret", [(8, -9, SECRET),
                                               (7, -8, SECRET),
                                               (7, -9, "wrong")])
def test_authentication_denies_before_effects(controller, engine, adapter, actor, chat, secret):
    item = card(controller, engine, adapter)
    assert not controller.handle_update(update(item, actor=actor, chat=chat), secret)["ok"]
    assert not adapter.writes
    assert controller.handle_update(update(item), SECRET)["ok"]


def test_approval_duplicate_update_and_card_labels(controller, engine, adapter):
    proposal = engine.propose(signal(adapter))
    rendered = controller.proposal_card(proposal, actor=7, chat_id=-9)
    assert "production BLOCKED" in rendered["text"]
    assert "not a fill" in rendered["text"]
    assert rendered["reply_markup"]["inline_keyboard"][-1][0]["text"] == "+1 lot"
    assert "Balance 100000.0" in rendered["text"]
    assert "Filled entries today 0/2 (IST)" in rendered["text"]
    assert rendered["reply_markup"]["inline_keyboard"][0][0]["text"] == "Approve Limit"
    assert rendered["reply_markup"]["inline_keyboard"][1][0]["text"] == "Modify to Market"
    assert "Modify to Limit: /live_limit" in rendered["text"]
    payload = update(proposal)
    assert controller.handle_update(payload, SECRET)["result"]["status"] == "PENDING"
    assert controller.handle_update(payload, SECRET)["duplicate"]
    assert len(adapter.writes) == 1


def test_modify_version_lots_market_limit_and_reject(controller, engine, adapter):
    item = card(controller, engine, adapter)
    changed = controller.handle_update(update(item, "lots:5"), SECRET)
    assert changed["result"]["proposal"]["quantity"] == 325
    assert not controller.handle_update(update(item, update_id=2), SECRET)["ok"]
    market = controller.handle_update(update(item, "market", version=2, update_id=3), SECRET)
    assert market["result"]["proposal"]["order_type"] == "MARKET"
    message = {"update_id": 4, "message": {
        "from": {"id": 7}, "chat": {"id": -9},
        "text": f"/live_limit {item['id']} 3 101.05",
    }}
    limit = controller.handle_update(message, SECRET)
    assert limit["result"]["proposal"]["version"] == 4
    assert limit["result"]["proposal"]["limit_price"] == 101.05
    result = controller.handle_update(update(item, "reject", version=4, update_id=5), SECRET)
    assert result["result"]["status"] == "REJECTED"
    assert not adapter.writes


def test_expired_and_bot_approval_rejected(controller, engine, adapter):
    item = card(controller, engine, adapter)
    payload = update(item)
    payload["callback_query"]["from"]["is_bot"] = True
    assert not controller.handle_update(payload, SECRET)["ok"]
    adapter.now += 121
    assert not controller.handle_update(update(item), SECRET)["ok"]
    assert not adapter.writes


def test_exit_command_creates_bound_preview(controller, engine, adapter):
    item = card(controller, engine, adapter)
    controller.handle_update(update(item), SECRET)
    adapter.report(item, 65, "FILLED", actual=65)
    message = {"update_id": 2, "message": {
        "from": {"id": 7}, "chat": {"id": -9}, "text": f"/live_exit {item['id']}",
    }}
    result = controller.handle_update(message, SECRET)
    assert result["ok"]
    exit_item = result["result"]["proposal"]
    assert exit_item["side"] == "SELL" and exit_item["actor"] == "7"
    assert len(adapter.writes) == 1
    assert controller.handle_update(update(exit_item, update_id=3), SECRET)["ok"]
    assert len(adapter.writes) == 2


def test_no_secret_no_updates_accepted(engine, adapter):
    controller = LiveTelegram(engine, secret="", allowed_actors=[7], allowed_chats=[-9])
    item = engine.propose(signal(adapter))
    assert not controller.handle_update(update(item), "")["ok"]
    assert not adapter.writes


@pytest.mark.parametrize("field,value", [
    ("sender_chat", {"id": -9}), ("forward_origin", {"type": "user"}),
    ("forward_from", {"id": 7}), ("forward_date", 1),
])
def test_forwarded_and_sender_chat_controls_denied(controller, engine, adapter, field, value):
    item = card(controller, engine, adapter)
    payload = update(item)
    payload["callback_query"]["message"][field] = value
    assert not controller.handle_update(payload, SECRET)["ok"]
    assert not adapter.writes


def test_callback_id_dedup_even_when_update_id_changes(controller, engine, adapter):
    item = card(controller, engine, adapter)
    payload = update(item, "lots:2")
    assert controller.handle_update(payload, SECRET)["ok"]
    payload["update_id"] = 2
    assert controller.handle_update(payload, SECRET)["duplicate"]
    assert engine.status()["intents"][0]["version"] == 2


def test_short_secret_and_invalid_ids_fail_closed(engine, adapter):
    controller = LiveTelegram(engine, secret="short", allowed_actors=[7], allowed_chats=[-9])
    item = engine.propose(signal(adapter))
    assert not controller.readiness()["authorization_configured"]
    assert not controller.handle_update(update(item), "short")["ok"]
    controller = LiveTelegram(engine, secret=SECRET, allowed_actors=["not-numeric"], allowed_chats=[-9])
    assert not controller.readiness()["authorization_configured"]
    assert not controller.handle_update(update(item), SECRET)["ok"]


def test_card_reports_unavailable_balance_without_claiming_value(controller, engine, adapter):
    item = engine.propose(signal(adapter))
    adapter.funds = lambda: {"verified": False}
    rendered = controller.proposal_card(item, actor=7, chat_id=-9)
    assert "Balance unavailable | as of unavailable" in rendered["text"]
    assert rendered["context"]["estimated_funds"] == 6500


def test_market_selection_only_modifies_then_requires_fresh_authenticated_approval(controller, engine, adapter):
    item = card(controller, engine, adapter)
    modified = controller.handle_update(update(item, "market", update_id=1), SECRET)
    assert modified["ok"]
    preview = modified["result"]
    assert preview["proposal"]["order_type"] == "MARKET"
    assert preview["proposal"]["version"] == 2
    assert preview["reply_markup"]["inline_keyboard"][0][0]["text"] == "Approve Market"
    assert "approve its new version separately" in preview["text"]
    assert not adapter.writes
    assert not controller.handle_update(update(item, version=1, update_id=2), SECRET)["ok"]
    assert not controller.handle_update(update(item, version=2, update_id=3), "wrong-secret")["ok"]
    assert not adapter.writes
    approved = controller.handle_update(update(item, version=2, update_id=4), SECRET)
    assert approved["ok"] and approved["result"]["status"] == "PENDING"
    assert len(adapter.writes) == 1 and adapter.writes[0]["order_type"] == "MARKET"
