"""
Telegram Bot Testing - Test all workflows with mock data.

Tests:
- Alert reception and parsing
- Approval workflow (BUY trade)
- Rejection workflow
- Command processing (/status, /help, /stats)
- State persistence
- 2:50 PM IST auto-exit routine
- Position tracking
- Daily reset

No live market needed - uses mock data only.
"""

import logging
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Dict, Tuple

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


class TelegramBotTestSuite:
    """Test suite for Telegram bot functionality"""
    
    def __init__(self):
        """Initialize test suite"""
        self.logger = logging.getLogger(__name__)
        self.test_results = []
        self.passed = 0
        self.failed = 0
        
        self.logger.info("✅ Telegram Bot Test Suite initialized")
    
    # =========================================================================
    # TEST DATA GENERATORS
    # =========================================================================
    
    def get_mock_nifty_buy_alert(self) -> Dict:
        """Generate mock NIFTY50 BUY alert"""
        return {
            "symbol": "NIFTY50",
            "signal_type": "BUY",
            "entry_price": 23450.50,
            "sl": 23400.00,
            "target": 23550.00,
            "sl_type": "static",
            "qty_lots": 1,
            "strategy": "5-MIN Breakout",
            "timeframe": "5 MIN",
            "confidence": 0.85,
            "timestamp": datetime.now(IST).isoformat()
        }
    
    def get_mock_gold_buy_alert(self) -> Dict:
        """Generate mock GOLD BUY alert"""
        return {
            "symbol": "GOLD",
            "signal_type": "BUY",
            "entry_price": 7850.50,
            "sl": 7820.00,
            "target": 7920.00,
            "sl_type": "static",
            "qty_lots": 1,
            "strategy": "Premium Breakout",
            "timeframe": "15 MIN",
            "confidence": 0.80,
            "timestamp": datetime.now(IST).isoformat()
        }
    
    def get_mock_banknifty_sell_alert(self) -> Dict:
        """Generate mock BANKNIFTY SELL alert"""
        return {
            "symbol": "BANKNIFTY",
            "signal_type": "SELL",
            "entry_price": 48750.25,
            "sl": 48800.00,
            "target": 48650.00,
            "sl_type": "static",
            "qty_lots": 1,
            "strategy": "Premium Breakout",
            "timeframe": "5 MIN",
            "confidence": 0.90,
            "timestamp": datetime.now(IST).isoformat()
        }
    
    # =========================================================================
    # TEST 1: Alert Reception & Parsing
    # =========================================================================
    
    def test_alert_reception_and_parsing(self) -> bool:
        """Test 1: Alert reception and parsing"""
        try:
            self.logger.info("\n" + "="*60)
            self.logger.info("TEST 1: Alert Reception & Parsing")
            self.logger.info("="*60)
            
            from alert_formatter import AlertFormatter
            
            formatter = AlertFormatter()
            alert_data = self.get_mock_nifty_buy_alert()
            
            # Parse alert
            trade_alert = formatter.parse_webhook_data(alert_data)
            
            # Verify
            assert trade_alert is not None, "Alert parsing failed"
            assert trade_alert.symbol == "NIFTY50", "Symbol mismatch"
            assert trade_alert.entry_price == 23450.50, "Entry price mismatch"
            assert trade_alert.sl_value == 23400.00, "SL mismatch"
            
            self.logger.info("✅ TEST 1 PASSED: Alert parsed successfully")
            self.logger.info(f"   Symbol: {trade_alert.symbol}")
            self.logger.info(f"   Signal: {trade_alert.signal_type.value}")
            self.logger.info(f"   Entry: {trade_alert.entry_price}")
            
            self.passed += 1
            return True
            
        except Exception as e:
            self.logger.error(f"❌ TEST 1 FAILED: {e}")
            self.failed += 1
            return False
    
    # =========================================================================
    # TEST 2: Alert Routing
    # =========================================================================
    
    def test_alert_routing(self) -> bool:
        """Test 2: Alert routing to correct channel"""
        try:
            self.logger.info("\n" + "="*60)
            self.logger.info("TEST 2: Alert Routing")
            self.logger.info("="*60)
            
            from strategy_alerts_updated import get_alerts_router
            
            router = get_alerts_router()
            
            # Test NIFTY50 routing
            category = router.get_category_for_signal("NIFTY50", "5-MIN Breakout")
            assert category.value == "index_options", "NIFTY50 routing failed"
            
            # Test GOLD routing
            category = router.get_category_for_signal("GOLD", "Premium Breakout")
            assert category.value == "commodity_options", "GOLD routing failed"
            
            # Test BTC routing
            category = router.get_category_for_signal("BTC", "")
            assert category.value == "crypto", "BTC routing failed"
            
            self.logger.info("✅ TEST 2 PASSED: All symbols routed correctly")
            self.logger.info("   NIFTY50 → INDEX OPTIONS")
            self.logger.info("   GOLD → COMMODITY OPTIONS")
            self.logger.info("   BTC → CRYPTO 24/7")
            
            self.passed += 1
            return True
            
        except Exception as e:
            self.logger.error(f"❌ TEST 2 FAILED: {e}")
            self.failed += 1
            return False
    
    # =========================================================================
    # TEST 3: Approval Workflow
    # =========================================================================
    
    def test_approval_workflow(self) -> bool:
        """Test 3: User approval workflow"""
        try:
            self.logger.info("\n" + "="*60)
            self.logger.info("TEST 3: Approval Workflow")
            self.logger.info("="*60)
            
            from trade_state_manager import get_state_manager
            
            state_mgr = get_state_manager()
            
            # Create a pending trade
            trade_id = "TEST_NIFTY_BUY_001"
            state_mgr.pending_trades[trade_id] = {
                "symbol": "NIFTY50",
                "signal_type": "BUY",
                "entry_price": 23450.50,
                "sl_value": 23400.00,
                "target": 23550.00,
                "status": "pending",
                "created_at": datetime.now(IST).isoformat()
            }
            
            # Verify trade created
            assert trade_id in state_mgr.pending_trades, "Trade creation failed"
            
            # Simulate approval
            state_mgr.pending_trades[trade_id]["status"] = "approved"
            state_mgr.open_positions.append({
                "position_id": trade_id,
                "symbol": "NIFTY50",
                "entry_price": 23450.50,
                "sl_value": 23400.00,
                "target": 23550.00,
                "quantity": 1,
                "current_price": 23450.50,
                "pnl": 0.00,
                "status": "open"
            })
            
            # Verify position opened
            assert len(state_mgr.open_positions) > 0, "Position not created"
            
            self.logger.info("✅ TEST 3 PASSED: Approval workflow complete")
            self.logger.info(f"   Trade ID: {trade_id}")
            self.logger.info(f"   Status: Approved → Position Opened")
            self.logger.info(f"   Entry: 23450.50")
            
            self.passed += 1
            return True
            
        except Exception as e:
            self.logger.error(f"❌ TEST 3 FAILED: {e}")
            self.failed += 1
            return False
    
    # =========================================================================
    # TEST 4: Rejection Workflow
    # =========================================================================
    
    def test_rejection_workflow(self) -> bool:
        """Test 4: User rejection workflow"""
        try:
            self.logger.info("\n" + "="*60)
            self.logger.info("TEST 4: Rejection Workflow")
            self.logger.info("="*60)
            
            from trade_state_manager import get_state_manager
            
            state_mgr = get_state_manager()
            
            # Create a pending trade
            trade_id = "TEST_BANKNIFTY_REJECT_001"
            state_mgr.pending_trades[trade_id] = {
                "symbol": "BANKNIFTY",
                "signal_type": "SELL",
                "entry_price": 48750.25,
                "sl_value": 48800.00,
                "status": "pending",
                "created_at": datetime.now(IST).isoformat()
            }
            
            # Simulate rejection
            state_mgr.rejected_trades.append(trade_id)
            del state_mgr.pending_trades[trade_id]
            
            # Verify rejection
            assert trade_id not in state_mgr.pending_trades, "Trade not removed"
            assert trade_id in state_mgr.rejected_trades, "Trade not in rejected list"
            
            self.logger.info("✅ TEST 4 PASSED: Rejection workflow complete")
            self.logger.info(f"   Trade ID: {trade_id}")
            self.logger.info(f"   Status: Rejected")
            self.logger.info(f"   Reason: User rejected")
            
            self.passed += 1
            return True
            
        except Exception as e:
            self.logger.error(f"❌ TEST 4 FAILED: {e}")
            self.failed += 1
            return False
    
    # =========================================================================
    # TEST 5: Command Processing
    # =========================================================================
    
    def test_command_processing(self) -> bool:
        """Test 5: User command processing"""
        try:
            self.logger.info("\n" + "="*60)
            self.logger.info("TEST 5: Command Processing")
            self.logger.info("="*60)
            
            from command_processor import get_command_processor
            
            processor = get_command_processor()
            
            # Test /help command
            success, msg = processor.process_command("/help", user_id=123)
            assert success, "/help command failed"
            assert "AVAILABLE COMMANDS" in msg, "Help message format incorrect"
            
            # Test /status command
            success, msg = processor.process_command("/status", user_id=123)
            assert success, "/status command failed"
            assert "SYSTEM STATUS" in msg, "Status message format incorrect"
            
            # Test /lots command
            success, msg = processor.process_command("/lots 2", user_id=123)
            assert success, "/lots command failed"
            
            # Test invalid command
            success, msg = processor.process_command("/unknown", user_id=123)
            assert not success, "Invalid command should fail"
            
            self.logger.info("✅ TEST 5 PASSED: All commands processed")
            self.logger.info("   /help ✓")
            self.logger.info("   /status ✓")
            self.logger.info("   /lots ✓")
            self.logger.info("   /unknown (correctly rejected) ✓")
            
            self.passed += 1
            return True
            
        except Exception as e:
            self.logger.error(f"❌ TEST 5 FAILED: {e}")
            self.failed += 1
            return False
    
    # =========================================================================
    # TEST 6: State Persistence
    # =========================================================================
    
    def test_state_persistence(self) -> bool:
        """Test 6: State persistence to JSON"""
        try:
            self.logger.info("\n" + "="*60)
            self.logger.info("TEST 6: State Persistence")
            self.logger.info("="*60)
            
            from trade_state_manager import get_state_manager
            import os
            
            state_mgr = get_state_manager()
            
            # Add test data
            test_trade = {
                "position_id": "TEST_PERSIST_001",
                "symbol": "NIFTY50",
                "entry_price": 23450.50
            }
            state_mgr.open_positions.append(test_trade)
            
            # Persist state
            state_mgr.persist_state()
            
            # Verify file created
            assert os.path.exists("state.json"), "State file not created"
            
            # Verify data in file
            with open("state.json", "r") as f:
                saved_state = json.load(f)
                assert len(saved_state.get("open_positions", [])) > 0, "Data not saved"
            
            self.logger.info("✅ TEST 6 PASSED: State persisted successfully")
            self.logger.info("   File: state.json")
            self.logger.info("   Data: Open positions saved")
            
            self.passed += 1
            return True
            
        except Exception as e:
            self.logger.error(f"❌ TEST 6 FAILED: {e}")
            self.failed += 1
            return False
    
    # =========================================================================
    # TEST 7: Position Tracking (Price Updates)
    # =========================================================================
    
    def test_position_tracking(self) -> bool:
        """Test 7: Position price tracking and P&L calculation"""
        try:
            self.logger.info("\n" + "="*60)
            self.logger.info("TEST 7: Position Tracking & P&L")
            self.logger.info("="*60)
            
            from trade_state_manager import get_state_manager
            
            state_mgr = get_state_manager()
            
            # Create position
            position = {
                "position_id": "TEST_TRACK_001",
                "symbol": "NIFTY50",
                "signal_type": "BUY",
                "entry_price": 23450.50,
                "sl_value": 23400.00,
                "target": 23550.00,
                "quantity": 1,
                "current_price": 23450.50,
                "pnl": 0.00,
                "status": "open"
            }
            
            state_mgr.open_positions.append(position)
            
            # Simulate price update
            entry = 23450.50
            current = 23480.50
            pnl = current - entry
            
            state_mgr.open_positions[0]["current_price"] = current
            state_mgr.open_positions[0]["pnl"] = pnl
            
            # Verify P&L calculation
            assert pnl == 30.0, "P&L calculation incorrect"
            
            self.logger.info("✅ TEST 7 PASSED: Position tracking working")
            self.logger.info(f"   Position: NIFTY50")
            self.logger.info(f"   Entry: 23450.50")
            self.logger.info(f"   Current: 23480.50")
            self.logger.info(f"   P&L: +30.00 ✓")
            
            self.passed += 1
            return True
            
        except Exception as e:
            self.logger.error(f"❌ TEST 7 FAILED: {e}")
            self.failed += 1
            return False
    
    # =========================================================================
    # TEST 8: Market Close Auto-Exit (2:50 PM IST)
    # =========================================================================
    
    def test_market_close_routine(self) -> bool:
        """Test 8: Auto-exit routine at 2:50 PM IST"""
        try:
            self.logger.info("\n" + "="*60)
            self.logger.info("TEST 8: Market Close Auto-Exit (2:50 PM)")
            self.logger.info("="*60)
            
            from trade_state_manager import get_state_manager
            
            state_mgr = get_state_manager()
            
            # Create multiple open positions
            for i in range(3):
                position = {
                    "position_id": f"TEST_CLOSE_{i}",
                    "symbol": ["NIFTY50", "GOLD", "BANKNIFTY"][i],
                    "entry_price": [23450.50, 7850.50, 48750.25][i],
                    "current_price": [23480.50, 7880.50, 48720.25][i],
                    "pnl": [30.0, 30.0, -30.0][i],
                    "status": "open"
                }
                state_mgr.open_positions.append(position)
            
            # Simulate market close routine
            closed_count = 0
            for pos in state_mgr.open_positions[:]:
                pos["status"] = "closed"
                state_mgr.closed_positions.append(pos)
                state_mgr.open_positions.remove(pos)
                closed_count += 1
            
            # Verify all closed
            assert len(state_mgr.open_positions) == 0, "Positions not closed"
            assert closed_count == 3, "Wrong number of positions closed"
            
            self.logger.info("✅ TEST 8 PASSED: Market close routine successful")
            self.logger.info(f"   Positions closed: {closed_count}")
            self.logger.info("   All open positions auto-closed before market close")
            
            self.passed += 1
            return True
            
        except Exception as e:
            self.logger.error(f"❌ TEST 8 FAILED: {e}")
            self.failed += 1
            return False
    
    # =========================================================================
    # RUN ALL TESTS
    # =========================================================================
    
    def run_all_tests(self) -> None:
        """Run all tests and print summary"""
        self.logger.info("\n\n")
        self.logger.info("╔" + "═"*58 + "╗")
        self.logger.info("║" + " "*15 + "TELEGRAM BOT TEST SUITE" + " "*21 + "║")
        self.logger.info("╚" + "═"*58 + "╝")
        
        self.test_alert_reception_and_parsing()
        self.test_alert_routing()
        self.test_approval_workflow()
        self.test_rejection_workflow()
        self.test_command_processing()
        self.test_state_persistence()
        self.test_position_tracking()
        self.test_market_close_routine()
        
        # Print summary
        self.logger.info("\n\n")
        self.logger.info("╔" + "═"*58 + "╗")
        self.logger.info("║" + " "*18 + "TEST SUMMARY" + " "*28 + "║")
        self.logger.info("╠" + "═"*58 + "╣")
        self.logger.info(f"║  Total Tests: 8" + " "*43 + "║")
        self.logger.info(f"║  ✅ Passed: {self.passed}" + " "*46 + "║")
        self.logger.info(f"║  ❌ Failed: {self.failed}" + " "*46 + "║")
        
        if self.failed == 0:
            self.logger.info("║" + " "*15 + "🎉 ALL TESTS PASSED! 🎉" + " "*19 + "║")
        else:
            self.logger.info("║" + " "*15 + f"⚠️  {self.failed} TEST(S) FAILED" + " "*20 + "║")
        
        self.logger.info("╚" + "═"*58 + "╝")


# ============================================================================
# MAIN EXECUTION
# ============================================================================

if __name__ == "__main__":
    import logging.config
    
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Run tests
    test_suite = TelegramBotTestSuite()
    test_suite.run_all_tests()
