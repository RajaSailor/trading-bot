"""
Alert Formatter Module - Format trade signals for Telegram with inline buttons.

Handles:
- Trade alert formatting with entry, SL, target
- Risk/reward calculations
- Inline keyboard button creation
- Emoji formatting for clarity
- Different formatting for different strategies
"""

import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class SignalType(Enum):
    """Signal types"""
    BUY = "BUY"
    SELL = "SELL"
    LONG = "LONG"
    SHORT = "SHORT"


class SLType(Enum):
    """Stop loss types"""
    STATIC = "static"
    C2C = "c2c"
    TRAIL = "trail"


@dataclass
class TradeAlert:
    """Trade alert data structure"""
    symbol: str
    signal_type: SignalType
    entry_price: float
    sl_value: float
    sl_type: SLType = SLType.STATIC
    target: float = 0.0
    trail_sl: float = 0.0
    qty_lots: int = 1
    strategy: str = "5-MIN Breakout"
    timeframe: str = "Intraday"
    channel: str = "INDEX OPTIONS ALERTS"
    option_contract: Optional[str] = None
    option_premium: Optional[float] = None
    greeks: Optional[Dict] = None


class AlertFormatter:
    """Format trade alerts for Telegram display with buttons"""
    
    # Emoji mappings
    SIGNAL_EMOJI = {
        "BUY": "📈",
        "SELL": "📉",
        "LONG": "📈",
        "SHORT": "📉",
    }
    
    STRATEGY_EMOJI = {
        "5-MIN Breakout": "⚡️",
        "Premium Breakout": "💎",
        "Intraday": "🔥",
        "Swing": "📊",
    }
    
    def __init__(self):
        """Initialize formatter"""
        self.logger = logging.getLogger(__name__)
    
    def format_trade_alert(self, alert: TradeAlert) -> str:
        """
        Format complete trade alert for Telegram
        
        Args:
            alert: TradeAlert object with all signal data
            
        Returns:
            Formatted message string
        """
        try:
            signal_emoji = self.SIGNAL_EMOJI.get(alert.signal_type.value, "📊")
            strategy_emoji = self.STRATEGY_EMOJI.get(alert.strategy, "⚡️")
            
            # Calculate risk/reward
            risk_points = abs(alert.entry_price - alert.sl_value)
            reward_points = abs(alert.target - alert.entry_price) if alert.target else risk_points * 2
            rr_ratio = reward_points / risk_points if risk_points > 0 else 0
            
            # Build message
            lines = [
                f"{signal_emoji} <b>{alert.symbol} - {alert.signal_type.value} Signal</b>",
                "",
                f"<b>Entry:</b> {alert.entry_price:.2f}",
                f"<b>SL:</b> {alert.sl_value:.2f} ({self._sl_type_text(alert.sl_type, alert.sl_value, alert.entry_price)})",
                f"<b>Target:</b> {alert.target:.2f}" if alert.target else "",
                f"<b>Trail SL:</b> +{alert.trail_sl:.2f}" if alert.trail_sl else "",
                "",
                f"Strategy: {strategy_emoji} {alert.strategy}",
                f"Timeframe: {alert.timeframe}",
                f"Lots: {alert.qty_lots}",
                "",
                f"<b>Risk/Reward:</b> 1:{rr_ratio:.2f}",
                f"Risk Points: {risk_points:.2f}",
                f"Reward Points: {reward_points:.2f}",
            ]
            
            # Add option details if available
            if alert.option_contract:
                lines.extend([
                    "",
                    "<b>📋 Option Contract:</b>",
                    f"Symbol: {alert.option_contract}",
                ])
                if alert.option_premium:
                    lines.append(f"Premium LTP: {alert.option_premium:.2f}")
                if alert.greeks:
                    lines.append(self._format_greeks(alert.greeks))
            
            # Add market hours and exit info
            lines.extend([
                "",
                "⏰ <b>Entry Window:</b> 9:15 AM - 2:50 PM IST",
                "❌ <b>Exit Before:</b> 2:50 PM IST (Auto-exit at market close)",
                "",
                f"<i>Channel: {alert.channel}</i>",
            ])
            
            message = "\n".join(filter(None, lines))
            return message
            
        except Exception as e:
            self.logger.error(f"Error formatting alert: {e}")
            return f"⚠️ Error formatting alert: {e}"
    
    def format_confirmation(self, alert: TradeAlert, status: str, order_id: Optional[str] = None) -> str:
        """
        Format execution confirmation message
        
        Args:
            alert: TradeAlert object
            status: "APPROVED", "REJECTED", "EXECUTED", etc.
            order_id: Optional DhanHQ order ID
            
        Returns:
            Formatted confirmation message
        """
        try:
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
                f"<b>Target:</b> {alert.target:.2f}" if alert.target else "",
                f"<b>Lots:</b> {alert.qty_lots}",
            ]
            
            if order_id:
                lines.insert(3, f"<b>Order ID:</b> {order_id}")
            
            if status == "EXECUTED":
                lines.append("")
                lines.append("✅ Position will be auto-tracked")
                lines.append("📊 Updates sent to SERVICE_ALERTS channel")
            
            message = "\n".join(filter(None, lines))
            return message
            
        except Exception as e:
            self.logger.error(f"Error formatting confirmation: {e}")
            return f"⚠️ Error formatting confirmation: {e}"
    
    def format_error(self, error_msg: str, trade_symbol: Optional[str] = None) -> str:
        """
        Format error message
        
        Args:
            error_msg: Error message text
            trade_symbol: Optional symbol the error relates to
            
        Returns:
            Formatted error message
        """
        lines = [
            "⚠️ <b>ERROR</b>",
            "",
            f"Message: {error_msg}",
        ]
        
        if trade_symbol:
            lines.insert(2, f"Symbol: {trade_symbol}")
        
        return "\n".join(lines)
    
    def create_trade_approval_buttons(self, trade_id: str) -> Dict:
        """
        Create inline keyboard for trade approval
        
        Args:
            trade_id: Unique trade identifier
            
        Returns:
            Inline keyboard dict for Telegram
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
    
    def create_sl_selection_buttons(self, trade_id: str) -> Dict:
        """
        Create inline keyboard for SL type selection
        
        Args:
            trade_id: Unique trade identifier
            
        Returns:
            Inline keyboard dict for Telegram
        """
        return {
            "inline_keyboard": [
                [
                    {
                        "text": "📍 SL -5",
                        "callback_data": f"sl_static_{trade_id}"
                    },
                    {
                        "text": "📊 SL C2C",
                        "callback_data": f"sl_c2c_{trade_id}"
                    }
                ],
                [
                    {
                        "text": "🎯 TRAIL +10",
                        "callback_data": f"sl_trail_{trade_id}"
                    },
                    {
                        "text": "⚙️ CUSTOM",
                        "callback_data": f"sl_custom_{trade_id}"
                    }
                ]
            ]
        }
    
    def create_exit_strategy_buttons(self, trade_id: str) -> Dict:
        """
        Create inline keyboard for exit strategy selection
        
        Args:
            trade_id: Unique trade identifier
            
        Returns:
            Inline keyboard dict for Telegram
        """
        return {
            "inline_keyboard": [
                [
                    {
                        "text": "🎯 TARGET",
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
                ]
            ]
        }
    
    def create_mode_selection_buttons(self) -> Dict:
        """
        Create inline keyboard for practice/real mode selection
        
        Returns:
            Inline keyboard dict for Telegram
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
                        "text": "❌ TURN OFF",
                        "callback_data": "mode_off"
                    }
                ]
            ]
        }
    
    def _sl_type_text(self, sl_type: SLType, sl_value: float, entry_price: float) -> str:
        """Get SL type description"""
        if sl_type == SLType.STATIC:
            points = abs(entry_price - sl_value)
            return f"-{points:.2f} points"
        elif sl_type == SLType.C2C:
            return "Close to Close"
        elif sl_type == SLType.TRAIL:
            return f"Trailing {sl_value:.2f}"
        return "Unknown"
    
    def _format_greeks(self, greeks: Dict) -> str:
        """Format option Greeks"""
        lines = ["<b>Greeks:</b>"]
        if "delta" in greeks:
            lines.append(f"  Delta: {greeks['delta']:.3f}")
        if "gamma" in greeks:
            lines.append(f"  Gamma: {greeks['gamma']:.3f}")
        if "theta" in greeks:
            lines.append(f"  Theta: {greeks['theta']:.3f}")
        if "vega" in greeks:
            lines.append(f"  Vega: {greeks['vega']:.3f}")
        return "\n".join(lines)


# Singleton instance
_formatter = AlertFormatter()


def get_formatter() -> AlertFormatter:
    """Get alert formatter instance"""
    return _formatter
