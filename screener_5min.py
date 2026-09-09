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
        self.commodity_instruments = universe.get("commodities", []) or universe.get("commodity_options", [])
        self.last_run = {"options": 0.0, "crypto": 0.0, "commodity": 0.0}

    def run_once(self, now: datetime | None = None) -> int:
        now = now or datetime.now(IST)
        alerts = 0
        logger.debug("📊 [5MIN] Starting scan...")

        if self._in_window(now.time(), dt_time(9, 15), dt_time(15, 39)):
            if time.time() - self.last_run["options"] >= 10:
                logger.debug("📊 [5MIN] Scanning NSE equity options (NIFTY/BANKNIFTY/NIFTY50 stocks)")
                alerts += self._scan_group(
                    self.index_option_instruments,
                    "5min",
                    StrategyEngine.GROUP_1,
                    category="index_options",
                )
                alerts += self._scan_group(
                    self.stock_option_instruments,
                    "5min",
                    StrategyEngine.GROUP_1,
                    category="nifty50_stock_options",
                )
                self.last_run["options"] = time.time()
            else:
                logger.debug(
                    "📊 [5MIN] Skipping options scan (throttled - %d seconds until next scan)",
                    int(10 - (time.time() - self.last_run["options"])),
                )

        if self._in_window(now.time(), dt_time(9, 0), dt_time(23, 30)):
            if time.time() - self.last_run["commodity"] >= 15:
                logger.debug("📊 [5MIN] Scanning MCX commodities (GOLD/CRUDE/SILVER/NATURALGAS)")
                alerts += self._scan_group(
                    self.commodity_instruments,
                    "15min",
                    StrategyEngine.GROUP_2,
                    category="commodity_options",
                )
                self.last_run["commodity"] = time.time()
            else:
                logger.debug(
                    "📊 [5MIN] Skipping commodity scan (throttled - %d seconds until next scan)",
                    int(15 - (time.time() - self.last_run["commodity"])),
                )

        if self._in_window(now.time(), dt_time(5, 10), dt_time(23, 45)):
            if time.time() - self.last_run["crypto"] >= 60:
                logger.debug("📊 [5MIN] Scanning crypto (BTCUSD/ETHUSD via TradingView)")
                alerts += self._scan_group(
                    self.crypto_instruments,
                    "5min",
                    StrategyEngine.GROUP_2,
                    category="crypto",
                )
                self.last_run["crypto"] = time.time()
            else:
                logger.debug(
                    "📊 [5MIN] Skipping crypto scan (throttled - %d seconds until next scan)",
                    int(60 - (time.time() - self.last_run["crypto"])),
                )

        logger.debug("📊 [5MIN] Scan complete - %s alerts sent", alerts)
        return alerts

    def _scan_group(
        self,
        instruments,
        interval: str,
        strategy_group: str,
        category: str | None = None,
    ) -> int:
        alerts = 0
        if not instruments:
            logger.debug("  ℹ️ No instruments in category: %s", category)
            return 0

        for instrument in instruments:
            try:
                effective_category = category or getattr(instrument, "category", None)
                logger.debug(
                    "  📈 Fetching %s candles for %s (%s)...",
                    interval,
                    instrument.symbol,
                    effective_category,
                )
                candles = self.data_manager.fetch_candles(instrument, interval)
                if not candles:
                    logger.debug("    ✗ No candles for %s", instrument.symbol)
                    continue
                logger.debug("    ✓ Got %s %s candles for %s", len(candles), interval, instrument.symbol)
                if len(candles) < 2:
                    logger.debug(
                        "    ✗ Insufficient candles for %s (need 2, got %d)",
                        instrument.symbol,
                        len(candles),
                    )
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
                    continue

                for signal in signals:
                    logger.info(
                        "🚀 [5MIN] SIGNAL DETECTED: %s %s @ %s (Category: %s)",
                        instrument.symbol,
                        signal.get("signal", "UNKNOWN"),
                        latest["close"],
                        effective_category,
                    )
                    option_data = calculate_option_details(
                        latest["close"],
                        self._instrument_type(effective_category),
                    )
                    signal["timeframe"] = "5-MINUTE"
                    accepted = self.position_manager.add_position(
                        symbol=instrument.symbol,
                        side=signal["signal"],
                        entry_price=signal["entry"],
                        stop_loss=signal["stop_loss"],
                        targets=signal["targets"],
                    )
                    if not accepted:
                        logger.warning(
                            "⚠️ [5MIN] Position rejected for %s (limit reached or other); alert skipped",
                            instrument.symbol,
                        )
                        continue

                    bot_category = self._get_bot_category(effective_category)
                    logger.debug("  📤 Routing %s alert to bot category: %s", instrument.symbol, bot_category)
                    if self.telegram_handler.send_signal_alert(bot_category, signal, option_data):
                        logger.info(
                            "✅ [5MIN] Alert sent to Telegram for %s (%s channel)",
                            instrument.symbol,
                            bot_category,
                        )
                        alerts += 1
                    else:
                        logger.warning("⚠️ [5MIN] Failed to send Telegram alert for %s", instrument.symbol)
            except Exception as e:
                logger.error("❌ [5MIN] Error scanning %s: %s", instrument.symbol, e, exc_info=True)
        return alerts

    def _get_bot_category(self, category: str | None) -> str:
        category_mapping = {
            "index_options": "index_options",
            "nifty50_stock_options": "nifty50_stock_options",
            "commodities": "commodity_options",
            "commodity_options": "commodity_options",
            "crypto": "crypto",
        }
        return category_mapping.get(category, "index_options")

    def _instrument_type(self, category: str) -> str:
        if category == "crypto":
            return "CRYPTO"
        if category in {"commodities", "commodity_options"}:
            return "COMMODITY"
        return "STOCK" if "stock" in category else "INDEX"

    @staticmethod
    def _in_window(now: dt_time, start: dt_time, end: dt_time) -> bool:
        return start <= now <= end
