from __future__ import annotations

import logging
import os
import threading
import time
from typing import Dict

from data_manager import DataManager
from dhan_client import DhanAPIClient
from main_screener import ScreenerController
from position_manager import PositionManager
from scheduler_manager import SchedulerManager
from service_monitor_handler import ServiceMonitorHandler
from telegram_handler import TelegramHandler
from telegram_trade_control_bot import TelegramTradeControlBot
from token_manager_auto import TokenManagerAuto
from trade_control_handler import TradeControlHandler
from trailing_stop_manager import TrailingStopManager


logger = logging.getLogger(__name__)


class UnifiedTradingSystem:
    """Orchestrates token refresh, screener lifecycle, trade approvals, and execution."""

    def __init__(self) -> None:
        self.data_manager = DataManager()
        self.telegram_handler = TelegramHandler()
        self.position_manager = PositionManager(max_positions=5)
        self.trade_control_handler = TradeControlHandler()
        self.trade_control_bot = TelegramTradeControlBot(self.telegram_handler, self.trade_control_handler)
        self.trailing_stop_manager = TrailingStopManager()
        self.token_manager = TokenManagerAuto()
        self.service_monitor = ServiceMonitorHandler(self.telegram_handler)

        access_token = os.getenv("ACCESS_TOKEN", "")
        self.dhan_client = DhanAPIClient(access_token, token_refresh_callback=self.token_manager.refresh_now)

        self.screener_controller = ScreenerController()
        self.screener_controller.premium_screener.trade_control_handler = self.trade_control_handler
        self.screener_controller.premium_screener.trade_control_bot = self.trade_control_bot

        self.scheduler = SchedulerManager(self.screener_controller.start, self.screener_controller.stop)
        self._stop_event = threading.Event()
        self._worker_thread: threading.Thread | None = None
        self._trailing_state: Dict[str, dict] = {}
        self._open_trade_requests: Dict[str, object] = {}

    def start(self) -> None:
        logger.info("🚀 Starting unified semi-automated trading system")
        self.token_manager.start()
        self.scheduler.start()
        self._stop_event.clear()
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True, name="trade-worker")
        self._worker_thread.start()

    def stop(self) -> None:
        logger.info("⏹️ Stopping unified semi-automated trading system")
        self._stop_event.set()
        self.scheduler.stop()
        self.screener_controller.stop()
        self.token_manager.stop()
        if self._worker_thread:
            self._worker_thread.join(timeout=3)

    def _worker_loop(self) -> None:
        status_cycles = 0
        while not self._stop_event.is_set():
            self._sync_dhan_token()
            self._execute_approved_trades()
            self._monitor_trailing_exits()
            status_cycles += 1
            if status_cycles % 60 == 0:
                self.service_monitor.send_status_alert(
                    bot_running=bool(self.screener_controller.get_status().get("running")),
                    active_positions=len(self.position_manager.get_open_positions()),
                    pending_trades=len(self.trade_control_handler.pending_requests()),
                )
            time.sleep(1)

    def _execute_approved_trades(self) -> None:
        for request in self.trade_control_handler.approved_for_execution():
            order_type = self._to_dhan_order_type(request.order_type)
            payload = {
                "symbol": request.option_symbol,
                "transaction_type": "BUY",
                "order_type": order_type,
                "quantity": request.quantity,
                "price": request.entry if order_type in {"LIMIT", "STOP_LOSS"} else None,
                "trigger_price": request.entry if order_type == "STOP_LOSS" else None,
            }

            response = self.dhan_client.place_order(**payload)
            if not self._order_accepted(response):
                logger.warning("Order placement not accepted for %s: %s", request.trade_id, response)
                continue

            order_id = str(response.get("orderId") or response.get("order_id", ""))
            if not order_id:
                logger.warning("Order accepted but missing order id for %s", request.trade_id)
                continue
            executed = self.trade_control_handler.mark_executed(request.trade_id, order_id)
            if not executed:
                continue

            self.trade_control_bot.send_execution_confirmation(executed)
            fill_price = self._extract_fill_price(response, fallback=executed.entry)
            state = self.trailing_stop_manager.initialize(
                side=executed.signal,
                entry_price=fill_price,
                stop_loss=executed.stop_loss,
                targets=executed.targets,
            )
            state = self.trailing_stop_manager.on_entry_filled(state, fill_price)
            self._trailing_state[executed.trade_id] = state
            self._open_trade_requests[executed.trade_id] = executed

    def _monitor_trailing_exits(self) -> None:
        for trade_id, state in list(self._trailing_state.items()):
            request = self._open_trade_requests.get(trade_id)
            if request is None:
                continue
            latest_price = self._fetch_trade_ltp(request)
            if latest_price is None:
                continue

            snapshot = self.trailing_stop_manager.evaluate(state, latest_price)
            if not snapshot.get("exited"):
                continue

            exit_response = self.dhan_client.place_order(
                symbol=request.option_symbol,
                transaction_type="SELL",
                order_type="MARKET",
                quantity=request.quantity,
            )
            if not self._order_accepted(exit_response):
                logger.warning("Exit order failed for %s: %s", trade_id, exit_response)
                continue

            self._trailing_state.pop(trade_id, None)
            self._open_trade_requests.pop(trade_id, None)

    def _fetch_trade_ltp(self, request) -> float | None:
        candles = self.data_manager.fetch_dhanhq_candles(request.option_symbol, "5min")
        if candles:
            return float(candles[-1]["close"])

        candles = self.data_manager.fetch_dhanhq_candles(request.symbol, "5min")
        if candles:
            return float(candles[-1]["close"])
        return None

    @staticmethod
    def _to_dhan_order_type(order_type: str) -> str:
        return {
            "STOPLOSS_LIMIT_BUY": "STOP_LOSS",
            "MARKET_BUY": "MARKET",
            "LIMIT_BUY": "LIMIT",
            "BRACKET_ORDER": "LIMIT",
            "COVER_ORDER": "MARKET",
        }.get(order_type, "LIMIT")

    def _sync_dhan_token(self) -> None:
        latest = os.getenv("ACCESS_TOKEN", "")
        if latest and latest != self.dhan_client.headers.get("access-token"):
            self.dhan_client.set_access_token(latest)

    @staticmethod
    def _order_accepted(response: dict) -> bool:
        if not response:
            return False
        if response.get("status") == "error":
            return False
        status = str(response.get("orderStatus") or response.get("order_status") or "").upper()
        if status in {"REJECTED", "CANCELLED", "FAILED"}:
            return False
        return bool(response.get("orderId") or response.get("order_id"))

    @staticmethod
    def _extract_fill_price(response: dict, fallback: float) -> float:
        for key in ("averagePrice", "average_price", "price", "executed_price"):
            value = response.get(key)
            if value is None:
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return float(fallback)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app = UnifiedTradingSystem()
    app.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        app.stop()
