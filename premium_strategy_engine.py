from __future__ import annotations

import logging
import math
from typing import Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

# Rule: reference = most recent RED candle within the last 20 completed 10-minute candles.
LOOKBACK_CANDLES = 20
TIMEFRAME = "10min"
STOP_LOSS_BUFFER_PCT = 0.05
TARGET_R_MULTIPLE = 2.0
TRIGGER_LIVE = "live_ltp"
TRIGGER_CANDLE = "candle_high"


def is_red(candle: dict) -> bool:
    """Strictly red: close below open. Dojis (close == open) are not red."""
    return float(candle["close"]) < float(candle["open"])


def trade_levels(red_high: float, red_low: float) -> dict:
    """Entry = red high, SL = red low - 5%, single target = entry + 2R."""
    entry = round(float(red_high), 2)
    stop_loss = round(float(red_low) * (1 - STOP_LOSS_BUFFER_PCT), 2)
    risk_points = round(entry - stop_loss, 2)
    target = round(entry + TARGET_R_MULTIPLE * risk_points, 2)
    return {
        "entry": entry,
        "entry_price": entry,
        "stop_loss": stop_loss,
        "risk_points": risk_points,
        "target": target,
        "targets": [target],
        "risk_reward": f"1:{TARGET_R_MULTIPLE:g}",
    }


class PremiumStrategyEngine:
    """Most-recent-RED-candle breakout engine for option premium candles."""

    def __init__(self, lookback: int = LOOKBACK_CANDLES) -> None:
        self.lookback = max(1, int(lookback))
        # Backward-compatible per-symbol caches; bounded to the lookback window.
        self.ce_candle_cache: Dict[str, List[dict]] = {}
        self.pe_candle_cache: Dict[str, List[dict]] = {}
        logger.info("✅ Premium Strategy Engine initialized (lookback=%s)", self.lookback)

    # ------------------------------------------------------------ reference
    def find_reference(self, candles: Sequence[dict]) -> Optional[dict]:
        """Most recent RED candle within the last ``lookback`` completed candles.

        ``breakout_index`` records a historical high crossing for diagnostics.
        Historical bars never consume the reference; only a delivered live
        observation can do that.
        """
        window = list(candles[-self.lookback:])
        for red_idx in range(len(window) - 1, -1, -1):
            red = window[red_idx]
            if not is_red(red):
                continue
            red_high = float(red["high"])
            breakout_index = None
            for idx in range(red_idx + 1, len(window)):
                if float(window[idx]["high"]) > red_high:
                    breakout_index = idx
                    break
            return {
                "candle": red,
                "index": red_idx,
                "window": window,
                "high": red_high,
                "low": float(red["low"]),
                "timestamp": red.get("timestamp"),
                "breakout_index": breakout_index,
                "armed": True,
            }
        return None

    def build_signal(
        self,
        symbol: str,
        option_type: str,
        category: str,
        reference: dict,
        breakout_price: float,
        breakout_timestamp,
        trigger: str,
        breakout_high: Optional[float] = None,
    ) -> dict:
        option_side = option_type.upper()
        red = reference["candle"]
        signal = {
            "signal": "CALL" if option_side == "CE" else "PUT",
            "option_type": option_side,
            "symbol": symbol,
            "category": category,
            "timeframe": TIMEFRAME,
            "trigger": trigger,
            "reference_timestamp": reference.get("timestamp"),
            "breakout_timestamp": breakout_timestamp,
            "reference_color": "RED",
            "reference_open": round(float(red["open"]), 2),
            "reference_close": round(float(red["close"]), 2),
            "reference_high": reference["high"],
            "reference_low": reference["low"],
            "breakout_high": round(float(breakout_high if breakout_high is not None else breakout_price), 2),
            "breakout_price": float(breakout_price),
        }
        signal.update(trade_levels(reference["high"], reference["low"]))
        return signal

    # ------------------------------------------------------------ evaluation
    def evaluate_candles(
        self,
        symbol: str,
        candles: Sequence[dict],
        option_type: str,
        category: str,
    ) -> Optional[dict]:
        """Historical diagnostics only; not eligible for live alerts or entries."""
        reference = self.find_reference(candles)
        if reference is None:
            logger.debug("📊 [%s] %s: no RED candle in last %s candles", symbol, option_type, self.lookback)
            return None
        if reference["breakout_index"] is None:
            logger.debug(
                "📊 [%s] %s: armed on RED high %.2f (%s)", symbol, option_type, reference["high"], reference["timestamp"]
            )
            return None
        breakout = reference["window"][reference["breakout_index"]]
        signal = self.build_signal(
            symbol,
            option_type,
            category,
            reference,
            breakout_price=float(breakout["close"]),
            breakout_timestamp=breakout.get("timestamp"),
            trigger=TRIGGER_CANDLE,
            breakout_high=float(breakout["high"]),
        )
        signal["breakout_candle_after"] = reference["breakout_index"] - reference["index"]
        signal["breakout_is_latest"] = reference["breakout_index"] == len(reference["window"]) - 1
        logger.info(
            "🚀 [%s] %s BREAKOUT (candle) above RED HIGH %.2f → %.2f",
            symbol,
            option_type,
            reference["high"],
            float(breakout["high"]),
        )
        return signal

    def evaluate_live_price(
        self,
        symbol: str,
        reference: Optional[dict],
        live_price: Optional[float],
        option_type: str,
        category: str,
        timestamp=None,
    ) -> Optional[dict]:
        """Immediate trigger: live premium strictly crosses the armed RED high."""
        if reference is None or not reference.get("armed") or live_price is None:
            return None
        try:
            price = float(live_price)
        except (TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(price) or price <= 0:
            return None
        previous = reference.get("previous_price")
        reference["previous_price"] = price
        if previous is None or previous > float(reference["high"]) or price <= float(reference["high"]):
            return None
        logger.info(
            "🚀 [%s] %s BREAKOUT (live) above RED HIGH %.2f → %.2f",
            symbol,
            option_type,
            reference["high"],
            price,
        )
        signal = self.build_signal(
            symbol, option_type, category, reference, price, timestamp, trigger=TRIGGER_LIVE
        )
        signal["previous_observed_price"] = previous
        signal["first_crossing_timestamp"] = timestamp
        return signal

    # ------------------------------------------------- backward compatibility
    def add_ce_candle(self, symbol: str, candle: dict) -> bool:
        return self._add_candle(self.ce_candle_cache, symbol, candle)

    def add_pe_candle(self, symbol: str, candle: dict) -> bool:
        return self._add_candle(self.pe_candle_cache, symbol, candle)

    def clear(self, symbol: Optional[str] = None) -> None:
        if symbol is None:
            self.ce_candle_cache.clear()
            self.pe_candle_cache.clear()
            return
        self.ce_candle_cache.pop(symbol, None)
        self.pe_candle_cache.pop(symbol, None)

    def evaluate_premiums(self, symbol: str, category: str) -> List[dict]:
        signals = []
        for option_type, cache in (("CE", self.ce_candle_cache), ("PE", self.pe_candle_cache)):
            signal = self.evaluate_candles(symbol, cache.get(symbol, []), option_type, category)
            if signal:
                signals.append(signal)
        return signals

    def _add_candle(self, cache: Dict[str, List[dict]], symbol: str, candle: dict) -> bool:
        bucket = cache.setdefault(symbol, [])
        current = {
            "open": float(candle["open"]),
            "high": float(candle["high"]),
            "low": float(candle["low"]),
            "close": float(candle["close"]),
            "timestamp": candle.get("timestamp", ""),
        }
        for index, existing in enumerate(bucket):
            if existing["timestamp"] == current["timestamp"]:
                bucket[index] = current
                break
        else:
            bucket.append(current)
        if len(bucket) > self.lookback:
            del bucket[: len(bucket) - self.lookback]
        return len(bucket) >= 2
