from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Dict

from data_manager import DataManager
from position_manager import PositionManager
from screener_15min import FifteenMinuteScreener
from screener_30min import ThirtyMinuteScreener
from screener_5min import FiveMinuteScreener
from telegram_handler import TelegramHandler
from webhook_handler import TradingViewWebhookHandler
from webhook_store import webhook_store


logger = logging.getLogger(__name__)


class ScreenerController:
    def __init__(self) -> None:
        self.data_manager = DataManager()
        self.telegram_handler = TelegramHandler()
        self.position_manager = PositionManager(max_positions=5)
        self.webhook_handler = TradingViewWebhookHandler(
            self.telegram_handler,
            self.position_manager,
            self.data_manager,
            store=webhook_store,
        )
        self.scanner_5 = FiveMinuteScreener(self.data_manager, self.telegram_handler, self.position_manager)
        self.scanner_15 = FifteenMinuteScreener(self.data_manager, self.telegram_handler, self.position_manager)
        self.scanner_30 = ThirtyMinuteScreener(self.data_manager, self.telegram_handler, self.position_manager)

        self._stop_event = threading.Event()
        self._paused = False
        self._thread: threading.Thread | None = None
        self._stats = {
            "running": False,
            "paused": False,
            "total_scans": 0,
            "total_alerts": 0,
            "last_scan_time": None,
            "errors": [],
            "last_webhook_health_check": None,
        }
        self._state_file = Path(__file__).with_name("screener_state.json")
        self._load_state()

    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return False
        self._stop_event.clear()
        self._paused = False
        self._stats["running"] = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        return True

    def stop(self) -> bool:
        if not self._thread or not self._thread.is_alive():
            self._stats["running"] = False
            return False
        self._stop_event.set()
        self._thread.join(timeout=3)
        if self._thread.is_alive():
            return False
        self._stats["running"] = False
        self._persist_state()
        self._thread = None
        return True

    def pause(self) -> bool:
        self._paused = True
        self._stats["paused"] = True
        return True

    def resume(self) -> bool:
        self._paused = False
        self._stats["paused"] = False
        return True

    def get_status(self) -> Dict[str, object]:
        return {
            **self._stats,
            "positions": len(self.position_manager.get_open_positions()),
        }

    def get_stats(self) -> Dict[str, object]:
        return self.get_status()

    def get_positions(self):
        return self.position_manager.get_open_positions()

    def get_alerts(self, limit: int = 50):
        return self.telegram_handler.get_alert_history(limit=limit)

    def process_tradingview_webhook(
        self,
        payload: dict,
        headers: Dict[str, str] | None = None,
        remote_addr: str | None = None,
        test_mode: bool = False,
    ) -> dict:
        return self.webhook_handler.process_tradingview_alert(
            payload,
            headers=headers,
            remote_addr=remote_addr,
            test_mode=test_mode,
        )

    def get_webhook_history(self, limit: int = 50):
        return webhook_store.get_webhook_history(limit=limit)

    def get_webhook_health(self) -> Dict[str, object]:
        return self.webhook_handler.get_health()

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            if self._paused:
                time.sleep(1)
                continue
            try:
                alerts = 0
                alerts += self.scanner_5.run_once()
                alerts += self.scanner_15.run_once()
                alerts += self.scanner_30.run_once()
                self._stats["total_scans"] += 1
                self._stats["total_alerts"] += alerts
                self._stats["last_scan_time"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                self._maybe_log_webhook_health()
                if self._stats["total_scans"] % 5 == 0:
                    self._persist_state()
            except Exception as exc:
                logger.exception("Screener loop error")
                self._stats["errors"].append(str(exc))
                self._stats["errors"] = self._stats["errors"][-100:]
            time.sleep(1)

    def _persist_state(self) -> None:
        payload = {
            "stats": self._stats,
            "positions": self.position_manager.get_open_positions(),
        }
        self._state_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _maybe_log_webhook_health(self) -> None:
        now = time.localtime()
        check_key = time.strftime("%Y-%m-%d", now)
        if now.tm_hour == 5 and self._stats.get("last_webhook_health_check") != check_key:
            self._stats["last_webhook_health_check"] = check_key
            logger.info("Daily webhook health check: %s", self.get_webhook_health())

    def _load_state(self) -> None:
        if not self._state_file.exists():
            return
        try:
            payload = json.loads(self._state_file.read_text(encoding="utf-8"))
            saved_stats = payload.get("stats", {})
            self._stats.update({
                "total_scans": saved_stats.get("total_scans", 0),
                "total_alerts": saved_stats.get("total_alerts", 0),
                "last_scan_time": saved_stats.get("last_scan_time"),
                "errors": saved_stats.get("errors", []),
            })
        except Exception:
            logger.warning("Unable to restore previous screener state")


screener_controller = ScreenerController()


if __name__ == "__main__":
    screener_controller.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        screener_controller.stop()
