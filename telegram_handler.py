from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import requests


logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


class TelegramHandler:
    BOT_CONFIG = {
        "index_options": {
            "channel_id": -1003966854994,
            "channel_env": "CHANNEL_INDEX_ID",
            "token_env": "BOT_INDEX_TOKEN",
            "description": "NIFTY/BANKNIFTY Options (9:15-15:40)",
        },
        "nifty50_stock_options": {
            "channel_id": -1003804613787,
            "channel_env": "CHANNEL_NIFTY50_OPTIONS_ID",
            "token_env": "BOT_NIFTY50_OPTIONS_TOKEN",
            "description": "NIFTY50 Stock Options",
        },
        "commodity_options": {
            "channel_id": -1004403277287,
            "channel_env": "CHANNEL_COMMODITY_ID",
            "token_env": "BOT_COMMODITY_TOKEN",
            "description": "GOLD/CRUDE/SILVER/NATURALGAS (MCX)",
        },
        "nifty50_intraday_5x": {
            "channel_id": -1004466883026,
            "channel_env": "CHANNEL_NIFTY50_5X_ID",
            "token_env": "BOT_NIFTY50_5X_TOKEN",
            "description": "NIFTY50 Intraday 5X Leverage",
        },
        "nifty50_pay_later": {
            "channel_id": -1003814243881,
            "channel_env": "CHANNEL_NIFTY50_PAY_LATER_ID",
            "token_env": "BOT_NIFTY50_PAY_LATER_TOKEN",
            "description": "NIFTY50 Pay Later/Margin",
        },
        "crypto": {
            "channel_id": -1004482078964,
            "channel_env": "CHANNEL_CRYPTO_ID",
            "token_env": "BOT_CRYPTO_TOKEN",
            "description": "BTCUSD/ETHUSD Crypto (24/7)",
        },
    }
    CHANNELS = {category: config["channel_id"] for category, config in BOT_CONFIG.items()}

    def __init__(self, token: Optional[str] = None) -> None:
        self.default_token = token or os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN", "")
        self.token = self.default_token
        self.default_chat_id = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID", "")
        self._bot_instances: Dict[str, object] = {}

        if not self.default_token:
            logger.error("❌ No default Telegram token found in environment")
        else:
            logger.info("✅ Default Telegram token loaded")

        logger.info("=" * 80)
        logger.info("📱 TELEGRAM BOT CONFIGURATION")
        logger.info("=" * 80)
        for category, config in self.BOT_CONFIG.items():
            category_token = os.getenv(config["token_env"]) or self.default_token
            configured_channel = os.getenv(config["channel_env"])
            channel_id = int(configured_channel or config["channel_id"])
            status = "✅" if category_token else "⚠️ (missing token)"
            logger.info(
                "%s [%s] Channel: %s | %s",
                status,
                category.upper(),
                channel_id,
                config["description"],
            )
        logger.info("=" * 80)

        self._alert_history: List[dict] = []
        self._alert_keys: set[str] = set()

    def _get_bot_for_category(self, category: str) -> tuple[str, int]:
        if category not in self.BOT_CONFIG:
            logger.warning("⚠️ Unknown Telegram category '%s', using default channel", category)
            fallback_chat = int(self.default_chat_id or os.getenv("CHAT_ID", "-1004321977761"))
            return self.default_token, fallback_chat

        config = self.BOT_CONFIG[category]
        category_token = os.getenv(config["token_env"]) or self.default_token
        configured_channel = os.getenv(config["channel_env"])
        channel_id = int(configured_channel or config["channel_id"])
        logger.debug("🔍 [%s] Routed to channel %s", category.upper(), channel_id)
        return category_token, channel_id

    def send_signal_alert(self, category: str, signal_data: dict, option_data: dict) -> bool:
        try:
            logger.debug(
                "📤 [%s] Attempting to send alert for %s",
                category.upper(),
                signal_data.get("symbol", "UNKNOWN"),
            )
            key = (
                f"{category}:{signal_data['symbol']}:{signal_data['signal']}:"
                f"{signal_data['reference_timestamp']}:{signal_data['breakout_timestamp']}"
            )
            if key in self._alert_keys:
                logger.debug("⚠️ Duplicate alert suppressed: %s", key)
                return False

            bot_token, chat_id = self._get_bot_for_category(category)

            message = self.format_signal_message(category, signal_data, option_data)
            logger.info("📨 [%s] Sending to channel %s", category.upper(), chat_id)
            sent = self._send_message(chat_id, message, bot_token)
            if sent:
                self._alert_keys.add(key)
                self._alert_history.append(
                    {
                        "category": category,
                        "channel_id": chat_id,
                        "symbol": signal_data.get("symbol"),
                        "signal": signal_data.get("signal"),
                        "sent_at": datetime.now(IST).isoformat(),
                    }
                )
                self._alert_history = self._alert_history[-200:]
                logger.info(
                    "✅ [%s] Alert sent to channel %s for %s %s",
                    category.upper(),
                    chat_id,
                    signal_data.get("symbol"),
                    signal_data.get("signal", "").upper(),
                )
            else:
                logger.warning(
                    "⚠️ [%s] Failed to deliver alert for %s to channel %s",
                    category.upper(),
                    signal_data.get("symbol"),
                    chat_id,
                )
            return sent
        except Exception as e:
            logger.error("❌ [%s] Failed to send alert: %s", category.upper(), e, exc_info=True)
            return False

    def send_to_channel(self, channel_type: str, alert_msg: str) -> bool:
        """Send raw alert to a configured Telegram channel."""
        try:
            channel_aliases = {
                "nifty50_options": "nifty50_stock_options",
                "nifty50_5x": "nifty50_intraday_5x",
                "nifty50_paylater": "nifty50_pay_later",
            }
            normalized_channel = channel_aliases.get(channel_type, channel_type)
            if normalized_channel not in self.BOT_CONFIG:
                logger.warning("❌ Unknown channel type: %s", channel_type)
                return False

            bot_token, chat_id = self._get_bot_for_category(normalized_channel)
            if not bot_token or not chat_id:
                logger.warning("❌ Channel config missing for %s", channel_type)
                return False

            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            payload = {"chat_id": chat_id, "text": alert_msg, "parse_mode": "HTML"}
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code == 200:
                logger.info("✅ Alert sent to %s", channel_type)
                return True

            logger.error("❌ Failed to send to %s: %s", channel_type, response.status_code)
            return False
        except Exception as e:
            logger.error("Error sending to channel: %s", e, exc_info=True)
            return False

    def send_confirmation_request(self, user_chat_id: int, signal_details: dict) -> bool:
        message = (
            "⚠️ Position limit reached (5 open positions).\n"
            "Reply YES to allow 6th position.\n\n"
            f"Symbol: {signal_details['symbol']}\n"
            f"Side: {signal_details['side']}\n"
            f"Entry: {signal_details['entry_price']}"
        )
        return self._send_message(user_chat_id, message, self.default_token)

    def send_sl_miss_alert(self, category: str, position: dict, current_price: float) -> bool:
        message = (
            "🚨 SL MISS ALERT\n\n"
            f"Symbol: {position['symbol']}\n"
            f"Side: {position['side']}\n"
            f"Stop Loss: {position['stop_loss']}\n"
            f"Current Price: {current_price}\n"
            f"Position: {position['position_id']}"
        )
        bot_token, chat_id = self._get_bot_for_category(category)
        return self._send_message(chat_id, message, bot_token)

    def get_alert_history(self, limit: int = 50) -> List[dict]:
        return self._alert_history[-limit:]

    def format_signal_message(self, category: str, signal_data: dict, option_data: dict) -> str:
        if signal_data.get("premium_strategy"):
            return self._format_premium_signal_message(category, signal_data, option_data)
        if signal_data.get("spot_strategy"):
            return self._format_spot_signal_message(category, signal_data, option_data)

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
            f"⏰ Signal Time (IST): {signal_data.get('signal_time_ist', datetime.now(IST).strftime('%H:%M:%S'))}\n"
            f"🕐 Timeframe: {signal_data.get('timeframe', '')}\n"
            f"📡 Source: {source or 'SCREENER'}\n"
            f"Channel: {category.replace('_', ' ').upper()}\n\n"
            "📢 DISCLAIMER: Educational purposes only."
        )

    def _format_premium_signal_message(self, category: str, signal_data: dict, option_data: dict) -> str:
        icon = "🚀 CALL ENTRY" if signal_data["signal"] == "CALL" else "📉 PUT ENTRY"
        option_type = option_data.get("option_type", signal_data.get("option_type", ""))
        premium_ltp = round(float(option_data.get("premium_ltp", signal_data["entry"])), 2)
        targets = signal_data["targets"]
        signal_time = signal_data.get("signal_time_ist", datetime.now(IST).strftime("%H:%M:%S"))
        signal_date = signal_data.get("signal_date_ist", datetime.now(IST).strftime("%d:%m:%Y"))
        return (
            f"{icon}\n"
            f"{signal_data['symbol']} | {signal_data.get('timeframe', '').upper()} ({option_type} Premium)\n\n"
            f"⏰ Signal Time: {signal_time} | {signal_date}\n\n"
            "📊 POSITION DETAILS:\n"
            f"Entry: {signal_data['entry']:.2f}\n"
            f"Target 1: {targets[0]:.2f} (+10 points)\n"
            f"Target 2: {targets[1]:.2f} (+20 points)\n"
            f"Target 3: {targets[2]:.2f} (+30 points)\n"
            f"Stop Loss: {signal_data['stop_loss']:.2f}\n\n"
            f"Strike: {option_data.get('option_symbol', 'N/A')}\n"
            f"Premium (LTP): ₹{premium_ltp:.2f}\n\n"
            f"Channel: {category.replace('_', ' ').upper()}\n\n"
            "📢 DISCLAIMER: Educational purposes only."
        )

    def _format_spot_signal_message(self, category: str, signal_data: dict, option_data: dict) -> str:
        is_long = signal_data["signal"] == "CALL"
        icon = "🚀 LONG ENTRY" if is_long else "📉 SHORT ENTRY"
        targets = signal_data["targets"]
        spot_ltp = round(float(option_data.get("spot_ltp", signal_data["entry"])), 2)
        instrument_label = option_data.get("instrument_label", "SPOT")
        timeframe = signal_data.get("timeframe", "").upper()
        header = (
            f"{signal_data['symbol']} | {timeframe} ({instrument_label})"
            if timeframe
            else f"{signal_data['symbol']} | {instrument_label}"
        )
        signal_time = signal_data.get("signal_time_ist", datetime.now(IST).strftime("%H:%M:%S"))
        signal_date = signal_data.get("signal_date_ist", datetime.now(IST).strftime("%d:%m:%Y"))
        target_lines = "\n".join(
            f"Target {index}: {float(target):.2f}"
            for index, target in enumerate(targets, start=1)
        )
        return (
            f"{icon}\n"
            f"{header}\n\n"
            f"⏰ Signal Time: {signal_time} | {signal_date}\n\n"
            "📊 POSITION DETAILS:\n"
            f"Entry: {signal_data['entry']:.2f}\n"
            f"{target_lines}\n"
            f"Stop Loss: {signal_data['stop_loss']:.2f}\n\n"
            f"Spot (LTP): ₹{spot_ltp:.2f}\n\n"
            f"Channel: {category.replace('_', ' ').upper()}\n\n"
            "📢 DISCLAIMER: Educational purposes only."
        )

    def _send_message(self, chat_id: int, message: str, token: Optional[str] = None) -> bool:
        token = token or self.default_token
        if not token:
            logger.warning("❌ No Telegram token available")
            return False

        try:
            if token not in self._bot_instances:
                from telegram import Bot
                self._bot_instances[token] = Bot(token=token)
                logger.debug("🤖 New bot instance created")

            bot = self._bot_instances[token]

            try:
                loop = asyncio.get_running_loop()
                loop.create_task(bot.send_message(chat_id=chat_id, text=message))
                return True
            except RuntimeError:
                asyncio.run(bot.send_message(chat_id=chat_id, text=message))
                return True

        except Exception as e:
            logger.error("❌ Telegram send failed to %s: %s: %s", chat_id, type(e).__name__, e)
            return False

    def test_all_bots(self) -> dict:
        results = {}
        logger.info("=" * 80)
        logger.info("🧪 TESTING ALL TELEGRAM BOTS")
        logger.info("=" * 80)

        for category, config in self.BOT_CONFIG.items():
            token, channel_id = self._get_bot_for_category(category)
            test_message = (
                f"✅ {config['description'].upper()}\n\n"
                f"🔔 Channel: {channel_id}\n"
                f"📱 Bot: {category}\n"
                f"⏰ Test Time: {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S IST')}\n\n"
                "If you see this message, the bot is working correctly! 🎉"
            )
            sent = self._send_message(channel_id, test_message, token)
            status = "✅ WORKING" if sent else "❌ FAILED"
            results[category] = {
                "category": category,
                "channel_id": channel_id,
                "token": f"{token[:15]}..." if token else "NONE",
                "sent": sent,
                "status": status,
            }
            logger.info("%s %s -> %s", status, category, channel_id)

        logger.info("=" * 80)
        logger.info("🧪 TEST COMPLETE")
        logger.info("=" * 80)
        return results
