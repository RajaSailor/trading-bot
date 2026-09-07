"""
Signal Processor - Detects trading breakouts and triggers Telegram alerts
Integrates with:
- DhanHQ API for real-time OHLC data
- TradingView webhook for external signals
- Telegram alerts (via telegram_alerts_fixed.py)
- Position manager for risk control

File: signal_processor.py
"""

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


class InstrumentType(Enum):
    """Instrument types supported"""
    INDEX = "INDEX"
    FUTIDX = "FUTIDX"  # Index Futures
    OPTIDX = "OPTIDX"  # Index Options
    FUTSTK = "FUTSTK"  # Stock Futures
    OPTSTK = "OPTSTK"  # Stock Options
    FUTCOM = "FUTCOM"  # Commodity Futures
    OPTCOM = "OPTCOM"  # Commodity Options


class SignalProcessor:
    """
    Processes market data and generates trading signals
    
    Features:
    - Multiple breakout detection strategies
    - Real-time candle analysis (1-min, 5-min, 15-min)
    - Options chain data integration
    - Telegram alert formatting
    - Signal validation & deduplication
    """
    
    # NIFTY 50 Index & Derivatives Security IDs
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
        "NIFTY_OPTIONS_CE": {
            "base_strike": 18250,  # Current ATM, adjust dynamically
            "exchange_segment": "NSE_FNO",
            "instrument": "OPTIDX",
            "description": "NIFTY 50 Call Options"
        },
        "NIFTY_OPTIONS_PE": {
            "base_strike": 18250,
            "exchange_segment": "NSE_FNO",
            "instrument": "OPTIDX",
            "description": "NIFTY 50 Put Options"
        }
    }
    
    # BANKNIFTY Index & Derivatives
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
        "BANKNIFTY_OPTIONS_CE": {
            "base_strike": 47500,  # Current ATM
            "exchange_segment": "NSE_FNO",
            "instrument": "OPTIDX",
            "description": "BANKNIFTY Call Options"
        },
        "BANKNIFTY_OPTIONS_PE": {
            "base_strike": 47500,
            "exchange_segment": "NSE_FNO",
            "instrument": "OPTIDX",
            "description": "BANKNIFTY Put Options"
        }
    }
    
    # MCX Commodities
    MCX_SECURITIES = {
        "CRUDEOIL": {
            "security_id": "565899",
            "exchange_segment": "MCX_COMM",
            "instrument": "FUTCOM",
            "description": "Crude Oil Futures",
            "lot_size": 100
        },
        "GOLD": {
            "security_id": "567647",
            "exchange_segment": "MCX_COMM",
            "instrument": "FUTCOM",
            "description": "Gold Futures",
            "lot_size": 100
        },
        "SILVER": {
            "security_id": "567649",
            "exchange_segment": "MCX_COMM",
            "instrument": "FUTCOM",
            "description": "Silver Futures",
            "lot_size": 30
        },
        "NATURALGAS": {
            "security_id": "565853",
            "exchange_segment": "MCX_COMM",
            "instrument": "FUTCOM",
            "description": "Natural Gas Futures",
            "lot_size": 1000
        }
    }
    
    # Nifty 50 Stock Futures & Options (Sample - Top 5)
    NIFTY50_STOCKS = {
        "RELIANCE": {"symbol": "RELIANCE", "nse_code": "2885"},
        "TCS": {"symbol": "TCS", "nse_code": "2675"},
        "INFY": {"symbol": "INFY", "nse_code": "2836"},
        "HDFC": {"symbol": "HDFC", "nse_code": "2281"},
        "ICICIBANK": {"symbol": "ICICIBANK", "nse_code": "2038"},
    }
    
    def __init__(self, dhan_client=None):
        """
        Initialize signal processor
        
        Args:
            dhan_client: DhanAPIClient instance for fetching market data
        """
        self.dhan_client = dhan_client
        self.last_signal_time: Dict[str, float] = {}
        self.min_signal_interval = 60  # Minimum 60 seconds between same signals
        self.candle_cache: Dict[str, List[dict]] = {}
        
        logger.info("✅ SignalProcessor initialized")
    
    def detect_breakout(
        self,
        candles: List[dict],
        symbol: str,
        threshold_percent: float = 0.1
    ) -> Optional[Dict]:
        """
        Detect breakout from candle data
        
        Args:
            candles: List of candle data [{open, high, low, close, time}, ...]
            symbol: Trading symbol
            threshold_percent: Breakout threshold as percentage
            
        Returns:
            Signal dict if breakout detected, None otherwise
            
        Example candle:
        {
            "timestamp": 1234567890,
            "open": 18250.5,
            "high": 18275.0,
            "low": 18225.0,
            "close": 18270.0,
            "volume": 1234567
        }
        """
        if len(candles) < 2:
            return None
        
        current_candle = candles[-1]
        previous_candle = candles[-2]
        
        # Calculate key levels
        previous_high = float(previous_candle["high"])
        previous_low = float(previous_candle["low"])
        current_close = float(current_candle["close"])
        current_high = float(current_candle["high"])
        current_low = float(current_candle["low"])
        
        # Threshold in points
        high_threshold = previous_high * (1 + threshold_percent / 100)
        low_threshold = previous_low * (1 - threshold_percent / 100)
        
        # Breakout detection
        signal = None
        
        # CALL Signal: Close above previous high
        if current_close > high_threshold:
            signal = {
                "type": SignalType.CALL,
                "symbol": symbol,
                "entry": round(current_close, 2),
                "previous_high": round(previous_high, 2),
                "reference_color": "GREEN",
                "reference_timestamp": self._timestamp_to_ist(previous_candle.get("timestamp")),
                "breakout_timestamp": self._timestamp_to_ist(current_candle.get("timestamp")),
                "breakout_price": round(current_close, 2),
                "breakout_candle_after": 1,
                "targets": self._calculate_targets(
                    entry=current_close,
                    pivot=previous_high,
                    direction="UP"
                ),
                "stop_loss": round(previous_low, 2),
                "reference_high": round(previous_high, 2),
                "reference_low": round(previous_low, 2),
            }
        
        # PUT Signal: Close below previous low
        elif current_close < low_threshold:
            signal = {
                "type": SignalType.PUT,
                "symbol": symbol,
                "entry": round(current_close, 2),
                "previous_low": round(previous_low, 2),
                "reference_color": "RED",
                "reference_timestamp": self._timestamp_to_ist(previous_candle.get("timestamp")),
                "breakout_timestamp": self._timestamp_to_ist(current_candle.get("timestamp")),
                "breakout_price": round(current_close, 2),
                "breakout_candle_after": 1,
                "targets": self._calculate_targets(
                    entry=current_close,
                    pivot=previous_low,
                    direction="DOWN"
                ),
                "stop_loss": round(previous_high, 2),
                "reference_high": round(previous_high, 2),
                "reference_low": round(previous_low, 2),
            }
        
        return signal
    
    def _calculate_targets(
        self,
        entry: float,
        pivot: float,
        direction: str,
        num_targets: int = 3
    ) -> List[float]:
        """
        Calculate target levels based on pivot and entry
        
        Uses a 1:1, 1.5:1, 2:1 risk-reward ratio
        """
        risk = abs(entry - pivot)
        
        if direction == "UP":
            targets = [
                round(entry + risk * 1.0, 2),      # T1: 1:1
                round(entry + risk * 1.5, 2),      # T2: 1.5:1
                round(entry + risk * 2.0, 2),      # T3: 2:1
            ]
        else:  # DOWN
            targets = [
                round(entry - risk * 1.0, 2),
                round(entry - risk * 1.5, 2),
                round(entry - risk * 2.0, 2),
            ]
        
        return targets[:num_targets]
    
    def get_option_chain_data(
        self,
        underlying_price: float,
        instrument_type: str = "FUTIDX",
        strike_range: int = 500
    ) -> Dict:
        """
        Get option chain data around current price
        
        Args:
            underlying_price: Current underlying asset price
            instrument_type: FUTIDX for index, FUTSTK for stocks
            strike_range: Range of strikes to fetch (e.g., ±500 points)
            
        Returns:
            Option data with ATM, call/put premiums
        """
        if not self.dhan_client:
            logger.warning("⚠️  DhanAPIClient not available for option data")
            return self._get_mock_option_data(underlying_price)
        
        try:
            # Round to nearest strike (250 for indices, 1 for stocks)
            strike_interval = 250 if instrument_type == "FUTIDX" else 1
            atm_strike = round(underlying_price / strike_interval) * strike_interval
            
            # Get quotes for ATM CE & PE
            option_data = {
                "underlying_price": round(underlying_price, 2),
                "atm_strike": int(atm_strike),
                "call_strike": int(atm_strike),
                "put_strike": int(atm_strike),
                "call_premium": 0.0,
                "put_premium": 0.0,
                "call_ltp": 0.0,
                "put_ltp": 0.0,
                "call_volume": 0,
                "put_volume": 0,
                "call_open_interest": 0,
                "put_open_interest": 0,
            }
            
            # TODO: Fetch actual premium via dhan_client.quote_data()
            # This requires live API connection
            
            return option_data
            
        except Exception as e:
            logger.error(f"❌ Error fetching option chain: {e}")
            return self._get_mock_option_data(underlying_price)
    
    def _get_mock_option_data(self, underlying_price: float) -> Dict:
        """
        Get mock option data for testing
        
        Replace with real API calls in production
        """
        atm_strike = round(underlying_price / 250) * 250
        
        # Simplified mock data
        return {
            "underlying_price": round(underlying_price, 2),
            "atm_strike": int(atm_strike),
            "call_strike": int(atm_strike),
            "put_strike": int(atm_strike),
            "call_premium": round(underlying_price * 0.005, 2),  # ~0.5% of underlying
            "put_premium": round(underlying_price * 0.005, 2),
            "call_ltp": round(underlying_price * 0.005, 2),
            "put_ltp": round(underlying_price * 0.005, 2),
            "call_volume": 5000,
            "put_volume": 4500,
            "call_open_interest": 125000,
            "put_open_interest": 118000,
        }
    
    def validate_signal(
        self,
        signal: Dict,
        symbol: str
    ) -> bool:
        """
        Validate signal to avoid duplicates and noise
        
        Returns True if signal should be processed
        """
        signal_key = f"{symbol}:{signal['type'].value}"
        current_time = datetime.now().timestamp()
        
        # Check minimum interval between signals
        if signal_key in self.last_signal_time:
            time_diff = current_time - self.last_signal_time[signal_key]
            if time_diff < self.min_signal_interval:
                logger.debug(
                    f"⏭️  Signal {signal_key} throttled "
                    f"({time_diff:.0f}s < {self.min_signal_interval}s)"
                )
                return False
        
        self.last_signal_time[signal_key] = current_time
        return True
    
    def format_signal_for_telegram(
        self,
        signal: Dict,
        option_data: Dict,
        source: str = "SCREENER"
    ) -> Tuple[Dict, Dict]:
        """
        Format signal data for Telegram alert
        
        Returns:
            (signal_data, option_data) formatted for telegram_alerts_fixed.py
        """
        formatted_signal = {
            "symbol": signal.get("symbol"),
            "signal": signal["type"].value,
            "entry": signal.get("entry"),
            "stop_loss": signal.get("stop_loss"),
            "targets": signal.get("targets", []),
            "reference_timestamp": signal.get("reference_timestamp"),
            "breakout_timestamp": signal.get("breakout_timestamp"),
            "breakout_price": signal.get("breakout_price"),
            "reference_color": signal.get("reference_color"),
            "reference_high": signal.get("reference_high"),
            "reference_low": signal.get("reference_low"),
            "timeframe": "5-MIN BREAKOUT",
            "source": source,
            "signal_time_ist": datetime.now().strftime("%H:%M:%S"),
        }
        
        return formatted_signal, option_data
    
    def get_nifty_securities(self) -> Dict:
        """Get all NIFTY 50 related security IDs"""
        return {
            **self.NIFTY_SECURITIES,
            "stocks": self.NIFTY50_STOCKS
        }
    
    def get_banknifty_securities(self) -> Dict:
        """Get all BANKNIFTY related security IDs"""
        return self.BANKNIFTY_SECURITIES
    
    def get_commodity_securities(self) -> Dict:
        """Get all MCX commodity security IDs"""
        return self.MCX_SECURITIES
    
    @staticmethod
    def _timestamp_to_ist(timestamp: int | float | None) -> str:
        """Convert Unix timestamp to IST format (HH:MM:SS)"""
        if timestamp is None:
            return datetime.now().strftime("%H:%M:%S")
        
        try:
            dt = datetime.fromtimestamp(float(timestamp))
            # Adjust for IST (UTC+5:30)
            dt = dt.replace(hour=(dt.hour + 5) % 24, minute=(dt.minute + 30) % 60)
            return dt.strftime("%H:%M:%S")
        except Exception:
            return datetime.now().strftime("%H:%M:%S")


# Test function
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    processor = SignalProcessor()
    
    # Test 1: Security IDs
    print("\n" + "="*80)
    print("AVAILABLE SECURITIES")
    print("="*80)
    
    print("\n🔵 NIFTY 50:")
    for name, details in processor.get_nifty_securities().items():
        if isinstance(details, dict) and "security_id" in details:
            print(f"  {name}: {details['security_id']} ({details['description']})")
    
    print("\n🟣 BANKNIFTY:")
    for name, details in processor.get_banknifty_securities().items():
        if isinstance(details, dict) and "security_id" in details:
            print(f"  {name}: {details['security_id']} ({details['description']})")
    
    print("\n🟡 MCX COMMODITIES:")
    for name, details in processor.get_commodity_securities().items():
        if isinstance(details, dict) and "security_id" in details:
            print(f"  {name}: {details['security_id']} ({details['description']})")
    
    # Test 2: Breakout Detection
    print("\n" + "="*80)
    print("BREAKOUT DETECTION TEST")
    print("="*80)
    
    # Mock candle data
    test_candles = [
        {
            "timestamp": int((datetime.now() - timedelta(minutes=1)).timestamp()),
            "open": 18200.0,
            "high": 18250.0,
            "low": 18180.0,
            "close": 18240.0,
            "volume": 1000000
        },
        {
            "timestamp": int(datetime.now().timestamp()),
            "open": 18240.0,
            "high": 18280.0,
            "low": 18235.0,
            "close": 18275.0,
            "volume": 1200000
        }
    ]
    
    signal = processor.detect_breakout(test_candles, "NIFTY 50")
    if signal:
        print(f"\n✅ Signal Detected: {signal['type'].value}")
        print(f"   Entry: {signal['entry']}")
        print(f"   Targets: {signal['targets']}")
        print(f"   SL: {signal['stop_loss']}")
    
    # Test 3: Option Chain Data
    print("\n" + "="*80)
    print("OPTION CHAIN DATA")
    print("="*80)
    
    option_data = processor.get_option_chain_data(18275.0)
    print(f"\nUnderlying: {option_data['underlying_price']}")
    print(f"ATM Strike: {option_data['atm_strike']}")
    print(f"Call Premium: ₹{option_data['call_premium']}")
    print(f"Put Premium: ₹{option_data['put_premium']}")
    
    print("\n✅ SignalProcessor tests complete!")
