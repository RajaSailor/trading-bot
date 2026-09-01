import datetime

class OptionStrategy:
    def __init__(self, capital=100000):
        self.capital = capital
        self.open_trades = []
        self.daily_trades = 0
        self.daily_pnl = 0

        # Risk parameters
        self.max_trades = 10
        self.max_loss = -20000
        self.max_profit = 40000
        self.lot_size_index = 10
        self.entry_time = datetime.time(9, 20)
        self.exit_time = datetime.time(15, 30)

    def can_take_trade(self, premium, is_index=True):
        """Check if premium and capital allow trade"""
        if is_index:
            return premium >= 100 and self.daily_trades < self.max_trades and \
                   self.daily_pnl > self.max_loss and self.daily_pnl < self.max_profit
        else:
            return premium * self.lot_size_index <= 40000

    def entry_condition_call(self, spot, strike, prev_candle):
        """Call entry: spot & strike break prev 5m high, put below prev 5m low"""
        return spot > prev_candle['high'] and strike > prev_candle['high']

    def entry_condition_put(self, spot, strike, prev_candle):
        """Put entry: spot below prev 5m low, put strike break prev 5m high, call below prev 5m low"""
        return spot < prev_candle['low'] and strike > prev_candle['high']

    def place_trade(self, trade_type, entry_price):
        """Place trade with SL/TP"""
        trade = {
            "type": trade_type,
            "entry": entry_price,
            "stop_loss": entry_price - 10 if trade_type == "CALL" else entry_price + 10,
            "target": entry_price + 10 if trade_type == "CALL" else entry_price - 10,
            "status": "OPEN"
        }
        self.open_trades.append(trade)
        self.daily_trades += 1
        return trade

    def update_trades(self, current_price):
        """Update trades with trailing SL logic"""
        for trade in self.open_trades:
            if trade["status"] == "OPEN":
                if trade["type"] == "CALL":
                    if current_price >= trade["entry"] + 5:
                        trade["stop_loss"] = max(trade["stop_loss"], trade["entry"])  # move to breakeven
                        trade["stop_loss"] = current_price - 3  # trail every 5 points by 3
                else:  # PUT
                    if current_price <= trade["entry"] - 5:
                        trade["stop_loss"] = min(trade["stop_loss"], trade["entry"])
                        trade["stop_loss"] = current_price + 3

                # Exit conditions
                if current_price <= trade["stop_loss"] or current_price >= trade["target"]:
                    trade["status"] = "CLOSED"
                    pnl = (trade["target"] - trade["entry"]) * self.lot_size_index if trade["type"] == "CALL" \
                          else (trade["entry"] - trade["target"]) * self.lot_size_index
                    self.daily_pnl += pnl
