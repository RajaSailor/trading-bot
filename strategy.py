"""
5-MINUTE BREAKOUT STRATEGY - RED/GREEN CANDLE ANALYSIS
Identifies breakouts from previous RED/GREEN candles on 5-minute timeframe
Focuses on ATM strikes for INDEX OPTIONS and NIFTY 50 STOCKS OPTIONS
File: strategy.py
"""

class FiveMinBreakoutStrategy:
    """5-minute RED/GREEN candle breakout strategy with ATM strike focus"""
    
    def __init__(self):
        self.candles = {}  # Store candles by symbol
        self.signals = {}  # Store active signals
        self.last_triggered = {}  # Prevent duplicate signals
    
    def add_candle(self, symbol, open_price, high, low, close, oi, timestamp):
        """Add a new 5-minute candle to the data"""
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
        
        # Keep only last 5 candles for analysis (avoiding memory issues)
        if len(self.candles[symbol]) > 5:
            self.candles[symbol].pop(0)
    
    def get_atm_strike(self, ltp):
        """
        Calculate ATM (At-The-Money) strike price
        Standard rounding to nearest 100 for INDEX options
        Standard rounding to nearest 10-50 for STOCK options
        """
        # For INDEX options (NIFTY, BANKNIFTY, SENSEX): Round to nearest 100
        if ltp > 10000:
            # Large indices like NIFTY/SENSEX
            atm_strike = round(ltp / 100) * 100
        else:
            # BANKNIFTY or smaller
            atm_strike = round(ltp / 100) * 100
        
        return int(atm_strike)
    
    def calculate_premium(self, ltp, strike_distance_percent=1.5):
        """
        Estimate option premium based on LTP
        Typically 1-3% of strike price for ATM options
        """
        premium = ltp * (strike_distance_percent / 100)
        return round(premium, 2)
    
    def check_call_breakout(self, symbol, ltp=None):
        """
        Check if current price breaks above previous RED candle's high
        CALL (Bullish) Signal - ATM STRIKE
        
        Pattern: RED candle → GREEN breakout above RED high
        """
        if symbol not in self.candles or len(self.candles[symbol]) < 2:
            return False, None
        
        candles = self.candles[symbol]
        current = candles[-1]  # Latest candle
        previous = candles[-2]  # Previous candle
        
        # Prevent duplicate signals for same candle
        signal_key = f"{symbol}_CALL_{previous['timestamp']}"
        if signal_key in self.last_triggered:
            return False, None
        
        # Look for RED candle (close < open) followed by HIGHER close
        if not previous["is_bullish"]:  # Previous was RED (bearish)
            previous_high = previous["high"]
            current_high = current["high"]
            
            # Breakout condition: Current close > Previous high
            if current["close"] > previous_high and current["high"] >= previous_high:
                # Mark this signal as triggered
                self.last_triggered[signal_key] = True
                
                # Calculate ATM strike based on current price
                atm_strike = self.get_atm_strike(current["close"])
                
                # Premium (expected option price)
                premium = self.calculate_premium(atm_strike)
                
                # Entry at current market close
                entry = current["close"]
                
                # Calculate target and stop loss based on candle range
                candle_range = previous_high - previous["low"]
                target = entry + (candle_range * 1.5)  # 1.5x candle range
                stop_loss = previous["low"]
                
                signal_data = {
                    "symbol": symbol,
                    "buy_side": "CALL",
                    "strike_price": atm_strike,
                    "premium": premium,
                    "entry": round(entry, 2),
                    "target": round(target, 2),
                    "stop_loss": round(stop_loss, 2),
                    "current_price": round(current["close"], 2),
                    "timestamp": current["timestamp"],
                    "candle_range": round(candle_range, 2),
                    "breakout_type": "RED_TO_GREEN",
                    "timeframe": "5-MIN"
                }
                
                return True, signal_data
        
        return False, None
    
    def check_put_breakout(self, symbol, ltp=None):
        """
        Check if current price breaks below previous GREEN candle's low
        PUT (Bearish) Signal - ATM STRIKE
        
        Pattern: GREEN candle → RED breakdown below GREEN low
        """
        if symbol not in self.candles or len(self.candles[symbol]) < 2:
            return False, None
        
        candles = self.candles[symbol]
        current = candles[-1]  # Latest candle
        previous = candles[-2]  # Previous candle
        
        # Prevent duplicate signals for same candle
        signal_key = f"{symbol}_PUT_{previous['timestamp']}"
        if signal_key in self.last_triggered:
            return False, None
        
        # Look for GREEN candle (close >= open) followed by LOWER close
        if previous["is_bullish"]:  # Previous was GREEN (bullish)
            previous_low = previous["low"]
            current_low = current["low"]
            
            # Breakout condition: Current close < Previous low
            if current["close"] < previous_low and current["low"] <= previous_low:
                # Mark this signal as triggered
                self.last_triggered[signal_key] = True
                
                # Calculate ATM strike based on current price
                atm_strike = self.get_atm_strike(current["close"])
                
                # Premium (expected option price)
                premium = self.calculate_premium(atm_strike)
                
                # Entry at current market close
                entry = current["close"]
                
                # Calculate target and stop loss based on candle range
                candle_range = previous["high"] - previous_low
                target = entry - (candle_range * 1.5)  # 1.5x candle range down
                stop_loss = previous["high"]
                
                signal_data = {
                    "symbol": symbol,
                    "buy_side": "PUT",
                    "strike_price": atm_strike,
                    "premium": premium,
                    "entry": round(entry, 2),
                    "target": round(target, 2),
                    "stop_loss": round(stop_loss, 2),
                    "current_price": round(current["close"], 2),
                    "timestamp": current["timestamp"],
                    "candle_range": round(candle_range, 2),
                    "breakout_type": "GREEN_TO_RED",
                    "timeframe": "5-MIN"
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
        
        # Clear triggered signals for this symbol
        keys_to_delete = [k for k in self.last_triggered.keys() if k.startswith(symbol)]
        for key in keys_to_delete:
            del self.last_triggered[key]

