"""
5-MINUTE BREAKOUT STRATEGY - RED/GREEN CANDLE ANALYSIS
Identifies breakouts from previous RED/GREEN candles
File: strategy.py
"""

class FiveMinBreakoutStrategy:
    """5-minute RED/GREEN candle breakout strategy"""
    
    def __init__(self):
        self.candles = {}  # Store candles by symbol
        self.signals = {}  # Store active signals
    
    def add_candle(self, symbol, open_price, high, low, close, oi, timestamp):
        """Add a new candle to the data"""
        if symbol not in self.candles:
            self.candles[symbol] = []
        
        candle = {
            "open": float(open_price),
            "high": float(high),
            "low": float(low),
            "close": float(close),
            "oi": float(oi),
            "timestamp": timestamp,
            "is_bullish": close >= open_price  # Green if close >= open
        }
        
        self.candles[symbol].append(candle)
        
        # Keep only last 5 candles for analysis
        if len(self.candles[symbol]) > 5:
            self.candles[symbol].pop(0)
    
    def check_call_breakout(self, symbol):
        """
        Check if current price breaks above previous RED candle's high
        CALL (Bullish) Signal
        """
        if symbol not in self.candles or len(self.candles[symbol]) < 2:
            return False, None
        
        candles = self.candles[symbol]
        current = candles[-1]  # Latest candle
        previous = candles[-2]  # Previous candle
        
        # Look for RED candle (close < open) followed by HIGHER close
        if not previous["is_bullish"]:  # Previous was RED
            previous_high = previous["high"]
            current_high = current["high"]
            
            # Breakout condition: Current close > Previous high
            if current["close"] > previous_high and current["high"] >= previous_high:
                strike_price = previous_high
                entry = current["close"]
                
                # Calculate target and stop loss
                candle_range = previous_high - previous["low"]
                target = entry + (candle_range * 1.5)
                stop_loss = previous["low"]
                
                signal_data = {
                    "symbol": symbol,
                    "buy_side": "CALL/BUY",
                    "strike_price": round(strike_price, 2),
                    "entry": round(entry, 2),
                    "target": round(target, 2),
                    "stop_loss": round(stop_loss, 2),
                    "current_price": round(current["close"], 2),
                    "timestamp": current["timestamp"],
                }
                
                return True, signal_data
        
        return False, None
    
    def check_put_breakout(self, symbol):
        """
        Check if current price breaks below previous GREEN candle's low
        PUT (Bearish) Signal
        """
        if symbol not in self.candles or len(self.candles[symbol]) < 2:
            return False, None
        
        candles = self.candles[symbol]
        current = candles[-1]  # Latest candle
        previous = candles[-2]  # Previous candle
        
        # Look for GREEN candle (close >= open) followed by LOWER close
        if previous["is_bullish"]:  # Previous was GREEN
            previous_low = previous["low"]
            current_low = current["low"]
            
            # Breakout condition: Current close < Previous low
            if current["close"] < previous_low and current["low"] <= previous_low:
                strike_price = previous_low
                entry = current["close"]
                
                # Calculate target and stop loss
                candle_range = previous["high"] - previous_low
                target = entry - (candle_range * 1.5)
                stop_loss = previous["high"]
                
                signal_data = {
                    "symbol": symbol,
                    "buy_side": "PUT/SELL",
                    "strike_price": round(strike_price, 2),
                    "entry": round(entry, 2),
                    "target": round(target, 2),
                    "stop_loss": round(stop_loss, 2),
                    "current_price": round(current["close"], 2),
                    "timestamp": current["timestamp"],
                }
                
                return True, signal_data
        
        return False, None
    
    def get_candle_history(self, symbol, limit=5):
        """Get last N candles for a symbol"""
        if symbol not in self.candles:
            return []
        return self.candles[symbol][-limit:]
    
    def reset_symbol(self, symbol):
        """Clear data for a symbol"""
        if symbol in self.candles:
            self.candles[symbol] = []
        if symbol in self.signals:
            self.signals[symbol] = None
