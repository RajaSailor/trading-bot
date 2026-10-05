"""Practice-only market data and background control integration."""

import logging
import os
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from atm_options_fetcher import ATMOptionsFetcher
from data_manager import _apply_rate_limit, _quote_rate_limiter, get_shared_data_manager

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


def market_timestamp(value):
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, (int, float)) or str(value).replace(".", "", 1).isdigit():
        moment = datetime.fromtimestamp(float(value), IST)
    else:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return moment.replace(tzinfo=IST) if moment.tzinfo is None else moment.astimezone(IST)


class PaperMarketData:
    """Use timestamped option quotes, never underlying prices or synthetic premiums."""

    def __init__(self, data_manager=None, session=None):
        self.data_manager = data_manager
        self.session = session or requests.Session()
        self._candles = {}

    def quote(self, trade):
        token = os.getenv("ACCESS_TOKEN")
        client = os.getenv("DHAN_CLIENT_ID") or os.getenv("API_KEY")
        if not token or not client:
            return None
        segment, sid = trade["exchange_segment"], int(trade["security_id"])
        try:
            _apply_rate_limit(_quote_rate_limiter)
            response = self.session.post(
                "https://api.dhan.co/v2/marketfeed/quote",
                json={segment: [sid]},
                headers={"access-token": token, "client-id": str(client)},
                timeout=5,
            )
            response.raise_for_status()
            quote = response.json()["data"][segment][str(sid)]
            return {
                "price": float(quote["last_price"]),
                "timestamp": market_timestamp(quote["last_trade_time"]),
            }
        except (requests.RequestException, KeyError, TypeError, ValueError, OverflowError, OSError):
            return None

    def candle(self, trade):
        now = datetime.now(IST)
        key = (trade["exchange_segment"], trade["security_id"])
        bucket = int(now.timestamp()) // 600
        cached = self._candles.get(key)
        if cached and cached[0] == bucket:
            return cached[1]
        try:
            manager = self.data_manager or get_shared_data_manager()
            segment = trade["segment"]
            contract = {
                **trade,
                "instrument_type": "OPTCOM" if segment == "commodity" else
                ("OPTIDX" if segment == "index" else "OPTSTK"),
            }
            candles = ATMOptionsFetcher(manager).fetch_option_candles(contract, "10min", now)
            completed = [
                candle for candle in candles
                if market_timestamp(candle["timestamp"]) + timedelta(minutes=10) <= now
                and market_timestamp(candle["timestamp"]).date() == now.date()
            ]
            previous = max(completed, key=lambda item: market_timestamp(item["timestamp"])) if completed else None
            self._candles[key] = (bucket, previous)
            return previous
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            return None


class PaperTradingRuntime:
    """Monitor risk even when the screener is disabled or the exchange is closed."""

    def __init__(self, engine, controller):
        self.engine = engine
        self.controller = controller
        self._stop = threading.Event()
        self._thread = None
        self._telegram_thread = None

    def submit(self, signal):
        try:
            if signal["action"] == "EXIT":
                for trade in self.engine.trades("open"):
                    if trade["symbol"] == signal["symbol"]:
                        self.engine.exit(trade["trade_id"], reason="MANUAL")
                return
            self.engine.submit(signal)
        except (ValueError, KeyError, TypeError) as exc:
            self.controller.notify(f"Paper signal rejected: {exc}")

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="paper-risk-monitor")
        self._thread.start()
        if self.controller.token:
            self._telegram_thread = threading.Thread(
                target=self._poll, daemon=True, name="paper-telegram-control"
            )
            self._telegram_thread.start()

    def _run(self):
        while not self._stop.is_set():
            try:
                self.engine.tick()
            except Exception as exc:
                logger.error("Paper monitor failed: %s", type(exc).__name__)
            self._stop.wait(0.5)

    def _poll(self):
        while not self._stop.is_set():
            try:
                self.controller.poll_once()
            except Exception as exc:
                logger.error("Paper Telegram poll failed: %s", type(exc).__name__)
            self._stop.wait(1)

    def stop(self):
        self._stop.set()
        for thread in (self._thread, self._telegram_thread):
            if thread:
                thread.join(timeout=35)
        if not self._thread or not self._thread.is_alive():
            self.engine.close()
