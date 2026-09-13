"""
DhanHQ Order Manager - Create, modify, track, and close orders with safety checks.

OFFICIAL FORMAT from DhanHQ docs:
https://github.com/Kalaiviswa/dhan-api-v2-docs/blob/main/03-orders.md

Safety Features:
- Order validation before placement
- Maximum loss limits
- Position size limits
- Risk-reward ratio checks
- Execution confirmation tracking
- Order status monitoring
"""

import logging
import json
from typing import Dict, Tuple, Optional, List
from datetime import datetime
from zoneinfo import ZoneInfo
from enum import Enum

from dhan_api_client import (
    DhanAPIClient, ExchangeSegment, OrderType, 
    TransactionType, ProductType, Validity
)

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


class OrderStatus(Enum):
    """Official DhanHQ order statuses"""
    PENDING = "PENDING"
    LIVE = "LIVE"
    EXECUTED = "EXECUTED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class DhanOrderManager:
    """
    Manages order creation, modification, tracking, and execution.
    
    Safety Checks:
    - Pre-order validation
    - Position size limits
    - Maximum loss per trade
    - Risk-reward ratio validation
    - Execution confirmation tracking
    """
    
    def __init__(
        self,
        dhan_client: DhanAPIClient,
        max_loss_per_trade: float = 500.0,
        max_position_size: int = 5,
        min_rr_ratio: float = 1.0
    ):
        """
        Initialize order manager
        
        Args:
            dhan_client: DhanAPIClient instance
            max_loss_per_trade: Maximum loss allowed per trade (₹)
            max_position_size: Maximum position size (lots)
            min_rr_ratio: Minimum risk-reward ratio
        """
        self.logger = logging.getLogger(__name__)
        self.client = dhan_client
        self.max_loss_per_trade = max_loss_per_trade
        self.max_position_size = max_position_size
        self.min_rr_ratio = min_rr_ratio
        
        # Track orders
        self.active_orders: Dict[str, Dict] = {}
        self.executed_orders: Dict[str, Dict] = {}
        self.rejected_orders: Dict[str, Dict] = {}
        
        self.logger.info("✅ DhanHQ Order Manager initialized")
        self.logger.info(f"   Max Loss/Trade: ₹{max_loss_per_trade}")
        self.logger.info(f"   Max Position Size: {max_position_size} lots")
        self.logger.info(f"   Min R:R Ratio: 1:{min_rr_ratio}")
    
    # =========================================================================
    # ORDER VALIDATION
    # =========================================================================
    
    def validate_order(
        self,
        symbol: str,
        transaction_type: str,
        quantity: int,
        entry_price: float,
        sl_price: float,
        target_price: float,
        current_price: Optional[float] = None
    ) -> Tuple[bool, str]:
        """
        Validate order before placement
        
        Checks:
        1. Position size limit
        2. Maximum loss limit
        3. Risk-reward ratio
        4. Price validity
        
        Args:
            symbol: Trading symbol
            transaction_type: BUY or SELL
            quantity: Order quantity (lots)
            entry_price: Entry price
            sl_price: Stop loss price
            target_price: Target price
            current_price: Current market price
            
        Returns:
            (is_valid, validation_message)
        """
        self.logger.info(f"🔍 Validating order for {symbol}...")
        
        # Check 1: Position size
        if quantity > self.max_position_size:
            msg = f"❌ Position size {quantity} exceeds limit {self.max_position_size}"
            self.logger.error(msg)
            return False, msg
        
        # Check 2: Price logic for BUY
        if transaction_type == "BUY":
            if sl_price >= entry_price:
                msg = f"❌ SL ({sl_price}) must be below entry ({entry_price})"
                self.logger.error(msg)
                return False, msg
            
            if target_price <= entry_price:
                msg = f"❌ Target ({target_price}) must be above entry ({entry_price})"
                self.logger.error(msg)
                return False, msg
        
        # Check 3: Price logic for SELL
        elif transaction_type == "SELL":
            if sl_price <= entry_price:
                msg = f"❌ SL ({sl_price}) must be above entry ({entry_price})"
                self.logger.error(msg)
                return False, msg
            
            if target_price >= entry_price:
                msg = f"❌ Target ({target_price}) must be below entry ({entry_price})"
                self.logger.error(msg)
                return False, msg
        
        # Check 4: Maximum loss
        max_loss_rupees = abs(entry_price - sl_price) * quantity
        if max_loss_rupees > self.max_loss_per_trade:
            msg = f"❌ Max loss ₹{max_loss_rupees} exceeds limit ₹{self.max_loss_per_trade}"
            self.logger.error(msg)
            return False, msg
        
        # Check 5: Risk-reward ratio
        risk = abs(entry_price - sl_price)
        reward = abs(target_price - entry_price)
        
        if risk > 0:
            rr_ratio = reward / risk
            if rr_ratio < self.min_rr_ratio:
                msg = f"❌ R:R ratio {rr_ratio:.2f} below minimum {self.min_rr_ratio}"
                self.logger.error(msg)
                return False, msg
        
        self.logger.info("✅ Order validation passed!")
        self.logger.info(f"   Position Size: {quantity}/{self.max_position_size}")
        self.logger.info(f"   Max Loss: ₹{max_loss_rupees:.2f}/{self.max_loss_per_trade}")
        self.logger.info(f"   R:R Ratio: {rr_ratio:.2f}:1")
        
        return True, "Order validation passed"
    
    # =========================================================================
    # ORDER CREATION
    # =========================================================================
    
    def create_market_order(
        self,
        symbol: str,
        exchange_segment: ExchangeSegment,
        transaction_type: TransactionType,
        quantity: int,
        entry_price: float,
        sl_price: float,
        target_price: float,
        strategy: str = "",
        correlation_id: Optional[str] = None
    ) -> Tuple[bool, str, Optional[str]]:
        """
        Create and place a MARKET order (Official DhanHQ format)
        
        Process:
        1. Validate order
        2. Place order via API
        3. Track order status
        4. Confirm execution
        
        Args:
            symbol: Trading symbol
            exchange_segment: Exchange (NSE_EQ, etc.)
            transaction_type: BUY or SELL
            quantity: Order quantity
            entry_price: Entry price
            sl_price: Stop loss price
            target_price: Target price
            strategy: Strategy name
            correlation_id: Optional correlation ID
            
        Returns:
            (success, message, order_id)
        """
        try:
            self.logger.info("\n" + "="*60)
            self.logger.info(f"CREATING MARKET ORDER: {symbol}")
            self.logger.info("="*60)
            
            # Step 1: Validate
            is_valid, validation_msg = self.validate_order(
                symbol=symbol,
                transaction_type=transaction_type.value,
                quantity=quantity,
                entry_price=entry_price,
                sl_price=sl_price,
                target_price=target_price
            )
            
            if not is_valid:
                self.logger.error(f"❌ {validation_msg}")
                self.rejected_orders[symbol] = {
                    "reason": validation_msg,
                    "timestamp": datetime.now(IST).isoformat()
                }
                return False, validation_msg, None
            
            # Step 2: Place order (Official format)
            success, msg, order_id = self.client.place_order(
                symbol=symbol,
                exchange_segment=exchange_segment,
                transaction_type=transaction_type,
                quantity=quantity,
                order_type=OrderType.MARKET,
                price=0.0,  # Market order
                product_type=ProductType.INTRADAY,
                validity=Validity.DAY,
                correlation_id=correlation_id
            )
            
            if not success:
                self.logger.error(f"❌ Order placement failed: {msg}")
                self.rejected_orders[order_id or symbol] = {
                    "reason": msg,
                    "timestamp": datetime.now(IST).isoformat()
                }
                return False, msg, None
            
            # Step 3: Track order
            order_record = {
                "orderId": order_id,
                "symbol": symbol,
                "transactionType": transaction_type.value,
                "quantity": quantity,
                "entryPrice": entry_price,
                "slPrice": sl_price,
                "targetPrice": target_price,
                "strategy": strategy,
                "status": OrderStatus.LIVE.value,
                "createdAt": datetime.now(IST).isoformat(),
                "executedAt": None,
                "executedPrice": None,
                "pnl": 0.0
            }
            
            self.active_orders[order_id] = order_record
            
            # Step 4: Confirmation
            self.logger.info("\n✅ ORDER CREATED SUCCESSFULLY")
            self.logger.info(f"   Order ID: {order_id}")
            self.logger.info(f"   Symbol: {symbol}")
            self.logger.info(f"   Type: {transaction_type.value}")
            self.logger.info(f"   Quantity: {quantity}")
            self.logger.info(f"   Entry: {entry_price}")
            self.logger.info(f"   SL: {sl_price}")
            self.logger.info(f"   Target: {target_price}")
            self.logger.info(f"   Strategy: {strategy}")
            self.logger.info(f"   Status: {OrderStatus.LIVE.value}")
            
            return True, f"Order {order_id} created successfully", order_id
            
        except Exception as e:
            self.logger.error(f"❌ Order creation error: {e}")
            return False, f"Error: {e}", None
    
    def create_limit_order(
        self,
        symbol: str,
        exchange_segment: ExchangeSegment,
        transaction_type: TransactionType,
        quantity: int,
        limit_price: float,
        sl_price: float,
        target_price: float,
        strategy: str = ""
    ) -> Tuple[bool, str, Optional[str]]:
        """
        Create LIMIT order (Official DhanHQ format)
        
        Args:
            symbol: Trading symbol
            exchange_segment: Exchange
            transaction_type: BUY or SELL
            quantity: Order quantity
            limit_price: Limit price
            sl_price: Stop loss price
            target_price: Target price
            strategy: Strategy name
            
        Returns:
            (success, message, order_id)
        """
        try:
            self.logger.info(f"📌 Creating LIMIT order for {symbol}...")
            
            # Validate using limit price as entry
            is_valid, validation_msg = self.validate_order(
                symbol=symbol,
                transaction_type=transaction_type.value,
                quantity=quantity,
                entry_price=limit_price,
                sl_price=sl_price,
                target_price=target_price
            )
            
            if not is_valid:
                return False, validation_msg, None
            
            # Place LIMIT order
            success, msg, order_id = self.client.place_order(
                symbol=symbol,
                exchange_segment=exchange_segment,
                transaction_type=transaction_type,
                quantity=quantity,
                order_type=OrderType.LIMIT,
                price=limit_price,  # Limit price
                product_type=ProductType.INTRADAY,
                validity=Validity.DAY
            )
            
            if success:
                self.active_orders[order_id] = {
                    "orderId": order_id,
                    "symbol": symbol,
                    "orderType": "LIMIT",
                    "limitPrice": limit_price,
                    "slPrice": sl_price,
                    "targetPrice": target_price,
                    "status": OrderStatus.PENDING.value
                }
                
                self.logger.info(f"✅ LIMIT order {order_id} created")
            
            return success, msg, order_id
            
        except Exception as e:
            self.logger.error(f"❌ Limit order error: {e}")
            return False, f"Error: {e}", None
    
    # =========================================================================
    # ORDER MANAGEMENT
    # =========================================================================
    
    def modify_order(
        self,
        order_id: str,
        new_quantity: Optional[int] = None,
        new_price: Optional[float] = None,
        new_stop_price: Optional[float] = None
    ) -> Tuple[bool, str]:
        """
        Modify an open order
        
        Args:
            order_id: Order ID to modify
            new_quantity: New quantity
            new_price: New price
            new_stop_price: New stop price
            
        Returns:
            (success, message)
        """
        try:
            if order_id not in self.active_orders:
                return False, f"Order {order_id} not found"
            
            order = self.active_orders[order_id]
            
            self.logger.info(f"🔄 Modifying order {order_id}...")
            
            success, msg = self.client.modify_order(
                order_id=order_id,
                quantity=new_quantity,
                price=new_price,
                stop_price=new_stop_price
            )
            
            if success:
                if new_quantity:
                    order["quantity"] = new_quantity
                if new_price:
                    order["price"] = new_price
                if new_stop_price:
                    order["stopPrice"] = new_stop_price
                
                order["modifiedAt"] = datetime.now(IST).isoformat()
                self.logger.info(f"✅ Order modified: {msg}")
            
            return success, msg
            
        except Exception as e:
            self.logger.error(f"❌ Modify error: {e}")
            return False, f"Error: {e}"
    
    def cancel_order(self, order_id: str) -> Tuple[bool, str]:
        """
        Cancel an open order
        
        Args:
            order_id: Order ID to cancel
            
        Returns:
            (success, message)
        """
        try:
            if order_id not in self.active_orders:
                return False, f"Order {order_id} not found"
            
            self.logger.info(f"❌ Cancelling order {order_id}...")
            
            success, msg = self.client.cancel_order(order_id=order_id)
            
            if success:
                order = self.active_orders.pop(order_id)
                order["status"] = OrderStatus.CANCELLED.value
                order["cancelledAt"] = datetime.now(IST).isoformat()
                
                self.rejected_orders[order_id] = order
                self.logger.info(f"✅ Order cancelled: {msg}")
            
            return success, msg
            
        except Exception as e:
            self.logger.error(f"❌ Cancel error: {e}")
            return False, f"Error: {e}"
    
    # =========================================================================
    # ORDER TRACKING & EXECUTION
    # =========================================================================
    
    def track_order(self, order_id: str) -> Tuple[bool, Dict]:
        """
        Track order status and execution
        
        Args:
            order_id: Order ID to track
            
        Returns:
            (success, order_details)
        """
        try:
            if order_id in self.active_orders:
                return True, self.active_orders[order_id]
            elif order_id in self.executed_orders:
                return True, self.executed_orders[order_id]
            else:
                return False, {}
                
        except Exception as e:
            self.logger.error(f"❌ Track error: {e}")
            return False, {}
    
    def mark_order_executed(
        self,
        order_id: str,
        executed_price: float,
        executed_quantity: int
    ) -> Tuple[bool, str]:
        """
        Mark order as executed (called from postback handler)
        
        Args:
            order_id: Order ID
            executed_price: Execution price
            executed_quantity: Executed quantity
            
        Returns:
            (success, message)
        """
        try:
            if order_id not in self.active_orders:
                return False, f"Order {order_id} not found"
            
            order = self.active_orders.pop(order_id)
            order["status"] = OrderStatus.EXECUTED.value
            order["executedAt"] = datetime.now(IST).isoformat()
            order["executedPrice"] = executed_price
            order["executedQuantity"] = executed_quantity
            
            # Calculate P&L
            if order["transactionType"] == "BUY":
                order["pnl"] = (executed_price - order["entryPrice"]) * executed_quantity
            else:
                order["pnl"] = (order["entryPrice"] - executed_price) * executed_quantity
            
            self.executed_orders[order_id] = order
            
            self.logger.info(f"✅ Order executed: {order_id}")
            self.logger.info(f"   Price: {executed_price}")
            self.logger.info(f"   Quantity: {executed_quantity}")
            
            return True, f"Order {order_id} executed at {executed_price}"
            
        except Exception as e:
            self.logger.error(f"❌ Execution error: {e}")
            return False, f"Error: {e}"
    
    # =========================================================================
    # POSITION CLOSING
    # =========================================================================
    
    def close_position(
        self,
        order_id: str,
        exit_price: float,
        reason: str = "manual"
    ) -> Tuple[bool, str, float]:
        """
        Close an executed position
        
        Args:
            order_id: Order ID to close
            exit_price: Exit price
            reason: Close reason (manual, sl, target, market_close)
            
        Returns:
            (success, message, pnl)
        """
        try:
            if order_id not in self.executed_orders:
                return False, f"Order {order_id} not found in executed", 0.0
            
            order = self.executed_orders[order_id]
            
            self.logger.info(f"📊 Closing position {order_id}...")
            
            # Calculate final P&L
            entry = order["executedPrice"]
            qty = order["executedQuantity"]
            
            if order["transactionType"] == "BUY":
                pnl = (exit_price - entry) * qty
            else:
                pnl = (entry - exit_price) * qty
            
            order["status"] = "CLOSED"
            order["closedAt"] = datetime.now(IST).isoformat()
            order["closeReason"] = reason
            order["exitPrice"] = exit_price
            order["finalPnL"] = pnl
            
            self.logger.info(f"✅ Position closed")
            self.logger.info(f"   Entry: {entry}")
            self.logger.info(f"   Exit: {exit_price}")
            self.logger.info(f"   P&L: ₹{pnl:.2f}")
            self.logger.info(f"   Reason: {reason}")
            
            return True, f"Position closed with P&L ₹{pnl:.2f}", pnl
            
        except Exception as e:
            self.logger.error(f"❌ Close error: {e}")
            return False, f"Error: {e}", 0.0
    
    # =========================================================================
    # REPORTING
    # =========================================================================
    
    def get_active_orders(self) -> List[Dict]:
        """Get all active orders"""
        return list(self.active_orders.values())
    
    def get_executed_orders(self) -> List[Dict]:
        """Get all executed orders"""
        return list(self.executed_orders.values())
    
    def get_rejected_orders(self) -> List[Dict]:
        """Get all rejected orders"""
        return list(self.rejected_orders.values())
    
    def get_daily_pnl(self) -> float:
        """Calculate daily P&L"""
        total_pnl = 0.0
        
        for order in self.executed_orders.values():
            if order.get("status") == "CLOSED":
                total_pnl += order.get("finalPnL", 0.0)
        
        return total_pnl
    
    def print_summary(self) -> None:
        """Print order manager summary"""
        self.logger.info("\n" + "="*60)
        self.logger.info("ORDER MANAGER SUMMARY")
        self.logger.info("="*60)
        
        self.logger.info(f"Active Orders: {len(self.active_orders)}")
        for order_id, order in self.active_orders.items():
            self.logger.info(f"  {order_id}: {order['symbol']} {order['status']}")
        
        self.logger.info(f"\nExecuted Orders: {len(self.executed_orders)}")
        
        closed_orders = [o for o in self.executed_orders.values() if o.get("status") == "CLOSED"]
        self.logger.info(f"Closed Orders: {len(closed_orders)}")
        
        daily_pnl = self.get_daily_pnl()
        self.logger.info(f"Daily P&L: ₹{daily_pnl:.2f}")
        
        self.logger.info(f"\nRejected Orders: {len(self.rejected_orders)}")


# ============================================================================
# SINGLETON INSTANCE
# ============================================================================

_order_manager: Optional[DhanOrderManager] = None


def get_order_manager(
    dhan_client: DhanAPIClient = None,
    max_loss: float = 500.0,
    max_position: int = 5
) -> DhanOrderManager:
    """
    Get or create DhanOrderManager instance
    
    Args:
        dhan_client: DhanAPIClient instance
        max_loss: Maximum loss per trade
        max_position: Maximum position size
        
    Returns:
        DhanOrderManager instance
    """
    global _order_manager
    
    if _order_manager is None:
        from dhan_api_client import get_dhan_client
        
        if dhan_client is None:
            dhan_client = get_dhan_client()
        
        _order_manager = DhanOrderManager(
            dhan_client=dhan_client,
            max_loss_per_trade=max_loss,
            max_position_size=max_position
        )
    
    return _order_manager
