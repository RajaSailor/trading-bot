"""
DhanHQ Telegram Bridge - Connect Telegram alerts to DhanHQ orders and send trading updates.

Workflow:
1. Receive trade signal from Telegram bot
2. Validate signal (safety checks)
3. Place order on DhanHQ
4. Track position
5. Send execution confirmation to Telegram
6. Monitor SL/Target hits
7. Send P&L updates to Telegram

Features:
- Real-time trade alerts
- Position monitoring
- P&L tracking
- SL/Target notifications
- Error handling & recovery
"""

import logging
import asyncio
import hmac
import os
from typing import Dict, Tuple, Optional, Callable, Union
from datetime import datetime
from zoneinfo import ZoneInfo
from enum import Enum

from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from dhan_integration import DhanIntegration, get_dhan_integration
from dhan_postback_handler import DhanPostbackHandler

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


class AlertType(Enum):
    """Types of trading alerts"""
    TRADE_SIGNAL = "TRADE_SIGNAL"
    SL_HIT = "SL_HIT"
    TARGET_HIT = "TARGET_HIT"
    POSITION_UPDATE = "POSITION_UPDATE"
    ERROR = "ERROR"


class DhanTelegramBridge:
    """
    Bridge between Telegram and DhanHQ trading system.
    
    Responsibilities:
    - Parse Telegram trade signals
    - Execute trades on DhanHQ
    - Send trade confirmations
    - Monitor positions
    - Send P&L updates
    - Handle errors
    """
    
    @staticmethod
    def _coerce_chat_id(value):
        """Accept numeric chat IDs and @channel usernames."""
        if value is None:
            return None
        if isinstance(value, int):
            return value

        text = str(value).strip()
        if not text:
            return None
        if text.lstrip("-").isdigit():
            return int(text)
        return text

    def __init__(
        self,
        telegram_bot_token: Optional[str] = None,
        dhan_integration: Optional[DhanIntegration] = None,
        postback_handler: Optional[DhanPostbackHandler] = None,
        alert_chat_id: Optional[Union[int, str]] = None,
        webhook_secret: Optional[str] = None
    ):
        """
        Initialize Telegram bridge
        
        Args:
            telegram_bot_token: Telegram bot token
            dhan_integration: DhanIntegration instance
            postback_handler: DhanPostbackHandler instance
            alert_chat_id: Chat ID for alerts
        """
        self.logger = logging.getLogger(__name__)

        # Backward compatibility for old call style: DhanTelegramBridge(dhan_integration)
        if isinstance(telegram_bot_token, DhanIntegration) and dhan_integration is None:
            dhan_integration = telegram_bot_token
            telegram_bot_token = None

        self.bot_token = telegram_bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        if not self.bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN required")

        default_chat_id = self._coerce_chat_id(os.getenv("TELEGRAM_CHAT_ID"))
        self.alert_chat_id = self._coerce_chat_id(alert_chat_id)
        if self.alert_chat_id is None:
            self.alert_chat_id = default_chat_id
        self.webhook_secret = webhook_secret or os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
        self.bot = Bot(token=self.bot_token)
        
        # Get or create integrations
        self.dhan = dhan_integration or get_dhan_integration()
        self.postback = postback_handler
        
        # Telegram application
        self.app = None
        
        # Setup callbacks
        self._setup_callbacks()
        
        self.logger.info("✅ DhanHQ Telegram Bridge initialized")
        self.logger.info(f"   Alert Chat ID: {alert_chat_id}")
    
    # =========================================================================
    # TELEGRAM SETUP
    # =========================================================================
    
    async def initialize_telegram_app(self) -> Application:
        """Initialize Telegram bot application"""
        try:
            self.app = Application.builder().token(self.bot_token).build()
            
            # Add handlers
            self.app.add_handler(CommandHandler("start", self.handle_start))
            self.app.add_handler(CommandHandler("status", self.handle_status))
            self.app.add_handler(CommandHandler("close", self.handle_close_command))
            self.app.add_handler(CommandHandler("positions", self.handle_positions))
            self.app.add_handler(CommandHandler("pnl", self.handle_pnl))
            self.app.add_handler(CommandHandler("help", self.handle_help))
            
            # Message handler for trade signals
            self.app.add_handler(MessageHandler(filters.TEXT, self.handle_trade_signal))
            
            self.logger.info("✅ Telegram app initialized")
            return self.app
            
        except Exception as e:
            self.logger.error(f"❌ Telegram initialization error: {e}")
            raise
    
    async def start_polling(self) -> None:
        """Start Telegram polling"""
        try:
            self.logger.info("🚀 Starting Telegram polling...")
            await self.app.initialize()
            await self.app.start()
            await self.app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
            self.logger.info("✅ Telegram polling started")
        except Exception as e:
            self.logger.error(f"❌ Polling error: {e}")
            raise
    
    async def stop_polling(self) -> None:
        """Stop Telegram polling"""
        try:
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()
            self.logger.info("🛑 Telegram polling stopped")
        except Exception as e:
            self.logger.error(f"❌ Stop error: {e}")

    async def set_webhook(self, webhook_url: str) -> None:
        """Set Telegram webhook URL"""
        await self.bot.set_webhook(url=webhook_url, secret_token=self.webhook_secret or None)
        self.logger.info("✅ Telegram webhook configured")

    async def clear_webhook(self) -> None:
        """Clear Telegram webhook"""
        await self.bot.delete_webhook(drop_pending_updates=False)
        self.logger.info("✅ Telegram webhook cleared")

    async def handle_webhook_update(self, payload: Dict, secret_token: str = None) -> bool:
        """Process Telegram webhook update payload"""
        try:
            if self.webhook_secret:
                if not secret_token or not hmac.compare_digest(secret_token, self.webhook_secret):
                    self.logger.warning("❌ Telegram webhook secret validation failed")
                    return False

            if self.app is None:
                await self.initialize_telegram_app()

            update = Update.de_json(payload, self.app.bot)
            await self.app.process_update(update)
            return True
        except Exception as e:
            self.logger.error(f"❌ Webhook update error: {e}")
            return False
    
    # =========================================================================
    # TRADE SIGNAL PARSING
    # =========================================================================
    
    def parse_trade_signal(self, message: str) -> Tuple[bool, Dict]:
        """
        Parse trade signal from message
        
        Expected Format:
        BUY NIFTY50 1 19400 SL:19300 TARGET:19600
        SELL BANKNIFTY 2 45000 SL:45100 TARGET:44900
        
        Args:
            message: Telegram message text
            
        Returns:
            (success, signal_dict)
        """
        try:
            parts = message.strip().upper().split()
            
            if len(parts) < 6:
                return False, {}
            
            signal = {
                "transaction_type": parts[0],  # BUY or SELL
                "symbol": parts[1],
                "quantity": int(parts[2]),
                "entry_price": float(parts[3]),
                "sl_price": None,
                "target_price": None,
                "strategy": ""
            }
            
            # Parse SL and TARGET
            for part in parts[4:]:
                if part.startswith("SL:"):
                    signal["sl_price"] = float(part.replace("SL:", ""))
                elif part.startswith("TARGET:"):
                    signal["target_price"] = float(part.replace("TARGET:", ""))
                else:
                    signal["strategy"] = part
            
            # Validate
            if not all([
                signal["transaction_type"] in ["BUY", "SELL"],
                signal["symbol"],
                signal["quantity"] > 0,
                signal["entry_price"] > 0,
                signal["sl_price"],
                signal["target_price"]
            ]):
                return False, {}
            
            self.logger.info(f"✅ Parsed signal: {signal['transaction_type']} {signal['symbol']}")
            return True, signal
            
        except Exception as e:
            self.logger.error(f"❌ Signal parse error: {e}")
            return False, {}
    
    # =========================================================================
    # TELEGRAM HANDLERS
    # =========================================================================
    
    async def handle_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /start command"""
        await update.message.reply_text(
            "🚀 *DhanHQ Trading Bot Started!*\n\n"
            "Commands:\n"
            "/status - Current status\n"
            "/positions - Open positions\n"
            "/pnl - Daily P&L\n"
            "/help - Help & signal format\n\n"
            "Send trade signal:\n"
            "BUY NIFTY50 1 19400 SL:19300 TARGET:19600",
            parse_mode="Markdown"
        )
    
    async def handle_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /help command"""
        await update.message.reply_text(
            "📖 *Trade Signal Format*\n\n"
            "`[TYPE] [SYMBOL] [QTY] [PRICE] SL:[PRICE] TARGET:[PRICE]`\n\n"
            "Examples:\n"
            "`BUY NIFTY50 1 19400 SL:19300 TARGET:19600`\n"
            "`SELL BANKNIFTY 2 45000 SL:45100 TARGET:44900`\n\n"
            "Types: BUY, SELL\n"
            "Symbols: NIFTY50, BANKNIFTY, GOLD, SILVER, etc.",
            parse_mode="Markdown"
        )
    
    async def handle_trade_signal(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle incoming trade signal"""
        try:
            message = update.message.text
            
            # Parse signal
            success, signal = self.parse_trade_signal(message)
            
            if not success:
                await update.message.reply_text(
                    "❌ Invalid signal format!\n\n"
                    "Use: BUY NIFTY50 1 19400 SL:19300 TARGET:19600"
                )
                return
            
            # Send acknowledgment
            await update.message.reply_text(
                f"⏳ Placing trade...\n"
                f"Symbol: {signal['symbol']}\n"
                f"Type: {signal['transaction_type']}\n"
                f"Entry: ₹{signal['entry_price']}"
            )
            
            # Place trade
            success, msg, order_id = self.dhan.place_trade(
                symbol=signal["symbol"],
                transaction_type=signal["transaction_type"],
                quantity=signal["quantity"],
                entry_price=signal["entry_price"],
                sl_price=signal["sl_price"],
                target_price=signal["target_price"],
                strategy=signal.get("strategy", "telegram_signal")
            )
            
            if success:
                # Send confirmation
                await self._send_trade_confirmation(
                    update,
                    order_id,
                    signal
                )
            else:
                await update.message.reply_text(f"❌ Trade failed: {msg}")
            
        except Exception as e:
            self.logger.error(f"❌ Signal handler error: {e}")
            await update.message.reply_text(f"❌ Error: {e}")
    
    async def handle_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /status command"""
        try:
            summary = self.dhan.get_summary()
            pos_summary = summary["positions"]
            
            message = (
                f"📊 *Trading Status*\n\n"
                f"Open: {pos_summary['openPositions']}\n"
                f"Closed: {pos_summary['closedPositions']}\n"
                f"Win Rate: {pos_summary['winRate']}\n"
                f"Daily P&L: ₹{summary['daily_pnl']:.2f}"
            )
            
            await update.message.reply_text(message, parse_mode="Markdown")
            
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")
    
    async def handle_positions(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /positions command"""
        try:
            positions = self.dhan.get_positions()
            open_pos = positions["open"]
            
            if not open_pos:
                await update.message.reply_text("✅ No open positions")
                return
            
            message = "📍 *Open Positions*\n\n"
            
            for pos in open_pos:
                message += (
                    f"*{pos['symbol']}*\n"
                    f"Type: {pos['transactionType']}\n"
                    f"Entry: ₹{pos['entryPrice']}\n"
                    f"Current: ₹{pos['currentPrice']}\n"
                    f"P&L: ₹{pos['currentPnL']:.2f}\n\n"
                )
            
            await update.message.reply_text(message, parse_mode="Markdown")
            
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")
    
    async def handle_pnl(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /pnl command"""
        try:
            summary = self.dhan.get_summary()
            
            message = (
                f"📈 *Daily P&L Summary*\n\n"
                f"Total P&L: ₹{summary['daily_pnl']:.2f}\n"
                f"Positions Closed: {summary['positions']['closedPositions']}\n"
                f"Wins: {summary['positions']['wins']}\n"
                f"Losses: {summary['positions']['losses']}\n"
                f"Win Rate: {summary['positions']['winRate']}"
            )
            
            await update.message.reply_text(message, parse_mode="Markdown")
            
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")
    
    async def handle_close_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /close command - close a position"""
        try:
            if not context.args:
                await update.message.reply_text(
                    "Usage: /close [order_id] [price]\n"
                    "Example: /close ORD_001 19500"
                )
                return
            
            order_id = context.args[0]
            close_price = float(context.args[1])
            
            success, msg, pnl = self.dhan.close_trade(order_id, close_price)
            
            if success:
                await update.message.reply_text(
                    f"✅ Position closed!\n"
                    f"Order: {order_id}\n"
                    f"Close Price: ₹{close_price}\n"
                    f"P&L: ₹{pnl:.2f}"
                )
            else:
                await update.message.reply_text(f"❌ Close failed: {msg}")
            
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")
    
    # =========================================================================
    # ALERT SENDING
    # =========================================================================

    async def send_alert(
        self,
        message: str,
        chat_id: Optional[Union[int, str]] = None,
        parse_mode: str = "Markdown",
        retries: int = 3,
        retry_delay: float = 1.0
    ) -> None:
        """Send alert message with retry support"""
        if retries < 1:
            raise ValueError("retries must be at least 1")

        target_chat_id = chat_id if chat_id is not None else self.alert_chat_id
        if target_chat_id in (None, "", 0):
            raise ValueError("TELEGRAM_CHAT_ID required for alerts")

        last_error = None
        for attempt in range(1, retries + 1):
            try:
                await self.bot.send_message(
                    chat_id=target_chat_id,
                    text=message,
                    parse_mode=parse_mode
                )
                return
            except Exception as e:
                last_error = e
                self.logger.warning(f"Alert send failed (attempt {attempt}/{retries}): {e}")
                if attempt < retries:
                    await asyncio.sleep(retry_delay)

        raise last_error
    
    async def _send_trade_confirmation(
        self,
        update: Update,
        order_id: str,
        signal: Dict
    ) -> None:
        """Send trade confirmation to Telegram"""
        try:
            message = (
                f"✅ *Trade Placed Successfully!*\n\n"
                f"Order ID: `{order_id}`\n"
                f"Symbol: {signal['symbol']}\n"
                f"Type: {signal['transaction_type']}\n"
                f"Quantity: {signal['quantity']}\n"
                f"Entry: ₹{signal['entry_price']}\n"
                f"SL: ₹{signal['sl_price']}\n"
                f"Target: ₹{signal['target_price']}\n"
                f"Time: {datetime.now(IST).strftime('%H:%M:%S')}"
            )
            
            await update.message.reply_text(message, parse_mode="Markdown")
            
        except Exception as e:
            self.logger.error(f"❌ Confirmation send error: {e}")
    
    async def send_sl_hit_alert(self, position: Dict) -> None:
        """Send SL hit alert to Telegram"""
        try:
            if not self.alert_chat_id:
                return
            
            message = (
                f"🚨 *STOP LOSS HIT!*\n\n"
                f"Symbol: {position['symbol']}\n"
                f"Type: {position['transactionType']}\n"
                f"Entry: ₹{position['entryPrice']}\n"
                f"Exit: ₹{position['slPrice']}\n"
                f"P&L: ₹{position.get('finalPnL', 0):.2f}\n"
                f"Time: {datetime.now(IST).strftime('%H:%M:%S')}"
            )
            await self.send_alert(message)
            
            self.logger.info(f"✅ SL hit alert sent for {position['symbol']}")
            
        except Exception as e:
            self.logger.error(f"❌ SL alert error: {e}")
    
    async def send_target_hit_alert(self, position: Dict) -> None:
        """Send target hit alert to Telegram"""
        try:
            if not self.alert_chat_id:
                return
            
            message = (
                f"🎯 *TARGET HIT!*\n\n"
                f"Symbol: {position['symbol']}\n"
                f"Type: {position['transactionType']}\n"
                f"Entry: ₹{position['entryPrice']}\n"
                f"Exit: ₹{position['targetPrice']}\n"
                f"P&L: ₹{position.get('finalPnL', 0):.2f}\n"
                f"Time: {datetime.now(IST).strftime('%H:%M:%S')}"
            )
            await self.send_alert(message)
            
            self.logger.info(f"✅ Target hit alert sent for {position['symbol']}")
            
        except Exception as e:
            self.logger.error(f"❌ Target alert error: {e}")
    
    # =========================================================================
    # CALLBACKS
    # =========================================================================
    
    def _setup_callbacks(self) -> None:
        """Setup event callbacks"""
        # SL hit callback
        def on_sl_hit(position: Dict):
            self.logger.info(f"🚨 SL hit in bridge: {position['symbol']}")
            asyncio.create_task(self.send_sl_hit_alert(position))
        
        # Target hit callback
        def on_target_hit(position: Dict):
            self.logger.info(f"🎯 Target hit in bridge: {position['symbol']}")
            asyncio.create_task(self.send_target_hit_alert(position))
        
        self.dhan.position_tracker.on_sl_hit(on_sl_hit)
        self.dhan.position_tracker.on_target_hit(on_target_hit)


# ============================================================================
# SINGLETON INSTANCE
# ============================================================================

_telegram_bridge: Optional[DhanTelegramBridge] = None


def get_telegram_bridge(
    telegram_bot_token: str = None,
    alert_chat_id: int = None
) -> DhanTelegramBridge:
    """
    Get or create DhanTelegramBridge instance
    
    Args:
        telegram_bot_token: Telegram bot token
        alert_chat_id: Chat ID for alerts
        
    Returns:
        DhanTelegramBridge instance
    """
    global _telegram_bridge
    
    if _telegram_bridge is None:
        import os
        
        telegram_bot_token = telegram_bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        alert_chat_id = alert_chat_id or int(os.getenv("ALERT_CHAT_ID", "0"))
        
        if not telegram_bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN required")
        
        _telegram_bridge = DhanTelegramBridge(
            telegram_bot_token=telegram_bot_token,
            alert_chat_id=alert_chat_id
        )
    
    return _telegram_bridge
