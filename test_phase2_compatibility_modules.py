import pytest

from config.dhanHQ_config import DhanHQConfig
from data_manager import DataManager
from dhan_api_client import DhanAPIClient as CoreDhanApiClient
from dhan_order_manager import DhanOrderManager
from dhan_position_tracker import DhanPositionTracker
from market_data import DataManager as Phase2DataManager
from orders import DhanOrderManager as Phase2OrderManager
from positions import DhanPositionTracker as Phase2PositionTracker
from webhook_handler import WebhookHandler as CoreWebhookHandler
from webhooks import WebhookHandler as Phase2WebhookHandler


def test_phase2_wrapper_exports_map_to_existing_implementations():
    assert Phase2DataManager is DataManager
    assert Phase2OrderManager is DhanOrderManager
    assert Phase2PositionTracker is DhanPositionTracker
    assert Phase2WebhookHandler is CoreWebhookHandler


def test_phase2_dhanhq_client_exports_core_client():
    from dhanHQ_client import DhanAPIClient as Phase2DhanApiClient

    assert Phase2DhanApiClient is CoreDhanApiClient


def test_dhanhq_config_defaults_and_endpoints_are_available():
    assert DhanHQConfig.API_BASE_URL.startswith("https://")
    assert DhanHQConfig.TIMEOUT_SECONDS > 0
    assert DhanHQConfig.MAX_RETRIES >= 0
    assert DhanHQConfig.endpoint_url("orders").endswith("/orders")


def test_dhanhq_config_invalid_endpoint_key_raises_helpful_error():
    with pytest.raises(ValueError) as exc_info:
        DhanHQConfig.endpoint_url("unknown")

    error_message = str(exc_info.value)
    assert "Unknown DhanHQ endpoint key 'unknown'" in error_message
    assert "orders" in error_message


def test_dhanhq_config_endpoint_url_uses_latest_environment_value(monkeypatch):
    monkeypatch.setenv("DHANHQ_API_BASE_URL", "https://example.com/v2")
    assert DhanHQConfig.API_BASE_URL == "https://example.com/v2"
    assert DhanHQConfig.endpoint_url("orders") == "https://example.com/v2/orders"
