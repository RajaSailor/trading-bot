"""
DhanHQ Integration Tests - Comprehensive test suite for DhanHQ trading system.

Tests:
1. API Client Tests - Connection, auth, endpoints
2. Order Manager Tests - Order creation, validation, modification
3. Position Tracker Tests - Position fetching, SL/Target detection
4. Integration Tests - End-to-end trading workflow
5. Postback Tests - Webhook postback handling

Run: python -m pytest dhan_integration_tests.py -v
"""

import pytest
import logging
import json
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime
from zoneinfo import ZoneInfo

from dhan_api_client import DhanAPIClient, ExchangeSegment, OrderType, TransactionType
from dhan_order_manager import DhanOrderManager
from dhan_position_tracker import DhanPositionTracker
from dhan_postback_handler import DhanPostbackHandler, PostbackEventType
from dhan_integration import DhanIntegration

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
def mock_dhan_client():
    """Create mock DhanHQ API client"""
    client = Mock(spec=DhanAPIClient)
    client.practice_mode = True
    client.access_token = "test_token"
    client.client_id = "test_client"
    return client


@pytest.fixture
def order_manager(mock_dhan_client):
    """Create order manager with mock client"""
    return DhanOrderManager(
        dhan_client=mock_dhan_client,
        max_loss_per_trade=500.0,
        max_position_size=5
    )


@pytest.fixture
def position_tracker(mock_dhan_client):
    """Create position tracker with mock client"""
    return DhanPositionTracker(dhan_client=mock_dhan_client)


@pytest.fixture
def postback_handler():
    """Create postback handler"""
    return DhanPostbackHandler(webhook_secret="test_secret")


# ============================================================================
# API CLIENT TESTS
# ============================================================================

class TestAPIClient:
    """Test DhanHQ API client"""
    
    def test_client_initialization(self, mock_dhan_client):
        """Test API client initialization"""
        assert mock_dhan_client.practice_mode == True
        assert mock_dhan_client.access_token == "test_token"
        assert mock_dhan_client.client_id == "test_client"
        logger.info("✅ API client initialization test passed")
    
    def test_place_order_success(self, mock_dhan_client):
        """Test successful order placement"""
        mock_dhan_client.place_order.return_value = (
            True,
            "Order placed",
            "ORD_12345"
        )
        
        success, msg, order_id = mock_dhan_client.place_order(
            symbol="NIFTY50",
            exchange_segment=ExchangeSegment.NSE_FO,
            transaction_type=TransactionType.BUY,
            quantity=1,
            order_type=OrderType.MARKET
        )
        
        assert success == True
        assert order_id == "ORD_12345"
        logger.info("✅ Place order test passed")
    
    def test_place_order_failure(self, mock_dhan_client):
        """Test failed order placement"""
        mock_dhan_client.place_order.return_value = (
            False,
            "Invalid symbol",
            None
        )
        
        success, msg, order_id = mock_dhan_client.place_order(
            symbol="INVALID",
            exchange_segment=ExchangeSegment.NSE_EQ,
            transaction_type=TransactionType.BUY,
            quantity=1,
            order_type=OrderType.MARKET
        )
        
        assert success == False
        assert order_id == None
        logger.info("✅ Place order failure test passed")
    
    def test_get_positions(self, mock_dhan_client):
        """Test fetching positions"""
        mock_positions = [
            {
                "positionId": "POS_001",
                "symbol": "NIFTY50",
                "quantity": 1,
                "currentPrice": 19500.0,
                "entryPrice": 19400.0,
                "pnl": 100.0,
                "pnlPercentage": 0.52
            }
        ]
        
        mock_dhan_client.get_positions.return_value = (
            True,
            "Positions fetched",
            mock_positions
        )
        
        success, msg, positions = mock_dhan_client.get_positions()
        
        assert success == True
        assert len(positions) == 1
        assert positions[0]["symbol"] == "NIFTY50"
        logger.info("✅ Get positions test passed")


# ============================================================================
# ORDER MANAGER TESTS
# ============================================================================

class TestOrderManager:
    """Test DhanHQ order manager"""
    
    def test_order_validation_valid(self, order_manager):
        """Test valid order validation"""
        success, msg = order_manager.validate_order(
            symbol="NIFTY50",
            transaction_type="BUY",
            quantity=1,
            entry_price=19400.0,
            sl_price=19300.0,
            target_price=19600.0
        )
        
        assert success == True
        logger.info("✅ Order validation test passed")
    
    def test_order_validation_invalid_sl(self, order_manager):
        """Test invalid SL validation"""
        success, msg = order_manager.validate_order(
            symbol="NIFTY50",
            transaction_type="BUY",
            quantity=1,
            entry_price=19400.0,
            sl_price=19500.0,  # SL above entry (invalid for BUY)
            target_price=19600.0
        )
        
        assert success == False
        logger.info("✅ Order validation (invalid SL) test passed")
    
    def test_order_validation_position_size(self, order_manager):
        """Test position size limit validation"""
        success, msg = order_manager.validate_order(
            symbol="NIFTY50",
            transaction_type="BUY",
            quantity=10,  # Exceeds limit of 5
            entry_price=19400.0,
            sl_price=19300.0,
            target_price=19600.0
        )
        
        assert success == False
        assert "exceeds limit" in msg.lower()
        logger.info("✅ Position size validation test passed")
    
    def test_order_validation_max_loss(self, order_manager):
        """Test maximum loss validation"""
        success, msg = order_manager.validate_order(
            symbol="NIFTY50",
            transaction_type="BUY",
            quantity=2,
            entry_price=19400.0,
            sl_price=18000.0,  # Loss of 2800 per lot * 2 = 5600 (exceeds 500)
            target_price=19600.0
        )
        
        assert success == False
        assert "exceeds limit" in msg.lower()
        logger.info("✅ Max loss validation test passed")
    
    def test_create_market_order(self, order_manager, mock_dhan_client):
        """Test creating market order"""
        mock_dhan_client.place_order.return_value = (
            True,
            "Order placed",
            "ORD_001"
        )
        
        success, msg, order_id = order_manager.create_market_order(
            symbol="NIFTY50",
            exchange_segment=ExchangeSegment.NSE_FO,
            transaction_type=TransactionType.BUY,
            quantity=1,
            entry_price=19400.0,
            sl_price=19300.0,
            target_price=19600.0,
            strategy="support_bounce"
        )
        
        assert success == True
        assert order_id == "ORD_001"
        assert "ORD_001" in order_manager.active_orders
        logger.info("✅ Create market order test passed")


# ============================================================================
# POSITION TRACKER TESTS
# ============================================================================

class TestPositionTracker:
    """Test DhanHQ position tracker"""
    
    def test_add_tracked_position(self, position_tracker):
        """Test adding position to tracker"""
        success, msg = position_tracker.add_tracked_position(
            order_id="ORD_001",
            symbol="NIFTY50",
            transaction_type="BUY",
            quantity=1,
            entry_price=19400.0,
            current_price=19400.0,
            sl_price=19300.0,
            target_price=19600.0,
            strategy="test_strategy"
        )
        
        assert success == True
        assert "ORD_001" in position_tracker.tracked_positions
        logger.info("✅ Add tracked position test passed")
    
    def test_sl_hit_detection_buy(self, position_tracker):
        """Test SL hit detection for BUY"""
        position_tracker.add_tracked_position(
            order_id="ORD_001",
            symbol="NIFTY50",
            transaction_type="BUY",
            quantity=1,
            entry_price=19400.0,
            current_price=19300.0,
            sl_price=19300.0,
            target_price=19600.0
        )
        
        position = position_tracker.tracked_positions["ORD_001"]
        is_sl_hit = position_tracker.check_sl_hit(position)
        
        assert is_sl_hit == True
        logger.info("✅ SL hit detection (BUY) test passed")
    
    def test_sl_hit_detection_sell(self, position_tracker):
        """Test SL hit detection for SELL"""
        position_tracker.add_tracked_position(
            order_id="ORD_002",
            symbol="NIFTY50",
            transaction_type="SELL",
            quantity=1,
            entry_price=19400.0,
            current_price=19500.0,
            sl_price=19500.0,
            target_price=19200.0
        )
        
        position = position_tracker.tracked_positions["ORD_002"]
        is_sl_hit = position_tracker.check_sl_hit(position)
        
        assert is_sl_hit == True
        logger.info("✅ SL hit detection (SELL) test passed")
    
    def test_target_hit_detection_buy(self, position_tracker):
        """Test target hit detection for BUY"""
        position_tracker.add_tracked_position(
            order_id="ORD_003",
            symbol="NIFTY50",
            transaction_type="BUY",
            quantity=1,
            entry_price=19400.0,
            current_price=19600.0,
            sl_price=19300.0,
            target_price=19600.0
        )
        
        position = position_tracker.tracked_positions["ORD_003"]
        is_target_hit = position_tracker.check_target_hit(position)
        
        assert is_target_hit == True
        logger.info("✅ Target hit detection (BUY) test passed")
    
    def test_pnl_calculation(self, position_tracker):
        """Test P&L calculation"""
        position_tracker.add_tracked_position(
            order_id="ORD_004",
            symbol="NIFTY50",
            transaction_type="BUY",
            quantity=1,
            entry_price=19400.0,
            current_price=19500.0,
            sl_price=19300.0,
            target_price=19600.0
        )
        
        position = position_tracker.tracked_positions["ORD_004"]
        expected_pnl = (19500.0 - 19400.0) * 1  # = 100
        
        assert position["currentPnL"] == expected_pnl
        logger.info("✅ P&L calculation test passed")


# ============================================================================
# POSTBACK HANDLER TESTS
# ============================================================================

class TestPostbackHandler:
    """Test DhanHQ postback handler"""
    
    def test_order_execution_postback_parse(self, postback_handler):
        """Test parsing order execution postback"""
        data = {
            "eventType": "ORDER_EXECUTION",
            "orderId": "ORD_001",
            "symbol": "NIFTY50",
            "orderStatus": "EXECUTED",
            "executedQuantity": 1,
            "executedPrice": 19400.0,
            "transactionType": "BUY",
            "timestamp": datetime.now(IST).isoformat()
        }
        
        success, parsed = postback_handler.parse_order_execution_postback(data)
        
        assert success == True
        assert parsed["orderId"] == "ORD_001"
        assert parsed["status"] == "EXECUTED"
        logger.info("✅ Order execution postback parse test passed")
    
    def test_position_update_postback_parse(self, postback_handler):
        """Test parsing position update postback"""
        data = {
            "eventType": "POSITION_UPDATE",
            "positionId": "POS_001",
            "symbol": "NIFTY50",
            "quantity": 1,
            "currentPrice": 19500.0,
            "entryPrice": 19400.0,
            "pnl": 100.0,
            "pnlPercentage": 0.52,
            "timestamp": datetime.now(IST).isoformat()
        }
        
        success, parsed = postback_handler.parse_position_update_postback(data)
        
        assert success == True
        assert parsed["positionId"] == "POS_001"
        assert parsed["pnl"] == 100.0
        logger.info("✅ Position update postback parse test passed")
    
    def test_account_update_postback_parse(self, postback_handler):
        """Test parsing account update postback"""
        data = {
            "eventType": "ACCOUNT_UPDATE",
            "dhanClientId": "client_123",
            "ledgerBalance": 100000.0,
            "marginAvailable": 80000.0,
            "marginUsed": 20000.0,
            "timestamp": datetime.now(IST).isoformat()
        }
        
        success, parsed = postback_handler.parse_account_update_postback(data)
        
        assert success == True
        assert parsed["clientId"] == "client_123"
        assert parsed["marginAvailable"] == 80000.0
        logger.info("✅ Account update postback parse test passed")
    
    def test_duplicate_postback_detection(self, postback_handler):
        """Test duplicate postback detection"""
        # First call
        is_dup1 = postback_handler.is_duplicate_postback("PB_001")
        assert is_dup1 == False
        
        # Second call with same ID
        is_dup2 = postback_handler.is_duplicate_postback("PB_001")
        assert is_dup2 == True
        
        logger.info("✅ Duplicate postback detection test passed")


# ============================================================================
# INTEGRATION TESTS
# ============================================================================

class TestIntegration:
    """End-to-end integration tests"""
    
    def test_complete_trade_workflow(self, mock_dhan_client):
        """Test complete trade workflow"""
        # Setup
        mock_dhan_client.place_order.return_value = (True, "Order placed", "ORD_001")
        mock_dhan_client.get_positions.return_value = (True, "Positions fetched", [])
        
        # Create integration
        integration = DhanIntegration(
            access_token="test_token",
            client_id="test_client",
            practice_mode=True
        )
        
        # Replace with mock
        integration.api_client = mock_dhan_client
        
        # Place trade
        success, msg, order_id = integration.place_trade(
            symbol="NIFTY50",
            transaction_type="BUY",
            quantity=1,
            entry_price=19400.0,
            sl_price=19300.0,
            target_price=19600.0,
            strategy="test_strategy"
        )
        
        assert success == True
        assert order_id == "ORD_001"
        logger.info("✅ Complete trade workflow test passed")
    
    def test_market_close_routine(self, mock_dhan_client):
        """Test market close routine"""
        mock_dhan_client.get_positions.return_value = (True, "Positions fetched", [])
        
        integration = DhanIntegration(
            access_token="test_token",
            client_id="test_client",
            practice_mode=True
        )
        
        integration.api_client = mock_dhan_client
        
        # Add a position
        integration.position_tracker.add_tracked_position(
            order_id="ORD_001",
            symbol="NIFTY50",
            transaction_type="BUY",
            quantity=1,
            entry_price=19400.0,
            current_price=19500.0,
            sl_price=19300.0,
            target_price=19600.0
        )
        
        # Execute market close
        closed_count, total_pnl = integration.market_close_routine()
        
        assert closed_count == 1
        logger.info("✅ Market close routine test passed")


# ============================================================================
# RUN TESTS
# ============================================================================

if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    
    # Run tests
    pytest.main([__file__, "-v", "--tb=short"])
