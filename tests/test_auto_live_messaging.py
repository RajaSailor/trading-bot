from unittest.mock import Mock

import pytest

from runtime_services import QueueConsumerWorker, SignalNotifier


def notifier():
    handler = Mock()
    handler.send_to_channel.return_value = True
    return SignalNotifier(handler, execution_only=True), handler


def test_auto_live_suppresses_generic_control_messages():
    sender, handler = notifier()
    sender.notify_signal_accepted({"symbol": "NIFTY", "action": "BUY"})
    sender.notify_position_update({"symbol": "NIFTY"}, 75, 100)
    sender.notify_service_alert("Funds unavailable", "entry skipped")
    assert not sender.send_test_message("trade_control", "probe")
    handler.send_to_channel.assert_not_called()


@pytest.mark.parametrize("symbol,category", [
    ("NIFTY", "index_options"), ("BANKNIFTY", "index_options"),
    ("SENSEX", "index_options"), ("GOLD", "commodity_options"),
    ("RELIANCE", "nifty50_stock_options"),
])
def test_all_scanner_categories_remain_on_signal_bot(symbol, category):
    sender, handler = notifier()
    assert sender.notify_strategy_signal({
        "symbol": symbol, "action": "BUY", "category": category,
    })
    assert handler.send_to_channel.call_args.args[0] == "service_alerts"


def test_only_real_nifty_entry_and_exit_events_reach_control():
    sender, handler = notifier()
    item = {
        "underlying": "NIFTY", "option_symbol": "NIFTY-CE",
        "average_price": 100, "exit_average_price": 110,
        "filled_quantity": 75, "entry_time": "entry", "exit_time": "exit",
        "available_funds": 25000, "reserved_cash": 7500, "realized_pnl": 750,
    }
    for kind in ("approval_requested", "submitted", "rejected", "day_summary"):
        assert not sender.notify_live_execution({"kind": kind, "proposal": item})
    assert not sender.notify_live_execution({
        "kind": "entry_executed", "proposal": {**item, "underlying": "BANKNIFTY"},
    })
    assert sender.notify_live_execution({"kind": "entry_executed", "proposal": item})
    assert sender.notify_live_execution({"kind": "exit_executed", "proposal": item})
    assert handler.send_to_channel.call_count == 2
    entry, exit_ = [call.args for call in handler.send_to_channel.call_args_list]
    assert entry[0] == exit_[0] == "trade_control"
    assert "₹25000.00" in entry[1] and "Units: 75" in entry[1]
    assert "₹750.00" in exit_[1] and "before charges" in exit_[1]
    assert "approval" not in entry[1].lower()


@pytest.mark.parametrize("symbol", ["BANKNIFTY", "SENSEX", "RELIANCE", "GOLD"])
def test_auto_live_never_submits_other_categories_to_any_execution(symbol):
    route = Mock()
    route.targets.return_value = False
    worker = QueueConsumerWorker(
        queue_processor=Mock(), order_executor=Mock(), trading_db=None,
        runtime_config={"nifty_live": {"enabled": True, "practice": False, "auto": True}},
        notifier=Mock(), paper_portfolio=Mock(), live_route=route,
    )
    assert worker._execute_signal({"symbol": symbol})["status"] == "signal_only"
    route.propose.assert_not_called()
    worker.paper_portfolio.submit.assert_not_called()
    worker.order_executor.execute_order.assert_not_called()


def test_reconciliation_runs_without_a_new_signal():
    route = Mock()
    queue = Mock()
    queue.process_next_signal.return_value = None
    worker = QueueConsumerWorker(
        queue_processor=queue, order_executor=Mock(), trading_db=None,
        runtime_config={}, notifier=Mock(), live_route=route,
    )
    route.tick.side_effect = worker._stop_event.set
    worker.run_forever()
    route.tick.assert_called_once()
