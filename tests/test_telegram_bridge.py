from unittest.mock import AsyncMock, MagicMock

import pytest

def _make_update(message_text: str):
    message = MagicMock()
    message.text = message_text
    message.reply_text = AsyncMock()

    update = MagicMock()
    update.message = message
    return update


def _make_context(args=None):
    context = MagicMock()
    context.args = args or []
    return context


def test_parse_trade_signal_valid_message(bridge):
    success, signal = bridge.parse_trade_signal(
        "BUY NIFTY50 1 19400 SL:19300 TARGET:19600 SCALP"
    )

    assert success is True
    assert signal["transaction_type"] == "BUY"
    assert signal["symbol"] == "NIFTY50"
    assert signal["quantity"] == 1
    assert signal["entry_price"] == 19400.0
    assert signal["sl_price"] == 19300.0
    assert signal["target_price"] == 19600.0
    assert signal["strategy"] == "SCALP"


def test_parse_trade_signal_invalid_message(bridge):
    success, signal = bridge.parse_trade_signal("BUY NIFTY50 1 19400")
    assert success is False
    assert signal == {}


def test_parse_trade_signal_invalid_quantity(bridge):
    success, signal = bridge.parse_trade_signal(
        "BUY NIFTY50 X 19400 SL:19300 TARGET:19600"
    )
    assert success is False
    assert signal == {}


@pytest.mark.asyncio
async def test_handle_trade_signal_invalid_format(bridge, mock_dhan_integration):
    update = _make_update("bad signal")
    await bridge.handle_trade_signal(update, _make_context())

    mock_dhan_integration.place_trade.assert_not_called()
    update.message.reply_text.assert_awaited_once()
    assert "Invalid signal format" in update.message.reply_text.await_args.args[0]


@pytest.mark.asyncio
async def test_handle_trade_signal_success(bridge, mock_dhan_integration):
    mock_dhan_integration.place_trade.return_value = (True, "ok", "ORD001")
    update = _make_update("BUY NIFTY50 1 19400 SL:19300 TARGET:19600")

    await bridge.handle_trade_signal(update, _make_context())

    mock_dhan_integration.place_trade.assert_called_once_with(
        symbol="NIFTY50",
        transaction_type="BUY",
        quantity=1,
        entry_price=19400.0,
        sl_price=19300.0,
        target_price=19600.0,
        strategy="",
    )
    assert update.message.reply_text.await_count == 2
    assert "ORD001" in update.message.reply_text.await_args_list[1].args[0]
    assert "Trade Placed Successfully" in update.message.reply_text.await_args_list[1].args[0]


@pytest.mark.asyncio
async def test_handle_trade_signal_trade_failure(bridge, mock_dhan_integration):
    mock_dhan_integration.place_trade.return_value = (False, "exchange rejected", None)
    update = _make_update("SELL BANKNIFTY 2 45000 SL:45100 TARGET:44900")

    await bridge.handle_trade_signal(update, _make_context())

    mock_dhan_integration.place_trade.assert_called_once_with(
        symbol="BANKNIFTY",
        transaction_type="SELL",
        quantity=2,
        entry_price=45000.0,
        sl_price=45100.0,
        target_price=44900.0,
        strategy="",
    )
    assert update.message.reply_text.await_count == 2
    assert "Trade failed: exchange rejected" in update.message.reply_text.await_args_list[1].args[0]


@pytest.mark.asyncio
async def test_handle_trade_signal_exception(bridge, mock_dhan_integration):
    mock_dhan_integration.place_trade.side_effect = RuntimeError("order api down")
    update = _make_update("BUY NIFTY50 1 19400 SL:19300 TARGET:19600")

    await bridge.handle_trade_signal(update, _make_context())

    assert update.message.reply_text.await_count == 2
    assert "Error: order api down" in update.message.reply_text.await_args_list[1].args[0]


@pytest.mark.asyncio
async def test_handle_status_command(bridge):
    update = _make_update("/status")
    await bridge.handle_status(update, _make_context())

    update.message.reply_text.assert_awaited_once()
    assert "Trading Status" in update.message.reply_text.await_args.args[0]


@pytest.mark.asyncio
async def test_handle_close_command_without_args(bridge):
    update = _make_update("/close")
    await bridge.handle_close_command(update, _make_context(args=[]))

    update.message.reply_text.assert_awaited_once()
    assert "Usage: /close" in update.message.reply_text.await_args.args[0]


@pytest.mark.asyncio
async def test_send_target_hit_alert_uses_telegram_bot_api(bridge):
    send_message = AsyncMock()
    object.__setattr__(bridge.bot, "send_message", send_message)

    await bridge.send_target_hit_alert(
        {
            "symbol": "NIFTY50",
            "transactionType": "BUY",
            "entryPrice": 19400,
            "targetPrice": 19600,
            "finalPnL": 2000,
        }
    )

    send_message.assert_awaited_once()
    kwargs = send_message.await_args.kwargs
    assert kwargs["chat_id"] == 12345
    assert kwargs["parse_mode"] == "Markdown"
    assert "TARGET HIT" in kwargs["text"]


@pytest.mark.asyncio
async def test_send_target_hit_alert_handles_telegram_api_error(bridge):
    send_message = AsyncMock(side_effect=RuntimeError("telegram down"))
    bridge.logger.error = MagicMock()
    object.__setattr__(bridge.bot, "send_message", send_message)

    await bridge.send_target_hit_alert(
        {
            "symbol": "NIFTY50",
            "transactionType": "BUY",
            "entryPrice": 19400,
            "targetPrice": 19600,
            "finalPnL": 2000,
        }
    )

    assert send_message.await_count == 3
    bridge.logger.error.assert_called_once()
    assert "Target alert error" in bridge.logger.error.call_args.args[0]
