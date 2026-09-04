from __future__ import annotations

import os
from datetime import datetime
from typing import Dict, List, Optional


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
        return (
            f"{icon}\n\n"
            f"Symbol: {signal_data['symbol']} | {signal_data.get('timeframe', '')} Breakout\n"
            f"Previous {signal_data['reference_color']} Candle: {signal_data['reference_timestamp']} "
            f"High: {signal_data['reference_high']} Low: {signal_data['reference_low']}\n"
            f"Breakout Candle: {signal_data['breakout_timestamp']} "
            f"(#{signal_data['breakout_candle_after']} after reference)\n"
            f"Strike: {option_data['call_strike']} CE / {option_data['put_strike']} PE\n"
            f"Premium (LTP): ₹{option_data['call_premium'] if signal_data['signal'] == 'CALL' else option_data['put_premium']}\n\n"
            f"📊 POSITION DETAILS:\n"
            f"Entry: {signal_data['entry']}\n"
            f"Target 1: {targets[0]}\n"
            f"Target 2: {targets[1]}\n"
            f"Target 3: {targets[2]}\n"
            f"Stop Loss: {signal_data['stop_loss']}\n\n"
            f"⏰ Time (IST): {datetime.now().strftime('%H:%M:%S')}\n"
            f"🕐 Timeframe: {signal_data.get('timeframe', '')}\n"
            f"Channel: {category}\n\n"
            "📢 DISCLAIMER: Educational purposes only. Not SEBI registered."
        )

    def _send_message(self, chat_id: int, message: str) -> bool:
        if not self.token:
            return False
        try:
            if self._bot is None:
                from telegram import Bot

                self._bot = Bot(token=self.token)
            import asyncio

            asyncio.run(self._bot.send_message(chat_id=chat_id, text=message))
            return True
        except Exception:
            return False
