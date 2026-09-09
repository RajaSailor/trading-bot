from __future__ import annotations

import logging
from typing import Dict, List


logger = logging.getLogger(__name__)


class PremiumStrategyEngine:
    """Breakout engine for ATM CE/PE premium candles."""

    def __init__(self, lookback: int = 7) -> None:
        self.lookback = lookback
        self.ce_candle_cache: Dict[str, List[dict]] = {}
        self.pe_candle_cache: Dict[str, List[dict]] = {}

    def add_ce_candle(self, symbol: str, candle: dict) -> bool:
        return self._add_candle(self.ce_candle_cache, symbol, candle)

    def add_pe_candle(self, symbol: str, candle: dict) -> bool:
        return self._add_candle(self.pe_candle_cache, symbol, candle)

    def evaluate_premiums(self, symbol: str, category: str) -> List[dict]:
        signals = []
        signals.extend(
            self._evaluate_premium_breakout(
                symbol,
                self.ce_candle_cache.get(symbol, []),
                "CE",
                category,
            )
        )
        signals.extend(
            self._evaluate_premium_breakout(
                symbol,
                self.pe_candle_cache.get(symbol, []),
                "PE",
                category,
            )
        )
        return signals

    def _add_candle(self, cache: Dict[str, List[dict]], symbol: str, candle: dict) -> bool:
        bucket = cache.setdefault(symbol, [])
        current = {
            "open": float(candle["open"]),
            "high": float(candle["high"]),
            "low": float(candle["low"]),
            "close": float(candle["close"]),
            "volume": float(candle.get("volume", 0)),
            "timestamp": str(candle.get("timestamp", "")),
        }

        for index, existing in enumerate(bucket):
            if existing["timestamp"] == current["timestamp"]:
                bucket[index] = current
                break
        else:
            bucket.append(current)

        cache[symbol] = bucket[-10:]
        return len(cache[symbol]) >= 3

    def _evaluate_premium_breakout(
        self,
        symbol: str,
        candles: List[dict],
        option_type: str,
        category: str,
    ) -> List[dict]:
        if len(candles) < 3:
            return []

        recent = candles[-self.lookback:]
        red_idx = None
        for index in range(len(recent) - 2, -1, -1):
            if recent[index]["close"] < recent[index]["open"]:
                red_idx = index
                break

        if red_idx is None:
            return []

        red_candle = recent[red_idx]
        red_high = float(red_candle["high"])
        red_low = float(red_candle["low"])

        for index in range(red_idx + 1, len(recent)):
            candle = recent[index]
            if float(candle["high"]) <= red_high:
                continue

            signal_type = "CALL" if option_type == "CE" else "PUT"
            logger.info(
                "🚀 [%s] %s premium breakout above %.2f",
                symbol,
                option_type,
                red_high,
            )
            return [
                {
                    "signal": signal_type,
                    "option_type": option_type,
                    "symbol": symbol,
                    "category": category,
                    "entry": round(red_high, 2),
                    "entry_price": round(red_high, 2),
                    "stop_loss": round(red_low, 2),
                    "targets": [
                        round(red_high + 10, 2),
                        round(red_high + 20, 2),
                        round(red_high + 30, 2),
                    ],
                    "reference_timestamp": red_candle.get("timestamp"),
                    "breakout_timestamp": candle.get("timestamp"),
                    "breakout_candle_after": index - red_idx,
                    "reference_color": "RED",
                    "reference_high": round(red_high, 2),
                    "reference_low": round(red_low, 2),
                    "breakout_high": round(float(candle["high"]), 2),
                    "breakout_price": round(float(candle["close"]), 2),
                }
            ]

        return []
