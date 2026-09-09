from __future__ import annotations

import time
import logging
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo

from atm_calculator import calculate_option_details
from strategy_engine import StrategyEngine


IST = ZoneInfo("Asia/Kolkata")
logger = logging.getLogger(__name__)


class FiveMinuteScreener:
    def __init__(self, data_manager, telegram_handler, position_manager) -> None:
        self.data_manager = data_manager
        self.telegram_handler = telegram_handler
        self.position_manager = position_manager
        self.engine = StrategyEngine(lookback=7)
        universe = self.data_manager.get_instruments()
        self.index_option_instruments = universe.get("index_options", [])
        self.stock_option_instruments = universe.get("nifty50_stock_options", [])
        self.crypto_instruments = universe.get("crypto", [])
        self.last_run = {"options": 0.0, "crypto": 0.0}

    def run_once(self, now: datetime | None = None) -> int:
        now = now or datetime.now(IST)
        alerts = 0
        logger.debug("📊 [5MIN] Starting scan...")

        if self._in_window(now.time(), dt_time(9, 15), dt_time(15, 39)):
            if time.time() - self.last_run["options"] >= 10:
                logger.debug("📊 [5MIN] Scanning options instruments")
                alerts += self._scan_group(self.index_option_instruments, "5min", StrategyEngine.GROUP_1)
                alerts += self._scan_group(self.stock_option_instruments, "5min", StrategyEngine.GROUP_1)
                self.last_run["options"] = time.time()
            else:
                logger.debug("📊 [5MIN] Skipping options scan (throttled)")

        if self._in_window(now.time(), dt_time(5, 10), dt_time(23, 45)):
            if time.time() - self.last_run["crypto"] >= 60:
                logger.debug("📊 [5MIN] Scanning crypto instruments")
                alerts += self._scan_group(self.crypto_instruments, "5min", StrategyEngine.GROUP_2)
                self.last_run["crypto"] = time.time()
            else:
                logger.debug("📊 [5MIN] Skipping crypto scan (throttled)")

        logger.debug("📊 [5MIN] Scan complete - %s alerts sent", alerts)
        return alerts

    def _scan_group(self, instruments, interval: str, strategy_group: str) -> int:
        alerts = 0
        for instrument in instruments:
            logger.debug("  📈 Fetching candles for %s...", instrument.symbol)
            candles = self.data_manager.fetch_candles(instrument, interval)
            if not candles:
                logger.debug("    ✗ No candles for %s", instrument.symbol)
                continue
            logger.debug("    ✓ Got %s candles for %s", len(candles), instrument.symbol)
            if len(candles) < 2:
                logger.debug("    ✗ Insufficient candles for %s", instrument.symbol)
                continue

            latest = candles[-1]
            for historical in candles[-8:-1]:
                self.engine.add_candle(instrument.symbol, historical)
            if not self.engine.add_candle(instrument.symbol, latest):
                logger.debug("    ✗ No complete pattern for %s", instrument.symbol)
                continue
            signals = self.engine.evaluate(instrument.symbol, strategy_group)
            if not signals:
                logger.debug("    ✗ No signal for %s", instrument.symbol)
            for signal in signals:
                logger.info("🚀 [5MIN] SIGNAL DETECTED: %s %s", instrument.symbol, signal.get("signal", "UNKNOWN"))
                option_data = calculate_option_details(latest["close"], self._instrument_type(instrument.category))
                signal["timeframe"] = "5-MINUTE"
                accepted = self.position_manager.add_position(
                    symbol=instrument.symbol,
                    side=signal["signal"],
                    entry_price=signal["entry"],
                    stop_loss=signal["stop_loss"],
                    targets=signal["targets"],
                )
                if not accepted:
                    logger.warning("⚠️ [5MIN] Position rejected for %s; alert skipped", instrument.symbol)
                    continue
                if self.telegram_handler.send_signal_alert(instrument.category, signal, option_data):
                    logger.info("✅ [5MIN] Alert sent to Telegram for %s", instrument.symbol)
                    alerts += 1
                else:
                    logger.warning("⚠️ [5MIN] Failed to send Telegram alert for %s", instrument.symbol)
        return alerts

    def _instrument_type(self, category: str) -> str:
        if category == "crypto":
            return "CRYPTO"
        return "STOCK" if "stock" in category else "INDEX"

    @staticmethod
    def _in_window(now: dt_time, start: dt_time, end: dt_time) -> bool:
        return start <= now <= end
