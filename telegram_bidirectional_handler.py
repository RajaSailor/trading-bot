"""
Telegram Bidirectional Handler - Master coordinator for all Telegram interactions.

This is the HEART of Phase 1 - coordinates:
- Alert receiving from webhook
- Alert routing to channels
- User approval/rejection handling
- Trade execution coordination
- Position tracking
- Command processing
- State persistence

Flow:
1. Alert arrives via webhook
2. Format and route to channel
3. User clicks approval/rejection button
4. Execute or reject trade
5. Track position
6. Send updates
7. Auto-close before market close
"""

import logging
import os
from typing import Dict, Optional, Tuple, Callable
from datetime import datetime
from zoneinfo import ZoneInfo
from enum import Enum

from alert_formatter import AlertFormatter, TradeAlert, SignalType
from trade_state_manager import get_state_manager
from trade_approval_system import get_approval_system
from trade_control_panel import get_control_panel
from command_processor import get_command_processor
from strategy_alerts_updated import get_alerts_router, TradingCategory

logger = logging.getLogger(__name__)

# IST timezone
IST = ZoneInfo("Asia/Kolkata")


class TelegramBotState(Enum):
    """Telegram bot states"""
    IDLE = "idle"
    WAITING_APPROVAL = "waiting_approval"
    EXECUTING = "executing"
    TRACKING = "tracking"
    ERROR = "error"


class TelegramBidirectionalHandler:
    """
    Master coordinator for all Telegram interactions.
    Manages bidirectional flow between webhook alerts and user approvals.
    """
    
    def __init__(self):
        """Initialize handler and inject dependencies"""
        self.logger = logging.getLogger(__name__)
        
        # Initialize all subsystems
        self.formatter = AlertFormatter()
        self.state_manager = get_state_manager()
        self.approval_system = get_approval_system()
        self.control_panel = get_control_panel()
        self.command_processor = get_command_processor()
        self.alerts_router = get_alerts_router()
        
        # State tracking
        self.bot_state = TelegramBotState.IDLE
        self.active_trades: Dict[str, Dict] = {}
        self.callback_handlers: Dict[str, Callable] = {}
        
        # Register callback handlers
        self._register_callbacks()
        
        self.logger.info("✅ Telegram Bidirectional Handler initialized")
        self.logger.info("🚀 PHASE 1 MASTER COORDINATOR READY")
    
    # =========================================================================
    # WEBHOOK ALERT RECEPTION & ROUTING
    # =========================================================================
    
    def handle_webhook_alert(
        self,
        webhook_data: Dict,
        source: str = "tradingview"
    ) -> Tuple[bool, str, Optional[str]]:
        """
        Handle alert received from webhook (TradingView, screener, etc.)
        
        Args:
            webhook_data: Alert data from webhook
            source: Source of alert (e.g., "tradingview", "screener")
            
        Returns:
            (success, message, trade_id)
        """
        try:
            self.logger.info(f"📨 Webhook alert received from {source}")
            
            # Parse webhook data into TradeAlert
            trade_alert = self.formatter.parse_webhook_data(webhook_data)
            if not trade_alert:
                return False, "❌ Invalid webhook data format", None
            
            self.logger.info(f"✅ Alert parsed: {trade_alert.symbol} {trade_alert.signal_type.value}")
            
            # Format alert message
            formatted_msg, inline_kb = self.formatter.format_alert_with_keyboard(trade_alert)
            
            # Route to appropriate channel
            routing_info = self.alerts_router.route_alert(
                symbol=trade_alert.symbol,
                strategy=trade_alert.strategy,
                signal_data={
                    "signal_type": trade_alert.signal_type.value,
                    "entry_price": trade_alert.entry_price,
                    "sl": trade_alert.sl_value,
                    "target": trade_alert.target,
                    "timeframe": trade_alert.timeframe,
                },
                formatted_message=formatted_msg
            )
            
            if not routing_info.get("success"):
                return False, f"❌ Routing failed: {routing_info.get('error')}", None
            
            channel_name = routing_info.get("channel_name")
            channel_id = routing_info.get("channel_id")
            
            self.logger.info(f"✅ Alert routed to {channel_name} ({channel_id})")
            
            # Create pending trade for approval
            trade_id = f"{trade_alert.symbol}_{trade_alert.signal_type.value}_{int(datetime.now(IST).timestamp())}"
            
            success, create_msg, created_trade_id = self.approval_system.create_trade_for_approval(
                symbol=trade_alert.symbol,
                signal_type=trade_alert.signal_type.value,
                entry_price=trade_alert.entry_price,
                sl_value=trade_alert.sl_value,
                sl_type=trade_alert.sl_type,
                target=trade_alert.target,
                trail_sl=trade_alert.trail_sl,
                qty_lots=trade_alert.qty_lots,
                strategy=trade_alert.strategy,
                timeframe=trade_alert.timeframe,
                channel_id=channel_id,
                message_id=0,  # Will be set after message is sent
                callback_data=f"trade_{created_trade_id or trade_id}"
            )
            
            if not success:
                return False, create_msg, None
            
            # Store in active trades
            self.active_trades[created_trade_id or trade_id] = {
                "alert": trade_alert,
                "channel_id": channel_id,
                "routing_info": routing_info,
                "created_at": datetime.now(IST).isoformat(),
                "status": "pending_approval"
            }
            
            self.state_manager.persist_state()
            
            msg = (
                f"✅ Alert received and routed\n"
                f"Channel: {channel_name}\n"
                f"Trade: {created_trade_id or trade_id}\n"
                f"Awaiting approval..."
            )
            
            return True, msg, created_trade_id or trade_id
            
        except Exception as e:
            self.logger.error(f"❌ Error handling webhook alert: {e}")
            return False, f"❌ Error: {e}", None
    
    # =========================================================================
    # USER INTERACTION - APPROVALS & REJECTIONS
    # =========================================================================
    
    def handle_user_approval(
        self,
        trade_id: str,
        user_id: int
    ) -> Tuple[bool, str, Optional[str]]:
        """
        Handle user clicking APPROVE button
        
        Args:
            trade_id: Trade to approve
            user_id: User who approved
            
        Returns:
            (success, message, order_id)
        """
        try:
            self.logger.info(f"✅ User {user_id} approved trade: {trade_id}")
            
            # Get trade details
            trade = self.state_manager.get_pending_trade(trade_id)
            if not trade:
                return False, f"❌ Trade not found: {trade_id}", None
            
            # Execute trade via approval system
            success, exec_msg, order_id = self.approval_system.handle_approval(
                trade_id=trade_id,
                user_id=user_id
            )
            
            if success:
                # Update active trade status
                if trade_id in self.active_trades:
                    self.active_trades[trade_id]["status"] = "executed"
                    self.active_trades[trade_id]["order_id"] = order_id
                
                self.state_manager.persist_state()
                
                self.logger.info(f"✅ Trade executed: {trade_id} - Order: {order_id}")
                return True, exec_msg, order_id
            else:
                return False, exec_msg, None
            
        except Exception as e:
            self.logger.error(f"❌ Error approving trade: {e}")
            return False, f"❌ Error: {e}", None
    
    def handle_user_rejection(
        self,
        trade_id: str,
        user_id: int,
        reason: str = "User rejected"
    ) -> Tuple[bool, str]:
        """
        Handle user clicking REJECT button
        
        Args:
            trade_id: Trade to reject
            user_id: User who rejected
            reason: Rejection reason
            
        Returns:
            (success, message)
        """
        try:
            self.logger.info(f"❌ User {user_id} rejected trade: {trade_id} - Reason: {reason}")
            
            success, msg = self.approval_system.handle_rejection(
                trade_id=trade_id,
                reason=reason,
                user_id=user_id
            )
            
            if success:
                # Remove from active trades
                if trade_id in self.active_trades:
                    del self.active_trades[trade_id]
                
                self.state_manager.persist_state()
            
            return success, msg
            
        except Exception as e:
            self.logger.error(f"❌ Error rejecting trade: {e}")
            return False, f"❌ Error: {e}"
    
    # =========================================================================
    # POSITION TRACKING & UPDATES
    # =========================================================================
    
    def handle_position_update(
        self,
        position_id: str,
        current_price: float,
        bid_ask: Optional[Tuple[float, float]] = None
    ) -> Optional[Dict]:
        """
        Handle position price update
        
        Args:
            position_id: Position to update
            current_price: Current market price
            bid_ask: Optional (bid, ask) tuple
            
        Returns:
            Alert dict if SL/target hit, None otherwise
        """
        try:
            alert = self.approval_system.handle_position_update(
                position_id=position_id,
                current_price=current_price
            )
            
            if alert:
                # Format alert message
                if alert["type"] == "SL_HIT":
                    msg = (
                        f"🚨 <b>STOP LOSS HIT!</b>\n"
                        f"Position: {alert['position_id']}\n"
                        f"Symbol: {alert['symbol']}\n"
                        f"Price: {alert['price']:.2f}\n"
                        f"P&L: {alert['pnl']:+.2f}"
                    )
                    # Auto-close position
                    self.approval_system.close_position_at_price(
                        position_id=position_id,
                        exit_price=current_price,
                        exit_reason="sl"
                    )
                
                elif alert["type"] == "TARGET_HIT":
                    msg = (
                        f"🎯 <b>TARGET HIT!</b>\n"
                        f"Position: {alert['position_id']}\n"
                        f"Symbol: {alert['symbol']}\n"
                        f"Price: {alert['price']:.2f}\n"
                        f"P&L: {alert['pnl']:+.2f}"
                    )
                    # Auto-close position
                    self.approval_system.close_position_at_price(
                        position_id=position_id,
                        exit_price=current_price,
                        exit_reason="target"
                    )
                
                self.logger.info(f"✅ Position alert: {alert['type']} for {alert['symbol']}")
                return alert
            
            return None
            
        except Exception as e:
            self.logger.error(f"❌ Error updating position: {e}")
            return None
    
    # =========================================================================
    # COMMAND HANDLING
    # =========================================================================
    
    def handle_user_command(
        self,
        command_text: str,
        user_id: int,
        chat_id: int
    ) -> Tuple[bool, str]:
        """
        Handle user command (e.g., /status, /help)
        
        Args:
            command_text: Command text
            user_id: User ID
            chat_id: Chat ID
            
        Returns:
            (success, response_message)
        """
        try:
            self.logger.info(f"📨 Command from user {user_id}: {command_text}")
            
            success, response = self.command_processor.process_command(
                command_text=command_text,
                user_id=user_id
            )
            
            self.logger.info(f"✅ Command processed: {command_text[:20]}...")
            
            return success, response
            
        except Exception as e:
            self.logger.error(f"❌ Error processing command: {e}")
            return False, f"❌ Error: {e}"
    
    # =========================================================================
    # CALLBACK ROUTING
    # =========================================================================
    
    def handle_callback_query(
        self,
        callback_data: str,
        user_id: int,
        query_id: str
    ) -> Optional[str]:
        """
        Handle button callback from Telegram
        
        Args:
            callback_data: Callback data from button
            user_id: User who clicked button
            query_id: Callback query ID (for answering)
            
        Returns:
            Response message, if any
        """
        try:
            self.logger.info(f"🔘 Callback from user {user_id}: {callback_data}")
            
            # Parse callback
            parts = callback_data.split("_")
            action = "_".join(parts[:-1]) if len(parts) > 1 else callback_data
            trade_id = parts[-1] if len(parts) > 1 else ""
            
            # Route to handler
            if action == "approve_trade":
                success, msg, order_id = self.handle_user_approval(trade_id, user_id)
                return msg
            
            elif action == "reject_trade":
                success, msg = self.handle_user_rejection(trade_id, user_id)
                return msg
            
            elif action == "edit_trade":
                return "📝 Edit feature coming soon..."
            
            elif action == "skip_trade":
                success, msg = self.handle_user_rejection(trade_id, user_id, "User skipped")
                return msg
            
            else:
                self.logger.warning(f"⚠️ Unknown callback action: {action}")
                return None
            
        except Exception as e:
            self.logger.error(f"❌ Error handling callback: {e}")
            return None
    
    # =========================================================================
    # SCHEDULED TASKS
    # =========================================================================
    
    def handle_market_close_routine(self) -> Tuple[int, List[str]]:
        """
        Handle 2:50 PM IST auto-exit routine
        
        Returns:
            (closed_count, closed_position_ids)
        """
        try:
            self.logger.info("⏰ Market close routine triggered (2:50 PM IST)")
            
            closed_count, closed_ids = self.approval_system.auto_exit_before_market_close()
            
            msg = f"✅ Auto-closed {closed_count} positions before market close"
            self.logger.info(msg)
            
            self.state_manager.persist_state()
            
            return closed_count, closed_ids
            
        except Exception as e:
            self.logger.error(f"❌ Error in market close routine: {e}")
            return 0, []
    
    def handle_daily_reset_routine(self) -> None:
        """
        Handle 9:00 AM IST daily reset (reset counters, cleanup)
        """
        try:
            self.logger.info("🔄 Daily reset routine triggered (9:00 AM IST)")
            
            # Reset daily counters
            self.state_manager.reset_daily_state()
            
            # Clear expired pending trades
            expired_count, expired_ids = self.approval_system.handle_expired_trades()
            
            self.state_manager.persist_state()
            
            self.logger.info(f"✅ Daily reset complete - Cleared {expired_count} expired trades")
            
        except Exception as e:
            self.logger.error(f"❌ Error in daily reset routine: {e}")
    
    def handle_expired_trades_cleanup(self) -> Tuple[int, List[str]]:
        """
        Check and clean up expired pending trades
        
        Returns:
            (expired_count, expired_trade_ids)
        """
        try:
            expired_count, expired_ids = self.approval_system.handle_expired_trades()
            
            if expired_count > 0:
                self.logger.info(f"⏰ Cleaned up {expired_count} expired trades")
                self.state_manager.persist_state()
            
            return expired_count, expired_ids
            
        except Exception as e:
            self.logger.error(f"❌ Error cleaning up expired trades: {e}")
            return 0, []
    
    # =========================================================================
    # STATUS & DIAGNOSTICS
    # =========================================================================
    
    def get_system_status(self) -> Dict:
        """
        Get complete system status
        
        Returns:
            Status dictionary
        """
        stats = self.state_manager.get_daily_stats()
        
        return {
            "timestamp": datetime.now(IST).isoformat(),
            "bot_state": self.bot_state.value,
            "active_trades": len(self.active_trades),
            "stats": stats,
            "subsystems": {
                "formatter": "✅ Ready",
                "state_manager": "✅ Ready",
                "approval_system": "✅ Ready",
                "control_panel": "✅ Ready",
                "command_processor": "✅ Ready",
                "alerts_router": "✅ Ready",
            }
        }
    
    def get_active_trades_summary(self) -> str:
        """Get summary of active trades"""
        if not self.active_trades:
            return "No active trades"
        
        lines = [f"<b>Active Trades: {len(self.active_trades)}</b>", ""]
        for trade_id, trade_data in self.active_trades.items():
            alert = trade_data.get("alert")
            status = trade_data.get("status")
            lines.append(f"• {alert.symbol} {status} ({trade_id})")
        
        return "\n".join(lines)
    
    # =========================================================================
    # CALLBACK REGISTRATION
    # =========================================================================
    
    def _register_callbacks(self) -> None:
        """Register all callback handlers"""
        self.control_panel.register_callback_handler("approve_trade", self.handle_user_approval)
        self.control_panel.register_callback_handler("reject_trade", self.handle_user_rejection)
        self.logger.info("✅ Callback handlers registered")


# Singleton instance
_handler: Optional[TelegramBidirectionalHandler] = None


def get_telegram_handler() -> TelegramBidirectionalHandler:
    """Get or create Telegram handler instance"""
    global _handler
    if _handler is None:
        _handler = TelegramBidirectionalHandler()
    return _handler
