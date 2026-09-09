from __future__ import annotations

import logging
import time
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo

from atm_options_fetcher import ATMOptionsFetcher
from premium_strategy_engine import PremiumStrategyEngine


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
        universe = self.data_manager.get_instruments()
        self.index_instruments = universe.get("index_options", [])
        self.commodity_instruments = universe.get("commodity_options", [])
        self.stock_instruments = universe.get("nifty50_stock_options", [])
        self.last_run = {
            "commodity_10min": 0.0,
            "index_10min": 0.0,
            "nifty50_15min": 0.0,
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

            if time.time() - self.last_run["nifty50_15min"] >= 15:
                alerts += self._scan_instruments(self.stock_instruments, "15min")
                self.last_run["nifty50_15min"] = time.time()

        return alerts

    def _scan_instruments(self, instruments, interval: str) -> int:
        alerts = 0
        for instrument in instruments:
            try:
                ce_candles, ce_option = self.fetcher.fetch_atm_premium_candles(instrument, "CE", interval)
                pe_candles, pe_option = self.fetcher.fetch_atm_premium_candles(instrument, "PE", interval)

                if ce_candles:
                    for candle in ce_candles:
                        self.engine.add_ce_candle(instrument.symbol, candle)
                if pe_candles:
                    for candle in pe_candles:
                        self.engine.add_pe_candle(instrument.symbol, candle)

                for signal in self.engine.evaluate_premiums(instrument.symbol, instrument.category):
                    option_data = ce_option if signal["option_type"] == "CE" else pe_option
                    if not option_data:
                        continue

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
                    if self.telegram_handler.send_signal_alert(instrument.category, signal, telegram_payload):
                        alerts += 1
            except Exception as exc:
                logger.error("❌ Premium screener failed for %s: %s", instrument.symbol, exc, exc_info=True)
        return alerts

    @staticmethod
    def _display_timeframe(interval: str) -> str:
        return f"{interval.replace('min', '')}-MINUTE BREAKOUT"

    @staticmethod
    def _in_window(now: dt_time, start: dt_time, end: dt_time) -> bool:
        return start <= now <= end
