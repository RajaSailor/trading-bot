from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Deque, Dict, Iterable, List, Optional


@dataclass
class CandleReference:
    index: int
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    color: str


def find_red_candle(candles: Iterable[dict]) -> Optional[dict]:
    for candle in reversed(list(candles)):
        if float(candle["close"]) < float(candle["open"]):
            return candle
    return None


def find_green_candle(candles: Iterable[dict]) -> Optional[dict]:
    for candle in reversed(list(candles)):
        if float(candle["close"]) > float(candle["open"]):
            return candle
    return None


def check_breakout_above(current_candle: dict, reference_high: float) -> bool:
    return float(current_candle["high"]) >= float(reference_high)


def check_breakout_below(current_candle: dict, reference_low: float) -> bool:
    return float(current_candle["low"]) <= float(reference_low)


def calculate_targets(entry: float, direction: str = "up") -> List[float]:
    if direction == "down":
        return [round(entry - 10, 2), round(entry - 20, 2), round(entry - 30, 2)]
    return [round(entry + 10, 2), round(entry + 20, 2), round(entry + 30, 2)]


class StrategyEngine:
    """Stateful breakout engine with persistent reference-candle tracking."""

    GROUP_1 = "group_1_same_red_logic"
    GROUP_2 = "group_2_opposite_logic"

    def __init__(self, lookback: int = 7) -> None:
        self.lookback = lookback
        self.candles: Dict[str, Deque[dict]] = {}
        self.candle_counter: Dict[str, int] = {}
        self.reference_state: Dict[str, Dict[str, Optional[CandleReference]]] = {}
        self.alert_keys: set[str] = set()

    def add_candle(self, symbol: str, candle: dict) -> bool:
        if symbol not in self.candles:
            self.candles[symbol] = deque(maxlen=200)
            self.candle_counter[symbol] = 0
            self.reference_state[symbol] = {"call": None, "put": None}

        current = {
            "open": float(candle["open"]),
            "high": float(candle["high"]),
            "low": float(candle["low"]),
            "close": float(candle["close"]),
            "timestamp": str(candle.get("timestamp") or datetime.utcnow().isoformat()),
        }
        if self.candles[symbol] and self.candles[symbol][-1]["timestamp"] == current["timestamp"]:
            return False

        self.candles[symbol].append(current)
        self.candle_counter[symbol] += 1
        return True

    def evaluate(self, symbol: str, strategy_group: str) -> List[dict]:
        if symbol not in self.candles or len(self.candles[symbol]) < 2:
            return []

        signals: List[dict] = []
        current = self.candles[symbol][-1]

        if strategy_group == self.GROUP_1:
            signal_call = self._evaluate_direction(symbol, "call", "red", "above", current)
            if signal_call:
                signal_call["option_side"] = "CE"
                signals.append(signal_call)
            signal_put = self._evaluate_direction(symbol, "put", "red", "above", current)
            if signal_put:
                signal_put["option_side"] = "PE"
                signals.append(signal_put)
        elif strategy_group == self.GROUP_2:
            signal_call = self._evaluate_direction(symbol, "call", "red", "above", current)
            if signal_call:
                signal_call["option_side"] = "CE"
                signals.append(signal_call)
            signal_put = self._evaluate_direction(symbol, "put", "green", "below", current)
            if signal_put:
                signal_put["option_side"] = "PE"
                signals.append(signal_put)
        else:
            raise ValueError(f"Unsupported strategy group: {strategy_group}")

        return signals

    def _evaluate_direction(
        self,
        symbol: str,
        side: str,
        reference_color: str,
        breakout_direction: str,
        current: dict,
    ) -> Optional[dict]:
        state = self.reference_state[symbol][side]
        if state is None:
            state = self._build_reference(symbol, reference_color)
            self.reference_state[symbol][side] = state

        if state is None:
            return None

        current_index = self.candle_counter[symbol]
        if current_index <= state.index:
            return None

        broke = (
            check_breakout_above(current, state.high)
            if breakout_direction == "above"
            else check_breakout_below(current, state.low)
        )
        if not broke:
            return None

        key = f"{symbol}:{side}:{state.timestamp}:{breakout_direction}:{reference_color}"
        if key in self.alert_keys:
            return None

        self.alert_keys.add(key)
        self.reference_state[symbol][side] = None

        entry = state.high if breakout_direction == "above" else state.low
        stop_loss = state.low if breakout_direction == "above" else state.high
        targets = calculate_targets(entry, "up" if breakout_direction == "above" else "down")

        return {
            "symbol": symbol,
            "signal": side.upper(),
            "reference_color": reference_color.upper(),
            "reference_timestamp": state.timestamp,
            "reference_high": state.high,
            "reference_low": state.low,
            "breakout_timestamp": current["timestamp"],
            "breakout_candle_after": current_index - state.index,
            "entry": round(entry, 2),
            "stop_loss": round(stop_loss, 2),
            "targets": targets,
            "breakout_direction": breakout_direction,
            "breakout_price": round(current["high"] if breakout_direction == "above" else current["low"], 2),
        }

    def _build_reference(self, symbol: str, color: str) -> Optional[CandleReference]:
        candles = list(self.candles[symbol])
        if len(candles) < 2:
            return None

        window_end = len(candles) - 1
        window_start = max(0, window_end - self.lookback)
        search_space = candles[window_start:window_end]
        match = find_red_candle(search_space) if color == "red" else find_green_candle(search_space)
        if not match:
            return None

        local_index = search_space.index(match)
        absolute_offset = window_start + local_index
        absolute_index = self.candle_counter[symbol] - (len(candles) - absolute_offset - 1)

        return CandleReference(
            index=absolute_index,
            timestamp=str(match["timestamp"]),
            open=float(match["open"]),
            high=float(match["high"]),
            low=float(match["low"]),
            close=float(match["close"]),
            color=color,
        )
