from __future__ import annotations

import logging
import os
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional
from zoneinfo import ZoneInfo

from atm_calculator import calculate_option_details
from webhook_store import webhook_store


logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

CATEGORY_MAP = {
    "INDEX_OPTIONS": "index_options",
    "NIFTY50_STOCK_OPTIONS": "nifty50_stock_options",
    "COMMODITY_OPTIONS": "commodity_options",
    "INTRADAY_5X": "nifty50_intraday_5x",
    "PAY_LATER": "nifty50_pay_later",
    "CRYPTO": "crypto",
}

GROUP_BY_CATEGORY = {
    "index_options": "GROUP 1",
    "nifty50_stock_options": "GROUP 1",
    "commodity_options": "GROUP 1",
    "nifty50_intraday_5x": "GROUP 2",
    "nifty50_pay_later": "GROUP 2",
    "crypto": "GROUP 2",
}

INSTRUMENT_TYPE_BY_CATEGORY = {
    "index_options": "INDEX",
    "nifty50_stock_options": "STOCK",
    "commodity_options": "INDEX",
    "nifty50_intraday_5x": "STOCK",
    "nifty50_pay_later": "STOCK",
    "crypto": "CRYPTO",
}


class TradingViewWebhookHandler:
    def __init__(self, telegram_handler, position_manager, data_manager, store=webhook_store) -> None:
        self.telegram_handler = telegram_handler
        self.position_manager = position_manager
        self.data_manager = data_manager
        self.store = store
        self._rate_limit_window = deque()
        self._received_count = 0
        self._duplicate_count = 0
        self._failure_count = 0
        self._last_received_at: Optional[str] = None
        self._last_error: Optional[str] = None

    def process_tradingview_alert(
        self,
        payload: dict,
        headers: Optional[Dict[str, str]] = None,
        remote_addr: Optional[str] = None,
        test_mode: bool = False,
    ) -> dict:
        try:
            self.validate_webhook_payload(payload, headers=headers, remote_addr=remote_addr)
            signal = self.extract_signal_data(payload)
            duplicate = self.store.check_duplicate_signal(
                signal["ticker"],
                signal["entry_price"],
                signal["signal_type"],
                self.store.duplicate_window_seconds,
            )
            if duplicate:
                self._duplicate_count += 1
                logger.info("Duplicate TradingView webhook ignored for %s %s", signal["ticker"], signal["signal_type"])
                return {
                    "success": True,
                    "duplicate": True,
                    "signal_id": duplicate["signal_id"],
                    "message": "duplicate signal ignored",
                    "status_code": 200,
                }

            option_data = calculate_option_details(
                signal["current_price"],
                INSTRUMENT_TYPE_BY_CATEGORY[signal["category"]],
            )
            telegram_sent = False
            position_id = None

            if not test_mode:
                position = self.position_manager.add_position(
                    symbol=signal["symbol"],
                    side=signal["signal"],
                    entry_price=signal["entry"],
                    stop_loss=signal["stop_loss"],
                    targets=signal["targets"],
                )
                if position is None:
                    raise ValueError("position limit reached or position rejected")
                position_id = position.position_id
                telegram_sent = self.route_to_telegram(signal, option_data)

            stored_signal = self.store.store_webhook_signal(
                {
                    **signal,
                    "ticker": signal["symbol"],
                    "signal_type": signal["signal"],
                    "entry_price": signal["entry"],
                    "current_price": signal["current_price"],
                    "position_id": position_id,
                    "telegram_sent": telegram_sent,
                    "test_mode": test_mode,
                    "_created_epoch": time.time(),
                }
            )
            self.data_manager.record_webhook_signal(stored_signal)
            self._received_count += 1
            self._last_received_at = datetime.now(timezone.utc).isoformat()
            self._last_error = None
            logger.info(
                "TradingView webhook processed ticker=%s signal=%s source=%s test_mode=%s",
                signal["symbol"],
                signal["signal"],
                signal["source"],
                test_mode,
            )
            return {
                "success": True,
                "duplicate": False,
                "signal_id": stored_signal["signal_id"],
                "telegram_sent": telegram_sent,
                "test_mode": test_mode,
                "status_code": 200,
            }
        except Exception as exc:
            return self.handle_webhook_error(exc)

    def validate_webhook_payload(
        self,
        payload: dict,
        headers: Optional[Dict[str, str]] = None,
        remote_addr: Optional[str] = None,
    ) -> None:
        if os.getenv("ENABLE_TRADINGVIEW_WEBHOOK", "true").lower() != "true":
            raise ValueError("TradingView webhook is disabled")
        if not isinstance(payload, dict):
            raise ValueError("Webhook payload must be a JSON object")

        self._enforce_rate_limit()
        self._validate_secret(payload, headers or {})
        self._validate_ip_whitelist(remote_addr)

        required_fields = {
            "ticker",
            "signal_type",
            "entry_price",
            "stop_loss",
            "target_1",
            "target_2",
            "target_3",
            "timeframe",
            "category",
            "reference_candle",
            "breakout_candle_time",
            "previous_candle_high",
            "previous_candle_low",
            "current_price",
        }
        missing = sorted(field for field in required_fields if field not in payload)
        if missing:
            raise ValueError(f"Missing required webhook fields: {', '.join(missing)}")

        if str(payload["signal_type"]).upper() not in {"CALL", "PUT"}:
            raise ValueError("signal_type must be CALL or PUT")

        category = CATEGORY_MAP.get(str(payload["category"]).upper())
        if category is None:
            raise ValueError("Unsupported TradingView category")

        for field in (
            "entry_price",
            "stop_loss",
            "target_1",
            "target_2",
            "target_3",
            "previous_candle_high",
            "previous_candle_low",
            "current_price",
        ):
            float(payload[field])

    def extract_signal_data(self, payload: dict) -> dict:
        category = CATEGORY_MAP[str(payload["category"]).upper()]
        breakout_time = self._format_clock(payload["breakout_candle_time"])
        timeframe = str(payload["timeframe"]).upper()
        signal_type = str(payload["signal_type"]).upper()
        current_price = round(float(payload["current_price"]), 2)
        entry = round(float(payload["entry_price"]), 2)
        stop_loss = round(float(payload["stop_loss"]), 2)
        targets = [
            round(float(payload["target_1"]), 2),
            round(float(payload["target_2"]), 2),
            round(float(payload["target_3"]), 2),
        ]

        return {
            "symbol": str(payload["ticker"]).upper(),
            "ticker": str(payload["ticker"]).upper(),
            "signal": signal_type,
            "signal_type": signal_type,
            "entry": entry,
            "entry_price": entry,
            "stop_loss": stop_loss,
            "targets": targets,
            "timeframe": self._display_timeframe(timeframe),
            "timeframe_code": timeframe,
            "category": category,
            "category_display": str(payload["category"]).replace("_", " "),
            "strategy_group": GROUP_BY_CATEGORY[category],
            "source": "TRADINGVIEW WEBHOOK",
            "reference_color": str(payload["reference_candle"]).upper(),
            "reference_timestamp": self._previous_candle_time(breakout_time, timeframe),
            "reference_high": round(float(payload["previous_candle_high"]), 2),
            "reference_low": round(float(payload["previous_candle_low"]), 2),
            "breakout_timestamp": breakout_time,
            "breakout_candle_after": 1,
            "breakout_price": current_price,
            "current_price": current_price,
            "signal_time_ist": datetime.now(IST).strftime("%H:%M:%S"),
        }

    def route_to_telegram(self, signal: dict, option_data: dict) -> bool:
        return self.telegram_handler.send_signal_alert(signal["category"], signal, option_data)

    def handle_webhook_error(self, error: Exception) -> dict:
        self._failure_count += 1
        self._last_error = str(error)
        logger.exception("TradingView webhook processing failed")
        return {
            "success": False,
            "error": str(error),
            "status_code": 400,
        }

    def get_health(self) -> dict:
        return {
            "status": "healthy" if self._last_error is None else "degraded",
            "enabled": os.getenv("ENABLE_TRADINGVIEW_WEBHOOK", "true").lower() == "true",
            "received_count": self._received_count,
            "duplicate_count": self._duplicate_count,
            "failure_count": self._failure_count,
            "last_received_at": self._last_received_at,
            "last_error": self._last_error,
            "recent_signals": len(self.store.get_webhook_history(limit=50)),
        }

    def _enforce_rate_limit(self) -> None:
        now = time.time()
        while self._rate_limit_window and now - self._rate_limit_window[0] > 60:
            self._rate_limit_window.popleft()
        if len(self._rate_limit_window) >= 100:
            raise ValueError("TradingView webhook rate limit exceeded")
        self._rate_limit_window.append(now)

    @staticmethod
    def _validate_secret(payload: dict, headers: Dict[str, str]) -> None:
        expected_secret = os.getenv("WEBHOOK_SECRET")
        if not expected_secret:
            return
        provided_secret = payload.get("secret") or headers.get("X-Webhook-Secret")
        if provided_secret != expected_secret:
            raise ValueError("Invalid webhook secret")

    @staticmethod
    def _validate_ip_whitelist(remote_addr: Optional[str]) -> None:
        allowed = os.getenv("WEBHOOK_IP_WHITELIST", "").strip()
        if not allowed:
            return
        if remote_addr not in {value.strip() for value in allowed.split(",") if value.strip()}:
            raise ValueError("Webhook IP not allowed")

    @staticmethod
    def _display_timeframe(timeframe: str) -> str:
        mapping = {
            "5-MIN": "5-MINUTE BREAKOUT",
            "15-MIN": "15-MINUTE BREAKOUT",
            "30-MIN": "30-MINUTE BREAKOUT",
            "5": "5-MINUTE BREAKOUT",
            "15": "15-MINUTE BREAKOUT",
            "30": "30-MINUTE BREAKOUT",
        }
        return mapping.get(timeframe, timeframe)

    @staticmethod
    def _format_clock(value: object) -> str:
        text = str(value)
        for fmt in ("%H:%M:%S", "%H:%M"):
            try:
                return datetime.strptime(text, fmt).strftime("%I:%M %p").lstrip("0")
            except ValueError:
                continue
        return text

    @staticmethod
    def _previous_candle_time(breakout_time: str, timeframe: str) -> str:
        minutes_map = {"5-MIN": 5, "15-MIN": 15, "30-MIN": 30, "5": 5, "15": 15, "30": 30}
        delta_minutes = minutes_map.get(timeframe)
        if delta_minutes is None:
            return breakout_time
        try:
            breakout_dt = datetime.strptime(breakout_time, "%I:%M %p")
            previous_dt = breakout_dt - timedelta(minutes=delta_minutes)
            return previous_dt.strftime("%I:%M %p").lstrip("0")
        except ValueError:
            return breakout_time
