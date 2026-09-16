from unittest.mock import MagicMock

import pytest

from dhan_telegram_bridge import DhanTelegramBridge


@pytest.fixture
def mock_position_tracker():
    tracker = MagicMock()
    tracker.on_sl_hit = MagicMock()
    tracker.on_target_hit = MagicMock()
    return tracker


@pytest.fixture
def mock_dhan_integration(mock_position_tracker):
    dhan = MagicMock()
    dhan.position_tracker = mock_position_tracker
    dhan.place_trade.return_value = (True, "Order placed", "ORD123")
    dhan.get_summary.return_value = {
        "daily_pnl": 1250.50,
        "positions": {
            "openPositions": 1,
            "closedPositions": 2,
            "wins": 2,
            "losses": 0,
            "winRate": "100%",
        },
    }
    dhan.get_positions.return_value = {"open": []}
    dhan.close_trade.return_value = (True, "Closed", 200.0)
    return dhan


@pytest.fixture
def bridge(mock_dhan_integration):
    return DhanTelegramBridge(
        telegram_bot_token="dummy-token",
        dhan_integration=mock_dhan_integration,
        alert_chat_id=12345,
    )
