"""
5-MINUTE BREAKOUT STRATEGY - RED/GREEN CANDLE ANALYSIS
Identifies breakouts from previous RED/GREEN candles on 5-minute timeframe
Focuses on ATM strikes for INDEX OPTIONS and NIFTY 50 STOCKS OPTIONS
File: strategy.py
"""

from atm_calculator import (
    calculate_atm_strikes,
    calculate_option_premium,
    get_premium_percent,
)


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
        
        # Keep only last 6 candles (current + 5 previous for analysis)
        if len(self.candles[symbol]) > 6:
            self.candles[symbol].pop(0)
    
    def get_atm_strike(self, ltp, symbol=None):
        """
        Calculate ATM (At-The-Money) strike price
        Standard rounding to nearest 100 for INDEX options
        Standard rounding to nearest 50 for STOCK options
        """
        atm_data = calculate_atm_strikes(symbol, ltp)
        return atm_data["atm_strike"] if atm_data else None
    
    def calculate_premium(self, ltp, volatility_percent=None):
        """
        Estimate option premium based on LTP
        1.5%-2.5% of LTP for ATM options based on volatility
        """
        premium_percent = get_premium_percent(volatility_percent)
        return calculate_option_premium(ltp, premium_percent), premium_percent

    def _get_previous_five_candles(self, symbol):
        """Return up to last 5 candles before current candle."""
        candles = self.candles.get(symbol, [])
        if len(candles) < 2:
            return []
        return candles[-6:-1]

    def _calculate_volatility_percent(self, candles):
        """Average candle range percentage."""
        if not candles:
            return 0.0
        ranges = []
        for candle in candles:
            close = candle.get("close", 0)
            if close and close > 0:
                ranges.append(((candle["high"] - candle["low"]) / close) * 100.0)
        if not ranges:
            return 0.0
        return round(sum(ranges) / len(ranges), 4)

    def _build_compared_candles(self, previous_candles):
        """
        Build comparable 1..5 previous candle details
        1 = immediate previous candle, 5 = fifth previous candle.
        """
        compared = []
        for idx, candle in enumerate(reversed(previous_candles), start=1):
            compared.append({
                "candle_number": idx,
                "timestamp": candle["timestamp"],
                "open": round(candle["open"], 2),
                "high": round(candle["high"], 2),
                "low": round(candle["low"], 2),
                "close": round(candle["close"], 2),
                "color": "GREEN" if candle["is_bullish"] else "RED",
            })
        return compared
    
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
        previous_candles = self._get_previous_five_candles(symbol)
        if not previous_candles:
            return False, None

        compared_candles = self._build_compared_candles(previous_candles)
        trigger_ltp = ltp if ltp and ltp > 0 else current["close"]
        atm_data = calculate_atm_strikes(symbol, trigger_ltp)
        if not atm_data:
            return False, None

        for candle_number, red_candle in enumerate(reversed(previous_candles), start=1):
            if red_candle["is_bullish"]:
                continue

            breakout_level = red_candle["high"]
            signal_key = f"{symbol}_CALL_{red_candle['timestamp']}_{round(breakout_level, 2)}"
            if signal_key in self.last_triggered:
                continue

            # Breakout condition: current candle breaks above high of a red candle
            if current["close"] > breakout_level and current["high"] >= breakout_level:
                self.last_triggered[signal_key] = True

                entry = current["close"]
                candle_range = breakout_level - red_candle["low"]
                target = entry + (candle_range * 1.5)
                stop_loss = red_candle["low"]

                volatility_percent = self._calculate_volatility_percent(previous_candles + [current])
                premium, premium_percent = self.calculate_premium(trigger_ltp, volatility_percent)

                signal_data = {
                    "symbol": symbol,
                    "buy_side": "CALL",
                    "strike_price": atm_data["call_strike"],
                    "atm_strike": atm_data["atm_strike"],
                    "call_strike": atm_data["call_strike"],
                    "put_strike": atm_data["put_strike"],
                    "strike_step": atm_data["step"],
                    "premium": premium,
                    "premium_percent": premium_percent,
                    "entry": round(entry, 2),
                    "target": round(target, 2),
                    "stop_loss": round(stop_loss, 2),
                    "current_price": round(current["close"], 2),
                    "timestamp": current["timestamp"],
                    "candle_range": round(candle_range, 2),
                    "breakout_level": round(breakout_level, 2),
                    "breakout_candle_number": candle_number,
                    "breakout_candle_color": "RED",
                    "breakout_candle_timestamp": red_candle["timestamp"],
                    "breakout_type": "RED_TO_GREEN",
                    "breakout_reason": (
                        f"Current candle closed above high of {candle_number} previous RED candle"
                    ),
                    "compared_candles": compared_candles,
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
        previous_candles = self._get_previous_five_candles(symbol)
        if not previous_candles:
            return False, None

        compared_candles = self._build_compared_candles(previous_candles)
        trigger_ltp = ltp if ltp and ltp > 0 else current["close"]
        atm_data = calculate_atm_strikes(symbol, trigger_ltp)
        if not atm_data:
            return False, None

        for candle_number, green_candle in enumerate(reversed(previous_candles), start=1):
            if not green_candle["is_bullish"]:
                continue

            breakout_level = green_candle["low"]
            signal_key = f"{symbol}_PUT_{green_candle['timestamp']}_{round(breakout_level, 2)}"
            if signal_key in self.last_triggered:
                continue

            # Breakdown condition: current candle breaks below low of a green candle
            if current["close"] < breakout_level and current["low"] <= breakout_level:
                self.last_triggered[signal_key] = True

                entry = current["close"]
                candle_range = green_candle["high"] - breakout_level
                target = entry - (candle_range * 1.5)
                stop_loss = green_candle["high"]

                volatility_percent = self._calculate_volatility_percent(previous_candles + [current])
                premium, premium_percent = self.calculate_premium(trigger_ltp, volatility_percent)

                signal_data = {
                    "symbol": symbol,
                    "buy_side": "PUT",
                    "strike_price": atm_data["put_strike"],
                    "atm_strike": atm_data["atm_strike"],
                    "call_strike": atm_data["call_strike"],
                    "put_strike": atm_data["put_strike"],
                    "strike_step": atm_data["step"],
                    "premium": premium,
                    "premium_percent": premium_percent,
                    "entry": round(entry, 2),
                    "target": round(target, 2),
                    "stop_loss": round(stop_loss, 2),
                    "current_price": round(current["close"], 2),
                    "timestamp": current["timestamp"],
                    "candle_range": round(candle_range, 2),
                    "breakout_level": round(breakout_level, 2),
                    "breakout_candle_number": candle_number,
                    "breakout_candle_color": "GREEN",
                    "breakout_candle_timestamp": green_candle["timestamp"],
                    "breakout_type": "GREEN_TO_RED",
                    "breakout_reason": (
                        f"Current candle closed below low of {candle_number} previous GREEN candle"
                    ),
                    "compared_candles": compared_candles,
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
