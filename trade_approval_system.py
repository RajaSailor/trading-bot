"""
Trade Approval System - Manage trade approval lifecycle and execution.

Handles:
- Trade approval/rejection processing
- Trade execution validation
- Order placement via DhanHQ
- Position creation
- Approval timeout handling
"""

import logging
import uuid
from typing import Dict, Optional, Tuple
from datetime import datetime
from zoneinfo import ZoneInfo
from trade_state_manager import get_state_manager, PendingTrade, OpenPosition

logger = logging.getLogger(__name__)

# IST timezone
IST = ZoneInfo("Asia/Kolkata")


class TradeApprovalSystem:
    """Manage trade approval and execution workflow"""
    
    def __init__(self):
        """Initialize approval system"""
        self.logger = logging.getLogger(__name__)
        self.state_manager = get_state_manager()
        self.dhan_client = None  # Will be injected
        self.logger.info("✅ Trade Approval System initialized")
    
    def set_dhan_client(self, dhan_client) -> None:
        """
        Set DhanHQ client for order placement
        
        Args:
            dhan_client: DhanHQ client instance
        """
        self.dhan_client = dhan_client
        self.logger.info("✅ DhanHQ client attached")
    
    # =========================================================================
    # APPROVAL WORKFLOW
    # =========================================================================
    
    def create_trade_for_approval(
        self,
        symbol: str,
        signal_type: str,  # "BUY", "SELL"
        entry_price: float,
        sl_value: float,
        sl_type: str = "static",  # "static", "c2c", "trail"
        target: float = 0.0,
        trail_sl: float = 0.0,
        qty_lots: int = 1,
        strategy: str = "5-MIN Breakout",
        timeframe: str = "Intraday",
        channel_id: int = 0,
        message_id: int = 0,
        callback_data: str = ""
    ) -> Tuple[bool, str, Optional[str]]:
        """
        Create a new trade pending user approval
        
        Args:
            symbol: Trading symbol
            signal_type: BUY or SELL
            entry_price: Entry price
            sl_value: Stop loss value
            sl_type: Type of SL (static/c2c/trail)
            target: Target level
            trail_sl: Trailing SL points
            qty_lots: Number of lots
            strategy: Strategy name
            timeframe: Timeframe
            channel_id: Telegram channel ID
            message_id: Telegram message ID
            callback_data: Callback identifier
            
        Returns:
            (success, message, trade_id)
        """
        try:
            # Validate constraints
            sl_points = abs(entry_price - sl_value)
            is_valid, constraint_msg = self.state_manager.validate_trade_constraints(
                symbol, sl_points
            )
            
            if not is_valid:
                self.logger.warning(f"❌ Trade rejected: {constraint_msg}")
                return False, constraint_msg, None
            
            # Generate unique trade ID
            trade_id = f"{symbol}_{signal_type}_{int(uuid.uuid4().int % 1000000)}"
            
            # Create pending trade
            trade = self.state_manager.create_pending_trade(
                trade_id=trade_id,
                symbol=symbol,
                entry_price=entry_price,
                sl_type=sl_type,
                sl_value=sl_value,
                target=target,
                qty_lots=qty_lots,
                strategy=strategy,
                timeframe=timeframe,
                channel_id=channel_id,
                message_id=message_id,
                callback_data=callback_data,
                expires_in_seconds=300  # 5 minute auto-reject
            )
            
            self.logger.info(
                f"✅ Trade created for approval: {trade_id}\n"
                f"   Symbol: {symbol}\n"
                f"   Entry: {entry_price} | SL: {sl_value} | Target: {target}\n"
                f"   Expires at: {trade.expires_at}"
            )
            
            return True, f"✅ Trade {trade_id} awaiting approval", trade_id
            
        except Exception as e:
            self.logger.error(f"❌ Error creating trade: {e}")
            return False, f"❌ Error creating trade: {e}", None
    
    def handle_approval(
        self,
        trade_id: str,
        user_id: int = 0
    ) -> Tuple[bool, str, Optional[str]]:
        """
        Handle trade approval from user
        
        Args:
            trade_id: Trade to approve
            user_id: User ID (for logging)
            
        Returns:
            (success, message, order_id)
        """
        try:
            # Get pending trade
            trade = self.state_manager.get_pending_trade(trade_id)
            if not trade:
                msg = f"❌ Trade not found: {trade_id}"
                self.logger.error(msg)
                return False, msg, None
            
            # Check if already approved/rejected
            if trade.approval_status != "pending":
                msg = f"❌ Trade already {trade.approval_status}: {trade_id}"
                self.logger.warning(msg)
                return False, msg, None
            
            # Mark as approved
            self.state_manager.approve_trade(trade_id)
            self.logger.info(f"✅ User approved trade: {trade_id}")
            
            # Execute the trade
            success, exec_msg, order_id = self.execute_approved_trade(trade)
            
            return success, exec_msg, order_id
            
        except Exception as e:
            self.logger.error(f"❌ Error handling approval: {e}")
            return False, f"❌ Error handling approval: {e}", None
    
    def handle_rejection(
        self,
        trade_id: str,
        reason: str = "User rejected",
        user_id: int = 0
    ) -> Tuple[bool, str]:
        """
        Handle trade rejection from user
        
        Args:
            trade_id: Trade to reject
            reason: Rejection reason
            user_id: User ID (for logging)
            
        Returns:
            (success, message)
        """
        try:
            # Get pending trade
            trade = self.state_manager.get_pending_trade(trade_id)
            if not trade:
                msg = f"❌ Trade not found: {trade_id}"
                self.logger.error(msg)
                return False, msg
            
            # Check if already approved/rejected
            if trade.approval_status != "pending":
                msg = f"❌ Trade already {trade.approval_status}: {trade_id}"
                self.logger.warning(msg)
                return False, msg
            
            # Mark as rejected
            self.state_manager.reject_trade(trade_id)
            self.state_manager.remove_pending_trade(trade_id)
            
            self.logger.info(f"❌ User rejected trade: {trade_id} - Reason: {reason}")
            
            msg = f"✅ Trade rejected: {trade_id}"
            return True, msg
            
        except Exception as e:
            self.logger.error(f"❌ Error handling rejection: {e}")
            return False, f"❌ Error handling rejection: {e}"
    
    # =========================================================================
    # TRADE EXECUTION
    # =========================================================================
    
    def execute_approved_trade(self, trade: PendingTrade) -> Tuple[bool, str, Optional[str]]:
        """
        Execute an approved trade via DhanHQ
        
        Args:
            trade: PendingTrade object
            
        Returns:
            (success, message, order_id)
        """
        try:
            # Validate DhanHQ client is available
            if not self.dhan_client:
                msg = "❌ DhanHQ client not available"
                self.logger.error(msg)
                return False, msg, None
            
            # Determine order side
            order_side = "BUY" if "BUY" in trade.callback_data.upper() else "SELL"
            
            # Calculate order quantity (convert lots to units)
            # Assuming 1 lot = 100 units for options
            order_qty = trade.qty_lots * 100
            
            self.logger.info(
                f"📤 Executing trade: {trade.trade_id}\n"
                f"   Side: {order_side}\n"
                f"   Symbol: {trade.symbol}\n"
                f"   Qty: {order_qty}\n"
                f"   Entry: {trade.entry_price}"
            )
            
            # Place order via DhanHQ
            # Note: This is a simplified example. Real implementation would:
            # - Resolve security ID from symbol
            # - Handle different order types
            # - Implement error handling
            try:
                order_response = self.dhan_client.place_order(
                    security_id=self._resolve_security_id(trade.symbol),
                    order_type="REGULAR",
                    quantity=order_qty,
                    price=trade.entry_price,
                    product_type="MIS",  # Intraday
                    side=order_side,
                    disclosed_quantity=0,
                    day_order_flag="DAY"
                )
                
                if not order_response or not order_response.get("data"):
                    msg = "❌ Order placement failed - No response"
                    self.logger.error(msg)
                    return False, msg, None
                
                order_id = order_response["data"].get("order_id")
                if not order_id:
                    msg = "❌ Order placement failed - No order ID"
                    self.logger.error(msg)
                    return False, msg, None
                
                # Create open position
                position_id = f"POS_{order_id}"
                position = self.state_manager.create_open_position(
                    position_id=position_id,
                    trade_id=trade.trade_id,
                    symbol=trade.symbol,
                    entry_price=trade.entry_price,
                    qty_lots=trade.qty_lots,
                    sl=trade.sl_value,
                    target=trade.target,
                    trail_sl=trade.trail_sl,
                    mode=self.state_manager.system_state.get("current_mode", "practice")
                )
                
                # Remove from pending trades
                self.state_manager.remove_pending_trade(trade.trade_id)
                
                # Persist state
                self.state_manager.persist_state()
                
                msg = (
                    f"✅ ORDER EXECUTED\n"
                    f"Order ID: {order_id}\n"
                    f"Symbol: {trade.symbol}\n"
                    f"Entry: {trade.entry_price}\n"
                    f"Qty: {order_qty}"
                )
                
                self.logger.info(f"✅ Trade executed successfully: {trade.trade_id}\nOrder ID: {order_id}")
                
                return True, msg, order_id
                
            except Exception as order_error:
                self.logger.error(f"❌ Order placement error: {order_error}")
                return False, f"❌ Order placement error: {order_error}", None
            
        except Exception as e:
            self.logger.error(f"❌ Error executing trade: {e}")
            return False, f"❌ Error executing trade: {e}", None
    
    # =========================================================================
    # POSITION MANAGEMENT
    # =========================================================================
    
    def handle_position_update(
        self,
        position_id: str,
        current_price: float
    ) -> Optional[Dict]:
        """
        Update position price and check for SL/Target hits
        
        Args:
            position_id: Position to update
            current_price: Current market price
            
        Returns:
            Alert dict if SL or target hit, None otherwise
        """
        try:
            position = self.state_manager.get_open_position(position_id)
            if not position:
                return None
            
            # Update price
            self.state_manager.update_position_price(position_id, current_price)
            
            # Check for SL hit
            if self._check_sl_hit(position, current_price):
                return {
                    "type": "SL_HIT",
                    "position_id": position_id,
                    "symbol": position.symbol,
                    "price": current_price,
                    "pnl": position.pnl
                }
            
            # Check for target hit
            if self._check_target_hit(position, current_price):
                return {
                    "type": "TARGET_HIT",
                    "position_id": position_id,
                    "symbol": position.symbol,
                    "price": current_price,
                    "pnl": position.pnl
                }
            
            return None
            
        except Exception as e:
            self.logger.error(f"❌ Error updating position: {e}")
            return None
    
    def close_position_at_price(
        self,
        position_id: str,
        exit_price: float,
        exit_reason: str = "manual"
    ) -> Tuple[bool, str]:
        """
        Close position at specified price
        
        Args:
            position_id: Position to close
            exit_price: Exit price
            exit_reason: Reason for exit
            
        Returns:
            (success, message)
        """
        try:
            position = self.state_manager.get_open_position(position_id)
            if not position:
                msg = f"❌ Position not found: {position_id}"
                self.logger.error(msg)
                return False, msg
            
            # Close via DhanHQ
            if self.dhan_client:
                try:
                    # Place exit order
                    exit_side = "SELL" if position.entry_time else "BUY"
                    order_qty = position.qty_lots * 100
                    
                    exit_response = self.dhan_client.place_order(
                        security_id=self._resolve_security_id(position.symbol),
                        order_type="REGULAR",
                        quantity=order_qty,
                        price=exit_price,
                        product_type="MIS",
                        side=exit_side,
                        disclosed_quantity=0,
                        day_order_flag="DAY"
                    )
                    
                except Exception as order_error:
                    self.logger.error(f"❌ Exit order error: {order_error}")
            
            # Record closure in state
            closed = self.state_manager.close_position(
                position_id=position_id,
                exit_price=exit_price,
                exit_reason=exit_reason
            )
            
            self.state_manager.persist_state()
            
            msg = (
                f"✅ Position closed: {position_id}\n"
                f"Symbol: {position.symbol}\n"
                f"Entry: {position.entry_price}\n"
                f"Exit: {exit_price}\n"
                f"P&L: {closed.pnl if closed else 'N/A'}"
            )
            
            return True, msg
            
        except Exception as e:
            self.logger.error(f"❌ Error closing position: {e}")
            return False, f"❌ Error closing position: {e}"
    
    # =========================================================================
    # TIMEOUT & EXPIRY HANDLING
    # =========================================================================
    
    def handle_expired_trades(self) -> Tuple[int, List[str]]:
        """
        Check for and handle expired pending trades (auto-reject after 5 min)
        
        Returns:
            (expired_count, expired_trade_ids)
        """
        try:
            expired = self.state_manager.check_expired_trades()
            
            if expired:
                self.logger.warning(f"⏰ Auto-rejected {len(expired)} expired trades")
                for trade_id in expired:
                    self.state_manager.remove_pending_trade(trade_id)
            
            self.state_manager.persist_state()
            
            return len(expired), expired
            
        except Exception as e:
            self.logger.error(f"❌ Error handling expired trades: {e}")
            return 0, []
    
    def auto_exit_before_market_close(self) -> Tuple[int, List[str]]:
        """
        Auto-exit all open positions before 2:50 PM IST
        
        Returns:
            (closed_count, closed_position_ids)
        """
        try:
            closed_positions = []
            positions = self.state_manager.get_all_open_positions()
            
            for position in positions:
                success, msg = self.close_position_at_price(
                    position_id=position.position_id,
                    exit_price=position.current_price,
                    exit_reason="market_close"
                )
                
                if success:
                    closed_positions.append(position.position_id)
                    self.logger.info(f"✅ Auto-closed before market close: {position.position_id}")
            
            return len(closed_positions), closed_positions
            
        except Exception as e:
            self.logger.error(f"❌ Error auto-exiting positions: {e}")
            return 0, []
    
    # =========================================================================
    # HELPER METHODS
    # =========================================================================
    
    def _resolve_security_id(self, symbol: str) -> int:
        """
        Resolve security ID from symbol via DhanHQ
        
        This is a simplified version. Real implementation would query
        DhanHQ security master or use cached mapping.
        
        Args:
            symbol: Trading symbol
            
        Returns:
            Security ID
        """
        # TODO: Implement actual security ID resolution
        # For now, return placeholder
        return 1000  # Placeholder
    
    def _check_sl_hit(self, position: OpenPosition, current_price: float) -> bool:
        """Check if stop loss has been hit"""
        if position.entry_price > position.sl:
            # Long position - SL below entry
            return current_price <= position.sl
        else:
            # Short position - SL above entry
            return current_price >= position.sl
    
    def _check_target_hit(self, position: OpenPosition, current_price: float) -> bool:
        """Check if target has been hit"""
        if position.target <= 0:
            return False
        
        if position.entry_price > position.sl:
            # Long position - target above entry
            return current_price >= position.target
        else:
            # Short position - target below entry
            return current_price <= position.target


# Singleton instance
_approval_system: Optional[TradeApprovalSystem] = None


def get_approval_system() -> TradeApprovalSystem:
    """Get or create approval system instance"""
    global _approval_system
    if _approval_system is None:
        _approval_system = TradeApprovalSystem()
    return _approval_system
