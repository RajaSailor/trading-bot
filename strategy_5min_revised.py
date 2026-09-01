"""
5-MINUTE CANDLE BREAKOUT STRATEGY - REVISED
Track most recent RED/GREEN candles and trigger on breakouts
File: strategy_5min_revised.py
"""

class FiveMinBreakoutStrategyRevised:
    """
    Revised 5-Minute Candle Breakout Strategy
    
    CALL SIDE:
    - Track most recent RED candle (any previous red in session)
    - When ANY GREEN candle breaks the high of this RED → CALL SIGNAL
    - Entry: RED candle high
    - Stop Loss: RED candle low
    - Target: Entry + 5
    
    PUT SIDE:
    - Track most recent GREEN candle (any previous green in session)
    - When ANY RED candle breaks the low of this GREEN → PUT SIGNAL
    - Entry: GREEN candle low
    - Stop Loss: GREEN candle high
    - Target: Entry - 5
    """
    
    def __init__(self):
        self.candle_history = {}
        self.most_recent_red = {}
        self.most_recent_green = {}
        self.triggered_signals = set()  # Prevent duplicate signals
    
    def add_candle(self, symbol, open_price, high, low, close, oi, timestamp):
        """Add new 5-min candle to history and track RED/GREEN"""
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
        
        # Update most recent RED candle
        if candle_color == "RED":
            self.most_recent_red[symbol] = candle
        
        # Update most recent GREEN candle
        if candle_color == "GREEN":
            self.most_recent_green[symbol] = candle
    
    def check_call_breakout(self, symbol, available_strikes=None):
        """
        Check CALL BUYING conditions:
        - Most recent RED candle exists
        - Current candle is GREEN
        - Current Close > Most Recent RED High (BREAKOUT)
        
        Returns: (signal_triggered, signal_data)
        """
        if symbol not in self.candle_history or len(self.candle_history[symbol]) == 0:
            return False, None
        
        # Must have a most recent red candle
        if symbol not in self.most_recent_red:
            return False, None
        
        current = self.candle_history[symbol][-1]
        previous_red = self.most_recent_red[symbol]
        
        # Condition 1: Current candle is GREEN
        curr_is_green = current["color"] == "GREEN"
        
        # Condition 2: Close breaks previous red high
        breakout = current["close"] > previous_red["high"]
        
        if curr_is_green and breakout:
            # Check if already signaled on this RED candle
            signal_key = f"{symbol}_CALL_{previous_red['timestamp']}"
            if signal_key in self.triggered_signals:
                return False, None
            
            self.triggered_signals.add(signal_key)
            
            # Find best Call strike (premium ≤ 100)
            best_strike = self.select_call_strike(available_strikes)
            
            entry_level = previous_red["high"]
            stop_loss = previous_red["low"]
            target = entry_level + 5
            
            signal_data = {
                "signal_type": "CALL",
                "buy_side": "BUY CALL",
                "strike_price": best_strike,
                "entry": round(entry_level, 2),
                "stop_loss": round(stop_loss, 2),
                "target": round(target, 2),
                "breakout_level": round(previous_red["high"], 2),
                "current_price": round(current["close"], 2),
                "timestamp": current["timestamp"],
                "red_candle_time": previous_red["timestamp"]
            }
            
            return True, signal_data
        
        return False, None
    
    def check_put_breakout(self, symbol, available_strikes=None):
        """
        Check PUT BUYING conditions:
        - Most recent GREEN candle exists
        - Current candle is RED
        - Current Close < Most Recent GREEN Low (BREAKOUT DOWN)
        
        Returns: (signal_triggered, signal_data)
        """
        if symbol not in self.candle_history or len(self.candle_history[symbol]) == 0:
            return False, None
        
        # Must have a most recent green candle
        if symbol not in self.most_recent_green:
            return False, None
        
        current = self.candle_history[symbol][-1]
        previous_green = self.most_recent_green[symbol]
        
        # Condition 1: Current candle is RED
        curr_is_red = current["color"] == "RED"
        
        # Condition 2: Close breaks previous green low
        breakout_down = current["close"] < previous_green["low"]
        
        if curr_is_red and breakout_down:
            # Check if already signaled on this GREEN candle
            signal_key = f"{symbol}_PUT_{previous_green['timestamp']}"
            if signal_key in self.triggered_signals:
                return False, None
            
            self.triggered_signals.add(signal_key)
            
            # Find best Put strike (premium ≤ 100)
            best_strike = self.select_put_strike(available_strikes)
            
            entry_level = previous_green["low"]
            stop_loss = previous_green["high"]
            target = entry_level - 5
            
            signal_data = {
                "signal_type": "PUT",
                "buy_side": "BUY PUT",
                "strike_price": best_strike,
                "entry": round(entry_level, 2),
                "stop_loss": round(stop_loss, 2),
                "target": round(target, 2),
                "breakout_level": round(previous_green["low"], 2),
                "current_price": round(current["close"], 2),
                "timestamp": current["timestamp"],
                "green_candle_time": previous_green["timestamp"]
            }
            
            return True, signal_data
        
        return False, None
    
    def select_call_strike(self, available_strikes=None):
        """Select Call strike with premium ≤ 100"""
        if not available_strikes:
            return "ATM"
        
        # Filter strikes with premium ≤ 100
        valid_strikes = [s for s in available_strikes if s.get("premium", 0) <= 100]
        
        if valid_strikes:
            return valid_strikes[0].get("strike_price", "ATM")
        
        return "ATM"
    
    def select_put_strike(self, available_strikes=None):
        """Select Put strike with premium ≤ 100"""
        if not available_strikes:
            return "ATM"
        
        # Filter strikes with premium ≤ 100
        valid_strikes = [s for s in available_strikes if s.get("premium", 0) <= 100]
        
        if valid_strikes:
            return valid_strikes[0].get("strike_price", "ATM")
        
        return "ATM"
    
    def get_most_recent_red(self, symbol):
        """Get most recent RED candle"""
        return self.most_recent_red.get(symbol)
    
    def get_most_recent_green(self, symbol):
        """Get most recent GREEN candle"""
        return self.most_recent_green.get(symbol)
    
    def get_current_candle(self, symbol):
        """Get current candle"""
        if symbol in self.candle_history and len(self.candle_history[symbol]) > 0:
            return self.candle_history[symbol][-1]
        return None
