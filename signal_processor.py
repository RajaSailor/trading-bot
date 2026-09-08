from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from enum import Enum

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
            "exchange_segment": "MCX",
            "instrument": "FUTCOM",
            "description": "Crude Oil Futures",
            "lot_size": 100
        },
        "GOLD": {
            "security_id": "567647",
            "exchange_segment": "MCX",
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
        current_time = datetime.now().timestamp()
        
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
            "signal_time_ist": datetime.now().strftime("%H:%M:%S"),
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
    
    @staticmethod
    def _timestamp_to_ist(timestamp) -> str:
        """Convert timestamp to IST format"""
        if timestamp is None:
            return datetime.now().strftime("%H:%M:%S")
        
        try:
            dt = datetime.fromtimestamp(float(timestamp))
            return dt.strftime("%H:%M:%S")
        except Exception:
            return datetime.now().strftime("%H:%M:%S")
