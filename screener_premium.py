from __future__ import annotations

import logging
import time
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo

from atm_options_fetcher import ATMOptionsFetcher
from premium_strategy_engine import PremiumStrategyEngine
from strategy_engine import StrategyEngine


IST = ZoneInfo("Asia/Kolkata")
logger = logging.getLogger(__name__)


class PremiumScreener:
    """Screen ATM option premium candles instead of underlying candles."""

    def __init__(self, data_manager, telegram_handler, position_manager) -> None:
        self.data_manager = data_manager
        self.telegram_handler = telegram_handler
        self.position_manager = position_manager
        self.fetcher = ATMOptionsFetcher(data_manager)
        self.engine = PremiumStrategyEngine(lookback=7)
        self.spot_engine = StrategyEngine(lookback=7)
        self.stock_option_scan_interval_seconds = 15 * 60
        self.spot_scan_interval_seconds = 15 * 60
        universe = self.data_manager.get_instruments()
        self.index_instruments = universe.get("index_options", [])
        self.commodity_instruments = universe.get("commodity_options", [])
        self.stock_instruments = universe.get("nifty50_stock_options", [])
        self.stock_spot_instruments = universe.get("nifty50_stock_spot", [])
        self.crypto_instruments = universe.get("crypto", [])
        self.last_run = {
            "commodity_10min": 0.0,
            "index_10min": 0.0,
            "nifty50_15min": 0.0,
            "stock_spot_15min": 0.0,
            "crypto_15min": 0.0,
        }

    def run_once(self, now: datetime | None = None) -> int:
        now = now or datetime.now(IST)
        alerts = 0

        if self._in_window(now.time(), dt_time(9, 0), dt_time(23, 30)):
            if time.time() - self.last_run["commodity_10min"] >= 10:
                alerts += self._scan_instruments(self.commodity_instruments, "10min")
                self.last_run["commodity_10min"] = time.time()

        if self._in_window(now.time(), dt_time(9, 15), dt_time(15, 39)):
            if time.time() - self.last_run["index_10min"] >= 10:
                alerts += self._scan_instruments(self.index_instruments, "10min")
                self.last_run["index_10min"] = time.time()

            if time.time() - self.last_run["nifty50_15min"] >= self.stock_option_scan_interval_seconds:
                alerts += self._scan_instruments(self.stock_instruments, "15min")
                self.last_run["nifty50_15min"] = time.time()

            if time.time() - self.last_run["stock_spot_15min"] >= self.spot_scan_interval_seconds:
                alerts += self._scan_spot_instruments(
                    self.stock_spot_instruments,
                    "15min",
                    StrategyEngine.GROUP_2,
                    primary_category="nifty50_stock_options",
                )
                self.last_run["stock_spot_15min"] = time.time()

        if time.time() - self.last_run["crypto_15min"] >= self.spot_scan_interval_seconds:
            alerts += self._scan_spot_instruments(
                self.crypto_instruments,
                "15min",
                StrategyEngine.GROUP_2,
                primary_category="crypto",
            )
            self.last_run["crypto_15min"] = time.time()

        return alerts

    def _scan_instruments(self, instruments, interval: str) -> int:
        alerts = 0
        logger.debug("📊 Scanning %s instruments for interval %s", len(instruments), interval)
        for instrument in instruments:
            try:
                logger.debug(
                    "📈 [%s] Fetching %s premiums (category=%s)...",
                    instrument.symbol,
                    interval,
                    instrument.category,
                )
                ce_candles, ce_option = self.fetcher.fetch_atm_premium_candles(instrument, "CE", interval)
                pe_candles, pe_option = self.fetcher.fetch_atm_premium_candles(instrument, "PE", interval)

                if ce_candles:
                    logger.info("✅ [%s] Got %s CE candles", instrument.symbol, len(ce_candles))
                    logger.debug(
                        "   First CE: O=%.2f, H=%.2f | Last CE: O=%.2f, H=%.2f",
                        float(ce_candles[0]["open"]),
                        float(ce_candles[0]["high"]),
                        float(ce_candles[-1]["open"]),
                        float(ce_candles[-1]["high"]),
                    )
                    for candle in ce_candles:
                        self.engine.add_ce_candle(instrument.symbol, candle)
                else:
                    logger.warning("❌ [%s] No CE candles fetched", instrument.symbol)
                if pe_candles:
                    logger.info("✅ [%s] Got %s PE candles", instrument.symbol, len(pe_candles))
                    logger.debug(
                        "   First PE: O=%.2f, H=%.2f | Last PE: O=%.2f, H=%.2f",
                        float(pe_candles[0]["open"]),
                        float(pe_candles[0]["high"]),
                        float(pe_candles[-1]["open"]),
                        float(pe_candles[-1]["high"]),
                    )
                    for candle in pe_candles:
                        self.engine.add_pe_candle(instrument.symbol, candle)
                else:
                    logger.warning("❌ [%s] No PE candles fetched", instrument.symbol)

                logger.debug("📊 [%s] Calling engine.evaluate_premiums()...", instrument.symbol)
                for signal in self.engine.evaluate_premiums(instrument.symbol, instrument.category):
                    option_data = ce_option if signal["option_type"] == "CE" else pe_option
                    if not option_data:
                        logger.warning(
                            "⚠️ [%s] Missing option metadata for %s signal",
                            instrument.symbol,
                            signal["option_type"],
                        )
                        continue

                    logger.info(
                        "🚀 [%s] %s for %s (%s) @ %.2f",
                        instrument.category,
                        signal["signal"],
                        instrument.symbol,
                        signal["option_type"],
                        signal["entry_price"],
                    )
                    signal["timeframe"] = self._display_timeframe(interval)
                    signal["premium_strategy"] = True
                    signal["signal_time_ist"] = datetime.now(IST).strftime("%H:%M:%S")
                    signal["signal_date_ist"] = datetime.now(IST).strftime("%d:%m:%Y")

                    accepted = self.position_manager.add_position(
                        symbol=instrument.symbol,
                        side=signal["signal"],
                        entry_price=signal["entry"],
                        stop_loss=signal["stop_loss"],
                        targets=signal["targets"],
                    )
                    if not accepted:
                        continue

                    telegram_payload = {
                        "option_symbol": option_data["option_symbol"],
                        "strike_price": option_data["atm_strike"],
                        "premium_ltp": option_data.get("premium_ltp", signal["entry"]),
                        "option_type": option_data["option_type"],
                    }
                    if self._dispatch_option_alert(instrument.category, signal, telegram_payload):
                        logger.info("✅ Alert sent for %s %s", instrument.symbol, signal["signal"])
                        alerts += 1
                    else:
                        logger.warning("⚠️ Alert rejected/skipped for %s %s", instrument.symbol, signal["signal"])
            except Exception as exc:
                logger.error("❌ Premium screener failed for %s: %s", instrument.symbol, exc, exc_info=True)
        logger.debug("📊 Instrument scan complete: %s alerts", alerts)
        return alerts

    def _scan_spot_instruments(
        self,
        instruments,
        interval: str,
        strategy_group: str,
        primary_category: str,
    ) -> int:
        alerts = 0
        logger.debug("📊 Scanning %s spot instruments for interval %s", len(instruments), interval)

        for instrument in instruments:
            try:
                engine = self._fresh_spot_engine()
                candles = self.data_manager.fetch_candles(instrument, interval)
                if len(candles) < 2:
                    logger.debug("❌ [%s] Insufficient spot candles", instrument.symbol)
                    continue

                latest = candles[-1]
                for historical in candles[-8:-1]:
                    engine.add_candle(instrument.symbol, historical)
                if not engine.add_candle(instrument.symbol, latest):
                    continue

                for signal in engine.evaluate(instrument.symbol, strategy_group):
                    logger.info(
                        "🚀 [%s] %s spot breakout for %s @ %.2f",
                        primary_category,
                        signal["signal"],
                        instrument.symbol,
                        signal["entry"],
                    )
                    signal["timeframe"] = self._display_timeframe(interval)
                    signal["spot_strategy"] = True
                    signal["signal_time_ist"] = datetime.now(IST).strftime("%H:%M:%S")
                    signal["signal_date_ist"] = datetime.now(IST).strftime("%d:%m:%Y")

                    accepted = self.position_manager.add_position(
                        symbol=instrument.symbol,
                        side=signal["signal"],
                        entry_price=signal["entry"],
                        stop_loss=signal["stop_loss"],
                        targets=signal["targets"],
                    )
                    if not accepted:
                        continue

                    spot_payload = {
                        "instrument_label": "SPOT",
                        "spot_ltp": latest["close"],
                    }
                    if self.telegram_handler.send_signal_alert(primary_category, signal, spot_payload):
                        alerts += 1
            except Exception as exc:
                logger.error("❌ Spot screener failed for %s: %s", instrument.symbol, exc, exc_info=True)

        return alerts

    @staticmethod
    def _display_timeframe(interval: str) -> str:
        return f"{interval.replace('min', '')}-MINUTE BREAKOUT"

    def _dispatch_option_alert(self, category: str, signal: dict, telegram_payload: dict) -> bool:
        send_results = [self.telegram_handler.send_signal_alert(category, signal, telegram_payload)]

        if category == "nifty50_stock_options":
            send_results.append(
                self.telegram_handler.send_signal_alert("nifty50_intraday_5x", signal, telegram_payload)
            )
            send_results.append(
                self.telegram_handler.send_signal_alert("nifty50_pay_later", signal, telegram_payload)
            )

        return all(send_results)

    def _fresh_spot_engine(self):
        engine_class = self.spot_engine.__class__
        try:
            return engine_class(lookback=getattr(self.spot_engine, "lookback", 7))
        except TypeError:
            return engine_class()

    @staticmethod
    def _in_window(now: dt_time, start: dt_time, end: dt_time) -> bool:
        return start <= now <= end
