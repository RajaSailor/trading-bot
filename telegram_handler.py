from __future__ import annotations

import asyncio
import html
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import requests


logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

# Canonical screener segments carried by the combined screener channel.
SCREENER_CATEGORIES = frozenset({"index_options", "commodity_options", "nifty50_stock_options"})
# Legacy spellings of the same three segments.
SCREENER_CATEGORY_ALIASES = frozenset({"index", "commodity", "nifty50_options"})
# Retired segments: alerts for these categories are rejected, never delivered.
DISABLED_CATEGORIES = frozenset(
    {"crypto", "nifty50_intraday_5x", "nifty50_5x", "nifty50_pay_later", "nifty50_paylater"}
)


def format_ist_timestamp(value: object, with_seconds: bool = False) -> str:
    """Render epoch seconds / ISO strings as ``DD-Mon-YYYY HH:MM IST``."""
    if value is None or value == "":
        return "N/A"
    moment: Optional[datetime] = None
    try:
        if isinstance(value, datetime):
            moment = value
        elif isinstance(value, (int, float)) or str(value).replace(".", "", 1).isdigit():
            moment = datetime.fromtimestamp(float(value), tz=timezone.utc)
        else:
            moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError, OSError):
        return str(value)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=IST)
    pattern = "%d-%b-%Y %H:%M:%S IST" if with_seconds else "%d-%b-%Y %H:%M IST"
    return moment.astimezone(IST).strftime(pattern)


def _fmt_price(value: object) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "N/A"


def _fmt_strike(value: object) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "N/A"
    return str(int(number)) if number.is_integer() else f"{number:.2f}"


_CATEGORY_LABELS = {
    "index_options": "INDEX OPTIONS",
    "commodity_options": "COMMODITY OPTIONS",
    "nifty50_stock_options": "STOCK OPTIONS",
}

def format_option_breakout_alert(
    alert: dict,
    practice_mode: Optional[bool] = None,
    compact: bool = False,
) -> str:
    """Compact option premium breakout message (HTML-safe).

    Expected keys: underlying, category, option_symbol, strike, option_type,
    action_text, expiry, strike_band, entry, stop_loss, risk_points, target,
    reference_low/high, reference_timestamp, breakout_timestamp, timeframe.
    """
    esc = lambda value: html.escape(str(value if value not in (None, "") else "N/A"))  # noqa: E731
    option_type = str(alert.get("option_type") or "").upper()
    action = alert.get("action_text") or ("BUY CALL" if option_type == "CE" else "BUY PUT")
    icon = "🚀"
    category = str(alert.get("category") or alert.get("route_category") or "")
    category_label = _CATEGORY_LABELS.get(category, category.replace("_", " ").upper() or "OPTIONS")
    target_1 = alert.get("target_1")
    target_2 = alert.get("target_2")
    try:
        entry = float(alert["entry"])
        stop_loss = float(alert["stop_loss"])
        risk = entry - stop_loss
        target_1 = round(entry + risk, 2)
        target_2 = round(entry + 2 * risk, 2)
    except (KeyError, TypeError, ValueError):
        if target_2 is None:
            target_2 = alert.get("target")
            if target_2 is None and alert.get("targets"):
                target_2 = alert["targets"][0]
    breakout_timestamp = alert.get("breakout_timestamp")
    breakout_ist = (
        format_ist_timestamp(breakout_timestamp, with_seconds=True)
        if breakout_timestamp not in (None, "")
        else alert.get("breakout_time_ist") or "N/A"
    )
    observed_price = alert.get("breakout_price")
    if observed_price is None:
        observed_price = alert.get("premium_ltp")
    lines = [
        f"{icon} <b>{esc(action)}</b> | <b>{esc(alert.get('underlying') or alert.get('symbol'))}</b> ({esc(category_label)})",
        f"Option: <b>{esc(alert.get('option_symbol'))}</b>",
        f"Strike: {esc(_fmt_strike(alert.get('strike')))} {esc(option_type)} | Band: {esc(alert.get('strike_band'))}",
        f"Expiry: {esc(alert.get('expiry'))}",
        "",
        "📊 <b>TRADE LEVELS</b>",
        f"Entry: {esc(_fmt_price(alert.get('entry')))}",
        f"Stop Loss: {esc(_fmt_price(alert.get('stop_loss')))}",
        f"Risk: {esc(_fmt_price(alert.get('risk_points')))} points",
        f"Target 1 (1R): {esc(_fmt_price(target_1))}",
        f"Target 2 (2R): {esc(_fmt_price(target_2))}",
        "",
        f"⚡ Breakout: {esc(breakout_ist)} @ {esc(_fmt_price(observed_price))}",
    ]
    lines.append(f"Underlying spot: {esc(_fmt_price(alert.get('spot_ltp')))}")
    if practice_mode:
        lines.append("🧪 PRACTICE MODE: alert only, no live order placed")
    elif practice_mode is False:
        lines.append("📌 SIGNAL ONLY: strategy observation, no broker entry implied")
    else:
        lines.append("📌 SIGNAL ONLY (mode unverified): strategy observation, no broker entry implied")
    lines.extend(["", "📢 DISCLAIMER: Educational purposes only."])
    return "\n".join(lines)


class TelegramHandler:
    """Telegram delivery for the final two-bot routing model.

    Exactly two bots/channels are active:
      * ``trade_control``  - automated/paper trade lifecycle + system/service alerts
      * ``service_alerts`` - combined options screener alerts (stock, index, commodity)

    The ``service_alerts`` key and its ``BOT_SERVICE_ALERTS_TOKEN`` /
    ``CHANNEL_SERVICE_ALERTS_ID`` env names are intentionally kept for deployment
    compatibility even though the channel now carries screener alerts.
    """

    BOT_CONFIG = {
        "trade_control": {
            "channel_id": -1001234567894,
            "channel_env": "CHANNEL_TRADE_CONTROL_ID",
            "token_env": "BOT_TRADE_CONTROL_TOKEN",
            "description": "Trade approvals, execution updates + service alerts",
        },
        "service_alerts": {
            "channel_id": -1001234567895,
            "channel_env": "CHANNEL_SERVICE_ALERTS_ID",
            "token_env": "BOT_SERVICE_ALERTS_TOKEN",
            "description": "Combined options screener alerts (stock/index/commodity)",
        },
    }
    CHANNELS = {category: config["channel_id"] for category, config in BOT_CONFIG.items()}
    # The three supported screener segments all deliver to the single screener bot.
    CHANNEL_ALIASES = {
        **{category: "service_alerts" for category in SCREENER_CATEGORIES | SCREENER_CATEGORY_ALIASES},
        "trade": "trade_control",
        "service": "service_alerts",
    }
    DISABLED_CATEGORIES = DISABLED_CATEGORIES

    def __init__(self, token: Optional[str] = None) -> None:
        self.default_token = token or self._env_first("TELEGRAM_BOT_TOKEN", "TELEGRAM_TOKEN")
        self.token = self.default_token
        self.default_chat_id = self._env_first("TELEGRAM_CHAT_ID", "CHAT_ID", "ALERT_CHAT_ID")
        self._bot_instances: Dict[str, object] = {}

        if not self.default_token:
            logger.error("❌ No default Telegram token found in environment")
        else:
            logger.info("✅ Default Telegram token loaded")

        logger.info("=" * 80)
        logger.info("📱 TELEGRAM BOT CONFIGURATION")
        logger.info("=" * 80)
        for category, config in self.BOT_CONFIG.items():
            category_token = self._env_first(config["token_env"], *config.get("token_aliases", [])) or self.default_token
            configured_channel = self._env_first(config["channel_env"], *config.get("channel_aliases", []))
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

    @staticmethod
    def _env_first(*names: str) -> str:
        for name in names:
            value = os.getenv(name, "")
            if value:
                return value
        return ""

    @classmethod
    def is_disabled_category(cls, category: str) -> bool:
        """Retired categories (crypto, NIFTY50 5X, NIFTY50 pay-later) are never routed."""
        if not isinstance(category, str):
            return False
        return category.lower() in cls.DISABLED_CATEGORIES

    def _get_bot_for_category(self, category: str) -> tuple[str, int]:
        normalized_category = self.CHANNEL_ALIASES.get(category, category)
        if normalized_category not in self.BOT_CONFIG:
            logger.warning("⚠️ Unknown Telegram category '%s', using default channel", category)
            fallback_chat = int(self.default_chat_id or "-1004321977761")
            return self.default_token, fallback_chat

        config = self.BOT_CONFIG[normalized_category]
        category_token = self._env_first(config["token_env"], *config.get("token_aliases", [])) or self.default_token
        configured_channel = self._env_first(config["channel_env"], *config.get("channel_aliases", []))
        channel_id = int(configured_channel or config["channel_id"])
        logger.debug("🔍 [%s] Routed to channel %s", normalized_category.upper(), channel_id)
        return category_token, channel_id

    def is_channel_ready(self, channel_type: str) -> bool:
        if self.is_disabled_category(channel_type):
            return False
        normalized_channel = self.CHANNEL_ALIASES.get(channel_type, channel_type)
        if normalized_channel not in self.BOT_CONFIG:
            return False
        token, chat_id = self._get_bot_for_category(normalized_channel)
        return bool(token and chat_id)

    def configured_channels_summary(self) -> Dict[str, bool]:
        return {
            channel: self.is_channel_ready(channel)
            for channel in self.BOT_CONFIG
        }

    def send_signal_alert(self, category: str, signal_data: dict, option_data: dict) -> bool:
        if self.is_disabled_category(category):
            logger.warning("⚠️ [%s] Category is disabled, alert not sent", category.upper())
            return False
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
            if self.is_disabled_category(channel_type):
                logger.warning("❌ Disabled channel type: %s", channel_type)
                return False
            normalized_channel = self.CHANNEL_ALIASES.get(channel_type, channel_type)
            if normalized_channel not in self.BOT_CONFIG:
                logger.warning("❌ Unknown channel type: %s", channel_type)
                return False

            bot_token, chat_id = self._get_bot_for_category(normalized_channel)
            if not bot_token or not chat_id:
                logger.warning("❌ Channel config missing for %s", channel_type)
                return False

            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            payload = {"chat_id": chat_id, "text": alert_msg, "parse_mode": "HTML"}
            response = self._http_session().post(url, json=payload, timeout=10)
            if response.status_code == 200:
                logger.info("✅ Alert sent to %s", channel_type)
                return True

            logger.error("❌ Failed to send to %s: HTTP %s", channel_type, response.status_code)
            return False
        except Exception as e:
            logger.error("Error sending to channel %s: %s", channel_type, e.__class__.__name__)
            return False

    def _http_session(self) -> requests.Session:
        """Per-thread keep-alive session: avoids a TLS handshake per alert."""
        local = self.__dict__.setdefault("_http_local", threading.local())
        session = getattr(local, "session", None)
        if session is None:
            session = requests.Session()
            local.session = session
        return session

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
        if self.is_disabled_category(category):
            logger.warning("⚠️ [%s] Category is disabled, SL miss alert not sent", category.upper())
            return False
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
        alert = {**signal_data, "category": signal_data.get("category") or category}
        alert.setdefault("underlying", signal_data.get("symbol"))
        for key in ("option_symbol", "option_type", "premium_ltp"):
            if option_data.get(key) is not None:
                alert[key] = option_data[key]
        if alert.get("strike") is None:
            alert["strike"] = option_data.get("strike_price", option_data.get("strike"))
        compact = self.CHANNEL_ALIASES.get(category, category) == "service_alerts"
        return format_option_breakout_alert(alert, compact=compact)

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
                "token_configured": bool(token),
                "sent": sent,
                "status": status,
            }
            logger.info("%s %s -> %s", status, category, channel_id)

        logger.info("=" * 80)
        logger.info("🧪 TEST COMPLETE")
        logger.info("=" * 80)
        return results
