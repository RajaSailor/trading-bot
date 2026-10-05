from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from paper_runtime import PaperMarketData, PaperTradingRuntime, market_timestamp
from runtime_services import QueueConsumerWorker

IST = ZoneInfo("Asia/Kolkata")


def test_deployment_starts_one_paper_worker_without_legacy_live_worker():
    root = Path(__file__).resolve().parents[1]
    procfile = (root / "Procfile").read_text()
    startup = (root / "deployment" / "startup.sh").read_text()
    assert "unified_main.py" not in procfile
    assert "--workers 1" in procfile
    assert "--workers 1" in startup
    assert "GUNICORN_WORKERS" not in startup


def test_market_timestamp_supports_dhan_time_and_epoch_milliseconds():
    moment = datetime(2026, 10, 5, 11, 0, tzinfo=IST)
    assert market_timestamp("05/10/2026 11:00:00") == moment
    assert market_timestamp(moment.timestamp() * 1000) == moment


def test_queue_routes_to_paper_without_execution_or_broker():
    runtime = Mock()
    executor = Mock()
    broker = Mock()
    worker = QueueConsumerWorker(
        Mock(), executor, Mock(),
        {"practice_mode": False, "auto_trading_enabled": True}, Mock(),
        dhan_integration=broker, paper_runtime=runtime,
    )
    signal = {"symbol": "NIFTY", "action": "BUY"}
    worker._execute_signal(signal)
    runtime.submit.assert_called_once_with(signal)
    executor.execute_order.assert_not_called()
    broker.place_trade.assert_not_called()


def test_runtime_reports_ineligible_signals_without_fallback():
    engine, controller = Mock(), Mock()
    engine.submit.side_effect = ValueError("Missing listed option lot_size")
    PaperTradingRuntime(engine, controller).submit({"symbol": "NIFTY", "action": "BUY"})
    controller.notify.assert_called_once_with("Paper signal rejected: Missing listed option lot_size")


def test_sell_signal_buys_put_option_instead_of_exiting():
    engine, controller = Mock(), Mock()
    signal = {"symbol": "NIFTY", "action": "SELL", "metadata": {"option_type": "PE"}}
    PaperTradingRuntime(engine, controller).submit(signal)
    engine.submit.assert_called_once_with(signal)
    engine.exit.assert_not_called()


def test_exit_signal_only_exits_matching_paper_positions():
    engine, controller = Mock(), Mock()
    engine.trades.return_value = [
        {"trade_id": "a", "symbol": "NIFTY"}, {"trade_id": "b", "symbol": "GOLD"}
    ]
    PaperTradingRuntime(engine, controller).submit({"symbol": "NIFTY", "action": "EXIT"})
    engine.exit.assert_called_once_with("a", reason="MANUAL")
    engine.submit.assert_not_called()


def test_stop_does_not_close_engine_while_polling_thread_is_alive():
    engine, controller = Mock(), Mock()
    runtime = PaperTradingRuntime(engine, controller)
    runtime._telegram_thread = Mock()
    runtime._telegram_thread.is_alive.return_value = True
    assert runtime.stop() is False
    engine.close.assert_not_called()


def test_quote_requires_source_timestamp_and_never_uses_receipt_time():
    session = Mock()
    adapter = PaperMarketData(session=session)
    trade = {"exchange_segment": "NSE_FNO", "security_id": 123}
    with patch.dict("os.environ", {"ACCESS_TOKEN": "test", "DHAN_CLIENT_ID": "test"}), \
            patch("paper_runtime._apply_rate_limit"):
        session.post.return_value.json.return_value = {
            "data": {"NSE_FNO": {"123": {"last_price": 100}}}
        }
        assert adapter.quote(trade) is None
        old = datetime.now(IST) - timedelta(minutes=5)
        session.post.return_value.json.return_value["data"]["NSE_FNO"]["123"]["last_trade_time"] = old.timestamp()
        quote = adapter.quote(trade)
        assert quote["timestamp"] == old
        assert quote["price"] == 100


def test_quote_batches_active_contracts_and_reuses_source_timestamp():
    session, engine = Mock(), Mock()
    adapter = PaperMarketData(session=session)
    adapter.portfolio = engine
    trades = [
        {"status": "OPEN", "exchange_segment": "NSE_FNO", "security_id": sid}
        for sid in (123, 456)
    ]
    engine.trades.return_value = trades
    source_time = datetime.now(IST).isoformat()
    session.post.return_value.json.return_value = {"data": {"NSE_FNO": {
        str(trade["security_id"]): {"last_price": 100, "last_trade_time": source_time}
        for trade in trades
    }}}
    with patch.dict("os.environ", {"ACCESS_TOKEN": "test", "DHAN_CLIENT_ID": "test"}), \
            patch("paper_runtime._apply_rate_limit"):
        assert adapter.quote(trades[0])["timestamp"].isoformat() == source_time
        assert adapter.quote(trades[1])["timestamp"].isoformat() == source_time
    assert session.post.call_count == 1
    assert session.post.call_args.kwargs["json"] == {"NSE_FNO": [123, 456]}


def test_trailing_provider_excludes_incomplete_and_previous_day_candles():
    manager = Mock()
    adapter = PaperMarketData(data_manager=manager)
    now = datetime(2026, 10, 5, 11, 5, tzinfo=IST)
    candles = [
        {"timestamp": (now - timedelta(days=1)).timestamp(), "low": 50},
        {"timestamp": now.replace(hour=10, minute=50).timestamp(), "low": 90},
        {"timestamp": now.replace(hour=11, minute=0).timestamp(), "low": 110},
    ]
    with patch("paper_runtime.datetime") as clock, \
            patch("paper_runtime.market_timestamp", side_effect=lambda value: datetime.fromtimestamp(value, IST)), \
            patch("paper_runtime.ATMOptionsFetcher") as fetcher:
        clock.now.return_value = now
        fetcher.return_value.fetch_option_candles.return_value = candles
        assert adapter.candle({
            "exchange_segment": "NSE_FNO", "security_id": 123, "segment": "index",
        }) == candles[1]
        assert fetcher.return_value.fetch_option_candles.call_args.args[0]["instrument_type"] == "OPTIDX"
        adapter._candles.clear()
        assert adapter.candle({
            "exchange_segment": "NSE_FNO", "security_id": 123, "segment": "index_options",
        }) == candles[1]
        assert fetcher.return_value.fetch_option_candles.call_args.args[0]["instrument_type"] == "OPTIDX"


def test_trailing_cache_refreshes_at_exchange_completed_candle_boundary():
    adapter = PaperMarketData(data_manager=Mock())
    trade = {"exchange_segment": "NSE_FNO", "security_id": 123, "segment": "index"}
    earlier = {"timestamp": datetime(2026, 10, 5, 9, 15, tzinfo=IST).timestamp(), "low": 90}
    newest = {"timestamp": datetime(2026, 10, 5, 9, 25, tzinfo=IST).timestamp(), "low": 95}
    with patch("paper_runtime.datetime") as clock, \
            patch("paper_runtime.market_timestamp", side_effect=lambda value: datetime.fromtimestamp(value, IST)), \
            patch("paper_runtime.ATMOptionsFetcher") as fetcher:
        fetcher.return_value.fetch_option_candles.side_effect = [[earlier], [earlier, newest]]
        clock.now.return_value = datetime(2026, 10, 5, 9, 30, tzinfo=IST)
        assert adapter.candle(trade) == earlier
        clock.now.return_value = datetime(2026, 10, 5, 9, 34, tzinfo=IST)
        assert adapter.candle(trade) == earlier
        clock.now.return_value = datetime(2026, 10, 5, 9, 35, tzinfo=IST)
        assert adapter.candle(trade) == newest
        assert fetcher.return_value.fetch_option_candles.call_count == 2
