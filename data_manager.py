from __future__ import annotations

import csv
import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import requests

logger = logging.getLogger(__name__)


@dataclass
class Instrument:
    """
    Instrument definition with all required DhanHQ API parameters.
    
    Fields:
    - symbol: Trading symbol (e.g., "NIFTY", "GOLD", "RELIANCE")
    - security_id: Dhan security ID for this instrument
    - exchange: Exchange code (NSE_FNO, NSE_EQ, MCX, BSE_FNO, etc.)
    - exchange_segment: Exchange segment for API (NSE_FNO, NSE_EQ, MCX, BSE_FNO, etc.)
    - instrument_type: Instrument type - "EQUITY", "FUTIDX", "FUTCOM", "OPTIDX", "OPTSTK", etc.
    - category: Internal category for grouping (index_options, commodity_options, etc.)
    - data_source: Where to fetch from (dhan_primary, tradingview_primary)
    - tradingview_symbol: Symbol on TradingView (if applicable)
    """
    symbol: str
    security_id: Optional[int]
    exchange: str
    exchange_segment: str
    instrument_type: str
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
        self._webhook_cache: Dict[Tuple[str, str], dict] = {}
        self._webhook_lock = threading.Lock()
        self._tv = None
        self._tv_interval = None
        self._dhan_client = None
        self._instrument_universe = self._build_instrument_universe()

    def get_instruments(self) -> Dict[str, List[Instrument]]:
        return self._instrument_universe

    def fetch_dhanhq_candles(self, symbol: str, interval: str) -> List[dict]:
        """
        Fetch intraday candles from DhanHQ using REST API directly.
        Uses POST /v2/charts/historical endpoint.
        
        DhanHQ API: POST /v2/charts/historical
        Required params: securityId, exchangeSegment, instrument, fromDate, toDate, expiryCode, oi
        
        CRITICAL PARAMETERS:
        - securityId: String (e.g., "565901" for GOLD)
        - exchangeSegment: "NSE_EQ", "NSE_FNO", "MCX", "BSE_EQ", "BSE_FNO", "IDX_I"
        - instrument: "EQUITY", "FUTIDX", "OPTIDX", "FUTCOM", "OPTSTK", etc.
        - expiryCode: -1 for current month futures, or specific code from contract file
        - oi: false (boolean)
        """
        cache_key = (f"dhan:{symbol}", interval)
        cached = self._read_cache(cache_key)
        if cached is not None:
            return cached

        instrument = self._lookup_instrument(symbol)
        if not instrument or instrument.security_id is None:
            logger.debug(f"Instrument {symbol} not found or missing security_id")
            return self._get_webhook_candle_fallback(symbol, interval, cache_key=cache_key)

        try:
            # Convert interval string (e.g., "5min") to numeric value
            interval_value = int(interval.replace("min", ""))
            
            # Get today's date for historical data fetching
            today = datetime.now().strftime("%Y-%m-%d")
            
            logger.debug(
                f"🔍 Fetching DhanHQ candles: symbol={symbol}, sec_id={instrument.security_id}, "
                f"exchange_segment={instrument.exchange_segment}, instrument={instrument.instrument_type}, "
                f"interval={interval_value}min"
            )
            
            # Make direct REST API call to DhanHQ historical endpoint
            candles = self._fetch_dhan_historical_data(
                security_id=str(instrument.security_id),
                exchange_segment=instrument.exchange_segment,
                instrument=instrument.instrument_type,
                from_date=today,
                to_date=today,
                expiry_code=-1,  # Current month for futures
            )
            
            if not candles:
                logger.debug(f"⚠️ No candles returned from DhanHQ for {symbol}")
                return self._get_webhook_candle_fallback(symbol, interval, cache_key=cache_key)
            
            self._write_cache(cache_key, candles)
            logger.info(f"✅ DhanHQ fetch successful for {symbol}: {len(candles)} candles ({interval_value}min)")
            return candles
            
        except Exception as exc:
            logger.warning(f"⚠️ DhanHQ fetch failed for {symbol}: {exc}. Using TradingView fallback.")
            return self._get_webhook_candle_fallback(symbol, interval, cache_key=cache_key)

    def _fetch_dhan_historical_data(
        self,
        security_id: str,
        exchange_segment: str,
        instrument: str,
        from_date: str,
        to_date: str,
        expiry_code: int = -1,
    ) -> List[dict]:
        """
        Direct REST API call to DhanHQ POST /v2/charts/historical endpoint
        
        Args:
            security_id: DhanHQ security ID as string (e.g., "565901" for GOLD)
            exchange_segment: "NSE_EQ", "NSE_FNO", "MCX", "BSE_EQ", "BSE_FNO", "IDX_I"
            instrument: "INDEX", "EQUITY", "FUTIDX", "FUTCOM", "OPTIDX", "OPTSTK", etc.
            from_date: Start date (YYYY-MM-DD)
            to_date: End date (YYYY-MM-DD)
            expiry_code: -1 for current month, or specific code
            
        Returns:
            List of OHLCV candles in standardized format
            
        Reference: DhanHQ v2 API documentation
        https://api.dhan.co/v2/charts/historical
        """
        try:
            access_token = os.getenv("ACCESS_TOKEN")
            if not access_token:
                logger.error("❌ ACCESS_TOKEN not found in environment")
                return []
            
            # ✅ CORRECT PAYLOAD - Matches DhanHQ v2 API specification exactly
            payload = {
                "securityId": security_id,        # String format
                "exchangeSegment": exchange_segment,  # NSE_EQ, NSE_FNO, MCX, BSE_EQ, etc.
                "instrument": instrument,         # EQUITY, FUTIDX, FUTCOM, etc.
                "fromDate": from_date,            # YYYY-MM-DD
                "toDate": to_date,                # YYYY-MM-DD
                "expiryCode": expiry_code,        # -1 for current month
                "oi": False,                      # Don't fetch open interest
            }
            
            # ✅ CORRECT HEADERS
            url = "https://api.dhan.co/v2/charts/historical"
            headers = {
                "access-token": access_token,
                "Content-Type": "application/json",
            }
            
            # Make the POST request
            logger.debug(f"📡 POST {url} with payload: {payload}")
            response = requests.post(url, json=payload, headers=headers, timeout=10)
            
            # Check HTTP status
            if response.status_code != 200:
                error_data = response.json() if response.text else {}
                error_msg = error_data.get("errorMessage", response.text)
                error_code = error_data.get("errorCode", "UNKNOWN")
                logger.error(
                    f"❌ DhanHQ API error ({response.status_code}, {error_code}): {error_msg}\n"
                    f"   Payload was: {payload}"
                )
                return []
            
            # Parse JSON response
            data = response.json()
            
            # Validate response structure
            if not data or "open" not in data or "close" not in data:
                logger.warning(
                    f"⚠️ Invalid response structure from DhanHQ\n"
                    f"   Expected: open[], high[], low[], close[], volume[], timestamp[]\n"
                    f"   Got: {list(data.keys()) if isinstance(data, dict) else type(data)}"
                )
                return []
            
            # Extract OHLCV arrays from response
            opens = data.get("open", [])
            highs = data.get("high", [])
            lows = data.get("low", [])
            closes = data.get("close", [])
            volumes = data.get("volume", [])
            timestamps = data.get("timestamp", [])
            
            if not opens:
                logger.warning(f"⚠️ No candle data in DhanHQ response (empty arrays)")
                return []
            
            # Convert arrays to candle objects
            candles = []
            for i in range(len(opens)):
                try:
                    candle = {
                        "open": float(opens[i]) if i < len(opens) else 0,
                        "high": float(highs[i]) if i < len(highs) else 0,
                        "low": float(lows[i]) if i < len(lows) else 0,
                        "close": float(closes[i]) if i < len(closes) else 0,
                        "volume": float(volumes[i]) if i < len(volumes) else 0,
                        "timestamp": timestamps[i] if i < len(timestamps) else 0,
                    }
                    
                    # Validate OHLC logic (no negative prices, high >= low, etc.)
                    if (candle["high"] >= candle["low"] >= 0 and 
                        candle["high"] >= candle["open"] >= candle["low"] >= 0 and
                        candle["high"] >= candle["close"] >= candle["low"] >= 0):
                        candles.append(candle)
                    else:
                        logger.debug(f"⚠️ Invalid candle skipped: {candle}")
                        
                except (ValueError, TypeError, KeyError) as e:
                    logger.debug(f"⚠️ Skipped candle {i}: {e}")
                    continue
            
            logger.info(f"✅ Successfully fetched {len(candles)} valid candles from DhanHQ")
            return candles
        
        except requests.exceptions.Timeout:
            logger.error("❌ DhanHQ API request timeout (10s)")
            return []
        except requests.exceptions.ConnectionError as e:
            logger.error(f"❌ Network error connecting to DhanHQ: {e}")
            return []
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ HTTP request failed: {e}")
            return []
        except Exception as e:
            logger.error(f"❌ Unexpected error fetching DhanHQ data: {e}", exc_info=True)
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
        candles = self.fetch_tradingview_candles(symbol, interval, os.getenv("TV_USERNAME", "Sailor_raja12390"))
        if candles:
            return candles
        return self._get_webhook_candle_fallback(symbol, interval, cache_key=(f"tv:{symbol}", interval))

    def record_webhook_signal(self, signal: dict) -> None:
        symbol = str(signal.get("ticker") or signal.get("symbol") or "").upper()
        if not symbol:
            return
        entry_price = float(signal.get("entry_price", signal.get("entry", 0.0)))
        current_price = float(signal.get("current_price", signal.get("reference_high", entry_price)))
        stop_loss = float(signal.get("stop_loss", signal.get("reference_low", entry_price)))
        high = max(entry_price, current_price, stop_loss)
        low = min(entry_price, current_price, stop_loss)
        interval = self._normalize_interval_label(str(signal.get("timeframe_code") or signal.get("timeframe") or ""))
        if not interval:
            return
        with self._webhook_lock:
            self._webhook_cache[(symbol, interval)] = {
                "ts": time.time(),
                "signal": signal,
                "interval": interval,
                "candles": [
                    {
                        "open": entry_price,
                        "high": high,
                        "low": low,
                        "close": current_price,
                        "timestamp": str(
                            signal.get("stored_at")
                            or signal.get("breakout_timestamp")
                            or datetime.now(timezone.utc).isoformat()
                        ),
                    }
                ],
            }

    def get_webhook_data_for_symbol(
        self,
        symbol: str,
        max_age_seconds: int = 1800,
        interval: Optional[str] = None,
    ) -> Optional[dict]:
        normalized_symbol = symbol.upper()
        normalized_interval = self._normalize_interval_label(interval or "")
        with self._webhook_lock:
            candidates = [
                (key, value)
                for key, value in self._webhook_cache.items()
                if key[0] == normalized_symbol and (not normalized_interval or key[1] == normalized_interval)
            ]
            if not candidates:
                return None
            candidates.sort(key=lambda item: item[1]["ts"], reverse=True)
            cache_key, entry = candidates[0]
            if time.time() - entry["ts"] > max_age_seconds:
                self._webhook_cache.pop(cache_key, None)
                return None
            return dict(entry)

    def is_tradingview_available(self, symbol: Optional[str] = None) -> bool:
        if symbol:
            return self.get_webhook_data_for_symbol(symbol) is not None
        with self._webhook_lock:
            keys = list(self._webhook_cache)
        return any(self.get_webhook_data_for_symbol(symbol_key, interval=interval_key) is not None for symbol_key, interval_key in keys)

    def _get_webhook_candle_fallback(
        self,
        symbol: str,
        interval: str,
        cache_key: Optional[Tuple[str, str]] = None,
    ) -> List[dict]:
        entry = self.get_webhook_data_for_symbol(symbol, interval=interval)
        if not entry:
            return []
        logger.info("📡 Using TradingView webhook fallback for %s on %s", symbol, interval)
        candles = entry["candles"]
        if cache_key is not None:
            self._write_cache(cache_key, candles)
        return candles

    @staticmethod
    def _normalize_interval_label(interval: str) -> str:
        normalized = str(interval).strip().lower()
        mapping = {
            "5-min": "5min",
            "5-minute breakout": "5min",
            "5-minute": "5min",
            "15-min": "15min",
            "15-minute breakout": "15min",
            "15-minute": "15min",
            "30-min": "30min",
            "30-minute breakout": "30min",
            "30-minute": "30min",
        }
        return mapping.get(normalized, normalized)

    def _build_instrument_universe(self) -> Dict[str, List[Instrument]]:
        """
        Build the complete instrument universe with CORRECT DhanHQ parameters.
        
        CRITICAL:
        - exchangeSegment: NSE_FNO, NSE_EQ, MCX (not MCX_COMM), BSE_FNO, IDX_I
        - instrument: EQUITY, FUTIDX, FUTCOM, OPTIDX, OPTSTK, etc.
        """
        stock_ids = self._load_nifty_stock_ids()

        def stock_instruments(category: str, source: str) -> List[Instrument]:
            return [
                Instrument(
                    symbol=symbol,
                    security_id=stock_ids.get(symbol),
                    exchange="NSE_EQ",
                    exchange_segment="NSE_EQ",
                    instrument_type="EQUITY",
                    category=category,
                    data_source=source,
                )
                for symbol in NIFTY_50_STOCKS
            ]

        return {
            "index_options": [
                Instrument(
                    symbol="NIFTY",
                    security_id=13,
                    exchange="NSE_FNO",
                    exchange_segment="NSE_FNO",
                    instrument_type="FUTIDX",  # ✅ Futures Index
                    category="index_options",
                    data_source="dhan_primary",
                ),
                Instrument(
                    symbol="BANKNIFTY",
                    security_id=25,
                    exchange="NSE_FNO",
                    exchange_segment="NSE_FNO",
                    instrument_type="OPTIDX",  # ✅ Options Index
                    category="index_options",
                    data_source="dhan_primary",
                ),
                Instrument(
                    symbol="SENSEX",
                    security_id=1,
                    exchange="BSE_FNO",
                    exchange_segment="BSE_FNO",
                    instrument_type="OPTIDX",
                    category="index_options",
                    data_source="dhan_primary",
                ),
            ],
            "nifty50_stock_options": stock_instruments("nifty50_stock_options", "dhan_primary"),
            "nifty50_intraday_5x": stock_instruments("nifty50_intraday_5x", "dhan_primary"),
            "nifty50_pay_later": stock_instruments("nifty50_pay_later", "dhan_primary"),
            "commodity_options": [
                Instrument(
                    symbol="GOLD",
                    security_id=565901,
                    exchange="MCX",
                    exchange_segment="MCX",  # ✅ FIXED: MCX not MCX_COMM
                    instrument_type="FUTCOM",  # ✅ Futures Commodity
                    category="commodity_options",
                    data_source="dhan_primary",
                ),
                Instrument(
                    symbol="SILVER",
                    security_id=565902,
                    exchange="MCX",
                    exchange_segment="MCX",  # ✅ FIXED: MCX not MCX_COMM
                    instrument_type="FUTCOM",
                    category="commodity_options",
                    data_source="dhan_primary",
                ),
                Instrument(
                    symbol="CRUDE OIL",
                    security_id=565899,
                    exchange="MCX",
                    exchange_segment="MCX",  # ✅ FIXED: MCX not MCX_COMM
                    instrument_type="FUTCOM",
                    category="commodity_options",
                    data_source="dhan_primary",
                ),
                Instrument(
                    symbol="NATURAL GAS",
                    security_id=565900,
                    exchange="MCX",
                    exchange_segment="MCX",  # ✅ FIXED: MCX not MCX_COMM
                    instrument_type="FUTCOM",
                    category="commodity_options",
                    data_source="dhan_primary",
                ),
            ],
            "crypto": [
                Instrument(
                    symbol="BTCUSD",
                    security_id=None,
                    exchange="CRYPTO",
                    exchange_segment="CRYPTO",
                    instrument_type="CRYPTO",
                    category="crypto",
                    data_source="tradingview_primary",
                    tradingview_symbol="BTCUSD",
                ),
                Instrument(
                    symbol="ETHUSD",
                    security_id=None,
                    exchange="CRYPTO",
                    exchange_segment="CRYPTO",
                    instrument_type="CRYPTO",
                    category="crypto",
                    data_source="tradingview_primary",
                    tradingview_symbol="ETHUSD",
                ),
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
