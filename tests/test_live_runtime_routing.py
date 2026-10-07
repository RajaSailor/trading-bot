from unittest.mock import Mock

import pytest

from runtime_services import QueueConsumerWorker
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
