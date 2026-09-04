from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional


@dataclass(frozen=True)
class Candle:
    open: float
    high: float
    low: float
    close: float
    timestamp: str

    @property
    def is_red(self) -> bool:
        return self.close < self.open


class MultiTimeframeBreakoutStrategy:
    """7-candle RED breakout strategy used across all configured timeframes."""

    def __init__(self) -> None:
        self._candles: Dict[str, Deque[Candle]] = defaultdict(lambda: deque(maxlen=7))
        self._triggered_keys = set()

    def add_candle(self, symbol_key: str, candle: Candle) -> None:
        self._candles[symbol_key].append(candle)

    def get_candles(self, symbol_key: str) -> List[Candle]:
        return list(self._candles.get(symbol_key, []))

    def _find_latest_red_index(self, candles: List[Candle]) -> Optional[int]:
        for idx in range(len(candles) - 1, -1, -1):
            if candles[idx].is_red:
                return idx
        return None

    def check_breakout(self, symbol_key: str, signal_side: str) -> Optional[dict]:
        candles = self.get_candles(symbol_key)
        if len(candles) < 2:
            return None

        red_index = self._find_latest_red_index(candles)
        if red_index is None:
            return None

        red_candle = candles[red_index]
        following = candles[red_index + 1 : red_index + 6]
        if not following:
            return None

        latest = candles[-1]
        latest_offset = len(candles) - 1 - red_index
        if not (1 <= latest_offset <= 5):
            return None

        if latest.high <= red_candle.high:
            return None

        if not any(candle.high > red_candle.high for candle in following):
            return None

        trigger_key = (
            symbol_key,
            signal_side,
            red_candle.timestamp,
            latest.timestamp,
        )
        if trigger_key in self._triggered_keys:
            return None

        self._triggered_keys.add(trigger_key)

        return {
            "side": signal_side,
            "entry": round(red_candle.high, 2),
            "stop_loss": round(red_candle.low, 2),
            "target_1": round(red_candle.high + 10, 2),
            "target_2": round(red_candle.high + 20, 2),
            "target_3": round(red_candle.high + 30, 2),
            "red_candle_time": red_candle.timestamp,
            "breakout_time": latest.timestamp,
        }
