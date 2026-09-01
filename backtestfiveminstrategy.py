import os
import datetime
import pandas as pd
from dotenv import load_dotenv
from dhanhq import DhanContext, dhanhq
from fiveminstrategy import OptionStrategy

load_dotenv()
API_KEY = os.getenv("API_KEY")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")

context = DhanContext(API_KEY, ACCESS_TOKEN)
dhanhq_client = dhanhq(context)

INDEX_IDS = {
    "NIFTY": 543388,
    "BANKNIFTY": 25258,
}

class Backtester:
    def __init__(self, capital=100000):
        self.strategy = OptionStrategy(capital=capital)
        self.trades = []

    def get_security_id(self, symbol: str):
        sid = INDEX_IDS.get(symbol.upper())
        if not sid:
            raise ValueError(f"Index {symbol} not supported")
        return sid

    def get_intraday(self, security_id, from_date, to_date):
        """Try intraday, fallback to daily if empty"""
        resp = dhanhq_client.intraday_minute_data(
            security_id=security_id,
            exchange_segment="NSE_EQ",
            instrument_type="INDEX",
            interval="5",
            from_date=from_date,
            to_date=to_date
        )
        df = pd.DataFrame(resp.get("data", [])) if isinstance(resp, dict) else pd.DataFrame(resp)
        if df.empty:
            return self.get_daily(security_id, from_date, to_date)
        df["datetime"] = pd.to_datetime(df.get("timestamp", df.get("datetime")), errors="coerce")
        return df.sort_values("datetime").reset_index(drop=True)

    def get_daily(self, security_id, from_date, to_date):
        """Fetch daily candles if intraday unavailable"""
        resp = dhanhq_client.historical_daily_data(
            security_id=security_id,
            exchange_segment="NSE_EQ",
            instrument_type="INDEX",
            from_date=from_date,
            to_date=to_date
        )
        df = pd.DataFrame(resp.get("data", [])) if isinstance(resp, dict) else pd.DataFrame(resp)
        if df.empty:
            return pd.DataFrame()
        df["datetime"] = pd.to_datetime(df.get("timestamp", df.get("date")), errors="coerce")
        return df.sort_values("datetime").reset_index(drop=True)

    def get_all_expiries(self, symbol):
        """Fetch all valid expiries for the symbol"""
        chain = dhanhq_client.option_chain(symbol)
        return sorted({c["expiryDate"] for c in chain["data"]})

    def get_option_chain(self, symbol, expiry_date):
        chain = dhanhq_client.option_chain(symbol, expiry_date)
        return pd.DataFrame(chain.get("data", []))

    def select_strikes(self, chain_df, spot_price):
        atm = min(chain_df["strikePrice"], key=lambda x: abs(x - spot_price))
        return atm, atm - 100, atm + 100

    def run(self, symbol, from_date, to_date):
        sec_id = self.get_security_id(symbol)
        spot_df = self.get_intraday(sec_id, from_date, to_date)
        if spot_df.empty:
            print("⚠️ No data available for given range (holiday or outside retention). Skipping.")
            return [], 0

        expiries = self.get_all_expiries(symbol)
        for expiry in expiries:
            chain_df = self.get_option_chain(symbol, expiry)
            if chain_df.empty:
                continue

            spot_price = float(spot_df.iloc[0].get("close", spot_df.iloc[0].get("last", 0)))
            atm, itm, otm = self.select_strikes(chain_df, spot_price)

            for i in range(1, len(spot_df)):
                candle = spot_df.iloc[i]
                prev_candle = spot_df.iloc[i - 1]
                t = candle["datetime"].time()
                if t < datetime.time(9, 20) or t > datetime.time(15, 30):
                    continue
                price = candle.get("close", candle.get("last", None))
                if price is None:
                    continue
                if self.strategy.entry_condition_call(price, atm, prev_candle):
                    trade = self.strategy.place_trade("CALL", atm)
                    self.trades.append({**trade, "time": candle["datetime"], "expiry": expiry})
                elif self.strategy.entry_condition_put(price, atm, prev_candle):
                    trade = self.strategy.place_trade("PUT", atm)
                    self.trades.append({**trade, "time": candle["datetime"], "expiry": expiry})
                self.strategy.update_trades(price)

        return self.trades, getattr(self.strategy, "daily_pnl", None)

    def get_journal(self):
        return pd.DataFrame(self.trades)


if __name__ == "__main__":
    backtester = Backtester(capital=100000)
    symbol = "NIFTY"
    from_date = "2021-01-01"
    to_date = "2026-01-01"

    trades, pnl = backtester.run(symbol, from_date, to_date)
    print("Total Trades:", len(trades))
    print("Final PnL:", pnl)
    journal = backtester.get_journal()
    print(journal.head())
