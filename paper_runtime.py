"""Practice-only market data and background control integration."""

import logging
import os
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from atm_options_fetcher import ATMOptionsFetcher
from data_manager import (
    _apply_rate_limit, _quote_rate_limiter, _session_anchor_minutes, get_shared_data_manager,
)

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


def market_timestamp(value):
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, (int, float)) or str(value).replace(".", "", 1).isdigit():
        epoch = float(value)
        moment = datetime.fromtimestamp(epoch / 1000 if epoch > 1e12 else epoch, IST)
    else:
        try:
            moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            moment = datetime.strptime(str(value), "%d/%m/%Y %H:%M:%S")
    return moment.replace(tzinfo=IST) if moment.tzinfo is None else moment.astimezone(IST)


class PaperMarketData:
    """Use timestamped option quotes, never underlying prices or synthetic premiums."""

    def __init__(self, data_manager=None, session=None):
        self.data_manager = data_manager
        self.session = session or requests.Session()
        self._candles = {}
        self.portfolio = None
        self._quotes = {}
        self._quotes_received_at = None

    def quote(self, trade):
        token = os.getenv("ACCESS_TOKEN")
        client = os.getenv("DHAN_CLIENT_ID") or os.getenv("API_KEY")
        if not token or not client:
            return None
        segment, sid = trade["exchange_segment"], int(trade["security_id"])
        now = datetime.now(IST)
        fresh_after = None
        for order_type, sent_at in (
            (trade.get("order_type"), trade.get("approved_at")),
            (trade.get("exit_order_type"), trade.get("exit_requested_at")),
        ):
            if order_type == "limit" and sent_at:
                deadline = market_timestamp(sent_at) + timedelta(seconds=5)
                if now >= deadline:
                    fresh_after = deadline
        key = (segment, sid)
        if (
            key in self._quotes and self._quotes_received_at
            and (now - self._quotes_received_at).total_seconds() < 0.5
            and (fresh_after is None or self._quotes_received_at >= fresh_after)
        ):
            return self._quotes[key]
        candidates = self.portfolio.trades() if self.portfolio is not None else []
        candidates = [
            candidate for candidate in candidates
            if candidate["status"] in {"OPEN", "EXIT_PENDING", "ENTRY_PENDING"}
        ] + [trade]
        instruments = {}
        for candidate in candidates:
            instruments.setdefault(candidate["exchange_segment"], set()).add(
                int(candidate["security_id"])
            )
        instruments = {exchange: sorted(ids) for exchange, ids in instruments.items()}
        try:
            _apply_rate_limit(_quote_rate_limiter)
            response = self.session.post(
                "https://api.dhan.co/v2/marketfeed/quote",
                json=instruments,
                headers={"access-token": token, "client-id": str(client)},
                timeout=5,
            )
            response.raise_for_status()
            quotes = {}
            for exchange, values in response.json()["data"].items():
                for security_id, quote in values.items():
                    try:
                        quotes[(exchange, int(security_id))] = {
                            "price": float(quote["last_price"]),
                            "timestamp": market_timestamp(quote["last_trade_time"]),
                        }
                    except (KeyError, TypeError, ValueError, OverflowError, OSError):
                        continue
            self._quotes = quotes
            self._quotes_received_at = datetime.now(IST)
            return quotes.get(key)
        except (requests.RequestException, AttributeError, KeyError, TypeError, ValueError, OverflowError, OSError):
            return None

    def candle(self, trade):
        now = datetime.now(IST)
        key = (trade["exchange_segment"], trade["security_id"])
        anchor = _session_anchor_minutes(trade["exchange_segment"])
        bucket = (now.date(), (now.hour * 60 + now.minute - anchor) // 10)
        cached = self._candles.get(key)
        if cached and cached[0] == bucket:
            return cached[1]
        try:
            manager = self.data_manager or get_shared_data_manager()
            segment = trade["segment"]
            contract = {
                **trade,
                "instrument_type": "OPTCOM" if segment == "commodity" else
                ("OPTIDX" if segment in {"index", "index_options"} else "OPTSTK"),
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
        if self._stop.is_set():
            raise RuntimeError("Paper runtime is stopping")
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
        stopped = all(
            not thread or not thread.is_alive()
            for thread in (self._thread, self._telegram_thread)
        )
        if stopped:
            self.engine.close()
        return stopped
