import asyncio
import logging
import os
import threading
import time
from datetime import datetime
from typing import Dict, Optional

import pytz
from dotenv import load_dotenv

from atm_calculator import calculate_atm_strikes
from position_manager import PositionManager
from screener_15min import FifteenMinuteScanner
from screener_30min import ThirtyMinuteScanner
from screener_5min import FiveMinuteScanner
from screener_crypto import CryptoScanner
from strategy_multi_timeframe import Candle, MultiTimeframeBreakoutStrategy
from telegram_alerts import TelegramAlertService

load_dotenv(dotenv_path="./.env", override=True)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

IST = pytz.timezone("Asia/Kolkata")

try:
    from dhanhq import DhanContext, dhanhq

    DHANHQ_AVAILABLE = True
except ImportError:
    DHANHQ_AVAILABLE = False


def _ist_now() -> datetime:
    return datetime.now(pytz.UTC).astimezone(IST)


def _is_within_market_hours(now: datetime, start, end) -> bool:
    if now.weekday() >= 5:
        return False
    current = now.time()
    return start <= current <= end


class MultiTimeframeScreener:
    def __init__(self) -> None:
        self.strategy = MultiTimeframeBreakoutStrategy()
        self.positions = PositionManager(max_positions=5)
        self.last_scan_by_job: Dict[str, float] = {}
        self.last_candle_by_symbol_key: Dict[str, str] = {}
        self.crypto_last_price: Dict[str, float] = {"BTC/USD": 60000.0, "ETH/USD": 3000.0}
        self.dhan_api = None

        self.system_chat_id = int(os.getenv("CHAT_ID", "0") or 0)
        self.system_token = os.getenv("TELEGRAM_TOKEN")

        self.channel_tokens = {
            -1003966854994: os.getenv("BOT_INDEX_TOKEN") or self.system_token,
            -1003804613787: os.getenv("BOT_NIFTY50_OPTIONS_TOKEN") or self.system_token,
            -1004403277287: os.getenv("BOT_COMMODITY_TOKEN") or self.system_token,
            -1004466883026: os.getenv("BOT_NIFTY50_5X_TOKEN") or self.system_token,
            -1003814243881: os.getenv("BOT_NIFTY50_PAY_LATER_TOKEN") or self.system_token,
            -1004482078964: os.getenv("BOT_CRYPTO_TOKEN") or self.system_token,
        }

        self.scanner_5 = FiveMinuteScanner()
        self.scanner_15 = FifteenMinuteScanner()
        self.scanner_30 = ThirtyMinuteScanner()
        self.scanner_crypto = CryptoScanner()

        if DHANHQ_AVAILABLE and os.getenv("API_KEY") and os.getenv("ACCESS_TOKEN"):
            try:
                context = DhanContext(client_id=os.getenv("API_KEY"), access_token=os.getenv("ACCESS_TOKEN"))
                self.dhan_api = dhanhq(context)
            except Exception as exc:
                logger.error("DhanHQ init failed: %s", exc)

    def _fetch_candle(self, symbol: str, config: dict, interval_minutes: int) -> Optional[Candle]:
        if config.get("asset_class") == "CRYPTO":
            base = self.crypto_last_price.get(symbol, 1000.0)
            drift = (int(time.time()) % 11) - 5
            close = max(1.0, base + drift)
            self.crypto_last_price[symbol] = close
            return Candle(
                open=close - 1,
                high=close + 1,
                low=close - 2,
                close=close,
                timestamp=_ist_now().strftime("%Y-%m-%d %H:%M"),
            )
        if not self.dhan_api:
            return None

        try:
            response = self.dhan_api.get_intraday_paracande(
                exchange_tokens=[],
                security_id=[config["security_id"]],
                exchange=config["exchange"],
                interval=interval_minutes,
            )
            if not response or response.get("status") != "success" or not response.get("data"):
                return None

            raw = response["data"][-1]
            ts = (
                raw.get("timestamp")
                or raw.get("time")
                or raw.get("datetime")
                or _ist_now().strftime("%H:%M:%S")
            )
            return Candle(
                open=float(raw.get("open", 0)),
                high=float(raw.get("high", 0)),
                low=float(raw.get("low", 0)),
                close=float(raw.get("close", 0)),
                timestamp=str(ts),
            )
        except Exception:
            return None

    def _send_message(self, chat_id: int, message: str) -> bool:
        token = self.channel_tokens.get(chat_id)
        service = TelegramAlertService(token=token)
        return service.send(chat_id=chat_id, message=message)

    def _send_signal(self, signal: dict, chat_id: int) -> bool:
        token = self.channel_tokens.get(chat_id)
        service = TelegramAlertService(token=token)
        message = service.format_signal(signal)
        return service.send(chat_id=chat_id, message=message)

    def _send_system_confirmation(self, signal: dict) -> None:
        if not self.system_chat_id or not self.system_token:
            return
        msg = (
            "⚠️ Confirmation required for 6th position\n\n"
            f"Symbol: {signal['symbol']}\n"
            f"Timeframe: {signal['timeframe']}\n"
            f"Side: {signal['side']}\n"
            "Reply from mobile control to confirm entry."
        )
        TelegramAlertService(token=self.system_token).send(self.system_chat_id, msg)

    def _process_signal(self, symbol: str, config: dict, timeframe: str, candle: Candle, side: str, chat_id: int) -> None:
        symbol_key = f"{symbol}|{timeframe}"
        breakout = self.strategy.check_breakout(symbol_key, side)
        if not breakout:
            return

        signal = {
            "symbol": symbol,
            "timeframe": timeframe,
            "asset_class": config.get("asset_class"),
            "entry": breakout["entry"],
            "stop_loss": breakout["stop_loss"],
            "target_1": breakout["target_1"],
            "target_2": breakout["target_2"],
            "target_3": breakout["target_3"],
            "red_candle_time": breakout["red_candle_time"],
            "breakout_time": breakout["breakout_time"],
            "side": side,
            "entry_time": _ist_now().isoformat(),
            "position_id": f"{symbol_key}:{side}:{breakout['breakout_time']}",
        }

        if config.get("asset_class") != "CRYPTO":
            signal.update(calculate_atm_strikes(symbol=symbol, ltp=candle.close, asset_class=config.get("asset_class", "STOCK")))

        register = self.positions.register_signal(signal)
        if register["requires_confirmation"]:
            self._send_system_confirmation(signal)
            screener_state["confirmation_requests"] += 1
            return

        sent = self._send_signal(signal, chat_id)
        if sent:
            screener_state["total_signals"] += 1
            if side == "CALL":
                screener_state["call_signals"] += 1
            else:
                screener_state["put_signals"] += 1

    def _process_price_events(self, symbol: str, timeframe: str, ltp: float, chat_id: int) -> None:
        events = self.positions.update_price(symbol=symbol, timeframe=timeframe, ltp=ltp)
        for event in events:
            if event["type"] == "SL_MISSED":
                self._send_message(chat_id, f"🚨 SL MISSED for {event['position_id']}")
                screener_state["sl_missed_alerts"] += 1

    def _scan_group(self, job_name: str, interval_seconds: int, interval_minutes: int, start, end, instruments: Dict[str, dict], chat_selector) -> None:
        now = _ist_now()
        if not _is_within_market_hours(now, start, end):
            return

        last_scan = self.last_scan_by_job.get(job_name, 0)
        if (time.time() - last_scan) < interval_seconds:
            return

        self.last_scan_by_job[job_name] = time.time()
        screener_state["total_scans"] += 1
        screener_state["last_scan_time"] = now.isoformat()

        for symbol, config in instruments.items():
            candle = self._fetch_candle(symbol, config, interval_minutes=interval_minutes)
            if not candle or candle.close <= 0:
                continue

            symbol_key = f"{symbol}|{config['timeframe']}"
            if self.last_candle_by_symbol_key.get(symbol_key) == candle.timestamp:
                continue

            self.last_candle_by_symbol_key[symbol_key] = candle.timestamp
            self.strategy.add_candle(symbol_key, candle)

            chat_id = chat_selector(config)
            self._process_signal(symbol, config, config["timeframe"], candle, "CALL", chat_id)
            self._process_signal(symbol, config, config["timeframe"], candle, "PUT", chat_id)
            self._process_price_events(symbol, config["timeframe"], candle.close, chat_id)

        screener_state["successful_scans"] += 1

    def loop(self) -> None:
        screener_state["running"] = True
        while screener_state["running"]:
            try:
                five_min = self.scanner_5.instruments()
                self._scan_group(
                    job_name="5MIN",
                    interval_seconds=self.scanner_5.interval_seconds,
                    interval_minutes=5,
                    start=self.scanner_5.market_open,
                    end=self.scanner_5.market_close,
                    instruments=five_min,
                    chat_selector=lambda c: self.scanner_5.index_channel_id if c["asset_class"] == "INDEX" else self.scanner_5.stocks_channel_id,
                )

                self._scan_group(
                    job_name="15MIN_COMMODITY",
                    interval_seconds=self.scanner_15.interval_seconds,
                    interval_minutes=15,
                    start=self.scanner_15.market_open,
                    end=self.scanner_15.market_close,
                    instruments=self.scanner_15.commodity_instruments(),
                    chat_selector=lambda _: self.scanner_15.commodity_channel_id,
                )

                self._scan_group(
                    job_name="15MIN_INTRADAY",
                    interval_seconds=self.scanner_15.interval_seconds,
                    interval_minutes=15,
                    start=self.scanner_15.market_open,
                    end=self.scanner_15.market_close,
                    instruments=self.scanner_15.intraday_instruments(),
                    chat_selector=lambda _: self.scanner_15.intraday_channel_id,
                )

                self._scan_group(
                    job_name="30MIN_PAYLATER",
                    interval_seconds=self.scanner_30.interval_seconds,
                    interval_minutes=30,
                    start=self.scanner_30.market_open,
                    end=self.scanner_30.market_close,
                    instruments=self.scanner_30.instruments(),
                    chat_selector=lambda _: self.scanner_30.pay_later_channel_id,
                )

                self._scan_group(
                    job_name="CRYPTO_5MIN",
                    interval_seconds=self.scanner_crypto.interval_seconds,
                    interval_minutes=5,
                    start=self.scanner_crypto.market_open,
                    end=self.scanner_crypto.market_close,
                    instruments=self.scanner_crypto.instruments(),
                    chat_selector=lambda _: self.scanner_crypto.channel_id,
                )

                screener_state["market_open"] = True
            except Exception as exc:
                screener_state["errors"].append(str(exc))
                logger.error("Screener loop error: %s", exc)
            time.sleep(1)


screener_state = {
    "running": False,
    "total_scans": 0,
    "successful_scans": 0,
    "call_signals": 0,
    "put_signals": 0,
    "total_signals": 0,
    "sl_missed_alerts": 0,
    "confirmation_requests": 0,
    "last_scan_time": None,
    "market_open": False,
    "dhan_connected": DHANHQ_AVAILABLE,
    "telegram_connected": True,
    "errors": [],
    "monitored_instruments": 57,
    "crypto_instruments": 2,
}

_screener_instance: Optional[MultiTimeframeScreener] = None


def start_screener():
    global _screener_instance
    if _screener_instance is None:
        _screener_instance = MultiTimeframeScreener()

    thread = threading.Thread(target=_screener_instance.loop, daemon=True)
    thread.start()
    return thread


def stop_screener():
    screener_state["running"] = False


def confirm_next_position() -> bool:
    if not _screener_instance:
        return False
    return _screener_instance.positions.confirm_pending_position()
