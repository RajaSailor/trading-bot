from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from enum import Enum
import time
from collections import deque
from copy import deepcopy
import hashlib
import json
import threading

from timezone_utils import ensure_timezone, now_local_iso

logger = logging.getLogger(__name__)


class SignalType(Enum):
    """Signal types"""
    CALL = "CALL"
    PUT = "PUT"
    BREAKOUT_UP = "BREAKOUT_UP"
    BREAKOUT_DOWN = "BREAKOUT_DOWN"


class SignalProcessor:
    """
    Processes market data and generates trading signals
    """
    
    NIFTY_SECURITIES = {
        "NIFTY_INDEX": {
            "security_id": "543388",
            "exchange_segment": "IDX_I",
            "instrument": "INDEX",
            "description": "NIFTY 50 Index"
        },
        "NIFTY_FUTURES": {
            "security_id": "13",
            "exchange_segment": "NSE_FNO",
            "instrument": "FUTIDX",
            "description": "NIFTY 50 Futures"
        },
    }
    
    BANKNIFTY_SECURITIES = {
        "BANKNIFTY_INDEX": {
            "security_id": "25258",
            "exchange_segment": "IDX_I",
            "instrument": "INDEX",
            "description": "BANKNIFTY Index"
        },
        "BANKNIFTY_FUTURES": {
            "security_id": "25",
            "exchange_segment": "NSE_FNO",
            "instrument": "FUTIDX",
            "description": "BANKNIFTY Futures"
        },
    }
    
    MCX_SECURITIES = {
        "CRUDEOIL": {
            "security_id": "565899",
            "exchange_segment": "MCX",  # ✅ FIXED: Changed from MCX_COMM to MCX
            "instrument": "FUTCOM",
            "description": "Crude Oil Futures",
            "lot_size": 100
        },
        "GOLD": {
            "security_id": "567647",
            "exchange_segment": "MCX",  # ✅ FIXED: Changed from MCX_COMM to MCX
            "instrument": "FUTCOM",
            "description": "Gold Futures",
            "lot_size": 100
        },
    }
    
    def __init__(self, dhan_client=None):
        self.dhan_client = dhan_client
        self.last_signal_time: Dict[str, float] = {}
        self.min_signal_interval = 60
        self.candle_cache: Dict[str, List[dict]] = {}
        self.last_api_call_time = 0  # Rate limiting
        self.min_api_interval = 0.5  # 500ms between API calls
        logger.info("✅ SignalProcessor initialized")
    
    def detect_breakout(
        self,
        candles: List[dict],
        symbol: str,
        threshold_percent: float = 0.1
    ) -> Optional[Dict]:
        """Detect breakout from candle data"""
        if len(candles) < 2:
            return None
        
        current_candle = candles[-1]
        previous_candle = candles[-2]
        
        previous_high = float(previous_candle["high"])
        previous_low = float(previous_candle["low"])
        current_close = float(current_candle["close"])
        
        high_threshold = previous_high * (1 + threshold_percent / 100)
        low_threshold = previous_low * (1 - threshold_percent / 100)
        
        signal = None
        
        if current_close > high_threshold:
            signal = {
                "type": SignalType.CALL,
                "symbol": symbol,
                "entry": round(current_close, 2),
                "previous_high": round(previous_high, 2),
                "reference_timestamp": self._timestamp_to_ist(previous_candle.get("timestamp")),
                "breakout_timestamp": self._timestamp_to_ist(current_candle.get("timestamp")),
                "targets": self._calculate_targets(
                    entry=current_close,
                    pivot=previous_high,
                    direction="UP"
                ),
                "stop_loss": round(previous_low, 2),
            }
        
        elif current_close < low_threshold:
            signal = {
                "type": SignalType.PUT,
                "symbol": symbol,
                "entry": round(current_close, 2),
                "previous_low": round(previous_low, 2),
                "reference_timestamp": self._timestamp_to_ist(previous_candle.get("timestamp")),
                "breakout_timestamp": self._timestamp_to_ist(current_candle.get("timestamp")),
                "targets": self._calculate_targets(
                    entry=current_close,
                    pivot=previous_low,
                    direction="DOWN"
                ),
                "stop_loss": round(previous_high, 2),
            }
        
        return signal
    
    def _calculate_targets(self, entry: float, pivot: float, direction: str) -> List[float]:
        """Calculate target levels"""
        risk = abs(entry - pivot)
        
        if direction == "UP":
            targets = [
                round(entry + risk * 1.0, 2),
                round(entry + risk * 1.5, 2),
                round(entry + risk * 2.0, 2),
            ]
        else:
            targets = [
                round(entry - risk * 1.0, 2),
                round(entry - risk * 1.5, 2),
                round(entry - risk * 2.0, 2),
            ]
        
        return targets
    
    def get_option_chain_data(self, underlying_price: float) -> Dict:
        """Get option chain data"""
        atm_strike = round(underlying_price / 250) * 250
        
        return {
            "underlying_price": round(underlying_price, 2),
            "atm_strike": int(atm_strike),
            "call_strike": int(atm_strike),
            "put_strike": int(atm_strike),
            "call_premium": round(underlying_price * 0.005, 2),
            "put_premium": round(underlying_price * 0.005, 2),
            "call_ltp": round(underlying_price * 0.005, 2),
            "put_ltp": round(underlying_price * 0.005, 2),
            "call_volume": 5000,
            "put_volume": 4500,
        }
    
    def validate_signal(self, signal: Dict, symbol: str) -> bool:
        """Validate signal to avoid duplicates"""
        signal_key = f"{symbol}:{signal['type'].value}"
        current_time = ensure_timezone().timestamp()
        
        if signal_key in self.last_signal_time:
            time_diff = current_time - self.last_signal_time[signal_key]
            if time_diff < self.min_signal_interval:
                return False
        
        self.last_signal_time[signal_key] = current_time
        return True
    
    def format_signal_for_telegram(self, signal: Dict, option_data: Dict, source: str = "SCREENER") -> Tuple[Dict, Dict]:
        """Format signal for Telegram"""
        formatted_signal = {
            "symbol": signal.get("symbol"),
            "signal": signal["type"].value,
            "entry": signal.get("entry"),
            "stop_loss": signal.get("stop_loss"),
            "targets": signal.get("targets", []),
            "reference_timestamp": signal.get("reference_timestamp"),
            "breakout_timestamp": signal.get("breakout_timestamp"),
            "timeframe": "5-MIN BREAKOUT",
            "source": source,
            "signal_time_ist": ensure_timezone().strftime("%H:%M:%S"),
        }
        
        return formatted_signal, option_data
    
    def get_nifty_securities(self) -> Dict:
        """Get NIFTY 50 securities"""
        return self.NIFTY_SECURITIES
    
    def get_banknifty_securities(self) -> Dict:
        """Get BANKNIFTY securities"""
        return self.BANKNIFTY_SECURITIES
    
    def get_commodity_securities(self) -> Dict:
        """Get MCX commodity securities"""
        return self.MCX_SECURITIES
    
    def apply_rate_limit(self) -> None:
        """Apply rate limiting to prevent DH-904 errors"""
        current_time = time.time()
        time_since_last = current_time - self.last_api_call_time
        
        if time_since_last < self.min_api_interval:
            sleep_time = self.min_api_interval - time_since_last
            time.sleep(sleep_time)
        
        self.last_api_call_time = time.time()
    
    @staticmethod
    def _timestamp_to_ist(timestamp) -> str:
        """Convert timestamp to IST format"""
        if timestamp is None:
            return ensure_timezone().strftime("%H:%M:%S")
        
        try:
            dt = datetime.fromtimestamp(float(timestamp))
            return ensure_timezone(dt).strftime("%H:%M:%S")
        except Exception:
            return ensure_timezone().strftime("%H:%M:%S")


class SignalQueueProcessor:
    """
    Webhook signal parsing, validation and queue-based processing.
    """

    def __init__(self):
        self._queue = deque()
        self._lock = threading.RLock()
        self._queued_ids: set[str] = set()
        self._processed_ids: set[str] = set()
        self._inflight_ids: set[str] = set()

    @staticmethod
    def _signal_id_for_payload(payload: Dict) -> str:
        material = {
            "symbol": payload.get("symbol"),
            "action": str(payload.get("action", "")).upper(),
            "strategy": payload.get("strategy", "default"),
            "category": payload.get("category"),
            "entry_price": payload.get("entry_price", payload.get("price")),
            "target_price": payload.get("target_price"),
            "stop_loss": payload.get("stop_loss"),
            "quantity": payload.get("quantity"),
            "reference_timestamp": payload.get("reference_timestamp"),
            "breakout_timestamp": payload.get("breakout_timestamp"),
            "metadata": payload.get("metadata", {}),
        }
        encoded = json.dumps(material, sort_keys=True, default=str)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]

    def parse_webhook_signal(self, payload: Dict) -> Optional[Dict]:
        symbol = payload.get("symbol")
        action = str(payload.get("action", "")).upper()
        if not symbol or action not in {"BUY", "SELL", "EXIT"}:
            return None
        timestamp = payload.get("timestamp") or now_local_iso()
        signal_id = payload.get("signal_id") or payload.get("idempotency_key") or self._signal_id_for_payload(payload)
        return {
            "signal_id": signal_id,
            "symbol": symbol,
            "action": action,
            "strategy": payload.get("strategy", "default"),
            "timestamp": timestamp,
            "category": payload.get("category"),
            "metadata": deepcopy(payload.get("metadata", {})),
        }

    def validate_signal(self, signal: Dict) -> bool:
        if not signal:
            return False
        if signal.get("action") not in {"BUY", "SELL", "EXIT"}:
            return False
        return bool(signal.get("symbol"))

    def enqueue_signal(self, signal: Dict) -> bool:
        if not self.validate_signal(signal):
            return False
        signal_id = signal.get("signal_id")
        with self._lock:
            if signal_id and (signal_id in self._queued_ids or signal_id in self._processed_ids or signal_id in self._inflight_ids):
                return False
            cloned_signal = deepcopy(signal)
            self._queue.append(cloned_signal)
            if signal_id:
                self._queued_ids.add(signal_id)
            return True

    def process_next_signal(self) -> Optional[Dict]:
        with self._lock:
            if not self._queue:
                return None
            signal = dict(self._queue.popleft())
            signal_id = signal.get("signal_id")
            if signal_id:
                self._inflight_ids.add(signal_id)
            signal["processed_at"] = now_local_iso()
            return signal

    def process_queue(self, limit: Optional[int] = None) -> List[Dict]:
        processed = []
        while self._queue and (limit is None or len(processed) < limit):
            next_signal = self.process_next_signal()
            if next_signal:
                processed.append(next_signal)
        return processed

    def queue_size(self) -> int:
        with self._lock:
            return len(self._queue)

    def mark_processed(self, signal_id: str | None) -> None:
        if not signal_id:
            return
        with self._lock:
            self._queued_ids.discard(signal_id)
            self._inflight_ids.discard(signal_id)
            self._processed_ids.add(signal_id)

    def requeue_signal(self, signal: Dict) -> bool:
        if not self.validate_signal(signal):
            return False
        signal_id = signal.get("signal_id")
        with self._lock:
            cloned_signal = deepcopy(signal)
            cloned_signal.pop("processed_at", None)
            self._queue.appendleft(cloned_signal)
            if signal_id:
                self._processed_ids.discard(signal_id)
                self._inflight_ids.discard(signal_id)
                self._queued_ids.add(signal_id)
            return True
