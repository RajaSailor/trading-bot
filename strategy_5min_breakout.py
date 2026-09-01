"""
5-MINUTE CANDLE BREAKOUT STRATEGY
Complete strategy implementation with your exact conditions
File: strategy_5min_breakout.py
"""

class FiveMinBreakoutStrategy:
    """
    5-Minute Candle Breakout Strategy
    
    CALL SIDE (Buy Call):
    - Previous candle is RED
    - Current candle is GREEN
    - Current Close > Previous Red High (BREAKOUT)
    - Select Call strike with premium ≤ 100
    
    PUT SIDE (Buy Put):
    - Previous candle is GREEN
    - Current candle is RED
    - Current Close < Previous Green Low (BREAKOUT DOWN)
    - Select Put strike with premium ≤ 100
    
    Entry: Breakout level (High for Call, Low for Put)
    Stop Loss: Previous candle low (for Call) / high (for Put)
    Target: Entry ± 5 points
    """
    
    def __init__(self, lookback_periods=20):
        self.lookback_periods = lookback_periods
        self.candle_history = {}
    
    def add_candle(self, symbol, open_price, high, low, close, oi, timestamp):
        """Add new 5-min candle to history"""
        if symbol not in self.candle_history:
            self.candle_history[symbol] = []
        
        # Determine candle color
        candle_color = "GREEN" if close >= open_price else "RED"
        
        candle = {
            "timestamp": timestamp,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "oi": oi,
            "color": candle_color
        }
        self.candle_history[symbol].append(candle)
        
        # Keep only last 20 candles
        if len(self.candle_history[symbol]) > self.lookback_periods:
            self.candle_history[symbol].pop(0)
    
    def check_call_breakout(self, symbol, available_strikes=None):
        """
        Check CALL BUYING conditions:
        - Previous candle RED
        - Current candle GREEN
        - Current Close > Previous Red High
        
        Returns: (signal_triggered, signal_data)
        """
        if symbol not in self.candle_history or len(self.candle_history[symbol]) < 2:
            return False, None
        
        candles = self.candle_history[symbol]
        current = candles[-1]
        previous = candles[-2]
        
        # Condition 1: Previous candle is RED
        prev_is_red = previous["color"] == "RED"
        
        # Condition 2: Current candle is GREEN
        curr_is_green = current["color"] == "GREEN"
        
        # Condition 3: Close breaks previous high
        breakout = current["close"] > previous["high"]
        
        if prev_is_red and curr_is_green and breakout:
            # Find best Call strike (premium ≤ 100)
            best_strike = self.select_call_strike(available_strikes)
            
            entry_level = previous["high"]
            stop_loss = previous["low"]
            target = entry_level + 5
            
            signal_data = {
                "signal_type": "CALL",
                "buy_side": "BUY CALL",
                "strike_price": best_strike,
                "entry": round(entry_level, 2),
                "stop_loss": round(stop_loss, 2),
                "target": round(target, 2),
                "breakout_level": round(previous["high"], 2),
                "current_price": round(current["close"], 2),
                "timestamp": current["timestamp"]
            }
            
            return True, signal_data
        
        return False, None
    
    def check_put_breakout(self, symbol, available_strikes=None):
        """
        Check PUT BUYING conditions:
        - Previous candle GREEN
        - Current candle RED
        - Current Close < Previous Green Low
        
        Returns: (signal_triggered, signal_data)
        """
        if symbol not in self.candle_history or len(self.candle_history[symbol]) < 2:
            return False, None
        
        candles = self.candle_history[symbol]
        current = candles[-1]
        previous = candles[-2]
        
        # Condition 1: Previous candle is GREEN
        prev_is_green = previous["color"] == "GREEN"
        
        # Condition 2: Current candle is RED
        curr_is_red = current["color"] == "RED"
        
        # Condition 3: Close breaks previous low
        breakout_down = current["close"] < previous["low"]
        
        if prev_is_green and curr_is_red and breakout_down:
            # Find best Put strike (premium ≤ 100)
            best_strike = self.select_put_strike(available_strikes)
            
            entry_level = previous["low"]
            stop_loss = previous["high"]
            target = entry_level - 5
            
            signal_data = {
                "signal_type": "PUT",
                "buy_side": "BUY PUT",
                "strike_price": best_strike,
                "entry": round(entry_level, 2),
                "stop_loss": round(stop_loss, 2),
                "target": round(target, 2),
                "breakout_level": round(previous["low"], 2),
                "current_price": round(current["close"], 2),
                "timestamp": current["timestamp"]
            }
            
            return True, signal_data
        
        return False, None
    
    def select_call_strike(self, available_strikes=None):
        """Select Call strike with premium ≤ 100 (nearest to ATM)"""
        if not available_strikes:
            return "ATM"  # Default to ATM
        
        # Filter strikes with premium ≤ 100
        valid_strikes = [s for s in available_strikes if s.get("premium", 0) <= 100]
        
        if valid_strikes:
            # Return closest to ATM
            return valid_strikes[0].get("strike_price", "ATM")
        
        return "ATM"
    
    def select_put_strike(self, available_strikes=None):
        """Select Put strike with premium ≤ 100 (nearest to ATM)"""
        if not available_strikes:
            return "ATM"  # Default to ATM
        
        # Filter strikes with premium ≤ 100
        valid_strikes = [s for s in available_strikes if s.get("premium", 0) <= 100]
        
        if valid_strikes:
            # Return closest to ATM
            return valid_strikes[0].get("strike_price", "ATM")
        
        return "ATM"
    
    def get_current_candle(self, symbol):
        """Get current candle data"""
        if symbol not in self.candle_history or len(self.candle_history[symbol]) == 0:
            return None
        
        return self.candle_history[symbol][-1]
    
    def get_previous_candle(self, symbol):
        """Get previous candle data"""
        if symbol not in self.candle_history or len(self.candle_history[symbol]) < 2:
            return None
        
        return self.candle_history[symbol][-2]
