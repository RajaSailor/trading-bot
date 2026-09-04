from __future__ import annotations

import csv
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple


@dataclass
class Instrument:
    symbol: str
    security_id: Optional[int]
    exchange: str
    category: str
    data_source: str
    tradingview_symbol: Optional[str] = None


NIFTY_50_STOCKS = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK", "BAJAJ-AUTO", "BAJAJFINANCE",
    "BAJAJFINSV", "BEL", "BHARTIARTL", "BPCL", "BRITANNIA", "CIPLA", "COALINDIA", "DRREDDY",
    "EICHERMOT", "GRASIM", "HCLTECH", "HDFC", "HDFC BANK", "HDFC LIFE", "HINDALCO", "HINDUNILVR",
    "ICICIBANK", "INDIGO", "INFY", "ITC", "JIOFINANCE", "JSWSTEEL", "KOTAKBANK", "LT", "M&M",
    "MARUTI", "MAXHEALTH", "NESTLEIND", "NTPC", "ONGC", "POWERGRID", "RELIANCE", "SBILIFE", "SBIN",
    "SHRIRAMFIN", "SUNPHARMA", "TCS", "TECHM", "TATACONSUME", "TATAMOTORS", "TATASTEEL", "TRENT",
    "TITAN", "ULTRATECH", "WIPRO",
]

_STOCK_ALIASES = {
    "HDFC BANK": "HDFCBANK",
    "HDFC LIFE": "HDFCLIFE",
    "JIOFINANCE": "JIOFIN",
    "TATACONSUME": "TATACONSUM",
    "ULTRATECH": "ULTRACEMCO",
    "BAJAJFINANCE": "BAJFINANCE",
}

_STOCK_ID_OVERRIDES = {
    "HDFC": 1333,
    "TATAMOTORS": 3456,
}


def _normalize_stock_symbol(name: str) -> str:
    return _STOCK_ALIASES.get(name, name)


class DataManager:
    def __init__(self, cache_ttl_seconds: int = 8) -> None:
        self.cache_ttl_seconds = cache_ttl_seconds
        self._cache: Dict[Tuple[str, str], dict] = {}
        self._tv = None
        self._tv_interval = None
        self._dhan_client = None
        self._instrument_universe = self._build_instrument_universe()

    def get_instruments(self) -> Dict[str, List[Instrument]]:
        return self._instrument_universe

    def fetch_dhanhq_candles(self, symbol: str, interval: str) -> List[dict]:
        cache_key = (f"dhan:{symbol}", interval)
        cached = self._read_cache(cache_key)
        if cached is not None:
            return cached

        instrument = self._lookup_instrument(symbol)
        if not instrument or instrument.security_id is None:
            return []

        try:
            if self._dhan_client is None:
                self._dhan_client = self._create_dhan_client()
            if self._dhan_client is None:
                return []

            interval_value = int(interval.replace("min", ""))
            response = self._dhan_client.get_intraday_paracande(
                security_id=[instrument.security_id],
                exchange=instrument.exchange,
                exchange_tokens=[],
                interval=interval_value,
            )
            candles = self._normalize_dhan_response(response)
            self._write_cache(cache_key, candles)
            return candles
        except Exception:
            return []

    def fetch_tradingview_candles(self, symbol: str, interval: str, account: Optional[str] = None) -> List[dict]:
        cache_key = (f"tv:{symbol}", interval)
        cached = self._read_cache(cache_key)
        if cached is not None:
            return cached

        try:
            if self._tv is None or self._tv_interval is None:
                self._tv, self._tv_interval = self._create_tv_client(account)
            if self._tv is None or self._tv_interval is None:
                return []

            tv_symbol = symbol.replace("/", "")
            bars = self._tv.get_hist(
                symbol=tv_symbol,
                exchange="BINANCE" if tv_symbol in {"BTCUSD", "ETHUSD"} else "NSE",
                interval=self._tv_interval[interval],
                n_bars=30,
            )
            candles = []
            if bars is None:
                return candles
            for idx, row in bars.iterrows():
                candles.append(
                    {
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "timestamp": idx.isoformat(),
                    }
                )
            self._write_cache(cache_key, candles)
            return candles
        except Exception:
            return []

    def fetch_candles(self, instrument: Instrument, interval: str) -> List[dict]:
        symbol = instrument.symbol
        if instrument.data_source == "tradingview_primary":
            candles = self.fetch_tradingview_candles(symbol, interval, os.getenv("TV_USERNAME", "Sailor_raja12390"))
            if candles:
                return candles
            return self.fetch_dhanhq_candles(symbol, interval)

        candles = self.fetch_dhanhq_candles(symbol, interval)
        if candles:
            return candles
        return self.fetch_tradingview_candles(symbol, interval, os.getenv("TV_USERNAME", "Sailor_raja12390"))

    def _build_instrument_universe(self) -> Dict[str, List[Instrument]]:
        stock_ids = self._load_nifty_stock_ids()

        def stock_instruments(category: str, source: str) -> List[Instrument]:
            return [
                Instrument(
                    symbol=symbol,
                    security_id=stock_ids.get(symbol),
                    exchange="NSE",
                    category=category,
                    data_source=source,
                )
                for symbol in NIFTY_50_STOCKS
            ]

        return {
            "index_options": [
                Instrument("NIFTY", 13, "NSE_FNO", "index_options", "dhan_primary"),
                Instrument("BANKNIFTY", 25, "NSE_FNO", "index_options", "dhan_primary"),
                Instrument("SENSEX", 1, "BSE_FNO", "index_options", "dhan_primary"),
            ],
            "nifty50_stock_options": stock_instruments("nifty50_stock_options", "dhan_primary"),
            "nifty50_intraday_5x": stock_instruments("nifty50_intraday_5x", "dhan_primary"),
            "nifty50_pay_later": stock_instruments("nifty50_pay_later", "dhan_primary"),
            "commodity_options": [
                Instrument("GOLD", 565901, "MCX_FUT", "commodity_options", "dhan_primary"),
                Instrument("SILVER", 565902, "MCX_FUT", "commodity_options", "dhan_primary"),
                Instrument("CRUDE OIL", 565899, "MCX_FUT", "commodity_options", "dhan_primary"),
                Instrument("NATURAL GAS", 565900, "MCX_FUT", "commodity_options", "dhan_primary"),
            ],
            "crypto": [
                Instrument("BTCUSD", None, "CRYPTO", "crypto", "tradingview_primary", tradingview_symbol="BTCUSD"),
                Instrument("ETHUSD", None, "CRYPTO", "crypto", "tradingview_primary", tradingview_symbol="ETHUSD"),
            ],
        }

    def _load_nifty_stock_ids(self) -> Dict[str, int]:
        ticker_to_name = {_normalize_stock_symbol(name): name for name in NIFTY_50_STOCKS}
        result: Dict[str, int] = {}
        csv_path = Path(__file__).with_name("security_list.csv")
        if csv_path.exists():
            with csv_path.open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    if row.get("SEM_EXM_EXCH_ID") != "NSE" or row.get("SEM_SEGMENT") != "E":
                        continue
                    if row.get("SEM_SERIES") != "EQ":
                        continue
                    ticker = row.get("SEM_TRADING_SYMBOL", "")
                    canonical_name = ticker_to_name.get(ticker)
                    if not canonical_name:
                        continue
                    result[canonical_name] = int(row["SEM_SMST_SECURITY_ID"])
                    if len(result) == len(NIFTY_50_STOCKS):
                        break

        for canonical_name, fallback_id in _STOCK_ID_OVERRIDES.items():
            result.setdefault(canonical_name, fallback_id)
        return result

    def _lookup_instrument(self, symbol: str) -> Optional[Instrument]:
        for instruments in self._instrument_universe.values():
            for instrument in instruments:
                if instrument.symbol == symbol:
                    return instrument
        return None

    def _read_cache(self, key: Tuple[str, str]) -> Optional[List[dict]]:
        item = self._cache.get(key)
        if not item:
            return None
        if time.time() - item["ts"] > self.cache_ttl_seconds:
            return None
        return item["candles"]

    def _write_cache(self, key: Tuple[str, str], candles: List[dict]) -> None:
        self._cache[key] = {"ts": time.time(), "candles": candles}

    def _create_dhan_client(self):
        try:
            from dhanhq import DhanContext, dhanhq

            client_id = os.getenv("API_KEY")
            access_token = os.getenv("ACCESS_TOKEN")
            if not client_id or not access_token:
                return None
            context = DhanContext(client_id=client_id, access_token=access_token)
            return dhanhq(context)
        except Exception:
            return None

    def _create_tv_client(self, account: Optional[str]):
        try:
            from tvDatafeed import Interval, TvDatafeed

            username = account or os.getenv("TV_USERNAME") or "Sailor_raja12390"
            tv_pass = os.getenv("TV_PASSWORD")
            tv = TvDatafeed(username, tv_pass) if tv_pass else TvDatafeed(username=username)
            interval_map = {
                "5min": Interval.in_5_minute,
                "15min": Interval.in_15_minute,
                "30min": Interval.in_30_minute,
            }
            return tv, interval_map
        except Exception:
            return None, None

    def _normalize_dhan_response(self, response: dict) -> List[dict]:
        data = response.get("data") if isinstance(response, dict) else []
        if not isinstance(data, list):
            return []
        candles = []
        for row in data[-30:]:
            candles.append(
                {
                    "open": float(row.get("open", 0.0)),
                    "high": float(row.get("high", 0.0)),
                    "low": float(row.get("low", 0.0)),
                    "close": float(row.get("close", 0.0)),
                    "timestamp": str(row.get("timestamp") or datetime.utcnow().isoformat()),
                }
            )
        return candles
