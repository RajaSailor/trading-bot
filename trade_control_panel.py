"""
Trade Control Panel - Create and manage Telegram UI buttons and keyboards.

Handles:
- Inline keyboard creation for trade approval
- SL type selection buttons
- Exit strategy buttons
- Mode selection (practice/real)
- Lot size controls
- Button callback routing
"""

import logging
from typing import Dict, List, Optional, Callable
from alert_formatter import TradeAlert

logger = logging.getLogger(__name__)


class TradeControlPanel:
    """Create and manage trade control UI in Telegram"""
    
    def __init__(self):
        """Initialize control panel"""
        self.logger = logging.getLogger(__name__)
        self.callback_handlers: Dict[str, Callable] = {}
        self.logger.info("✅ Trade Control Panel initialized")
    
    # =========================================================================
    # KEYBOARD CREATION
    # =========================================================================
    
    def create_trade_approval_keyboard(self, trade_id: str) -> Dict:
        """
        Create inline keyboard for trade approval/rejection
        
        Args:
            trade_id: Unique trade identifier
            
        Returns:
            Telegram inline keyboard dict
        """
        return {
            "inline_keyboard": [
                [
                    {
                        "text": "✅ APPROVE",
                        "callback_data": f"approve_trade_{trade_id}"
                    },
                    {
                        "text": "❌ REJECT",
                        "callback_data": f"reject_trade_{trade_id}"
                    }
                ],
                [
                    {
                        "text": "✏️ EDIT",
                        "callback_data": f"edit_trade_{trade_id}"
                    },
                    {
                        "text": "⏭️ SKIP",
                        "callback_data": f"skip_trade_{trade_id}"
                    }
                ]
            ]
        }
    
    def create_sl_selection_keyboard(self, trade_id: str) -> Dict:
        """
        Create inline keyboard for stop loss type selection
        
        Args:
            trade_id: Unique trade identifier
            
        Returns:
            Telegram inline keyboard dict
        """
        return {
            "inline_keyboard": [
                [
                    {
                        "text": "📍 SL -5 Points",
                        "callback_data": f"sl_static_5_{trade_id}"
                    },
                    {
                        "text": "📊 SL C2C",
                        "callback_data": f"sl_c2c_{trade_id}"
                    }
                ],
                [
                    {
                        "text": "🎯 Trail +10",
                        "callback_data": f"sl_trail_10_{trade_id}"
                    },
                    {
                        "text": "⚙️ Custom SL",
                        "callback_data": f"sl_custom_{trade_id}"
                    }
                ],
                [
                    {
                        "text": "🔙 Back",
                        "callback_data": f"back_to_approval_{trade_id}"
                    }
                ]
            ]
        }
    
    def create_exit_strategy_keyboard(self, trade_id: str) -> Dict:
        """
        Create inline keyboard for exit strategy selection
        
        Args:
            trade_id: Unique trade identifier
            
        Returns:
            Telegram inline keyboard dict
        """
        return {
            "inline_keyboard": [
                [
                    {
                        "text": "🎯 TARGET EXIT",
                        "callback_data": f"exit_target_{trade_id}"
                    },
                    {
                        "text": "⏰ BEFORE 2:50",
                        "callback_data": f"exit_before_close_{trade_id}"
                    }
                ],
                [
                    {
                        "text": "🖱️ MANUAL EXIT",
                        "callback_data": f"exit_manual_{trade_id}"
                    }
                ],
                [
                    {
                        "text": "🔙 Back",
                        "callback_data": f"back_to_approval_{trade_id}"
                    }
                ]
            ]
        }
    
    def create_mode_selection_keyboard(self) -> Dict:
        """
        Create inline keyboard for trading mode selection (practice/real)
        
        Returns:
            Telegram inline keyboard dict
        """
        return {
            "inline_keyboard": [
                [
                    {
                        "text": "🧪 PRACTICE MODE",
                        "callback_data": "mode_practice"
                    },
                    {
                        "text": "💰 REAL MODE",
                        "callback_data": "mode_real"
                    }
                ],
                [
                    {
                        "text": "⏹️ TURN OFF",
                        "callback_data": "mode_off"
                    }
                ]
            ]
        }
    
    def create_lot_size_keyboard(self, current_lots: int = 1, trade_id: str = "") -> Dict:
        """
        Create inline keyboard for lot size adjustment
        
        Args:
            current_lots: Current lot size
            trade_id: Optional trade identifier
            
        Returns:
            Telegram inline keyboard dict
        """
        return {
            "inline_keyboard": [
                [
                    {
                        "text": "➖ DECREASE",
                        "callback_data": f"lots_decrease_{trade_id}" if trade_id else "lots_decrease"
                    },
                    {
                        "text": f"📦 {current_lots}",
                        "callback_data": "noop"
                    },
                    {
                        "text": "➕ INCREASE",
                        "callback_data": f"lots_increase_{trade_id}" if trade_id else "lots_increase"
                    }
                ]
            ]
        }
    
    def create_entry_edit_keyboard(self, trade_id: str) -> Dict:
        """
        Create inline keyboard for entry price editing
        
        Args:
            trade_id: Unique trade identifier
            
        Returns:
            Telegram inline keyboard dict
        """
        return {
            "inline_keyboard": [
                [
                    {
                        "text": "📉 LOWER ENTRY",
                        "callback_data": f"entry_lower_{trade_id}"
                    },
                    {
                        "text": "📈 HIGHER ENTRY",
                        "callback_data": f"entry_higher_{trade_id}"
                    }
                ],
                [
                    {
                        "text": "📝 ENTER CUSTOM",
                        "callback_data": f"entry_custom_{trade_id}"
                    }
                ],
                [
                    {
                        "text": "🔙 Back",
                        "callback_data": f"back_to_approval_{trade_id}"
                    }
                ]
            ]
        }
    
    # =========================================================================
    # COMPLETE ALERT MESSAGE WITH KEYBOARD
    # =========================================================================
    
    def format_approval_message(
        self,
        alert: TradeAlert,
        trade_id: str
    ) -> tuple[str, Dict]:
        """
        Format complete trade alert message with approval keyboard
        
        Args:
            alert: TradeAlert object
            trade_id: Unique trade ID
            
        Returns:
            (formatted_message, inline_keyboard)
        """
        # Calculate risk/reward
        risk_points = abs(alert.entry_price - alert.sl_value)
        reward_points = abs(alert.target - alert.entry_price) if alert.target else risk_points * 2
        rr_ratio = reward_points / risk_points if risk_points > 0 else 0
        
        # Build message
        lines = [
            f"📊 <b>{alert.symbol} - {alert.signal_type.value}</b>",
            "",
            f"<b>Entry:</b> {alert.entry_price:.2f}",
            f"<b>SL:</b> {alert.sl_value:.2f} ({self._get_sl_description(alert.sl_type, alert.sl_value, alert.entry_price)})",
            f"<b>Target:</b> {alert.target:.2f}" if alert.target else "",
            "",
            f"<b>Strategy:</b> {alert.strategy}",
            f"<b>Timeframe:</b> {alert.timeframe}",
            f"<b>Lots:</b> {alert.qty_lots}",
            "",
            f"<b>Risk/Reward:</b> 1:{rr_ratio:.2f}",
            f"<b>Risk:</b> {risk_points:.2f} pts | <b>Reward:</b> {reward_points:.2f} pts",
            "",
            "⏰ <b>Entry Window:</b> 9:15 AM - 2:50 PM IST",
            "❌ <b>Exit Before:</b> 2:50 PM IST (Auto-close)",
            "",
            f"<i>Channel: {alert.channel}</i>",
        ]
        
        message = "\n".join(filter(None, lines))
        keyboard = self.create_trade_approval_keyboard(trade_id)
        
        return message, keyboard
    
    def format_confirmation_message(
        self,
        alert: TradeAlert,
        status: str,
        order_id: Optional[str] = None
    ) -> str:
        """
        Format trade execution confirmation message
        
        Args:
            alert: TradeAlert object
            status: "APPROVED", "REJECTED", "EXECUTED", etc.
            order_id: Optional DhanHQ order ID
            
        Returns:
            Formatted confirmation message
        """
        status_emoji = {
            "APPROVED": "✅",
            "REJECTED": "❌",
            "EXECUTED": "✅",
            "PENDING": "⏳",
            "CANCELLED": "❌",
        }.get(status, "ℹ️")
        
        lines = [
            f"{status_emoji} <b>Trade {status}</b>",
            "",
            f"<b>Symbol:</b> {alert.symbol}",
            f"<b>Signal:</b> {alert.signal_type.value}",
            f"<b>Entry:</b> {alert.entry_price:.2f}",
            f"<b>SL:</b> {alert.sl_value:.2f}",
        ]
        
        if alert.target:
            lines.append(f"<b>Target:</b> {alert.target:.2f}")
        
        lines.extend([
            f"<b>Lots:</b> {alert.qty_lots}",
        ])
        
        if order_id:
            lines.insert(2, f"<b>Order ID:</b> {order_id}")
        
        if status == "EXECUTED":
            lines.extend([
                "",
                "✅ Position is now being tracked",
                "📊 Updates will be sent to SERVICE_ALERTS channel",
                "🎯 Auto-exit at target or 2:50 PM IST"
            ])
        
        return "\n".join(filter(None, lines))
    
    def format_status_message(self, stats: Dict) -> str:
        """
        Format system status message
        
        Args:
            stats: Dictionary with system statistics
            
        Returns:
            Formatted status message
        """
        mode_emoji = "🧪" if stats.get("mode") == "practice" else "💰"
        active_emoji = "✅" if stats.get("trading_active") else "❌"
        
        lines = [
            f"{mode_emoji} <b>TRADING STATUS</b>",
            "",
            f"Mode: {stats.get('mode', 'unknown').upper()}",
            f"Active: {active_emoji} {'YES' if stats.get('trading_active') else 'NO'}",
            "",
            "<b>Today's Stats:</b>",
            f"Trades Executed: {stats.get('trades_executed', 0)}/{stats.get('max_trades', 0)}",
            f"Open Positions: {stats.get('open_positions', 0)}/{stats.get('max_positions', 0)}",
            f"Closed Positions: {stats.get('closed_positions', 0)}",
            f"Daily P&L: {stats.get('daily_pnl', 0):.2f}",
            f"Lot Size: {stats.get('lot_size', 1)}",
            "",
            f"Last Updated: {stats.get('last_updated', 'N/A')}",
        ]
        
        return "\n".join(filter(None, lines))
    
    def format_position_update(
        self,
        symbol: str,
        entry_price: float,
        current_price: float,
        pnl: float,
        sl: float,
        target: float
    ) -> str:
        """
        Format position update message
        
        Args:
            symbol: Trading symbol
            entry_price: Entry price
            current_price: Current price
            pnl: Current P&L
            sl: Stop loss
            target: Target level
            
        Returns:
            Formatted position update message
        """
        pnl_emoji = "📈" if pnl >= 0 else "📉"
        pnl_color = "+" if pnl >= 0 else ""
        
        lines = [
            f"📊 <b>{symbol}</b>",
            "",
            f"<b>Entry:</b> {entry_price:.2f}",
            f"<b>Current:</b> {current_price:.2f}",
            f"<b>P&L:</b> {pnl_emoji} {pnl_color}{pnl:.2f}",
            "",
            f"<b>SL:</b> {sl:.2f}",
            f"<b>Target:</b> {target:.2f}",
            "",
            f"Distance to SL: {abs(current_price - sl):.2f} pts",
            f"Distance to Target: {abs(target - current_price):.2f} pts",
        ]
        
        return "\n".join(filter(None, lines))
    
    # =========================================================================
    # CALLBACK ROUTING
    # =========================================================================
    
    def register_callback_handler(self, callback_type: str, handler: Callable) -> None:
        """
        Register a callback handler function
        
        Args:
            callback_type: Type of callback (e.g., "approve_trade", "reject_trade")
            handler: Callable function to handle the callback
        """
        self.callback_handlers[callback_type] = handler
        self.logger.info(f"✅ Registered callback handler: {callback_type}")
    
    def handle_callback(self, callback_data: str, user_id: int) -> Optional[str]:
        """
        Route callback to appropriate handler
        
        Args:
            callback_data: Callback data from Telegram button
            user_id: User ID who clicked button
            
        Returns:
            Response message from handler, if any
        """
        try:
            # Parse callback data
            parts = callback_data.split("_")
            callback_type = "_".join(parts[:-1]) if len(parts) > 1 else callback_data
            
            # Get handler
            handler = self.callback_handlers.get(callback_type)
            if handler:
                return handler(callback_data, user_id)
            else:
                self.logger.warning(f"⚠️ No handler for callback: {callback_type}")
                return None
            
        except Exception as e:
            self.logger.error(f"❌ Error handling callback: {e}")
            return None
    
    # =========================================================================
    # HELPER METHODS
    # =========================================================================
    
    def _get_sl_description(self, sl_type: str, sl_value: float, entry_price: float) -> str:
        """Get human-readable SL description"""
        if sl_type == "static":
            points = abs(entry_price - sl_value)
            return f"-{points:.2f} points"
        elif sl_type == "c2c":
            return "Close to Close"
        elif sl_type == "trail":
            return f"Trail {sl_value:.2f}"
        return "Unknown"
    
    def create_quick_actions_keyboard(self) -> Dict:
        """
        Create quick actions keyboard for common operations
        
        Returns:
            Telegram inline keyboard dict
        """
        return {
            "inline_keyboard": [
                [
                    {
                        "text": "📊 STATUS",
                        "callback_data": "cmd_status"
                    },
                    {
                        "text": "📈 STATS",
                        "callback_data": "cmd_stats"
                    }
                ],
                [
                    {
                        "text": "🧪 PRACTICE",
                        "callback_data": "cmd_practice_on"
                    },
                    {
                        "text": "💰 REAL",
                        "callback_data": "cmd_real_on"
                    }
                ],
                [
                    {
                        "text": "⏹️ STOP",
                        "callback_data": "cmd_stop"
                    },
                    {
                        "text": "🆘 EMERGENCY",
                        "callback_data": "cmd_emergency"
                    }
                ]
            ]
        }
    
    def create_help_keyboard(self) -> Dict:
        """
        Create help/information keyboard
        
        Returns:
            Telegram inline keyboard dict
        """
        return {
            "inline_keyboard": [
                [
                    {
                        "text": "📋 COMMANDS",
                        "callback_data": "help_commands"
                    },
                    {
                        "text": "❓ FAQ",
                        "callback_data": "help_faq"
                    }
                ],
                [
                    {
                        "text": "⚙️ SETTINGS",
                        "callback_data": "help_settings"
                    },
                    {
                        "text": "🔒 SECURITY",
                        "callback_data": "help_security"
                    }
                ]
            ]
        }


# Singleton instance
_control_panel: Optional[TradeControlPanel] = None


def get_control_panel() -> TradeControlPanel:
    """Get or create control panel instance"""
    global _control_panel
    if _control_panel is None:
        _control_panel = TradeControlPanel()
    return _control_panel
