from unittest.mock import Mock

import pytest


@pytest.fixture
def application(monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "development")
    import main
    monkeypatch.setattr(main, "live_route", None)
    return main


def test_disabled_live_endpoint_never_initializes_execution(application):
    response = application.app.test_client().post("/telegram/live", json={"update_id": 1})
    assert response.status_code == 503
    assert application.live_route is None


def test_live_endpoint_passes_header_not_body_secret(application, monkeypatch):
    route = Mock()
    route.handle_update.return_value = ({"ok": False, "error": "invalid webhook secret"}, 403)
    monkeypatch.setattr(application, "live_route", route)
    payload = {"update_id": 1, "secret": "untrusted-body"}
    response = application.app.test_client().post(
        "/telegram/live", json=payload,
        headers={"X-Telegram-Bot-Api-Secret-Token": "header-value"},
    )
    assert response.status_code == 403
    route.handle_update.assert_called_once_with(payload, "header-value")


def test_live_endpoint_bounds_body_before_controller(application, monkeypatch):
    route = Mock()
    monkeypatch.setattr(application, "live_route", route)
    response = application.app.test_client().post(
        "/telegram/live", data=b"x" * 65537, content_type="application/json"
    )
    assert response.status_code == 413
    route.handle_update.assert_not_called()


def test_live_and_paper_webhooks_remain_separate(application, monkeypatch):
    route = Mock()
    route.handle_update.return_value = ({"ok": True}, 200)
    paper = Mock()
    paper.handle_update.return_value = ({"ok": True}, 200)
    monkeypatch.setattr(application, "live_route", route)
    monkeypatch.setattr(application, "paper_telegram_control", paper)
    client = application.app.test_client()
    assert client.post("/telegram/live", json={"update_id": 1}).status_code == 200
    paper.handle_update.assert_not_called()
    assert client.post("/telegram/paper", json={"update_id": 2}).status_code == 200
    route.handle_update.assert_called_once()


@pytest.mark.parametrize("internal", [False, True])
def test_queue_origin_is_server_supplied_not_webhook_claim(application, monkeypatch, internal):
    from signal_processor import SignalQueueProcessor
    monkeypatch.setattr(application, "signal_queue_processor", SignalQueueProcessor())
    monkeypatch.setattr(application, "paper_portfolio", None)
    monkeypatch.setattr(application, "risk_manager", None)
    monkeypatch.setattr(application, "trading_db", None)
    monkeypatch.setattr(application, "signal_notifier", None)
    monkeypatch.setattr(application, "runtime_config", {"max_position_size": 5})
    result, _ = application._queue_signal_from_payload(
        {
            "symbol": "NIFTY", "action": "BUY", "entry_price": 100,
            "stop_loss": 90, "quantity": 1, "_scanner_origin": True,
            "metadata": {"source": "scanner"},
        },
        scanner_origin=internal,
    )
    assert result["signal"]["_scanner_origin"] is internal
