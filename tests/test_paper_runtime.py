import threading
from unittest.mock import Mock

from runtime_services import PaperPortfolioWorker, QueueConsumerWorker


def test_queue_routes_to_portfolio_without_broker_or_legacy_executor():
    portfolio = Mock()
    data = Mock()
    quote = {"price": 101, "timestamp": 123}
    data.fetch_quotes.return_value = {("NSE_FNO", 42): quote}
    broker, executor, db = Mock(), Mock(), Mock()
    worker = QueueConsumerWorker(
        Mock(), executor, db,
        {"practice_mode": False, "auto_trading_enabled": True},
        Mock(), dhan_integration=broker, paper_portfolio=portfolio, quote_provider=data,
    )
    signal = {"action": "BUY", "metadata": {"security_id": 42, "exchange_segment": "NSE_FNO"}}
    worker._execute_signal(signal)
    portfolio.submit.assert_called_once_with(signal, quote=quote)
    broker.place_trade.assert_not_called()
    executor.execute_order.assert_not_called()
    db.claim_order.assert_not_called()


def test_scheduler_recovers_before_data_and_batches_contracts():
    portfolio, data = Mock(), Mock()
    contracts = [
        {"exchange_segment": "NSE_FNO", "security_id": 42},
        {"exchange_segment": "NSE_FNO", "security_id": 42},
        {"exchange_segment": "MCX_COMM", "security_id": 43},
    ]
    portfolio.contracts.return_value = contracts
    data.fetch_quotes.return_value = {}
    data.fetch_paper_candles.return_value = []
    events = []
    portfolio.tick.side_effect = lambda *a, **kw: events.append("tick")
    data.fetch_quotes.side_effect = lambda *a: events.append("quotes") or {}
    worker = PaperPortfolioWorker(portfolio, data)
    worker.run_once()
    assert events == ["tick", "quotes", "tick", "tick"]
    data.fetch_quotes.assert_called_once_with({"NSE_FNO": [42], "MCX_COMM": [43]})
    assert worker.status()["last_run_at"]


def test_quote_outage_does_not_skip_protection_or_fabricate_a_price():
    portfolio, data = Mock(), Mock()
    portfolio.contracts.return_value = [{"exchange_segment": "NSE_FNO", "security_id": 42}]
    data.fetch_quotes.side_effect = TimeoutError()
    data.fetch_paper_candles.side_effect = TimeoutError()
    PaperPortfolioWorker(portfolio, data).run_once()
    assert portfolio.tick.call_count == 3
    assert all(call.args[0] == {} for call in portfolio.tick.call_args_list)


def test_main_forces_practice_and_forwards_webhook_header(monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.setenv("PRACTICE_MODE", "false")
    monkeypatch.setenv("AUTO_TRADING_ENABLED", "true")
    import main

    assert main._load_runtime_config()["practice_mode"] is True
    assert main._load_runtime_config()["auto_trading_enabled"] is False
    control = Mock()
    control.handle_update.return_value = ({"ok": False}, 403)
    monkeypatch.setattr(main, "paper_telegram_control", control)
    response = main.app.test_client().post(
        "/telegram/paper", json={"update_id": 1},
        headers={"X-Telegram-Bot-Api-Secret-Token": "test-header"},
    )
    assert response.status_code == 403
    control.handle_update.assert_called_once_with({"update_id": 1}, "test-header")
    control.reset_mock()
    oversized = main.app.test_client().post(
        "/telegram/paper", data=b"x" * 65537, content_type="application/json",
    )
    assert oversized.status_code == 413
    control.handle_update.assert_not_called()


def test_main_reports_persistent_account_not_legacy_positions(monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "development")
    import main

    portfolio = Mock()
    portfolio.snapshot.return_value = {
        "orders": [{"id": "paper-order"}],
        "positions": [{"id": "contract-position", "quantity": 65}],
        "account": {"cash": 493500},
        "daily": {"filled": 1},
    }
    monkeypatch.setattr(main, "paper_portfolio", portfolio)
    client = main.app.test_client()
    assert client.get("/orders").get_json()["orders"] == [{"id": "paper-order"}]
    result = client.get("/positions").get_json()
    assert result["positions"][0]["quantity"] == 65
    assert result["account"]["cash"] == 493500


def test_slow_telegram_delivery_does_not_stop_protection():
    portfolio = Mock()
    blocked, release, protected = threading.Event(), threading.Event(), threading.Event()

    def deliver():
        blocked.set()
        release.wait(5)

    portfolio.deliver_notifications.side_effect = deliver
    worker = PaperPortfolioWorker(portfolio, Mock())
    count = 0

    def protect():
        nonlocal count
        count += 1
        if count >= 2 and blocked.is_set():
            protected.set()

    worker.run_once = protect
    try:
        assert worker.start(0.1)
        assert blocked.wait(2)
        assert protected.wait(2)
    finally:
        release.set()
        worker.stop()
