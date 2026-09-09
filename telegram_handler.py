from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from typing import Dict, List, Optional


logger = logging.getLogger(__name__)


class TelegramHandler:
    CHANNELS = {
        "index_options": -1003966854994,
        "nifty50_stock_options": -1003804613787,
        "nifty50_intraday_5x": -1004466883026,
        "nifty50_pay_later": -1003814243881,
        "commodity_options": -1004403277287,
        "crypto": -1004482078964,
    }

    def __init__(self, token: Optional[str] = None) -> None:
        self.token = token or os.getenv("TELEGRAM_TOKEN", "")
        self._bot = None
        self._alert_history: List[dict] = []
        self._alert_keys: set[str] = set()

    def send_signal_alert(self, category: str, signal_data: dict, option_data: dict) -> bool:
        key = (
            f"{category}:{signal_data['symbol']}:{signal_data['signal']}:"
            f"{signal_data['reference_timestamp']}:{signal_data['breakout_timestamp']}"
        )
        if key in self._alert_keys:
            return False

        message = self.format_signal_message(category, signal_data, option_data)
        sent = self._send_message(self.CHANNELS[category], message)
        if sent:
            self._alert_keys.add(key)
            self._alert_history.append(
                {
                    "category": category,
                    "signal": signal_data,
                    "option_data": option_data,
                    "sent_at": datetime.utcnow().isoformat(),
                }
            )
            self._alert_history = self._alert_history[-200:]
        return sent

    def send_confirmation_request(self, user_chat_id: int, signal_details: dict) -> bool:
        message = (
            "⚠️ Position limit reached (5 open positions).\n"
            "Reply YES to allow 6th position.\n\n"
            f"Symbol: {signal_details['symbol']}\n"
            f"Side: {signal_details['side']}\n"
            f"Entry: {signal_details['entry_price']}"
        )
        return self._send_message(user_chat_id, message)

    def send_sl_miss_alert(self, category: str, position: dict, current_price: float) -> bool:
        message = (
            "🚨 SL MISS ALERT\n\n"
            f"Symbol: {position['symbol']}\n"
            f"Side: {position['side']}\n"
            f"Stop Loss: {position['stop_loss']}\n"
            f"Current Price: {current_price}\n"
            f"Position: {position['position_id']}"
        )
        return self._send_message(self.CHANNELS[category], message)

    def get_alert_history(self, limit: int = 50) -> List[dict]:
        return self._alert_history[-limit:]

    def format_signal_message(self, category: str, signal_data: dict, option_data: dict) -> str:
        icon = "🚀 CALL ENTRY" if signal_data["signal"] == "CALL" else "📉 PUT ENTRY"
        targets = signal_data["targets"]
        source = signal_data.get("source")
        timeframe_label = signal_data.get("timeframe", "")
        symbol_line = (
            f"Symbol: {signal_data['symbol']} | {timeframe_label}\n"
            if timeframe_label.endswith("BREAKOUT")
            else f"Symbol: {signal_data['symbol']} | {timeframe_label} Breakout\n"
        )
        source_banner = "[FROM TRADINGVIEW WEBHOOK]\n\n" if source == "TRADINGVIEW WEBHOOK" else ""
        breakout_line = (
            f"Breakout at: {signal_data['breakout_timestamp']} at {signal_data['breakout_price']}\n"
            if source == "TRADINGVIEW WEBHOOK"
            else f"Breakout Candle: {signal_data['breakout_timestamp']} "
            f"(#{signal_data['breakout_candle_after']} after reference)\n"
        )
        entry_label = (
            f"{signal_data['entry']} ({signal_data['reference_color']} HIGH)"
            if source == "TRADINGVIEW WEBHOOK" and signal_data["signal"] == "CALL"
            else f"{signal_data['entry']} ({signal_data['reference_color']} LOW)"
            if source == "TRADINGVIEW WEBHOOK"
            else f"{signal_data['entry']}"
        )
        target_distances = [
            abs(round(float(target) - float(signal_data["entry"]), 2))
            for target in targets
        ]
        stop_loss_label = (
            f"{signal_data['stop_loss']} ({signal_data['reference_color']} LOW)"
            if source == "TRADINGVIEW WEBHOOK" and signal_data["signal"] == "CALL"
            else f"{signal_data['stop_loss']} ({signal_data['reference_color']} HIGH)"
            if source == "TRADINGVIEW WEBHOOK"
            else f"{signal_data['stop_loss']}"
        )
        target_labels = [
            f"{distance:.2f}".rstrip("0").rstrip(".")
            for distance in target_distances
        ]
        return (
            f"{icon}\n"
            f"{source_banner}"
            f"{symbol_line}"
            f"Previous {signal_data['reference_color']} Candle: {signal_data['reference_timestamp']} "
            f"High: {signal_data['reference_high']} Low: {signal_data['reference_low']}\n"
            f"{breakout_line}"
            f"Strike: {option_data['call_strike']} CE / {option_data['put_strike']} PE\n"
            f"Premium (LTP): ₹{option_data['call_premium'] if signal_data['signal'] == 'CALL' else option_data['put_premium']}\n\n"
            f"📊 POSITION DETAILS:\n"
            f"Entry: {entry_label}\n"
            f"Target 1: {targets[0]} ({target_labels[0]} points)\n"
            f"Target 2: {targets[1]} ({target_labels[1]} points)\n"
            f"Target 3: {targets[2]} ({target_labels[2]} points)\n"
            f"Stop Loss: {stop_loss_label}\n\n"
            f"⏰ Signal Time (IST): {signal_data.get('signal_time_ist', datetime.now().strftime('%H:%M:%S'))}\n"
            f"🕐 Timeframe: {signal_data.get('timeframe', '')}\n"
            f"📡 Source: {source or 'SCREENER'}\n"
            f"Channel: {category.replace('_', ' ').upper()}\n\n"
            "📢 DISCLAIMER: Educational purposes only."
        )

    def _send_message(self, chat_id: int, message: str) -> bool:
        if not self.token:
            logger.warning("Telegram token not configured, cannot send message")
            return False
        
        try:
            if self._bot is None:
                from telegram import Bot
                self._bot = Bot(token=self.token)
                logger.info("Telegram bot initialized")
            
            logger.info(f"Attempting to send message to chat_id={chat_id}")
            
            # Try synchronous send first
            import asyncio
            try:
                loop = asyncio.get_running_loop()
                logger.debug("Event loop detected, using async send")
                # Schedule the coroutine
                task = loop.create_task(self._send_async(chat_id, message))
                logger.debug(f"Task scheduled: {task}")
                return True
            except RuntimeError:
                logger.debug("No event loop, using synchronous send")
                # No event loop, run synchronously
                asyncio.run(self._send_message_sync(chat_id, message))
                return True
                
        except Exception as e:
            logger.error(f"Telegram send failed: {type(e).__name__}: {e}")
            return False
    
    async def _send_async(self, chat_id: int, message: str) -> None:
        try:
            logger.info(f"Sending async message to {chat_id}")
            await self._bot.send_message(chat_id=chat_id, text=message)
            logger.info(f"Message sent successfully to {chat_id}")
        except Exception as e:
            logger.error(f"Async send failed: {type(e).__name__}: {e}")
    
    async def _send_message_sync(self, chat_id: int, message: str) -> None:
        try:
            logger.info(f"Sending sync message to {chat_id}")
            await self._bot.send_message(chat_id=chat_id, text=message)
            logger.info(f"Message sent successfully to {chat_id}")
        except Exception as e:
            logger.error(f"Sync send failed: {type(e).__name__}: {e}")
