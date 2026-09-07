"""
FIXED Telegram Alert Handler - Multi-bot Support with Proper Event Loop Management
Fixes:
1. Support for 6 different Telegram bots (Index, Commodity, Crypto, Nifty50, etc)
2. Proper async event loop management (no crashes)
3. Deduplication of alerts
4. Proper error handling and retry logic
5. Thread-safe message queue
File: telegram_alerts_fixed.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import queue
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class TelegramAlertsFixed:
    """
    Fixed Telegram handler with multi-bot support and proper async management
    
    Features:
    - 6 independent bot channels (Index, Commodity, Crypto, Nifty50, etc)
    - Background thread for async message sending
    - Alert deduplication
    - Automatic retry on failure
    - Thread-safe queue
    """
    
    # Bot tokens from .env
    BOTS = {
        "index_options": {
            "token_env": "BOT_INDEX_TOKEN",
            "channel_env": "CHANNEL_INDEX_ID",
            "description": "NIFTY 50 & BANKNIFTY Index Options"
        },
        "commodity": {
            "token_env": "BOT_COMMODITY_TOKEN",
            "channel_env": "CHANNEL_COMMODITY_ID",
            "description": "Gold, Silver, Crude Oil, Natural Gas"
        },
        "crypto": {
            "token_env": "BOT_CRYPTO_TOKEN",
            "channel_env": "CHANNEL_CRYPTO_ID",
            "description": "Bitcoin, Ethereum, Crypto Pairs"
        },
        "nifty50_5x": {
            "token_env": "BOT_NIFTY50_5X_TOKEN",
            "channel_env": "CHANNEL_NIFTY50_5X_ID",
            "description": "NIFTY 50 Intraday 5X Leverage"
        },
        "nifty50_options": {
            "token_env": "BOT_NIFTY50_OPTIONS_TOKEN",
            "channel_env": "CHANNEL_NIFTY50_OPTIONS_ID",
            "description": "NIFTY 50 Stock Options"
        },
        "nifty50_pay_later": {
            "token_env": "BOT_NIFTY50_PAY_LATER_TOKEN",
            "channel_env": "CHANNEL_NIFTY50_PAY_LATER_ID",
            "description": "NIFTY 50 Pay Later"
        },
    }
    
    def __init__(self) -> None:
        self.bots: Dict[str, Dict] = {}
        self._alert_history: List[dict] = []
        self._alert_keys: set[str] = set()
        self._message_queue: queue.Queue = queue.Queue()
        self._stop_event = threading.Event()
        self._sender_thread: Optional[threading.Thread] = None
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock = threading.Lock()
        
        # Initialize all bots
        self._init_bots()
        
        # Start background sender thread
        self._start_sender_thread()
        
        logger.info("✅ TelegramAlertsFixed initialized with {} bots".format(len(self.bots)))
    
    def _init_bots(self) -> None:
        """Initialize all configured telegram bots"""
        for bot_name, config in self.BOTS.items():
            token = os.getenv(config["token_env"])
            channel_id = os.getenv(config["channel_env"])
            
            if token and channel_id:
                try:
                    channel_id = int(channel_id)
                    self.bots[bot_name] = {
                        "token": token,
                        "channel_id": channel_id,
                        "description": config["description"],
                        "status": "READY",
                        "last_message_time": 0,
                        "failed_count": 0
                    }
                    logger.info(f"✅ Bot '{bot_name}' initialized: {config['description']}")
                except Exception as e:
                    logger.error(f"❌ Failed to init bot '{bot_name}': {e}")
            else:
                logger.warning(f"⚠️  Bot '{bot_name}' not configured (missing token or channel)")
    
    def _start_sender_thread(self) -> None:
        """Start background thread for async message sending"""
        if self._sender_thread is None or not self._sender_thread.is_alive():
            self._stop_event.clear()
            self._sender_thread = threading.Thread(
                target=self._message_sender_loop,
                daemon=True,
                name="TelegramSenderThread"
            )
            self._sender_thread.start()
            logger.info("✅ Telegram sender background thread started")
    
    def _message_sender_loop(self) -> None:
        """Background loop that processes message queue"""
        # Create a new event loop for this thread
        self._event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._event_loop)
        
        logger.info("✅ Message sender loop started with event loop")
        
        while not self._stop_event.is_set():
            try:
                # Get message from queue (timeout prevents blocking forever)
                try:
                    bot_name, message, retry_count = self._message_queue.get(timeout=1)
                except queue.Empty:
                    continue
                
                # Send via the event loop
                self._event_loop.run_until_complete(
                    self._send_message_async(bot_name, message, retry_count)
                )
                
            except Exception as e:
                logger.error(f"❌ Error in message sender loop: {e}")
                time.sleep(1)
        
        logger.info("✅ Message sender loop stopped")
    
    async def _send_message_async(
        self, 
        bot_name: str, 
        message: str, 
        retry_count: int = 0
    ) -> bool:
        """
        Async function to send message via Telegram bot
        
        Args:
            bot_name: Key from self.BOTS
            message: Message text to send
            retry_count: Current retry attempt
            
        Returns:
            True if sent successfully, False otherwise
        """
        if bot_name not in self.bots:
            logger.error(f"❌ Unknown bot: {bot_name}")
            return False
        
        bot_config = self.bots[bot_name]
        
        try:
            from telegram import Bot
            from telegram.error import TelegramError
            
            bot = Bot(token=bot_config["token"])
            
            # Rate limiting: Don't send more than 1 per second
            time_since_last = time.time() - bot_config["last_message_time"]
            if time_since_last < 1.0:
                await asyncio.sleep(1.0 - time_since_last)
            
            # Send message
            await bot.send_message(
                chat_id=bot_config["channel_id"],
                text=message,
                parse_mode="HTML"
            )
            
            # Update success metrics
            with self._lock:
                bot_config["last_message_time"] = time.time()
                bot_config["failed_count"] = 0
                bot_config["status"] = "READY"
            
            logger.info(
                f"✅ Alert sent to '{bot_name}' "
                f"({bot_config['description']})"
            )
            return True
            
        except Exception as e:
            logger.error(
                f"❌ Failed to send to '{bot_name}' "
                f"(retry {retry_count}/3): {type(e).__name__}: {e}"
            )
            
            # Retry logic
            if retry_count < 3:
                wait_time = 2 ** retry_count  # Exponential backoff
                logger.info(f"⏱️  Retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)
                self._message_queue.put((bot_name, message, retry_count + 1))
            else:
                with self._lock:
                    self.bots[bot_name]["failed_count"] += 1
                    self.bots[bot_name]["status"] = "FAILED"
            
            return False
    
    def send_signal_alert(
        self,
        bot_category: str,
        signal_data: dict,
        option_data: dict
    ) -> bool:
        """
        Send signal alert to appropriate bot channel
        
        Args:
            bot_category: One of the bot keys (e.g., 'index_options', 'commodity')
            signal_data: Trade signal details
            option_data: Option chain data (strike, premium, etc)
            
        Returns:
            True if alert queued successfully
        """
        if bot_category not in self.bots:
            logger.error(f"❌ Unknown bot category: {bot_category}")
            return False
        
        # Deduplication key
        key = (
            f"{bot_category}:{signal_data['symbol']}:{signal_data['signal']}:"
            f"{signal_data['reference_timestamp']}:{signal_data['breakout_timestamp']}"
        )
        
        if key in self._alert_keys:
            logger.debug(f"⏭️  Duplicate alert suppressed for {key}")
            return False
        
        # Format message
        message = self._format_signal_message(signal_data, option_data)
        
        # Queue message
        self._message_queue.put((bot_category, message, 0))
        
        # Track alert
        with self._lock:
            self._alert_keys.add(key)
            self._alert_history.append({
                "category": bot_category,
                "symbol": signal_data.get("symbol"),
                "signal": signal_data.get("signal"),
                "sent_at": datetime.utcnow().isoformat(),
                "status": "QUEUED"
            })
            # Keep only last 500 alerts
            self._alert_history = self._alert_history[-500:]
        
        logger.info(f"📤 Alert queued for '{bot_category}': {signal_data.get('symbol')} {signal_data.get('signal')}")
        return True
    
    def send_market_update(
        self,
        bot_category: str,
        update_type: str,
        update_data: dict
    ) -> bool:
        """
        Send general market updates (not trades)
        
        Args:
            bot_category: Bot category
            update_type: Type of update (e.g., 'market_open', 'market_close')
            update_data: Update details
        """
        message = self._format_update_message(update_type, update_data)
        self._message_queue.put((bot_category, message, 0))
        return True
    
    def send_error_alert(
        self,
        bot_category: str,
        error_type: str,
        error_message: str
    ) -> bool:
        """Send critical error alert"""
        message = (
            f"🚨 <b>ERROR ALERT</b>\n\n"
            f"<b>Type:</b> {error_type}\n"
            f"<b>Message:</b> {error_message}\n"
            f"<b>Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S IST')}"
        )
        self._message_queue.put((bot_category, message, 0))
        return True
    
    def _format_signal_message(self, signal_data: dict, option_data: dict) -> str:
        """Format trade signal as HTML message"""
        icon = "🚀" if signal_data["signal"] == "CALL" else "📉"
        targets = signal_data.get("targets", [])
        symbol = signal_data.get("symbol", "N/A")
        entry = signal_data.get("entry", "N/A")
        stop_loss = signal_data.get("stop_loss", "N/A")
        
        target_lines = "\n".join([
            f"  • T{i+1}: {target}"
            for i, target in enumerate(targets[:3])
        ])
        
        message = (
            f"{icon} <b>{signal_data['signal']} SIGNAL</b>\n\n"
            f"<b>Symbol:</b> {symbol}\n"
            f"<b>Entry:</b> {entry}\n"
            f"<b>SL:</b> {stop_loss}\n"
            f"<b>Targets:</b>\n{target_lines}\n"
            f"<b>Strike:</b> {option_data.get('call_strike', 'N/A')} CE / "
            f"{option_data.get('put_strike', 'N/A')} PE\n"
            f"<b>Premium:</b> ₹{option_data.get('call_premium', 'N/A')}\n\n"
            f"<i>Time: {datetime.now().strftime('%H:%M:%S IST')}</i>"
        )
        return message
    
    def _format_update_message(self, update_type: str, update_data: dict) -> str:
        """Format market update as HTML message"""
        if update_type == "market_open":
            return f"✅ <b>MARKET OPEN</b>\n{update_data.get('message', '')}"
        elif update_type == "market_close":
            return f"🔴 <b>MARKET CLOSED</b>\n{update_data.get('message', '')}"
        else:
            return f"📢 {update_data.get('message', 'Update')}"
    
    def get_alert_history(self, limit: int = 50) -> List[dict]:
        """Get recent alert history"""
        with self._lock:
            return self._alert_history[-limit:]
    
    def get_bot_status(self) -> Dict[str, dict]:
        """Get status of all configured bots"""
        with self._lock:
            return {
                name: {
                    "status": config["status"],
                    "description": config["description"],
                    "failed_count": config["failed_count"],
                    "queue_size": self._message_queue.qsize()
                }
                for name, config in self.bots.items()
            }
    
    def shutdown(self) -> None:
        """Gracefully shutdown the alert handler"""
        logger.info("🛑 Shutting down Telegram alerts...")
        self._stop_event.set()
        
        if self._sender_thread and self._sender_thread.is_alive():
            self._sender_thread.join(timeout=5)
        
        if self._event_loop:
            self._event_loop.close()
        
        logger.info("✅ Telegram alerts shutdown complete")


# Global instance
_telegram_alerts: Optional[TelegramAlertsFixed] = None


def get_telegram_alerts() -> TelegramAlertsFixed:
    """Get or create global telegram alerts instance"""
    global _telegram_alerts
    if _telegram_alerts is None:
        _telegram_alerts = TelegramAlertsFixed()
    return _telegram_alerts


# Test function
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    alerts = get_telegram_alerts()
    
    # Check bot status
    print("\n" + "="*80)
    print("BOT STATUS")
    print("="*80)
    status = alerts.get_bot_status()
    for bot_name, bot_status in status.items():
        print(f"  {bot_name}: {bot_status}")
    
    # Send test alert
    print("\n" + "="*80)
    print("SENDING TEST ALERT")
    print("="*80)
    
    if "index_options" in alerts.bots:
        test_signal = {
            "symbol": "NIFTY 50",
            "signal": "CALL",
            "entry": 18250.5,
            "stop_loss": 18200.0,
            "targets": [18300, 18350, 18400],
            "reference_timestamp": datetime.now().strftime("%H:%M:%S"),
            "breakout_timestamp": datetime.now().strftime("%H:%M:%S"),
        }
        
        test_option = {
            "call_strike": 18250,
            "put_strike": 18250,
            "call_premium": 42.5,
            "put_premium": 38.2
        }
        
        alerts.send_signal_alert("index_options", test_signal, test_option)
        print("✅ Test alert queued")
        
        # Wait for sending
        time.sleep(3)
    
    # Check history
    history = alerts.get_alert_history()
    print(f"\nRecent alerts: {len(history)}")
    for alert in history[-3:]:
        print(f"  - {alert}")
    
    alerts.shutdown()
