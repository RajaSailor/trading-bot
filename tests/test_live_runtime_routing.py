from unittest.mock import Mock

import pytest

from runtime_services import MarketScannerWorker, QueueConsumerWorker, SignalNotifier
from telegram_handler import TelegramHandler
from live_runtime import LiveRoute, _ids, build_live_route


def worker(route=None):
    return QueueConsumerWorker(
        queue_processor=Mock(),
        order_executor=Mock(),
        trading_db=None,
        runtime_config={"practice_mode": True, "auto_trading_enabled": False},
        notifier=Mock(),
        paper_portfolio=Mock(),
        live_route=route,
    )


@pytest.mark.parametrize("option_type", ["CE", "PE"])
def test_configured_nifty_approval_never_duplicates_paper_submission(option_type):
    route = Mock()
    route.targets.return_value = True
    route.propose.return_value = {"status": "awaiting_approval"}
    consumer = worker(route)
    signal = {
        "symbol": "NIFTY", "action": "BUY",
        "metadata": {"source": "scanner", "option_type": option_type},
    }
    assert consumer._execute_signal(signal)["status"] == "awaiting_approval"
    route.propose.assert_called_once_with(signal)
    consumer.paper_portfolio.submit.assert_not_called()
    consumer.order_executor.execute_order.assert_not_called()


def test_blocked_live_route_does_not_fallback_to_paper():
    route = Mock()
    route.targets.return_value = True
    route.propose.return_value = {"status": "blocked", "reason": "reconciliation_unavailable"}
    consumer = worker(route)
    assert consumer._execute_signal({"symbol": "NIFTY"})["status"] == "blocked"
    consumer.paper_portfolio.submit.assert_not_called()


@pytest.mark.parametrize("symbol", ["BANKNIFTY", "SENSEX", "RELIANCE", "GOLD"])
def test_other_service_categories_keep_existing_paper_runtime(symbol):
    route = Mock()
    route.targets.return_value = False
    consumer = worker(route)
    signal = {"symbol": symbol}
    consumer._execute_signal(signal)
    consumer.paper_portfolio.submit.assert_called_once_with(signal, quote=None)
    route.propose.assert_not_called()


def test_default_runtime_remains_paper():
    consumer = worker()
    signal = {"symbol": "NIFTY"}
    consumer._execute_signal(signal)
    consumer.paper_portfolio.submit.assert_called_once_with(signal, quote=None)


@pytest.mark.parametrize("symbol", ["NIFTY", "BANKNIFTY", "GOLD", "RELIANCE"])
@pytest.mark.parametrize("practice", [True, False])
def test_premium_signals_without_live_route_never_submit_paper(symbol, practice):
    consumer = worker()
    consumer.runtime_config["practice_mode"] = practice
    result = consumer._execute_signal({
        "symbol": symbol, "strategy": "premium_screener",
        "metadata": {"premium_strategy": True},
    })
    assert result["status"] == "signal_only"
    consumer.paper_portfolio.submit.assert_not_called()
    consumer.order_executor.execute_order.assert_not_called()


def config(path, **overrides):
    values = {
        "enabled": True, "practice": True, "auto": False, "db_path": str(path),
        "allowed_users": "", "chat_id": "", "webhook_secret": "",
    }
    values.update(overrides)
    return values


def test_disabled_factory_does_not_create_live_database(tmp_path):
    path = tmp_path / "live.db"
    assert build_live_route(config(path, enabled=False), paper_db_path=tmp_path / "paper.db") is None
    assert not path.exists()


@pytest.mark.parametrize("practice,auto", [(True, False), (True, True), (False, False), (False, True)])
def test_all_flag_combinations_remain_production_blocked(tmp_path, practice, auto):
    route = build_live_route(
        config(tmp_path / "live.db", practice=practice, auto=auto),
        paper_db_path=tmp_path / "paper.db",
    )
    signal = {
        "symbol": "NIFTY", "strategy": "premium_screener", "action": "BUY",
        "_scanner_origin": True,
        "metadata": {"underlying": "NIFTY", "source": "scanner", "premium_strategy": True},
    }
    assert route.targets(signal)
    assert route.propose(signal)["status"] == "blocked"
    assert route.status()["production_ready"] is False
    assert route.status()["mode"] == "blocked"


def test_runtime_health_reports_missing_live_adapter_and_startup_reconciliation(tmp_path):
    route = build_live_route(
        config(tmp_path / "live.db"), paper_db_path=tmp_path / "paper.db",
    )
    status = route.status()
    assert status["production_ready"] is False
    assert status["checks"]["broker_adapter"] is False
    assert status["checks"]["static_ip_verified"] is False
    assert status["reconciliation"]["status"] == "unavailable"
    assert status["last_broker_error_category"] == "adapter_unavailable"
    assert any("static-IP authorization" in blocker for blocker in status["blockers"])
    assert status["limits"]["daily_entry_cap"] == 2
    assert status["limits"]["open_position_cap"] == 1


@pytest.mark.parametrize("path", [":memory:", "file:live?mode=memory", ""])
def test_live_database_must_be_persistent(tmp_path, path):
    with pytest.raises(RuntimeError):
        build_live_route(config(path), paper_db_path=tmp_path / "paper.db")


def test_live_database_cannot_alias_paper_database(tmp_path):
    paper = tmp_path / "paper.db"
    paper.touch()
    alias = tmp_path / "live.db"
    alias.symlink_to(paper)
    with pytest.raises(RuntimeError):
        build_live_route(config(alias), paper_db_path=paper)


@pytest.mark.parametrize("source,strategy", [("manual", "premium_screener"), ("scanner", "webhook")])
def test_manual_generic_routes_cannot_bypass_scanner_whitelist(source, strategy):
    execution = Mock(enabled=True)
    route = LiveRoute(execution, Mock())
    result = route.propose({
        "symbol": "NIFTY", "strategy": strategy,
        "metadata": {"source": source, "underlying": "NIFTY"},
    })
    assert result["status"] == "rejected"
    execution.propose.assert_not_called()


def test_caller_claiming_scanner_source_without_internal_origin_cannot_propose():
    execution = Mock(enabled=True)
    route = LiveRoute(execution, Mock())
    result = route.propose({
        "symbol": "NIFTY", "strategy": "premium_screener",
        "metadata": {"source": "scanner", "underlying": "NIFTY"},
    })
    assert result["status"] == "rejected"
    execution.propose.assert_not_called()


@pytest.mark.parametrize(
    "signal,expected",
    [
        ({"symbol": "NIFTY", "metadata": {"underlying": "NIFTY"}}, True),
        ({"symbol": "BANKNIFTY", "metadata": {"underlying": "NIFTY"}}, False),
        ({"symbol": "NIFTY", "metadata": {"underlying": "BANKNIFTY"}}, False),
        ({"symbol": "RELIANCE", "metadata": {"underlying": "RELIANCE"}}, False),
    ],
)
def test_real_live_route_target_requires_exact_nifty_underlying(signal, expected):
    route = LiveRoute(Mock(enabled=True), Mock())
    assert route.targets(signal) is expected


def test_nifty_auto_mode_keeps_non_nifty_signal_only_and_never_submits_paper():
    route = Mock()
    route.targets.return_value = False
    consumer = worker(route)
    consumer.runtime_config["nifty_live"] = {"enabled": True, "auto": True}

    result = consumer._execute_signal({
        "symbol": "GOLD", "metadata": {"underlying": "GOLD", "category": "commodity_options"},
    })
    assert result["status"] == "signal_only"
    consumer.paper_portfolio.submit.assert_not_called()
    route.propose.assert_not_called()


def test_nifty_auto_mode_blocks_missing_route_instead_of_paper_fallback():
    consumer = worker()
    consumer.runtime_config["nifty_live"] = {"enabled": True, "auto": True}
    result = consumer._execute_signal({"symbol": "NIFTY", "metadata": {}})
    assert result["status"] == "blocked"
    consumer.paper_portfolio.submit.assert_not_called()


@pytest.mark.parametrize("value", ["", "123,bad", "True", "1.5", "-123"])
def test_live_user_allowlist_rejects_non_numeric_or_non_positive_ids(value):
    assert not _ids(value, users=True)


def test_live_control_chat_must_be_one_exact_numeric_id():
    assert _ids("-100123") == {"-100123"}
    assert not _ids("-100123,-100456")


def test_authorization_setup_does_not_claim_webhook_registration(tmp_path):
    route = build_live_route(
        config(tmp_path / "live.db", allowed_users="123,456", chat_id="-100123",
               webhook_secret="x" * 32),
        paper_db_path=tmp_path / "paper.db",
    )
    assert route.status()["telegram_authorization_configured"] is True
    assert route.status()["inbound_registration_verified"] is False


def test_runtime_never_delegates_approval_updates_even_with_configured_auth(tmp_path):
    route = build_live_route(
        config(tmp_path / "live.db", practice=False, auto=False,
               allowed_users="7", chat_id="-9", webhook_secret="x" * 32),
        paper_db_path=tmp_path / "paper.db",
    )
    route.control.handle_update = Mock()
    result, code = route.handle_update({"update_id": 1}, "x" * 32)
    assert code == 403 and not result["ok"]
    assert route.execution.auto_mode is True
    route.control.handle_update.assert_not_called()


def test_runtime_status_reports_actual_execution_without_secret_proposal_data(tmp_path):
    import json
    from live_broker import LiveBroker
    from live_execution import LiveExecution
    from tests.test_live_broker import FakeAdapter
    from tests.test_live_execution import filled

    adapter = FakeAdapter()
    execution = LiveExecution(
        tmp_path / "live.db", LiveBroker(adapter, allow_simulation=True, clock=lambda: adapter.now),
        enabled=True, practice=False, auto=True, clock=lambda: adapter.now,
    )
    entry = filled(execution, adapter)
    with execution._db() as db:
        item = execution._get(db, entry["id"])
        item.update(api_key="private-adapter-key", last_error="private-error-token",
                    actor="private-actor")
        execution._save(db, item)
    route = LiveRoute(execution, Mock(secret="private-webhook-secret-value-32-characters",
                                     allowed_actors={"7"}, allowed_chats={"-9"}))
    status = route.status()
    assert status["execution"]["account"] == execution.status()["account"]
    reported = status["execution"]["intents"][0]
    assert reported["status"] == "FILLED" and reported["owned_quantity"] == 65
    assert reported["order_type"] == "LIMIT"
    assert status["order_policy"] == "LIMIT_ONLY" and status["trailing_enabled"] is False
    assert "private-" not in json.dumps(status)


@pytest.mark.parametrize("new_value,expected", [(None, -1002), ("", None), ("-1003", -1003),
                                                     ("invalid", None)])
def test_index_route_legacy_fallback_only_when_new_key_absent(monkeypatch, new_value, expected):
    monkeypatch.setenv("BOT_SERVICE_ALERTS_TOKEN", "strategy-token")
    monkeypatch.setenv("CHANNEL_SERVICE_ALERTS_ID", "-1002")
    if new_value is None:
        monkeypatch.delenv("INDEX_OPTIONS_CHANNEL_ID", raising=False)
    else:
        monkeypatch.setenv("INDEX_OPTIONS_CHANNEL_ID", new_value)
    handler = TelegramHandler()
    assert handler._get_bot_for_category("index_options") == ("strategy-token", expected)
    assert handler.is_channel_ready("index_options") is (expected is not None)


@pytest.mark.parametrize("category,env", [
    ("trade_control", "CHANNEL_TRADE_CONTROL_ID"),
    ("commodity_options", "COMMODITY_OPTIONS_CHANNEL_ID"),
    ("nifty50_stock_options", "STOCK_OPTIONS_CHANNEL_ID"),
])
def test_missing_routes_never_use_generic_or_legacy_destinations(monkeypatch, category, env):
    monkeypatch.setenv("BOT_SERVICE_ALERTS_TOKEN", "strategy-token")
    monkeypatch.setenv("BOT_TRADE_CONTROL_TOKEN", "control-token")
    monkeypatch.setenv("CHANNEL_SERVICE_ALERTS_ID", "-1002")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "default-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-9999")
    monkeypatch.delenv(env, raising=False)
    handler = TelegramHandler()
    handler._send_message = Mock()
    assert not handler.is_channel_ready(category)
    assert not handler.send_to_channel(category, "probe")
    handler._send_message.assert_not_called()


@pytest.mark.parametrize("category,token_env,channel_env", [
    ("trade_control", "BOT_TRADE_CONTROL_TOKEN", "CHANNEL_TRADE_CONTROL_ID"),
    ("index_options", "BOT_SERVICE_ALERTS_TOKEN", "INDEX_OPTIONS_CHANNEL_ID"),
])
def test_default_token_never_substitutes_category_credentials(monkeypatch, category, token_env, channel_env):
    monkeypatch.delenv(token_env, raising=False)
    monkeypatch.setenv(channel_env, "-1001")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "default-token")
    handler = TelegramHandler(token="caller-default")
    assert handler._get_bot_for_category(category) == ("", -1001)
    assert not handler.is_channel_ready(category)
    assert not handler._send_message(-1001, "probe", "")


@pytest.mark.parametrize("category,env", [
    ("index_options", "INDEX_OPTIONS_CHANNEL_ID"),
    ("commodity_options", "COMMODITY_OPTIONS_CHANNEL_ID"),
    ("stock_options", "STOCK_OPTIONS_CHANNEL_ID"),
    ("nifty50_stock_options", "STOCK_OPTIONS_CHANNEL_ID"),
])
def test_category_routes_use_same_bot_with_own_destination(monkeypatch, category, env):
    monkeypatch.setenv("BOT_SERVICE_ALERTS_TOKEN", "strategy-token")
    monkeypatch.setenv(env, "-1007")
    assert TelegramHandler()._get_bot_for_category(category) == ("strategy-token", -1007)


@pytest.mark.parametrize("symbol,expected", [
    ("NIFTY", ["index_options"]), ("BANKNIFTY", ["index_options"]),
    ("GOLD", ["commodity_options"]), ("M&M", ["nifty50_stock_options"]),
    ("RELIANCE", ["nifty50_stock_options"]), ("NIFTYFAKE", []),
    ("GOLDFAKE", []), ("UNKNOWN", []),
])
def test_notifier_classifies_exact_known_underlyings(symbol, expected):
    assert SignalNotifier(Mock()).channels_for_signal({"symbol": symbol}) == expected


def test_unknown_strategy_does_not_reach_trade_control():
    handler = Mock()
    notifier = SignalNotifier(handler)
    assert not notifier.notify_strategy_signal({
        "symbol": "UNKNOWN", "action": "BUY", "category": "manual",
    })
    handler.send_to_channel.assert_not_called()
    assert TelegramHandler()._get_bot_for_category("unknown") == ("", None)


@pytest.mark.parametrize("status,filled,practice,expected", [
    ("FILLED", 2, False, True), ("PARTIALLY_FILLED", 1, False, True),
    ("FILLED", 0, False, False), ("PENDING", 0, False, False),
    ("REJECTED", 0, False, False), ("FILLED", 2, True, False),
])
@pytest.mark.parametrize("action", ["BUY", "EXIT"])
def test_notifier_only_announces_actual_live_fills(status, filled, practice, expected, action):
    from order_executor import Order, OrderStatus
    handler = Mock()
    notifier = SignalNotifier(handler, trade_control_lifecycle_only=True)
    order = Order("NIFTY", "BUY", 2, 100, filled_quantity=filled, status=OrderStatus(status))
    notifier.notify_order({"symbol": "NIFTY", "action": action, "signal_id": "s1"}, order, practice)
    assert handler.send_to_channel.called is expected
    if expected:
        channel, message = handler.send_to_channel.call_args.args
        assert channel == "trade_control"
        assert ("EXIT EXECUTED" if action == "EXIT" else "ENTRY EXECUTED") in message
        assert f"Actual filled quantity: {filled}" in message
        assert "Broker fill price: 100.00" in message


def test_notifier_rejects_control_tests_accepted_position_and_service_messages():
    handler = Mock()
    notifier = SignalNotifier(handler)
    signal = {"symbol": "NIFTY", "action": "BUY"}
    notifier.notify_signal_accepted(signal)
    notifier.notify_position_update(signal, 1, 100)
    notifier.notify_service_alert("service", "detail")
    assert not notifier.send_test_message("trade", "test")
    handler.send_to_channel.assert_not_called()


def test_lifecycle_maintenance_runs_even_when_scanner_disabled():
    import threading
    called = threading.Event()
    lifecycle = Mock()
    lifecycle.maintenance.side_effect = lambda *_: called.set()
    scanner = MarketScannerWorker(lambda *_: True, {"scanner_enabled": False},
                                  signal_lifecycle=lifecycle)
    try:
        assert not scanner.start()
        assert called.wait(2)
        assert scanner.status()["lifecycle_maintenance_running"]
        assert scanner._scanner is None
    finally:
        scanner.stop()


def test_lifecycle_maintenance_wait_is_at_most_five_seconds(monkeypatch):
    lifecycle = Mock()
    scanner = MarketScannerWorker(lambda *_: True, {"scanner_enabled": True},
                                  signal_lifecycle=lifecycle)
    waits = []
    def stop_after_wait(seconds):
        waits.append(seconds)
        scanner._stop_event.set()
    monkeypatch.setattr(scanner._stop_event, "wait", stop_after_wait)
    scanner._maintain_lifecycle()
    lifecycle.maintenance.assert_called_once()
    assert len(lifecycle.maintenance.call_args.args) == 1
    assert not lifecycle.maintenance.call_args.kwargs
    assert waits == [5.0]


@pytest.mark.parametrize("value,expected", [(0, 1.0), (2, 2.0), (60, 5.0),
                                          ("invalid", 5.0), (None, 5.0)])
def test_lifecycle_poll_interval_is_configurable_and_bounded(value, expected):
    scanner = MarketScannerWorker(
        lambda *_: True, {"scanner_enabled": False, "scanner_poll_seconds": value},
    )
    assert scanner.lifecycle_poll_seconds == expected


def test_scanner_passes_lifecycle_into_premium_screener(monkeypatch):
    constructor = Mock()
    monkeypatch.setattr("runtime_services.PremiumScreener", constructor)
    monkeypatch.setattr("runtime_services.get_shared_data_manager", Mock())
    lifecycle = Mock()
    scanner = MarketScannerWorker(lambda *_: True, {"scanner_enabled": True},
                                  signal_lifecycle=lifecycle)
    scanner._build_scanner()
    assert constructor.call_args.kwargs["signal_lifecycle"] is lifecycle


def test_lifecycle_managed_payload_does_not_duplicate_strategy_delivery():
    handler = Mock()
    notifier = SignalNotifier(handler)
    assert notifier.notify_strategy_signal({
        "symbol": "NIFTY", "action": "BUY", "_lifecycle_managed": True,
    })
    handler.send_to_channel.assert_not_called()


def test_parent_owned_lifecycle_disables_scanner_maintenance_thread():
    lifecycle = Mock()
    scanner = MarketScannerWorker(
        lambda *_: True, {"scanner_enabled": False}, signal_lifecycle=lifecycle,
        lifecycle_maintenance_enabled=False,
    )
    assert not scanner.start()
    assert scanner._maintenance_thread is None
    lifecycle.maintenance.assert_not_called()
