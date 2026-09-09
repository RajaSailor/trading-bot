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
        logger.info("✅ Premium Strategy Engine initialized (lookback=%s)", lookback)

    def add_ce_candle(self, symbol: str, candle: dict) -> bool:
        return self._add_candle(self.ce_candle_cache, symbol, candle)

    def add_pe_candle(self, symbol: str, candle: dict) -> bool:
        return self._add_candle(self.pe_candle_cache, symbol, candle)

    def evaluate_premiums(self, symbol: str, category: str) -> List[dict]:
        logger.debug("📊 [%s] Starting premium evaluation...", symbol)
        logger.debug(
            "📊 [%s] CE candles cached: %s, PE candles cached: %s",
            symbol,
            len(self.ce_candle_cache.get(symbol, [])),
            len(self.pe_candle_cache.get(symbol, [])),
        )
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
        logger.debug("📊 [%s] Total signals after evaluation: %s", symbol, len(signals))
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

        cache[symbol] = bucket[-500:]
        return len(cache[symbol]) >= 3

    def _evaluate_premium_breakout(
        self,
        symbol: str,
        candles: List[dict],
        option_type: str,
        category: str,
    ) -> List[dict]:
        logger.debug(
            "📊 [%s] %s: Starting breakout evaluation with %s candles",
            symbol,
            option_type,
            len(candles),
        )
        if len(candles) < 3:
            logger.debug(
                "📊 [%s] %s: Insufficient candles (%s/3 required)",
                symbol,
                option_type,
                len(candles),
            )
            return []

        recent = candles[-self.lookback:]
        signal = self._find_breakout(symbol, recent, option_type, category)
        if signal:
            return [signal]

        if len(candles) > len(recent):
            logger.debug(
                "📊 [%s] %s: No breakout in last %s candles, checking full cached history (%s)",
                symbol,
                option_type,
                len(recent),
                len(candles),
            )
            signal = self._find_breakout(symbol, candles, option_type, category)
            if signal:
                return [signal]

        logger.debug("📊 [%s] %s: No breakout found after RED candle", symbol, option_type)
        return []

    def _find_breakout(
        self,
        symbol: str,
        candles: List[dict],
        option_type: str,
        category: str,
    ) -> dict | None:
        start_index = max(0, len(candles) - self.lookback)
        for idx, candle in enumerate(candles[start_index:], start=start_index):
            color = "🔴 RED" if float(candle["close"]) <= float(candle["open"]) else "🟢 GREEN"
            logger.debug(
                "  Candle[%s]: %s | O=%.2f, H=%.2f, L=%.2f, C=%.2f",
                idx,
                color,
                float(candle["open"]),
                float(candle["high"]),
                float(candle["low"]),
                float(candle["close"]),
            )

        for red_idx in range(len(candles) - 2, -1, -1):
            red_candle = candles[red_idx]
            if float(red_candle["close"]) > float(red_candle["open"]):
                continue

            red_high = float(red_candle["high"])
            red_low = float(red_candle["low"])
            logger.info(
                "🔴 [%s] %s RED candle found at index %s: O=%.2f, H=%.2f, L=%.2f, C=%.2f",
                symbol,
                option_type,
                red_idx,
                float(red_candle["open"]),
                red_high,
                red_low,
                float(red_candle["close"]),
            )
            logger.debug(
                "📊 [%s] %s: Looking for breakout ABOVE red_high=%.2f",
                symbol,
                option_type,
                red_high,
            )
            for index in range(red_idx + 1, len(candles)):
                candle = candles[index]
                breakout_high = float(candle["high"])
                is_breakout = breakout_high > red_high
                logger.debug(
                    "  Checking candle[%s]: H=%.2f > %.2f? %s",
                    index,
                    breakout_high,
                    red_high,
                    "✅ YES" if is_breakout else "❌ NO",
                )
                if not is_breakout:
                    continue

                signal_type = "CALL" if option_type == "CE" else "PUT"
                logger.info(
                    "🚀 [%s] %s BREAKOUT DETECTED! Premium breaks above RED HIGH: %.2f → %.2f",
                    symbol,
                    option_type,
                    red_high,
                    breakout_high,
                )
                return {
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
                    "breakout_high": round(breakout_high, 2),
                    "breakout_price": round(float(candle["close"]), 2),
                }

        logger.debug("📊 [%s] %s: No RED candle found in selected candles", symbol, option_type)
        return None
