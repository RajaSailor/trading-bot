"""
Mock DhanHQ Webhook Responses - Simulate DhanHQ postback for testing.

Simulates:
- Order confirmation postbacks
- Position updates
- SL/Target hit notifications
- Execution confirmations
- Order rejection responses
- Real-time price updates for positions

Uses official DhanHQ response formats from:
https://github.com/Kalaiviswa/dhan-api-v2-docs/blob/main/13-live-order-update.md
"""

import logging
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Dict, Optional
from enum import Enum

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


class OrderStatus(Enum):
    """DhanHQ Order Status codes (from official docs)"""
    PENDING = "PENDING"
    LIVE = "LIVE"
    EXECUTED = "EXECUTED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class ExchangeSegment(Enum):
    """DhanHQ Exchange segments (from official docs)"""
    NSE_EQ = "NSE_EQ"      # NSE Equity
    NSE_FO = "NSE_FO"      # NSE Futures & Options
    BSE_EQ = "BSE_EQ"      # BSE Equity
    MCXSX = "MCXSX"        # MCX Commodity Futures


class MockDhanWebhook:
    """
    Mock DhanHQ Webhook Responses
    Generates realistic postback responses based on official DhanHQ format
    """
    
    def __init__(self):
        """Initialize mock webhook generator"""
        self.logger = logging.getLogger(__name__)
        self.order_counter = 1000000
        self.logger.info("✅ Mock DhanHQ Webhook initialized")
    
    # =========================================================================
    # OFFICIAL DHANQ RESPONSE FORMATS (From Docs)
    # =========================================================================
    
    def get_order_confirmation_response(
        self,
        symbol: str,
        transaction_type: str,
        quantity: int,
        price: float,
        order_type: str = "MARKET"
    ) -> Dict:
        """
        Generate DhanHQ order confirmation postback.
        
        Response format from official docs:
        https://github.com/Kalaiviswa/dhan-api-v2-docs/blob/main/13-live-order-update.md
        
        Args:
            symbol: Trading symbol (e.g., "NIFTY50")
            transaction_type: BUY or SELL
            quantity: Order quantity (in lots)
            price: Order price
            order_type: MARKET, LIMIT, STOP_LOSS
            
        Returns:
            Order confirmation response dict (official format)
        """
        self.order_counter += 1
        
        response = {
            "orderId": str(self.order_counter),
            "dhanClientId": "1111778442",
            "correlationId": f"TEST_{datetime.now(IST).timestamp()}",
            "orderStatus": "LIVE",
            "executionStatus": "PENDING",
            "transactionType": transaction_type,
            "exchangeSegment": "NSE_EQ",
            "productType": "INTRADAY",
            "orderType": order_type,
            "validity": "DAY",
            "securityId": self._get_security_id(symbol),
            "quantity": quantity,
            "price": price,
            "disclosedQuantity": 0,
            "bidsQuantity": 0,
            "asksQuantity": 0,
            "remainingQuantity": quantity,
            "filledQuantity": 0,
            "cancelledQuantity": 0,
            "averagePrice": 0.0,
            "totalTradedValue": 0.0,
            "ipoAllotmentQuantity": 0,
            "booked_profit": 0.0,
            "booked_loss": 0.0,
            "booked_brokerage": 0.0,
            "orderedTime": datetime.now(IST).isoformat(),
            "exchangeOrderId": f"EX{self.order_counter}",
            "exchangeTime": datetime.now(IST).isoformat(),
            "isAmendable": True,
            "isCancellable": True,
            "isSpreadOrder": False,
            "modifiedTime": None,
            "legDetails": []
        }
        
        self.logger.info(
            f"✅ Order Confirmation Generated\n"
            f"   Order ID: {response['orderId']}\n"
            f"   Symbol: {symbol}\n"
            f"   Type: {transaction_type}\n"
            f"   Qty: {quantity}\n"
            f"   Price: {price}\n"
            f"   Status: LIVE"
        )
        
        return response
    
    def get_order_execution_response(
        self,
        order_id: str,
        symbol: str,
        quantity: int,
        execution_price: float
    ) -> Dict:
        """
        Generate DhanHQ order execution postback (after fill).
        
        Args:
            order_id: Order ID from confirmation
            symbol: Trading symbol
            quantity: Executed quantity
            execution_price: Price at execution
            
        Returns:
            Execution response dict (official format)
        """
        response = {
            "orderId": order_id,
            "dhanClientId": "1111778442",
            "orderStatus": "EXECUTED",
            "executionStatus": "SUCCESS",
            "transactionType": "BUY",
            "exchangeSegment": "NSE_EQ",
            "productType": "INTRADAY",
            "orderType": "MARKET",
            "securityId": self._get_security_id(symbol),
            "quantity": quantity,
            "price": 0.0,
            "remainingQuantity": 0,
            "filledQuantity": quantity,
            "averagePrice": execution_price,
            "totalTradedValue": quantity * execution_price,
            "booked_profit": 0.0,
            "booked_loss": 0.0,
            "orderedTime": datetime.now(IST).isoformat(),
            "executionTime": datetime.now(IST).isoformat(),
            "exchangeOrderId": f"EX{order_id}",
            "exchangeTime": datetime.now(IST).isoformat()
        }
        
        self.logger.info(
            f"✅ Order Execution Confirmed\n"
            f"   Order ID: {order_id}\n"
            f"   Symbol: {symbol}\n"
            f"   Filled Qty: {quantity}\n"
            f"   Avg Price: {execution_price}\n"
            f"   Total Value: {quantity * execution_price}"
        )
        
        return response
    
    def get_order_rejected_response(
        self,
        order_id: str,
        reason: str = "INSUFFICIENT_FUNDS"
    ) -> Dict:
        """
        Generate DhanHQ order rejection postback.
        
        Args:
            order_id: Order ID
            reason: Rejection reason
            
        Returns:
            Rejection response dict
        """
        response = {
            "orderId": order_id,
            "dhanClientId": "1111778442",
            "orderStatus": "REJECTED",
            "executionStatus": "FAILURE",
            "reason": reason,
            "reasonCode": "ERR_" + reason,
            "orderedTime": datetime.now(IST).isoformat(),
            "rejectionTime": datetime.now(IST).isoformat()
        }
        
        self.logger.warning(
            f"❌ Order Rejected\n"
            f"   Order ID: {order_id}\n"
            f"   Reason: {reason}"
        )
        
        return response
    
    def get_position_update_response(
        self,
        symbol: str,
        quantity: int,
        entry_price: float,
        current_price: float
    ) -> Dict:
        """
        Generate position update with P&L calculation.
        
        Args:
            symbol: Trading symbol
            quantity: Position quantity
            entry_price: Entry price
            current_price: Current market price
            
        Returns:
            Position update response dict
        """
        pnl = (current_price - entry_price) * quantity
        pnl_percentage = ((current_price - entry_price) / entry_price) * 100
        
        response = {
            "positionId": f"POS_{symbol}_{datetime.now(IST).timestamp()}",
            "symbol": symbol,
            "securityId": self._get_security_id(symbol),
            "exchangeSegment": "NSE_EQ",
            "quantity": quantity,
            "buyQuantity": quantity if pnl >= 0 else 0,
            "sellQuantity": quantity if pnl < 0 else 0,
            "entryPrice": entry_price,
            "currentPrice": current_price,
            "pnl": round(pnl, 2),
            "pnlPercentage": round(pnl_percentage, 2),
            "intraday_pnl": round(pnl, 2),
            "overnight_pnl": 0.0,
            "mtm": round(pnl, 2),
            "totalValue": round(current_price * quantity, 2),
            "productType": "INTRADAY",
            "timestamp": datetime.now(IST).isoformat()
        }
        
        self.logger.info(
            f"📊 Position Update\n"
            f"   Symbol: {symbol}\n"
            f"   Qty: {quantity}\n"
            f"   Entry: {entry_price}\n"
            f"   Current: {current_price}\n"
            f"   P&L: {response['pnl']} ({response['pnl_percentage']}%)"
        )
        
        return response
    
    def get_target_hit_response(
        self,
        position_id: str,
        symbol: str,
        target_price: float,
        current_price: float,
        entry_price: float,
        quantity: int
    ) -> Dict:
        """
        Generate target hit notification.
        
        Args:
            position_id: Position ID
            symbol: Trading symbol
            target_price: Target price set by user
            current_price: Price that hit target
            entry_price: Entry price
            quantity: Position quantity
            
        Returns:
            Target hit response dict
        """
        profit = (current_price - entry_price) * quantity
        
        response = {
            "alertType": "TARGET_HIT",
            "positionId": position_id,
            "symbol": symbol,
            "targetPrice": target_price,
            "currentPrice": current_price,
            "entryPrice": entry_price,
            "quantity": quantity,
            "profit": round(profit, 2),
            "profitPercentage": round(((current_price - entry_price) / entry_price) * 100, 2),
            "timestamp": datetime.now(IST).isoformat(),
            "message": f"🎯 TARGET HIT for {symbol}! Current: {current_price}, Target: {target_price}"
        }
        
        self.logger.info(
            f"🎯 TARGET HIT ALERT\n"
            f"   Symbol: {symbol}\n"
            f"   Target: {target_price}\n"
            f"   Current: {current_price}\n"
            f"   Profit: {profit}"
        )
        
        return response
    
    def get_sl_hit_response(
        self,
        position_id: str,
        symbol: str,
        sl_price: float,
        current_price: float,
        entry_price: float,
        quantity: int
    ) -> Dict:
        """
        Generate stop loss hit notification.
        
        Args:
            position_id: Position ID
            symbol: Trading symbol
            sl_price: Stop loss price set by user
            current_price: Price that hit SL
            entry_price: Entry price
            quantity: Position quantity
            
        Returns:
            SL hit response dict
        """
        loss = (current_price - entry_price) * quantity
        
        response = {
            "alertType": "SL_HIT",
            "positionId": position_id,
            "symbol": symbol,
            "slPrice": sl_price,
            "currentPrice": current_price,
            "entryPrice": entry_price,
            "quantity": quantity,
            "loss": round(loss, 2),
            "lossPercentage": round(((current_price - entry_price) / entry_price) * 100, 2),
            "timestamp": datetime.now(IST).isoformat(),
            "message": f"🚨 STOP LOSS HIT for {symbol}! Current: {current_price}, SL: {sl_price}",
            "autoCloseExecuted": True
        }
        
        self.logger.warning(
            f"🚨 STOP LOSS HIT ALERT\n"
            f"   Symbol: {symbol}\n"
            f"   SL: {sl_price}\n"
            f"   Current: {current_price}\n"
            f"   Loss: {loss}"
        )
        
        return response
    
    def get_position_close_response(
        self,
        position_id: str,
        symbol: str,
        exit_price: float,
        entry_price: float,
        quantity: int,
        exit_reason: str = "user_initiated"
    ) -> Dict:
        """
        Generate position close confirmation.
        
        Args:
            position_id: Position ID
            symbol: Trading symbol
            exit_price: Exit price
            entry_price: Entry price
            quantity: Position quantity
            exit_reason: Reason for closing (sl, target, user_initiated, market_close)
            
        Returns:
            Position close response dict
        """
        pnl = (exit_price - entry_price) * quantity
        
        response = {
            "alertType": "POSITION_CLOSED",
            "positionId": position_id,
            "symbol": symbol,
            "entryPrice": entry_price,
            "exitPrice": exit_price,
            "quantity": quantity,
            "pnl": round(pnl, 2),
            "pnlPercentage": round(((exit_price - entry_price) / entry_price) * 100, 2),
            "exitReason": exit_reason,
            "closedTime": datetime.now(IST).isoformat(),
            "message": f"✅ Position CLOSED for {symbol} at {exit_price}. P&L: {pnl}"
        }
        
        self.logger.info(
            f"✅ Position Closed\n"
            f"   Symbol: {symbol}\n"
            f"   Entry: {entry_price}\n"
            f"   Exit: {exit_price}\n"
            f"   P&L: {pnl}\n"
            f"   Reason: {exit_reason}"
        )
        
        return response
    
    def get_account_balance_response(self) -> Dict:
        """
        Generate DhanHQ account balance response.
        
        Returns:
            Account balance response dict
        """
        response = {
            "dhanClientId": "1111778442",
            "ledgerBalance": 100000.00,
            "marginUsed": 25000.00,
            "marginAvailable": 75000.00,
            "totalUsableBalance": 100000.00,
            "debitBalance": 0.00,
            "dayOpeningBalance": 100000.00,
            "marginMultiplier": 4.0,
            "totalMarginLimit": 400000.00,
            "appliedMarginalMargin": 0.0,
            "realizedProfit": 0.0,
            "unrealizedProfit": 0.0,
            "totalProfit": 0.0,
            "turnover": 0.0,
            "payout": 0.0,
            "payinAmount": 0.0,
            "timestamp": datetime.now(IST).isoformat()
        }
        
        self.logger.info(
            f"💰 Account Balance\n"
            f"   Ledger: {response['ledgerBalance']}\n"
            f"   Available: {response['marginAvailable']}\n"
            f"   Used: {response['marginUsed']}"
        )
        
        return response
    
    # =========================================================================
    # HELPER METHODS
    # =========================================================================
    
    def _get_security_id(self, symbol: str) -> str:
        """
        Get DhanHQ security ID for symbol.
        (In production, these come from Dhan's symbol master)
        
        Args:
            symbol: Trading symbol
            
        Returns:
            Security ID
        """
        security_ids = {
            "NIFTY50": "99926010",
            "BANKNIFTY": "99926023",
            "GOLD": "99926028",
            "CRUDE": "99926027",
            "SILVER": "99926029",
            "RELIANCE": "12345",
            "TCS": "12346",
            "INFY": "12347",
            "BTC": "99999999",
            "ETH": "99999998"
        }
        
        return security_ids.get(symbol, "00000000")
    
    def simulate_complete_trade_flow(
        self,
        symbol: str,
        quantity: int,
        entry_price: float,
        target_price: float,
        sl_price: float
    ) -> None:
        """
        Simulate a complete trade flow from order to close.
        
        Sequence:
        1. Order confirmation (LIVE)
        2. Order execution
        3. Position updates
        4. Target/SL hit
        5. Position close
        
        Args:
            symbol: Trading symbol
            quantity: Order quantity
            entry_price: Entry price
            target_price: Target price
            sl_price: Stop loss price
        """
        self.logger.info("\n" + "="*60)
        self.logger.info(f"SIMULATING COMPLETE TRADE FLOW FOR {symbol}")
        self.logger.info("="*60)
        
        # Step 1: Order confirmation
        conf_response = self.get_order_confirmation_response(
            symbol=symbol,
            transaction_type="BUY",
            quantity=quantity,
            price=entry_price
        )
        order_id = conf_response["orderId"]
        
        # Step 2: Order execution
        exec_response = self.get_order_execution_response(
            order_id=order_id,
            symbol=symbol,
            quantity=quantity,
            execution_price=entry_price
        )
        
        # Step 3: Position updates (simulate price movements)
        current_price = entry_price
        for i in range(3):
            current_price += 10 if i % 2 == 0 else -5
            self.get_position_update_response(
                symbol=symbol,
                quantity=quantity,
                entry_price=entry_price,
                current_price=current_price
            )
        
        # Step 4: Target hit
        self.get_target_hit_response(
            position_id=f"POS_{symbol}_{order_id}",
            symbol=symbol,
            target_price=target_price,
            current_price=target_price,
            entry_price=entry_price,
            quantity=quantity
        )
        
        # Step 5: Position close
        self.get_position_close_response(
            position_id=f"POS_{symbol}_{order_id}",
            symbol=symbol,
            exit_price=target_price,
            entry_price=entry_price,
            quantity=quantity,
            exit_reason="target"
        )
        
        self.logger.info("✅ Complete trade flow simulation finished!")


# ============================================================================
# MAIN TESTING
# ============================================================================

if __name__ == "__main__":
    import logging.config
    
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Create mock webhook generator
    mock = MockDhanWebhook()
    
    # Test 1: Order confirmation
    print("\n" + "="*60)
    print("TEST 1: Order Confirmation")
    print("="*60)
    conf = mock.get_order_confirmation_response("NIFTY50", "BUY", 1, 23450.50)
    print(json.dumps(conf, indent=2))
    
    # Test 2: Account balance
    print("\n" + "="*60)
    print("TEST 2: Account Balance")
    print("="*60)
    balance = mock.get_account_balance_response()
    print(json.dumps(balance, indent=2))
    
    # Test 3: Complete trade flow
    mock.simulate_complete_trade_flow(
        symbol="GOLD",
        quantity=1,
        entry_price=7850.50,
        target_price=7920.00,
        sl_price=7820.00
    )
