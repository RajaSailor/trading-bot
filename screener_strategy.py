"""
SCREENER STRATEGY - CORE 5-MIN CANDLE LOGIC
Strategy module for breakout detection and alerts
"""

class ScreenerStrategy:
    """
    5-Min Candle Breakout Strategy
    Conditions:
    1. Close > Previous High (breakout)
    2. Volume/OI surge (liquidity)
    3. Price above 20-period average
    """
    
    def __init__(self, lookback_periods=20):
        self.lookback_periods = lookback_periods
        self.candle_history = {}
    
    def add_candle(self, symbol, open_price, high, low, close, oi, timestamp):
        """Add new 5-min candle to history"""
        if symbol not in self.candle_history:
            self.candle_history[symbol] = []
        
        candle = {
            "timestamp": timestamp,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "oi": oi
        }
        self.candle_history[symbol].append(candle)
        
        # Keep only last 20 candles
        if len(self.candle_history[symbol]) > self.lookback_periods:
            self.candle_history[symbol].pop(0)
    
    def get_average_price(self, symbol):
        """Calculate 20-period average price"""
        if symbol not in self.candle_history or len(self.candle_history[symbol]) < 2:
            return None
        
        prices = [c["close"] for c in self.candle_history[symbol]]
        return sum(prices) / len(prices)
    
    def check_breakout(self, symbol):
        """
        Check if current candle breaks out
        Returns: (alert_triggered, alert_message)
        """
        if symbol not in self.candle_history or len(self.candle_history[symbol]) < 2:
            return False, "Not enough data"
        
        candles = self.candle_history[symbol]
        current = candles[-1]
        previous = candles[-2]
        
        # Condition 1: Breakout (Close > Previous High)
        breakout = current["close"] > previous["high"]
        
        # Condition 2: OI Surge (Current OI > Previous OI by at least 5%)
        oi_surge = current["oi"] > previous["oi"] * 1.05 if previous["oi"] > 0 else False
        
        # Condition 3: Price above 20-period average
        avg_price = self.get_average_price(symbol)
        above_average = current["close"] > avg_price if avg_price else False
        
        # All conditions met
        if breakout and oi_surge and above_average:
            message = (
                f"🚀 BREAKOUT ALERT: {symbol}\n"
                f"  Open: {current['open']:.2f}\n"
                f"  High: {current['high']:.2f}\n"
                f"  Low: {current['low']:.2f}\n"
                f"  Close: {current['close']:.2f} ↗️ (Prev High: {previous['high']:.2f})\n"
                f"  OI: {current['oi']:.0f} (Surge: +{((current['oi']/previous['oi']-1)*100):.1f}%)\n"
                f"  Avg (20): {avg_price:.2f} ✓\n"
                f"  Time: {current['timestamp']}"
            )
            return True, message
        else:
            reason = []
            if not breakout:
                reason.append(f"No breakout (Close {current['close']:.2f} <= Prev High {previous['high']:.2f})")
            if not oi_surge:
                reason.append(f"No OI surge (Current {current['oi']:.0f} vs Prev {previous['oi']:.0f})")
            if not above_average:
                reason.append(f"Below average (Close {current['close']:.2f} vs Avg {avg_price:.2f})")
            
            return False, " | ".join(reason)
    
    def get_signal_summary(self, symbol):
        """Get current strategy signal for symbol"""
        if symbol not in self.candle_history or len(self.candle_history[symbol]) == 0:
            return None
        
        candles = self.candle_history[symbol]
        current = candles[-1]
        
        return {
            "symbol": symbol,
            "close": current["close"],
            "oi": current["oi"],
            "timestamp": current["timestamp"],
            "candle_count": len(candles)
        }
