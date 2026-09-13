"""
Command Processor - Handle user commands in Telegram.

Supported commands:
- /start - Welcome message
- /help - Show all available commands
- /status - Current positions and system state
- /stats - Daily performance metrics
- /next_signal - Wait for next trade signal
- /position_exit - Manually close current position
- /emergency_stop - Kill all operations
- /practice_mode - Switch to practice trading
- /real_mode - Switch to real money trading
- /lots {N} - Set lot size
- /max_positions {N} - Set max open positions
- /max_trades {N} - Set max trades per day
"""

import logging
from typing import Dict, Optional, Tuple
from datetime import datetime
from zoneinfo import ZoneInfo
from trade_state_manager import get_state_manager
from trade_approval_system import get_approval_system

logger = logging.getLogger(__name__)

# IST timezone
IST = ZoneInfo("Asia/Kolkata")


class CommandProcessor:
    """Process user commands in Telegram"""
    
    def __init__(self):
        """Initialize command processor"""
        self.logger = logging.getLogger(__name__)
        self.state_manager = get_state_manager()
        self.approval_system = get_approval_system()
        self.logger.info("✅ Command Processor initialized")
    
    def process_command(self, command_text: str, user_id: int = 0) -> Tuple[bool, str]:
        """
        Process user command
        
        Args:
            command_text: Full command text (e.g., "/status" or "/lots 2")
            user_id: Telegram user ID (for logging)
            
        Returns:
            (success, response_message)
        """
        try:
            # Parse command
            parts = command_text.strip().split()
            if not parts:
                return False, "❌ Empty command"
            
            cmd = parts[0].lower()
            args = parts[1:] if len(parts) > 1 else []
            
            self.logger.info(f"📨 Processing command: {cmd} (user: {user_id})")
            
            # Route to handler
            if cmd == "/start":
                return self.cmd_start()
            elif cmd == "/help":
                return self.cmd_help()
            elif cmd == "/status":
                return self.cmd_status()
            elif cmd == "/stats":
                return self.cmd_stats()
            elif cmd == "/next_signal":
                return self.cmd_next_signal()
            elif cmd == "/position_exit":
                return self.cmd_position_exit()
            elif cmd == "/emergency_stop":
                return self.cmd_emergency_stop()
            elif cmd == "/practice_mode":
                return self.cmd_practice_mode()
            elif cmd == "/real_mode":
                return self.cmd_real_mode()
            elif cmd == "/lots":
                return self.cmd_lots(args)
            elif cmd == "/max_positions":
                return self.cmd_max_positions(args)
            elif cmd == "/max_trades":
                return self.cmd_max_trades(args)
            elif cmd == "/time_entry":
                return self.cmd_time_entry()
            elif cmd == "/time_exit":
                return self.cmd_time_exit()
            else:
                return False, f"❌ Unknown command: {cmd}\nType /help for available commands"
            
        except Exception as e:
            self.logger.error(f"❌ Error processing command: {e}")
            return False, f"❌ Error: {e}"
    
    # =========================================================================
    # COMMAND HANDLERS
    # =========================================================================
    
    def cmd_start(self) -> Tuple[bool, str]:
        """Welcome message"""
        msg = (
            "🚀 <b>TRADING BOT - WELCOME!</b>\n\n"
            "Semi-automated trading system with manual approval.\n\n"
            "<b>Quick Start:</b>\n"
            "1. Wait for trade alerts in channels\n"
            "2. Review alert details\n"
            "3. Click [APPROVE] or [REJECT]\n"
            "4. Position auto-tracked and closed\n\n"
            "<b>Available Commands:</b>\n"
            "/help - Show all commands\n"
            "/status - Current state\n"
            "/stats - Daily performance\n"
            "/practice_mode - Switch to practice\n"
            "/real_mode - Switch to real money\n"
            "/emergency_stop - Kill all operations\n\n"
            "Type /help for more information."
        )
        return True, msg
    
    def cmd_help(self) -> Tuple[bool, str]:
        """Show all available commands"""
        msg = (
            "📋 <b>AVAILABLE COMMANDS</b>\n\n"
            "<b>Status & Info:</b>\n"
            "/status - Show current positions and system state\n"
            "/stats - Show daily performance metrics\n"
            "/time_entry - Show entry time window\n"
            "/time_exit - Show exit time (before 2:50 PM)\n\n"
            "<b>Trading Control:</b>\n"
            "/practice_mode - Switch to PRACTICE trading\n"
            "/real_mode - Switch to REAL money trading\n"
            "/lots {N} - Set lot size to N\n"
            "/max_positions {N} - Set max open positions\n"
            "/max_trades {N} - Set max trades per day\n\n"
            "<b>Position Management:</b>\n"
            "/next_signal - Wait for next trade signal\n"
            "/position_exit - Manually close current position\n\n"
            "<b>Emergency:</b>\n"
            "/emergency_stop - KILL ALL OPERATIONS\n\n"
            "<b>Info:</b>\n"
            "/start - Welcome message\n"
            "/help - This message\n\n"
            "💡 <b>Tip:</b> Buttons in alerts are easier than commands!"
        )
        return True, msg
    
    def cmd_status(self) -> Tuple[bool, str]:
        """Show current status"""
        try:
            stats = self.state_manager.get_daily_stats()
            
            mode_emoji = "🧪" if stats["mode"] == "practice" else "💰"
            active_emoji = "✅" if stats["trading_active"] else "❌"
            
            msg = (
                f"{mode_emoji} <b>SYSTEM STATUS</b>\n\n"
                f"<b>Mode:</b> {stats['mode'].upper()}\n"
                f"<b>Trading Active:</b> {active_emoji}\n\n"
                f"<b>Today's Activity:</b>\n"
                f"Trades Executed: {stats['trades_executed']}/{stats['max_trades']}\n"
                f"Trades Remaining: {stats['trades_remaining']}\n"
                f"Open Positions: {stats['open_positions']}/{stats['max_positions']}\n"
                f"Closed Positions: {stats['closed_positions']}\n"
                f"Daily P&L: {stats['daily_pnl']:+.2f}\n\n"
                f"<b>Settings:</b>\n"
                f"Lot Size: {stats['lot_size']}\n"
                f"Min SL: 5 points\n"
                f"Max Daily Loss: 2%\n\n"
                f"<b>Market Hours:</b>\n"
                f"Entry: 9:15 AM - 2:50 PM IST\n"
                f"Exit: Before 2:50 PM IST\n\n"
                f"Updated: {stats['last_updated']}"
            )
            
            return True, msg
            
        except Exception as e:
            self.logger.error(f"❌ Error getting status: {e}")
            return False, f"❌ Error: {e}"
    
    def cmd_stats(self) -> Tuple[bool, str]:
        """Show daily performance statistics"""
        try:
            closed_positions = self.state_manager.closed_positions
            
            if not closed_positions:
                msg = (
                    "📊 <b>DAILY STATS</b>\n\n"
                    "No trades closed yet today.\n"
                    "Stats will appear after first closed position."
                )
                return True, msg
            
            # Calculate statistics
            total_trades = len(closed_positions)
            total_pnl = sum(p.pnl for p in closed_positions)
            winning_trades = sum(1 for p in closed_positions if p.pnl > 0)
            losing_trades = sum(1 for p in closed_positions if p.pnl < 0)
            breakeven_trades = total_trades - winning_trades - losing_trades
            
            win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
            
            # Average P&L
            avg_pnl = total_pnl / total_trades if total_trades > 0 else 0
            
            # Best and worst
            best_trade = max((p.pnl for p in closed_positions), default=0)
            worst_trade = min((p.pnl for p in closed_positions), default=0)
            
            msg = (
                "📊 <b>DAILY PERFORMANCE</b>\n\n"
                f"<b>Overview:</b>\n"
                f"Total Trades: {total_trades}\n"
                f"Daily P&L: {total_pnl:+.2f}\n"
                f"Avg P&L: {avg_pnl:+.2f}\n\n"
                f"<b>Win Rate:</b>\n"
                f"Winning: {winning_trades} ({win_rate:.1f}%)\n"
                f"Losing: {losing_trades}\n"
                f"Breakeven: {breakeven_trades}\n\n"
                f"<b>Extremes:</b>\n"
                f"Best Trade: {best_trade:+.2f}\n"
                f"Worst Trade: {worst_trade:+.2f}\n"
                f"Range: {best_trade - worst_trade:.2f}\n\n"
                f"<b>Updated:</b> {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S IST')}"
            )
            
            return True, msg
            
        except Exception as e:
            self.logger.error(f"❌ Error getting stats: {e}")
            return False, f"❌ Error: {e}"
    
    def cmd_next_signal(self) -> Tuple[bool, str]:
        """Wait for next signal"""
        msg = (
            "⏳ <b>WAITING FOR NEXT SIGNAL</b>\n\n"
            "The screener is monitoring markets and will send\n"
            "the next trade alert to the relevant channel.\n\n"
            "Expected alerts:\n"
            "📊 INDEX OPTIONS - NIFTY/BANKNIFTY\n"
            "🌾 COMMODITIES - GOLD/CRUDE/SILVER\n"
            "🔥 INTRADAY - 5X LEVERAGE\n"
            "💰 PAY LATER - MARGIN/BNPL\n\n"
            "Stay tuned! 🎯"
        )
        return True, msg
    
    def cmd_position_exit(self) -> Tuple[bool, str]:
        """Manually close current position"""
        try:
            open_positions = self.state_manager.get_all_open_positions()
            
            if not open_positions:
                return False, "❌ No open positions to close"
            
            if len(open_positions) == 1:
                pos = open_positions[0]
                success, msg = self.approval_system.close_position_at_price(
                    position_id=pos.position_id,
                    exit_price=pos.current_price,
                    exit_reason="manual"
                )
                return success, msg
            else:
                msg = (
                    "⚠️ <b>MULTIPLE POSITIONS OPEN</b>\n\n"
                    "Positions:\n"
                )
                for i, pos in enumerate(open_positions, 1):
                    msg += f"{i}. {pos.symbol} - Entry: {pos.entry_price:.2f}\n"
                
                msg += "\nUse /position_exit with position ID to close specific position.\n"
                msg += "Or manually close from chart."
                
                return False, msg
            
        except Exception as e:
            self.logger.error(f"❌ Error closing position: {e}")
            return False, f"❌ Error: {e}"
    
    def cmd_emergency_stop(self) -> Tuple[bool, str]:
        """Emergency stop - kill all operations"""
        try:
            # Close all open positions
            open_positions = self.state_manager.get_all_open_positions()
            for pos in open_positions:
                self.approval_system.close_position_at_price(
                    position_id=pos.position_id,
                    exit_price=pos.current_price,
                    exit_reason="emergency_stop"
                )
            
            # Disable trading
            self.state_manager.set_trading_active(False)
            self.state_manager.persist_state()
            
            msg = (
                "🚨 <b>EMERGENCY STOP ACTIVATED</b>\n\n"
                f"❌ All operations halted\n"
                f"❌ {len(open_positions)} positions closed\n"
                f"❌ Trading DISABLED\n"
                f"❌ System in SAFE MODE\n\n"
                "No new signals will be processed.\n"
                "Contact admin to restart."
            )
            
            self.logger.warning("🚨 EMERGENCY STOP activated!")
            return True, msg
            
        except Exception as e:
            self.logger.error(f"❌ Error in emergency stop: {e}")
            return False, f"❌ Error: {e}"
    
    def cmd_practice_mode(self) -> Tuple[bool, str]:
        """Switch to practice mode"""
        try:
            self.state_manager.set_mode("practice")
            self.state_manager.persist_state()
            
            msg = (
                "🧪 <b>PRACTICE MODE ACTIVATED</b>\n\n"
                "✅ All trades will be in PRACTICE mode\n"
                "✅ No real money will be used\n"
                "✅ Perfect for testing\n\n"
                "You can safely test the system and validate\n"
                "strategies without any financial risk."
            )
            
            return True, msg
            
        except Exception as e:
            self.logger.error(f"❌ Error setting practice mode: {e}")
            return False, f"❌ Error: {e}"
    
    def cmd_real_mode(self) -> Tuple[bool, str]:
        """Switch to real money mode"""
        try:
            self.state_manager.set_mode("real")
            self.state_manager.persist_state()
            
            msg = (
                "💰 <b>REAL MONEY MODE ACTIVATED</b>\n\n"
                "⚠️ <b>WARNING:</b>\n"
                "Real money trading is now ENABLED.\n"
                "Approved trades will use real capital.\n\n"
                "✅ Make sure you:\n"
                "1. Have tested in PRACTICE mode\n"
                "2. Understand all risks\n"
                "3. Have sufficient capital\n"
                "4. Have set proper risk limits\n\n"
                "You can switch back to PRACTICE anytime.\n"
                "Type /practice_mode to switch back."
            )
            
            return True, msg
            
        except Exception as e:
            self.logger.error(f"❌ Error setting real mode: {e}")
            return False, f"❌ Error: {e}"
    
    def cmd_lots(self, args: list) -> Tuple[bool, str]:
        """Set lot size"""
        try:
            if not args or not args[0].isdigit():
                return False, "❌ Usage: /lots {number}\nExample: /lots 2"
            
            lot_size = int(args[0])
            if lot_size <= 0 or lot_size > 10:
                return False, "❌ Lot size must be between 1 and 10"
            
            self.state_manager.set_lot_size(lot_size)
            self.state_manager.persist_state()
            
            msg = f"✅ Lot size updated to: {lot_size}"
            return True, msg
            
        except Exception as e:
            self.logger.error(f"❌ Error setting lot size: {e}")
            return False, f"❌ Error: {e}"
    
    def cmd_max_positions(self, args: list) -> Tuple[bool, str]:
        """Set max open positions"""
        try:
            if not args or not args[0].isdigit():
                return False, "❌ Usage: /max_positions {number}\nExample: /max_positions 3"
            
            max_pos = int(args[0])
            if max_pos <= 0 or max_pos > 10:
                return False, "❌ Max positions must be between 1 and 10"
            
            self.state_manager.set_max_positions(max_pos)
            self.state_manager.persist_state()
            
            msg = f"✅ Max open positions updated to: {max_pos}"
            return True, msg
            
        except Exception as e:
            self.logger.error(f"❌ Error setting max positions: {e}")
            return False, f"❌ Error: {e}"
    
    def cmd_max_trades(self, args: list) -> Tuple[bool, str]:
        """Set max trades per day"""
        try:
            if not args or not args[0].isdigit():
                return False, "❌ Usage: /max_trades {number}\nExample: /max_trades 10"
            
            max_trades = int(args[0])
            if max_trades <= 0 or max_trades > 100:
                return False, "❌ Max trades must be between 1 and 100"
            
            self.state_manager.set_max_trades(max_trades)
            self.state_manager.persist_state()
            
            msg = f"✅ Max trades per day updated to: {max_trades}"
            return True, msg
            
        except Exception as e:
            self.logger.error(f"❌ Error setting max trades: {e}")
            return False, f"❌ Error: {e}"
    
    def cmd_time_entry(self) -> Tuple[bool, str]:
        """Show entry time window"""
        msg = (
            "⏰ <b>ENTRY TIME WINDOW</b>\n\n"
            "Entry is allowed between:\n"
            "🟢 <b>09:15 AM IST</b> (Market Open)\n"
            "🔴 <b>02:50 PM IST</b> (Before Market Close)\n\n"
            "After 2:50 PM, no new trades will be accepted.\n\n"
            "Current time: " + datetime.now(IST).strftime("%H:%M IST")
        )
        return True, msg
    
    def cmd_time_exit(self) -> Tuple[bool, str]:
        """Show exit time (auto-exit before market close)"""
        msg = (
            "⏰ <b>EXIT TIME POLICY</b>\n\n"
            "All positions MUST be closed before:\n"
            "🔴 <b>03:00 PM IST</b> (Market Close)\n\n"
            "Auto-exit triggers at:\n"
            "⏳ <b>02:50 PM IST</b>\n\n"
            "At 2:50 PM, all open positions will be\n"
            "automatically closed at market price.\n\n"
            "Current time: " + datetime.now(IST).strftime("%H:%M IST")
        )
        return True, msg


# Singleton instance
_processor: Optional[CommandProcessor] = None


def get_command_processor() -> CommandProcessor:
    """Get or create command processor instance"""
    global _processor
    if _processor is None:
        _processor = CommandProcessor()
    return _processor
