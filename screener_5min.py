from __future__ import annotations

import time
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo

from atm_calculator import calculate_option_details
from strategy_engine import StrategyEngine


IST = ZoneInfo("Asia/Kolkata")


class FiveMinuteScreener:
    def __init__(self, data_manager, telegram_handler, position_manager) -> None:
        self.data_manager = data_manager
        self.telegram_handler = telegram_handler
        self.position_manager = position_manager
        self.engine = StrategyEngine(lookback=7)
        universe = self.data_manager.get_instruments()
        self.index_option_instruments = universe["index_options"]
        self.stock_option_instruments = universe["nifty50_stock_options"]
        self.crypto_instruments = universe["crypto"]
        self.last_run = {"options": 0.0, "crypto": 0.0}

    def run_once(self, now: datetime | None = None) -> int:
        now = now or datetime.now(IST)
        alerts = 0

        if self._in_window(now.time(), dt_time(9, 15), dt_time(15, 39)):
            if time.time() - self.last_run["options"] >= 10:
                alerts += self._scan_group(self.index_option_instruments, "5min", StrategyEngine.GROUP_1)
                alerts += self._scan_group(self.stock_option_instruments, "5min", StrategyEngine.GROUP_1)
                self.last_run["options"] = time.time()

        if self._in_window(now.time(), dt_time(5, 10), dt_time(23, 45)):
            if time.time() - self.last_run["crypto"] >= 60:
                alerts += self._scan_group(self.crypto_instruments, "5min", StrategyEngine.GROUP_2)
                self.last_run["crypto"] = time.time()

        return alerts

    def _scan_group(self, instruments, interval: str, strategy_group: str) -> int:
        alerts = 0
        for instrument in instruments:
            candles = self.data_manager.fetch_candles(instrument, interval)
            if len(candles) < 2:
                continue

            latest = candles[-1]
            for historical in candles[-8:-1]:
                self.engine.add_candle(instrument.symbol, historical)
            if not self.engine.add_candle(instrument.symbol, latest):
                continue
            signals = self.engine.evaluate(instrument.symbol, strategy_group)
            for signal in signals:
                option_data = calculate_option_details(latest["close"], self._instrument_type(instrument.category))
                signal["timeframe"] = "5-MINUTE"
                accepted = self.position_manager.add_position(
                    symbol=instrument.symbol,
                    side=signal["signal"],
                    entry_price=signal["entry"],
                    stop_loss=signal["stop_loss"],
                    targets=signal["targets"],
                )
                if accepted and self.telegram_handler.send_signal_alert(instrument.category, signal, option_data):
                    alerts += 1
        return alerts

    def _instrument_type(self, category: str) -> str:
        return "STOCK" if "stock" in category else "INDEX"

    @staticmethod
    def _in_window(now: dt_time, start: dt_time, end: dt_time) -> bool:
        return start <= now <= end
