from __future__ import annotations

import csv
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import requests
from nse_symbol_mapping import NSESymbolMapper

try:
    from dhanhq import DhanContext, dhanhq
except ImportError:  # pragma: no cover - handled at runtime when SDK is unavailable
    DhanContext = None
    dhanhq = None

logger = logging.getLogger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))

# Global rate limiter to prevent DH-904 errors
_api_rate_limiter = {
    'last_call': 0,
    'min_interval': 1.0,  # 1 second between API calls
    'lock': threading.Lock()
}


def _apply_rate_limit() -> None:
    """Apply rate limiting between API calls to prevent DH-904 errors"""
    with _api_rate_limiter['lock']:
        current_time = time.time()
        time_since_last = current_time - _api_rate_limiter['last_call']
        
        if time_since_last < _api_rate_limiter['min_interval']:
            sleep_time = _api_rate_limiter['min_interval'] - time_since_last
            logger.debug(f"⏸️  Rate limiting: sleeping {sleep_time:.2f}s")
            time.sleep(sleep_time)
        
        _api_rate_limiter['last_call'] = time.time()


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
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK", "BAJAJ-AUTO", "BAJFINANCE",
    "BAJAJFINSV", "BEL", "BHARTIARTL", "BPCL", "BRITANNIA", "CIPLA", "COALINDIA", "DRREDDY",
    "EICHERMOT", "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE", "HINDALCO", "HINDUNILVR",
    "ICICIBANK", "INDIGO", "INFY", "ITC", "JIOFIN", "JSWSTEEL", "KOTAKBANK", "LT", "M&M",
    "MARUTI", "NESTLEIND", "NTPC", "ONGC", "POWERGRID", "RELIANCE", "SBILIFE", "SBIN",
    "SHRIRAMFIN", "SUNPHARMA", "TCS", "TECHM", "TATACONSUM", "TATAMOTORS", "TATASTEEL", "TRENT",
    "TITAN", "ULTRACEMCO", "WIPRO",
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


class DhanAPIClient:
    """DhanHQ API Client for fetching market data"""
    
    def __init__(self, access_token: str):
        self.access_token = access_token
        self.base_url = "https://api.dhan.co"
        self.headers = {
            "access-token": access_token,
            "Content-Type": "application/json"
        }
    
    def fetch_dhanhq_candles(
        self,
        security_id: str,
        exchange_segment: str,
        instrument: str,
        from_date: str,
        to_date: str,
        interval: str = "5"
    ) -> List[dict]:
        """
        Fetch OHLC candles from DhanHQ
        
        Args:
            security_id: DhanHQ security ID
            exchange_segment: NSE_EQ, NSE_FNO, MCX, etc.
            instrument: EQUITY, FUTIDX, FUTCOM, etc.
            from_date: Start date (YYYY-MM-DD)
            to_date: End date (YYYY-MM-DD)
            interval: Interval in minutes ("5", "15", etc.)
            
        Returns:
            List of candles with OHLCV data
        """
        try:
            payload = {
                "securityId": security_id,
                "exchangeSegment": exchange_segment,
                "instrument": instrument,
                "fromDate": from_date,
                "toDate": to_date,
                "expiryCode": -1,
                "oi": False
            }
            
            response = requests.post(
                f"{self.base_url}/v2/charts/historical",
                json=payload,
                headers=self.headers,
                timeout=10
            )
            
            if response.status_code != 200:
                logger.error(f"DhanHQ API error: {response.status_code} - {response.text}")
                return []
            
            data = response.json()
            if not data or "open" not in data:
                return []
            
            # Convert arrays to candle objects
            candles = []
            opens = data.get("open", [])
            highs = data.get("high", [])
            lows = data.get("low", [])
            closes = data.get("close", [])
            volumes = data.get("volume", [])
            timestamps = data.get("timestamp", [])
            
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
                    candles.append(candle)
                except Exception as e:
                    logger.debug(f"Error parsing candle {i}: {e}")
                    continue
            
            return candles
            
        except Exception as e:
            logger.error(f"Error fetching candles: {e}")
            return []
    
    def quote_data(self, securities: Dict) -> Dict:
        """Fetch quote data (LTP, bid/ask) for securities"""
        try:
            response = requests.post(
                f"{self.base_url}/v1/quotes/intraday",
                json=securities,
                headers=self.headers,
                timeout=10
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"Quote data error: {response.status_code}")
                return {}
                
        except Exception as e:
            logger.error(f"Error fetching quote data: {e}")
            return {}
    
    def place_order(self, order_data: Dict) -> Dict:
        """Place a new order"""
        try:
            response = requests.post(
                f"{self.base_url}/v1/orders/regular",
                json=order_data,
                headers=self.headers,
                timeout=10
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"Order placement error: {response.status_code}")
                return {}
                
        except Exception as e:
            logger.error(f"Error placing order: {e}")
            return {}
    
    def get_profile(self) -> Dict:
        """Get user profile information"""
        try:
            response = requests.get(
                f"{self.base_url}/v1/users/profile",
                headers=self.headers,
                timeout=10
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                return {}
                
        except Exception as e:
            logger.error(f"Error fetching profile: {e}")
            return {}


class DataManager:
    def __init__(self, cache_ttl_seconds: int = 8) -> None:
        self.cache_ttl_seconds = cache_ttl_seconds
        self._cache: Dict[Tuple[str, str], dict] = {}
        self._webhook_cache: Dict[Tuple[str, str], dict] = {}
        self._webhook_lock = threading.Lock()
        self._tv = None
        self._tv_interval = None
        self._dhan_client = None
        self._security_master_cache: List[Dict[str, Any]] = []
        self._security_master_cache_ts: float = 0.0
        self._security_master_cache_ttl_seconds: int = 3600
        self._instrument_universe = self._build_instrument_universe()

    def get_instruments(self) -> Dict[str, List[Instrument]]:
        return self._instrument_universe

    def fetch_dhanhq_candles(self, symbol: str, interval: str) -> List[dict]:
        """Fetch intraday candles from DhanHQ"""
        cache_key = (f"dhan:{symbol}", interval)
        cached = self._read_cache(cache_key)
        if cached is not None:
            return cached

        instrument = self._lookup_instrument(symbol)
        if not instrument:
            logger.debug(f"Instrument {symbol} not found")
            return []

        security_id = instrument.security_id
        if security_id is None:
            security_id = self._find_security_id(symbol, instrument.exchange_segment, instrument.instrument_type)
            if security_id is None:
                logger.error(f"Could not resolve security_id for {symbol}")
                return []

        try:
            interval_value = int(interval.replace("min", ""))
            today = datetime.now(IST).strftime("%Y-%m-%d")
            
            logger.debug(
                f"Fetching DhanHQ candles: symbol={symbol}, "
                f"security_id={security_id}, interval={interval_value}min"
            )
            
            # Apply rate limiting BEFORE API call
            _apply_rate_limit()
            
            candles = self._fetch_dhan_intraday_data(
                security_id=security_id,
                exchange_segment=instrument.exchange_segment,
                instrument_type=instrument.instrument_type,
                from_date=today,
                to_date=today,
                interval=interval_value,
                symbol=symbol  # Pass symbol for debug logging
            )
            
            if candles:
                self._write_cache(cache_key, candles)
                logger.info(f"✅ DhanHQ fetch successful for {symbol}: {len(candles)} candles")
                return candles
            
            return []
            
        except Exception as exc:
            logger.warning(f"Error fetching DhanHQ data: {exc}")
            return []

    def fetch_candles(self, instrument: Instrument, interval: str) -> List[dict]:
        """Unified candle fetching - routes to correct provider"""
        # Normalize interval format
        if interval.endswith("min"):
            interval_normalized = interval
        else:
            interval_normalized = f"{interval}min"
        
        # Route based on data source
        if instrument.data_source == "dhan_primary":
            return self.fetch_dhanhq_candles(instrument.symbol, interval_normalized)
        
        # Default: return empty if no provider configured
        logger.warning(f"No data source configured for {instrument.symbol}")
        return []

    def _fetch_dhan_intraday_data(
        self,
        security_id: int,
        exchange_segment: str,
        instrument_type: str,
        from_date: str,
        to_date: str,
        interval: int = 5,
        symbol: str = "UNKNOWN"
    ) -> List[dict]:
        """Fetch intraday candles via DhanHQ intraday endpoint."""
        try:
            access_token = os.getenv("ACCESS_TOKEN")
            if not access_token:
                logger.error("ACCESS_TOKEN not found")
                return []

            payload = {
                "securityId": str(security_id),          # ✅ STRING (critical!)
                "exchangeSegment": exchange_segment,     # ✅ e.g., "NSE_FNO"
                "instrument": instrument_type,           # ✅ e.g., "FUTIDX"
                "interval": interval,                    # ✅ INTEGER (5, 15, 30, 60, etc.)
                "fromDate": from_date,                   # ✅ "YYYY-MM-DD"
                "toDate": to_date,                       # ✅ "YYYY-MM-DD"
                "expiryCode": -1,                        # ✅ Current active contract (all supported segments)
                "oi": False                              # Optional: open interest
            }
            
            url = "https://api.dhan.co/v2/charts/intraday"
            headers = {
                "access-token": access_token,
                "Content-Type": "application/json",
            }
            
            logger.debug(f"[{symbol}] DhanHQ API request to /charts/intraday:")
            logger.debug(f"[{symbol}] Payload: {json.dumps(payload, indent=2)}")
            
            response = requests.post(url, json=payload, headers=headers, timeout=10)
            
            logger.debug(f"[{symbol}] Response status: {response.status_code}")
            logger.debug(f"[{symbol}] Response headers: {dict(response.headers)}")
            
            # CRITICAL DEBUG: Print raw response text
            logger.debug(f"[{symbol}] Raw response body (first 500 chars): {response.text[:500]}")
            
            if response.status_code != 200:
                logger.error(f"[{symbol}] DhanHQ API error: {response.status_code} - {response.text}")
                return []
            
            try:
                data = response.json()
            except json.JSONDecodeError as e:
                logger.error(f"[{symbol}] Failed to parse JSON response: {e}")
                logger.error(f"[{symbol}] Raw response: {response.text}")
                return []
            
            logger.debug(f"[{symbol}] Parsed JSON response structure:")
            logger.debug(f"[{symbol}] Response keys: {list(data.keys()) if isinstance(data, dict) else 'NOT A DICT'}")
            
            # Debug: Check if response is valid
            if not data:
                logger.warning(f"[{symbol}] Empty response from DhanHQ API")
                return []
            
            if isinstance(data, dict):
                logger.debug(f"[{symbol}] Response is dict with {len(data)} keys")
                for key, value in data.items():
                    if isinstance(value, list):
                        logger.debug(f"[{symbol}]   {key}: list with {len(value)} items")
                    else:
                        logger.debug(f"[{symbol}]   {key}: {type(value).__name__}")
            else:
                logger.warning(f"[{symbol}] Response is not a dict: {type(data).__name__}")
                return []
            
            # Check for expected keys
            if "open" not in data:
                logger.warning(f"[{symbol}] No 'open' key in response. Keys available: {list(data.keys())}")
                return []
            
            candles = []
            opens = data.get("open", [])
            highs = data.get("high", [])
            lows = data.get("low", [])
            closes = data.get("close", [])
            volumes = data.get("volume", [])
            timestamps = data.get("timestamp", [])
            
            logger.debug(f"[{symbol}] Candle arrays sizes: open={len(opens)}, high={len(highs)}, low={len(lows)}, close={len(closes)}, volume={len(volumes)}, timestamp={len(timestamps)}")
            
            for i in range(len(opens)):
                try:
                    candle = {
                        "open": float(opens[i]),
                        "high": float(highs[i]),
                        "low": float(lows[i]),
                        "close": float(closes[i]),
                        "volume": float(volumes[i]),
                        "timestamp": timestamps[i],
                    }
                    candles.append(candle)
                except Exception as e:
                    logger.debug(f"[{symbol}] Error parsing candle {i}: {e}")
                    continue
            
            logger.info(f"[{symbol}] ✅ Successfully parsed {len(candles)} intraday candles from DhanHQ")
            if candles:
                logger.debug(f"[{symbol}] First candle: {candles[0]}")
                logger.debug(f"[{symbol}] Last candle: {candles[-1]}")
            return candles
        
        except Exception as e:
            logger.error(f"[{symbol}] Error fetching intraday data: {e}", exc_info=True)
            return []

    def _build_instrument_universe(self) -> Dict[str, List[Instrument]]:
        """Build instrument universe"""
        stock_instruments = [
            Instrument(
                symbol=symbol,
                security_id=_STOCK_ID_OVERRIDES.get(symbol),
                exchange="NSE",
                exchange_segment="NSE_EQ",
                instrument_type="EQUITY",
                category="nifty50_stock_options",
                data_source="dhan_primary",
            )
            for symbol in NIFTY_50_STOCKS
        ]
        return {
            "index_options": [
                Instrument(
                    symbol="NIFTY",
                    security_id=None,
                    exchange="NSE_FNO",
                    exchange_segment="NSE_FNO",
                    instrument_type="FUTIDX",
                    category="index_options",
                    data_source="dhan_primary",
                ),
                Instrument(
                    symbol="BANKNIFTY",
                    security_id=None,
                    exchange="NSE_FNO",
                    exchange_segment="NSE_FNO",
                    instrument_type="FUTIDX",
                    category="index_options",
                    data_source="dhan_primary",
                ),
                Instrument(
                    symbol="SENSEX",
                    security_id=None,
                    exchange="BSE",
                    exchange_segment="BSE_FNO",
                    instrument_type="FUTIDX",
                    category="index_options",
                    data_source="dhan_primary",
                ),
            ],
            "commodity_options": [
                Instrument(
                    symbol="GOLD",
                    security_id=None,
                    exchange="MCX",
                    exchange_segment="MCX_COMM",
                    instrument_type="FUTCOM",
                    category="commodity_options",
                    data_source="dhan_primary",
                ),
                Instrument(
                    symbol="SILVER",
                    security_id=None,
                    exchange="MCX",
                    exchange_segment="MCX_COMM",
                    instrument_type="FUTCOM",
                    category="commodity_options",
                    data_source="dhan_primary",
                ),
                Instrument(
                    symbol="CRUDE OIL",
                    security_id=None,
                    exchange="MCX",
                    exchange_segment="MCX_COMM",
                    instrument_type="FUTCOM",
                    category="commodity_options",
                    data_source="dhan_primary",
                ),
                Instrument(
                    symbol="NATURALGAS",
                    security_id=None,
                    exchange="MCX",
                    exchange_segment="MCX_COMM",
                    instrument_type="FUTCOM",
                    category="commodity_options",
                    data_source="dhan_primary",
                ),
            ],
            "nifty50_stock_options": stock_instruments,
        }

    def _fetch_security_master_with_cache(self) -> List[Dict[str, Any]]:
        """Fetch and cache Dhan security master for dynamic security ID lookup."""
        now = time.time()
        if (
            self._security_master_cache
            and now - self._security_master_cache_ts <= self._security_master_cache_ttl_seconds
        ):
            logger.debug(f"Using cached security master ({len(self._security_master_cache)} securities)")
            return self._security_master_cache

        try:
            api_key = os.getenv("API_KEY")
            access_token = os.getenv("ACCESS_TOKEN")
            if not api_key or not access_token:
                logger.error("API_KEY or ACCESS_TOKEN not found")
                return self._security_master_cache

            if DhanContext is None or dhanhq is None:
                logger.error("dhanhq SDK is unavailable for security master fetch")
                return self._security_master_cache

            dhan_context = DhanContext(api_key, access_token)
            dhan = dhanhq(dhan_context)

            logger.debug("Fetching security master from dhanhq SDK...")
            fetch_security_list = getattr(dhan, "fetch_security_list", None)
            if fetch_security_list is None and hasattr(dhan, "security"):
                fetch_security_list = getattr(dhan.security, "fetch_security_list", None)
            if fetch_security_list is None:
                logger.error("dhanhq SDK client does not expose fetch_security_list")
                return self._security_master_cache

            security_frame = fetch_security_list("compact")
            if security_frame is None or security_frame.empty:
                logger.warning("Security list DataFrame is empty")
                return self._security_master_cache

            logger.debug(f"Security list fetched: {len(security_frame)} records")
            logger.debug(f"DataFrame columns: {security_frame.columns.tolist()}")
            securities = security_frame.to_dict("records")

            if securities:
                self._security_master_cache = securities
                self._security_master_cache_ts = now
                logger.info(f"✅ Loaded {len(securities)} total securities from dhanhq SDK")
                return securities

            logger.error("No securities converted from DataFrame")
            return self._security_master_cache
        except Exception as exc:
            logger.error(f"Failed to fetch security master: {exc}", exc_info=True)

        return self._security_master_cache

    def _find_security_id(
        self,
        symbol: str,
        exchange_segment: str,
        instrument_type: str | None = None,
    ) -> Optional[int]:
        """Find current security ID for a symbol from security master."""
        securities = self._fetch_security_master_with_cache()
        if not securities:
            logger.error("No securities in cache for lookup")
            return None

        symbol_upper = symbol.upper()
        symbol_aliases = NSESymbolMapper.get_possible_dhan_names(symbol_upper)
        normalized_symbols = {
            "".join(ch for ch in _normalize_stock_symbol(alias.upper()) if ch.isalnum())
            for alias in symbol_aliases
        }
        normalized_symbols.add("".join(ch for ch in symbol_upper if ch.isalnum()))
        requested_instrument = (instrument_type or "").upper()

        def matches_exchange(exchange_id: str) -> bool:
            if exchange_segment in {"NSE_FNO", "NSE_EQ"}:
                return "NSE" in exchange_id
            if exchange_segment in {"BSE_FNO", "BSE_EQ"}:
                return "BSE" in exchange_id
            if exchange_segment in {"MCX_COMM", "MCX_OPT"}:
                return "MCX" in exchange_id
            return False

        def instrument_match_score(security: Dict[str, Any]) -> int:
            if not requested_instrument:
                return 0

            exchange_instrument = str(security.get("SEM_EXCH_INSTRUMENT_TYPE", "")).upper()
            instrument_name = str(security.get("SEM_INSTRUMENT_NAME", "")).upper()
            value = f"{exchange_instrument} {instrument_name}"

            if requested_instrument == "EQUITY":
                if not value.strip():
                    return 0
                if "OPT" in value or "FUT" in value:
                    return -1
                if any(token in value for token in ("EQUITY", "EQ", "INDEX")):
                    return 3
                return 1

            if requested_instrument == "FUTIDX":
                if not value.strip():
                    return 0
                if requested_instrument == exchange_instrument or requested_instrument == instrument_name:
                    return 3
                if "OPT" in value:
                    return -1
                if "FUTIDX" in value or "FUTURE INDEX" in value:
                    return 3
                if "FUT" in value and ("IDX" in value or "INDEX" in value):
                    return 2
                return 0

            if not value.strip():
                return 0
            if requested_instrument == exchange_instrument or requested_instrument == instrument_name:
                return 3
            if requested_instrument in value:
                return 2
            return -1

        def symbol_match_score(trading_symbol: str) -> int:
            normalized_trading_symbol = "".join(ch for ch in trading_symbol if ch.isalnum())
            if normalized_trading_symbol in normalized_symbols:
                return 3
            if any(
                normalized_trading_symbol.startswith(candidate)
                for candidate in normalized_symbols
                if candidate
            ):
                return 2
            if any(candidate and candidate in normalized_trading_symbol for candidate in normalized_symbols):
                return 1
            return 0

        best_match: Optional[tuple[int, int, int]] = None

        for security in securities:
            trading_symbol = str(security.get("SM_SYMBOL_NAME", "")).upper()
            exchange_id = str(security.get("SEM_EXM_EXCH_ID", "")).upper()
            match_score = symbol_match_score(trading_symbol)
            type_score = instrument_match_score(security)
            if matches_exchange(exchange_id) and match_score and type_score >= 0:
                try:
                    security_id = security.get("SEM_SMST_SECURITY_ID")
                    if security_id:
                        candidate = (match_score, type_score, int(security_id))
                        if best_match is None or candidate[:2] > best_match[:2]:
                            best_match = candidate
                except (ValueError, TypeError):
                    pass

        if best_match is not None:
            logger.info(f"✅ Found security_id={best_match[2]} for {symbol} on {exchange_segment}")
            return best_match[2]

        logger.warning(f"Could not find security_id for {symbol} on {exchange_segment}")
        return None
    
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
