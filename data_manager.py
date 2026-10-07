from __future__ import annotations

import csv
import json
import logging
import hashlib
import math
import os
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import requests
from nse_symbol_mapping import NSESymbolMapper
from exchange_calendar import exchange_for_instrument, previous_trading_day
from security_master import (
    KIND_COMMODITY,
    KIND_INDEX,
    KIND_STOCK,
    SecurityMasterCache,
    SecurityMasterIndex,
    UnderlyingRef,
    UniverseEntry,
    UniverseSpec,
    get_shared_security_master,
    normalize_root,
)

try:
    from dhanhq import DhanContext, dhanhq
except ImportError:  # pragma: no cover - handled at runtime when SDK is unavailable
    DhanContext = None
    dhanhq = None

logger = logging.getLogger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))
_IST_OFFSET_SECONDS = 5 * 3600 + 30 * 60

# DhanHQ /v2/charts/intraday only serves these minute intervals natively.
# Any other interval (e.g. 10min) is built by aggregating a native base interval.
DHAN_NATIVE_INTERVALS = (1, 5, 15, 25, 60)

# Session start (IST minutes since midnight) used to anchor aggregated buckets so
# 10-minute candles line up with exchange/TradingView bars (NSE/BSE 09:15, MCX 09:00).
_SESSION_ANCHOR_MINUTES = {
    "MCX_COMM": 9 * 60,
    "MCX_OPT": 9 * 60,
}
_DEFAULT_SESSION_ANCHOR_MINUTES = 9 * 60 + 15

# Optional runtime dependencies used by the live market scanner data sources.
MARKET_DATA_DEPENDENCIES = {
    "dhanhq": "DhanHQ security master + intraday candles (required for the scanner)",
    "pandas": "DhanHQ security master DataFrame (required for the scanner)",
    "websockets": "TradingView websocket candles (optional, falls back to DhanHQ)",
}
REQUIRED_SCANNER_DEPENDENCIES = ("dhanhq", "pandas")


def market_data_dependency_status() -> Dict[str, bool]:
    """Return which optional market-data packages are importable."""
    import importlib.util

    status: Dict[str, bool] = {}
    for name in MARKET_DATA_DEPENDENCIES:
        try:
            status[name] = importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):
            status[name] = False
    if DhanContext is None or dhanhq is None:
        status["dhanhq"] = False
    return status


def log_market_data_dependency_status(status: Optional[Dict[str, bool]] = None) -> Dict[str, bool]:
    """Log actionable diagnostics for scanner data-source dependencies."""
    status = status if status is not None else market_data_dependency_status()
    for name, purpose in MARKET_DATA_DEPENDENCIES.items():
        if status.get(name):
            logger.info("✅ Market data dependency '%s' available: %s", name, purpose)
        elif name in REQUIRED_SCANNER_DEPENDENCIES:
            logger.error(
                "❌ Market data dependency '%s' is missing: %s. "
                "Install it with `pip install -r requirements.txt` (Render build command).",
                name,
                purpose,
            )
        else:
            logger.warning(
                "⚠️ Market data dependency '%s' is missing: %s. Continuing with DhanHQ candles.",
                name,
                purpose,
            )
    return status


def _parse_interval_minutes(interval: Any) -> int:
    return int(str(interval).replace("min", "").replace("-", "").strip())


def _dhan_base_interval(target_minutes: int) -> int:
    """Largest native DhanHQ interval that evenly divides ``target_minutes``."""
    for base in sorted(DHAN_NATIVE_INTERVALS, reverse=True):
        if base < target_minutes and target_minutes % base == 0:
            return base
    return 1


def _session_anchor_minutes(exchange_segment: str) -> int:
    return _SESSION_ANCHOR_MINUTES.get(str(exchange_segment).upper(), _DEFAULT_SESSION_ANCHOR_MINUTES)


def _timestamp_to_epoch(value: Any) -> Optional[float]:
    """Convert epoch numbers / numeric strings / ISO strings to epoch seconds.

    Naive ISO datetimes are interpreted as IST (exchange time).
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            epoch = float(value)
            return epoch / 1000.0 if epoch > 1e11 else epoch
        except (TypeError, ValueError):
            pass
        try:
            parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=IST)
    return parsed.timestamp()


def _format_bucket_timestamp(bucket_epoch: int, template: Any) -> Any:
    """Render a bucket timestamp in the same style as the source candles."""
    if isinstance(template, (int, float)) or (
        isinstance(template, str) and template.strip().lstrip("-").replace(".", "", 1).isdigit()
    ):
        return int(bucket_epoch)
    bucket_dt = datetime.fromtimestamp(bucket_epoch, IST)
    template_dt = template if isinstance(template, datetime) else None
    if template_dt is None and isinstance(template, str):
        try:
            template_dt = datetime.fromisoformat(template.strip().replace("Z", "+00:00"))
        except ValueError:
            template_dt = None
    if template_dt is not None and template_dt.tzinfo is None:
        bucket_dt = bucket_dt.replace(tzinfo=None)
    if isinstance(template, datetime):
        return bucket_dt
    return bucket_dt.isoformat()


def aggregate_candles(
    candles: List[dict],
    target_minutes: int,
    anchor_minutes: int = 0,
    now: Optional[float] = None,
    drop_incomplete: bool = True,
) -> List[dict]:
    """Aggregate smaller OHLCV candles (e.g. 5min) into ``target_minutes`` candles.

    Buckets are aligned to ``target_minutes`` boundaries in IST, anchored at
    ``anchor_minutes`` after IST midnight (session start). For each bucket:
    open=first open, high=max high, low=min low, close=last close and
    volume=sum of volumes (when present). When ``drop_incomplete`` is true, a
    bucket whose end time is still in the future (relative to ``now``) is
    excluded so a still-forming candle cannot produce a breakout signal.
    """
    if target_minutes <= 0:
        raise ValueError("target_minutes must be positive")

    bucket_seconds = target_minutes * 60
    anchor_seconds = (anchor_minutes * 60) % bucket_seconds
    now_epoch = time.time() if now is None else float(now)

    parsed: List[Tuple[float, dict]] = []
    for candle in candles or []:
        epoch = _timestamp_to_epoch(candle.get("timestamp"))
        if epoch is None:
            logger.debug("Skipping candle without usable timestamp during aggregation: %s", candle)
            continue
        parsed.append((epoch, candle))
    parsed.sort(key=lambda item: item[0])

    buckets: Dict[int, dict] = {}
    order: List[int] = []
    for epoch, candle in parsed:
        local_seconds = epoch + _IST_OFFSET_SECONDS - anchor_seconds
        bucket_start = int(local_seconds // bucket_seconds * bucket_seconds + anchor_seconds - _IST_OFFSET_SECONDS)
        open_, high, low, close = (
            float(candle["open"]),
            float(candle["high"]),
            float(candle["low"]),
            float(candle["close"]),
        )
        bucket = buckets.get(bucket_start)
        if bucket is None:
            bucket = {
                "timestamp": _format_bucket_timestamp(bucket_start, candle.get("timestamp")),
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
            }
            buckets[bucket_start] = bucket
            order.append(bucket_start)
        else:
            bucket["high"] = max(bucket["high"], high)
            bucket["low"] = min(bucket["low"], low)
            bucket["close"] = close
        if candle.get("volume") is not None:
            bucket["volume"] = bucket.get("volume", 0.0) + float(candle["volume"])

    aggregated: List[dict] = []
    for bucket_start in order:
        if drop_incomplete and bucket_start + bucket_seconds > now_epoch:
            logger.debug("Dropping incomplete %smin bucket starting at %s", target_minutes, bucket_start)
            continue
        aggregated.append(buckets[bucket_start])
    return aggregated

# Global rate limiter to prevent DH-904 errors
_api_rate_limiter = {
    'last_call': 0,
    'min_interval': 1.0,  # 1 second between API calls
    'lock': threading.Lock()
}

# Serialize LTP and quote requests across workers in this process. The interval
# is local tuning, not a claim about broker limits. Deploy one quote worker;
# separate processes require external coordination of the broker budget.
_quote_rate_limiter = {
    'last_call': 0,
    'min_interval': 1.0,
    'lock': threading.Lock(),
    'retry_at': 0.0,
    'failures': 0,
}

DHAN_LTP_URL = "https://api.dhan.co/v2/marketfeed/ltp"
DHAN_QUOTE_URL = "https://api.dhan.co/v2/marketfeed/quote"
DHAN_LTP_MAX_INSTRUMENTS = 1000
QUOTE_CACHE_TTL = 1.0
QUOTE_MAX_AGE = 10.0
QUOTE_CACHE_MAX = 2000
_market_quote_cache = OrderedDict()
_quote_interest = OrderedDict()


def _positive_price(value: Any) -> Optional[float]:
    try:
        value = float(value)
        return value if math.isfinite(value) and value > 0 else None
    except (TypeError, ValueError):
        return None


def _quote_max_age() -> float:
    return _positive_price(os.getenv("PAPER_QUOTE_FRESHNESS_SECONDS")) or QUOTE_MAX_AGE


def _quote_trade_timestamp(value: Any) -> Optional[float]:
    if isinstance(value, str) and re.fullmatch(r"\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}", value.strip()):
        try:
            return datetime.strptime(value.strip(), "%d/%m/%Y %H:%M:%S").replace(tzinfo=IST).timestamp()
        except ValueError:
            return None
    return _timestamp_to_epoch(value)


def _parse_market_quote(quote: dict, requested_at: float) -> Optional[dict]:
    price = _positive_price(quote.get("last_price"))
    if price is None:
        return None
    depth = quote.get("depth") or {}

    def best_price(side, choose):
        levels = depth.get(side, []) if isinstance(depth, dict) else []
        if not isinstance(levels, list):
            return None
        prices = [_positive_price(level.get("price")) for level in levels
                  if isinstance(level, dict)
                  and ("quantity" not in level or _positive_price(level["quantity"]) is not None)]
        return choose((price for price in prices if price is not None), default=None)

    bid = best_price("buy", max) or _positive_price(quote.get("bid"))
    ask = best_price("sell", min) or _positive_price(quote.get("ask"))
    if bid is not None and ask is not None and bid > ask:
        return None
    traded_at = _quote_trade_timestamp(quote.get("last_trade_time"))
    trade_timestamp = (
        min(requested_at, traded_at)
        if traded_at is not None and math.isfinite(traded_at)
        and 0 <= requested_at - traded_at <= _quote_max_age()
        else None
    )
    timestamp = requested_at
    if bid is None or ask is None:
        if (traded_at is None or not math.isfinite(traded_at)
                or requested_at - traded_at > _quote_max_age() or traded_at > requested_at + 1):
            return None
        timestamp = min(requested_at, traded_at)
    return {"price": price, "bid": bid, "ask": ask, "timestamp": timestamp,
            "trade_timestamp": trade_timestamp,
            "model": "depth" if bid is not None and ask is not None else "ltp"}


def _quote_backoff(response=None) -> None:
    limiter = _quote_rate_limiter
    limiter["failures"] = min(limiter.get("failures", 0) + 1, 6)
    delay = min(60.0, 2.0 ** limiter["failures"])
    if response is not None:
        try:
            value = response.headers.get("Retry-After", "")
            try:
                retry_after = float(value)
            except ValueError:
                retry_after = parsedate_to_datetime(value).timestamp() - time.time()
            if math.isfinite(retry_after) and retry_after > 0:
                delay = min(60.0, max(delay, retry_after))
        except (TypeError, ValueError, OverflowError):
            pass
    limiter["retry_at"] = time.time() + delay


def _apply_rate_limit(limiter: Optional[dict] = None) -> None:
    """Apply rate limiting between API calls to prevent DH-904 errors"""
    limiter = limiter or _api_rate_limiter
    with limiter['lock']:
        current_time = time.time()
        time_since_last = current_time - limiter['last_call']
        
        if time_since_last < limiter['min_interval']:
            sleep_time = limiter['min_interval'] - time_since_last
            logger.debug(f"⏸️  Rate limiting: sleeping {sleep_time:.2f}s")
            time.sleep(sleep_time)
        
        limiter['last_call'] = time.time()


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

_TRADINGVIEW_SYMBOL_OVERRIDES = {
    "NIFTY": "NSE:NIFTY50",
    "BANKNIFTY": "NSE:BANKNIFTY",
    "SENSEX": "BSE:SENSEX",
    "GOLD": "MCX:GOLD1!",
    "SILVER": "MCX:SILVER1!",
    "CRUDE OIL": "MCX:CRUDE1!",
    "NATURALGAS": "MCX:NGAS1!",
    "BTC": "BINANCE:BTCUSDT",
    "ETH": "BINANCE:ETHUSDT",
}


def _normalize_stock_symbol(name: str) -> str:
    return _STOCK_ALIASES.get(name, name)


def _tradingview_symbol_for(name: str, exchange: str = "NSE") -> str:
    return _TRADINGVIEW_SYMBOL_OVERRIDES.get(name, f"{exchange}:{name}")


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


_OPTION_UNIVERSE_KINDS = {
    "index_options": KIND_INDEX,
    "commodity_options": KIND_COMMODITY,
    "nifty50_stock_options": KIND_STOCK,
}


class DataManager:
    def __init__(
        self,
        cache_ttl_seconds: int = 8,
        security_master: Optional[SecurityMasterCache] = None,
    ) -> None:
        self.cache_ttl_seconds = cache_ttl_seconds
        self._cache: Dict[Tuple[str, str], dict] = {}
        self._contract_cache = OrderedDict()
        self._contract_cache_lock = threading.Lock()
        self._webhook_cache: Dict[Tuple[str, str], dict] = {}
        self._webhook_lock = threading.Lock()
        self._tv = None
        self._tv_fetcher = None
        self._tv_interval = None
        self._dhan_client = None
        # Shared (process-wide) filtered security master; never a per-component copy.
        self._security_master = security_master or get_shared_security_master()
        self._instrument_universe = self._build_instrument_universe()
        self._universe_spec = self._build_universe_spec()

    def get_instruments(self) -> Dict[str, List[Instrument]]:
        return self._instrument_universe

    @property
    def universe_spec(self) -> UniverseSpec:
        return self._universe_spec

    def _build_universe_spec(self) -> UniverseSpec:
        entries = []
        for category, kind in _OPTION_UNIVERSE_KINDS.items():
            for instrument in self._instrument_universe.get(category, []):
                entries.append(
                    UniverseEntry(
                        symbol=instrument.symbol,
                        root=normalize_root(instrument.symbol),
                        exchange=exchange_for_instrument(instrument),
                        kind=kind,
                    )
                )
        aliases: Dict[str, str] = {alias: canonical for alias, canonical in _STOCK_ALIASES.items()}
        for index_symbol in NSESymbolMapper.NSE_INDICES:
            for alias in NSESymbolMapper.get_possible_dhan_names(index_symbol):
                aliases[alias] = index_symbol
        return UniverseSpec(entries, aliases)

    def security_index(self, today=None) -> Optional[SecurityMasterIndex]:
        """Filtered security master for ``today`` (IST); loaded once per day, shared."""
        day = today or datetime.now(IST).date()
        return self._security_master.get_index(self._universe_spec, day)

    def release_security_master(self) -> bool:
        """Free the filtered security master (e.g. while every exchange is closed)."""
        return self._security_master.release()

    def security_master_status(self) -> dict:
        return self._security_master.status()

    def resolve_underlying(self, symbol: str, today=None) -> Optional[UnderlyingRef]:
        entry = self._universe_spec.resolve(symbol)
        if entry is None:
            return None
        index = self.security_index(today)
        if index is None:
            return None
        return index.underlying(entry.root)

    def fetch_ltp(self, instruments: Dict[str, List[int]]) -> Dict[Tuple[str, int], float]:
        """Scanner-compatible batched LTP API, sharing quote pacing/backoff."""
        return self._fetch_marketfeed(instruments, quotes=False)

    def fetch_quotes(self, instruments: Dict[str, List[int]]) -> Dict[Tuple[str, int], dict]:
        """Executable market quotes only; never substitute candle closes.

        Cache reads preserve timestamps. Concurrent callers share a bounded
        interest union, cache and request lock; a cooldown returns no stale data.
        """
        return self._fetch_marketfeed(instruments, quotes=True)

    def _fetch_marketfeed(self, instruments, quotes):
        access_token = os.getenv("ACCESS_TOKEN")
        client_id = os.getenv("DHAN_CLIENT_ID") or os.getenv("API_KEY")
        requested = set()
        for segment, ids in instruments.items():
            if not isinstance(ids, (list, tuple, set)):
                continue
            for sid in ids:
                if isinstance(sid, bool) or not isinstance(sid, (int, float, str)):
                    continue
                try:
                    normalized = int(sid)
                except (TypeError, ValueError, OverflowError):
                    continue
                if normalized <= 0 or (isinstance(sid, float) and sid != normalized):
                    continue
                requested.add((str(segment), normalized))
        if not requested or not access_token or not client_id:
            return {}
        account = hashlib.sha256(f"{client_id}:{access_token}".encode()).digest()
        url = DHAN_QUOTE_URL if quotes else DHAN_LTP_URL
        headers = {
            "access-token": access_token,
            "client-id": str(client_id),
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        fetched = {}
        with _quote_rate_limiter["lock"]:
            now = time.time()
            for key, item in list(_market_quote_cache.items()):
                if now - item["received"] >= QUOTE_CACHE_TTL:
                    del _market_quote_cache[key]
            for key, seen in list(_quote_interest.items()):
                if now - seen >= 60:
                    del _quote_interest[key]
            if quotes:
                for key in sorted(requested):
                    interest_key = (account, key)
                    _quote_interest[interest_key] = now
                    _quote_interest.move_to_end(interest_key)
                while len(_quote_interest) > QUOTE_CACHE_MAX:
                    _quote_interest.popitem(last=False)
            union = requested | ({key for owner, key in _quote_interest if owner == account} if quotes else set())
            missing = sorted(key for key in union if (account, url, key) not in _market_quote_cache)
            for offset in range(0, len(missing), DHAN_LTP_MAX_INSTRUMENTS):
                if time.time() < _quote_rate_limiter.get("retry_at", 0):
                    break
                wait = _quote_rate_limiter["min_interval"] - (time.time() - _quote_rate_limiter["last_call"])
                if wait > 0:
                    time.sleep(wait)
                requested_at = time.time()
                _quote_rate_limiter["last_call"] = requested_at
                payload = {}
                batch_keys = missing[offset:offset + DHAN_LTP_MAX_INSTRUMENTS]
                for segment, sid in batch_keys:
                    payload.setdefault(segment, []).append(sid)
                try:
                    response = requests.post(url, json=payload, headers=headers, timeout=5)
                except requests.RequestException as exc:
                    logger.warning("Dhan marketfeed request failed: %s", exc.__class__.__name__)
                    _quote_backoff()
                    break
                if response.status_code != 200:
                    logger.warning("Dhan marketfeed HTTP %s", response.status_code)
                    if response.status_code == 429 or response.status_code >= 500:
                        _quote_backoff(response)
                    break
                try:
                    data = response.json().get("data") or {}
                    if not isinstance(data, dict):
                        raise ValueError
                except (ValueError, AttributeError):
                    logger.warning("Dhan marketfeed returned invalid JSON")
                    _quote_backoff()
                    break
                _quote_rate_limiter["failures"] = 0
                _quote_rate_limiter["retry_at"] = 0
                for segment, sid in batch_keys:
                    values = data.get(segment)
                    raw = values.get(str(sid), values.get(sid)) if isinstance(values, dict) else None
                    if not isinstance(raw, dict):
                        continue
                    value = _parse_market_quote(raw, requested_at) if quotes else _positive_price(raw.get("last_price"))
                    if value is not None:
                        fetched[(segment, sid)] = value
                        _market_quote_cache[(account, url, (segment, sid))] = {
                            "received": requested_at, "value": value,
                        }
                while len(_market_quote_cache) > QUOTE_CACHE_MAX:
                    _market_quote_cache.popitem(last=False)
            result = {}
            for key in requested:
                cached = _market_quote_cache.get((account, url, key))
                if key in fetched:
                    value = fetched[key]
                elif cached is not None and time.time() - cached["received"] < QUOTE_CACHE_TTL:
                    value = cached["value"]
                else:
                    continue
                if quotes:
                    if time.time() - value["timestamp"] > _quote_max_age():
                        continue
                    value = dict(value)
                result[key] = value
            return result

    def fetch_contract_candles(self, security_id, exchange_segment, instrument_type,
                               from_date, to_date, interval=10, symbol="UNKNOWN", now=None):
        """Share actual-contract candles between scanner and paper trailing."""
        minutes = _parse_interval_minutes(interval)
        now = time.time() if now is None else _timestamp_to_epoch(now)
        seconds = minutes * 60
        anchor = _session_anchor_minutes(exchange_segment) * 60 % seconds
        bucket = int((now + _IST_OFFSET_SECONDS - anchor) // seconds)
        key = (int(security_id), exchange_segment, instrument_type, from_date, to_date, minutes, bucket)
        with self._contract_cache_lock:
            if key in self._contract_cache:
                return [dict(candle) for candle in self._contract_cache[key]]
            _apply_rate_limit()
            candles = self._fetch_dhan_intraday_data(
                security_id=security_id, exchange_segment=exchange_segment,
                instrument_type=instrument_type, from_date=from_date, to_date=to_date,
                interval=minutes, symbol=symbol,
            )
            if candles:
                self._contract_cache[key] = [dict(candle) for candle in candles]
            while len(self._contract_cache) > 512:
                self._contract_cache.popitem(last=False)
            return candles

    def fetch_paper_candles(self, contract: dict, now=None) -> List[dict]:
        """Previous complete candles for the actual option, never its underlying."""
        now_epoch = _timestamp_to_epoch(now) if now is not None else time.time()
        if now_epoch is None or not math.isfinite(now_epoch):
            return []
        minutes = _parse_interval_minutes(contract.get("timeframe", "10min"))
        if minutes <= 0:
            return []
        today = datetime.fromtimestamp(now_epoch, IST).date()
        segment = contract["exchange_segment"]
        exchange = "MCX" if segment.startswith("MCX") else "BSE" if segment.startswith("BSE") else "NSE"
        candles = self.fetch_contract_candles(
            security_id=int(contract["security_id"]), exchange_segment=segment,
            instrument_type=contract["instrument_type"],
            from_date=previous_trading_day(exchange, today).isoformat(),
            to_date=today.isoformat(), interval=minutes,
            symbol=contract.get("option_symbol", "UNKNOWN"),
            now=now_epoch,
        )
        complete = []
        for candle in candles:
            start = _timestamp_to_epoch(candle.get("timestamp"))
            low = _positive_price(candle.get("low"))
            if start is not None and math.isfinite(start) and low is not None and start + minutes * 60 <= now_epoch:
                complete.append({"timestamp": start, "end_timestamp": start + minutes * 60, "low": low})
        return sorted(complete, key=lambda candle: candle["timestamp"])

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

        candles = self._fetch_dhanhq_candles_for_instrument(instrument, interval)
        if candles:
            self._write_cache(cache_key, candles)
        return candles

    def _fetch_dhanhq_candles_for_instrument(self, instrument: Instrument, interval: str) -> List[dict]:
        symbol = instrument.symbol

        security_id = instrument.security_id
        exchange_segment = instrument.exchange_segment
        instrument_type = instrument.instrument_type
        if security_id is None:
            security_id = self._find_security_id(symbol, instrument.exchange_segment, instrument.instrument_type)
            if security_id is None:
                logger.error(f"Could not resolve security_id for {symbol}")
                return []

        try:
            interval_value = int(interval.replace("min", ""))
            today_ist = datetime.now(IST).date()
            today = today_ist.strftime("%Y-%m-%d")
            # Include the previous trading session so the 20-candle lookback is
            # available right from the open.
            from_date = previous_trading_day(exchange_for_instrument(instrument), today_ist).strftime("%Y-%m-%d")
            
            logger.debug(
                f"Fetching DhanHQ candles: symbol={symbol}, "
                f"security_id={security_id}, interval={interval_value}min"
            )
            
            # Apply rate limiting BEFORE API call
            _apply_rate_limit()
            
            candles = self._fetch_dhan_intraday_data(
                security_id=security_id,
                exchange_segment=exchange_segment,
                instrument_type=instrument_type,
                from_date=from_date,
                to_date=today,
                interval=interval_value,
                symbol=symbol  # Pass symbol for debug logging
            )
            if candles:
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
        if instrument.data_source == "tradingview_primary":
            return self.fetch_tradingview_candles(instrument, interval_normalized)

        # Default: return empty if no provider configured
        logger.warning(f"No data source configured for {instrument.symbol}")
        return []

    def fetch_tradingview_candles(self, instrument: Instrument, interval: str) -> List[dict]:
        """Fetch candles from TradingView with DhanHQ fallback where supported."""
        cache_key = (f"tv:{instrument.symbol}", interval)
        cached = self._read_cache(cache_key)
        if cached is not None:
            return cached

        tradingview_symbol = instrument.tradingview_symbol or _tradingview_symbol_for(
            instrument.symbol,
            instrument.exchange if ":" not in instrument.exchange else "NSE",
        )
        candles: List[dict] = []

        try:
            fetcher = self._get_tradingview_fetcher()
            candles = fetcher.fetch_candles(tradingview_symbol, interval)
        except Exception as exc:
            logger.warning("TradingView fetch failed for %s: %s", instrument.symbol, exc)

        if candles:
            self._write_cache(cache_key, candles)
            return candles

        if instrument.category != "crypto":
            logger.info("Falling back to DhanHQ candles for %s", instrument.symbol)
            return self._fetch_dhanhq_candles_for_instrument(instrument, interval)

        logger.warning("No TradingView candles available for crypto symbol %s", instrument.symbol)
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
        """Fetch intraday candles via DhanHQ, aggregating non-native intervals.

        DhanHQ only serves 1/5/15/25/60-minute candles. For other intervals
        (e.g. the 10-minute breakout timeframe) a native base interval is fetched
        and aggregated into session-aligned OHLCV buckets in IST; the still-forming
        latest bucket is excluded.
        """
        interval_minutes = _parse_interval_minutes(interval)
        if interval_minutes in DHAN_NATIVE_INTERVALS:
            return self._request_dhan_intraday_data(
                security_id=security_id,
                exchange_segment=exchange_segment,
                instrument_type=instrument_type,
                from_date=from_date,
                to_date=to_date,
                interval=interval_minutes,
                symbol=symbol,
            )

        base_interval = _dhan_base_interval(interval_minutes)
        logger.debug(
            f"[{symbol}] DhanHQ has no native {interval_minutes}min candles; "
            f"aggregating {base_interval}min candles"
        )
        base_candles = self._request_dhan_intraday_data(
            security_id=security_id,
            exchange_segment=exchange_segment,
            instrument_type=instrument_type,
            from_date=from_date,
            to_date=to_date,
            interval=base_interval,
            symbol=symbol,
        )
        candles = aggregate_candles(
            base_candles,
            interval_minutes,
            anchor_minutes=_session_anchor_minutes(exchange_segment),
        )
        logger.debug(
            f"[{symbol}] Aggregated {len(base_candles)} x {base_interval}min candles "
            f"into {len(candles)} x {interval_minutes}min candles"
        )
        return candles

    def _request_dhan_intraday_data(
        self,
        security_id: int,
        exchange_segment: str,
        instrument_type: str,
        from_date: str,
        to_date: str,
        interval: int = 5,
        symbol: str = "UNKNOWN"
    ) -> List[dict]:
        """Fetch native-interval intraday candles via DhanHQ intraday endpoint."""
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
            if response.status_code != 200:
                logger.error(f"[{symbol}] DhanHQ API error: {response.status_code}")
                return []
            
            try:
                data = response.json()
            except json.JSONDecodeError as e:
                logger.error(f"[{symbol}] Failed to parse JSON response: {e.__class__.__name__}")
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
            logger.error(f"[{symbol}] Error fetching intraday data: {e.__class__.__name__}")
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
                tradingview_symbol=_tradingview_symbol_for(symbol),
            )
            for symbol in NIFTY_50_STOCKS
        ]
        stock_spot_instruments = [
            Instrument(
                symbol=symbol,
                security_id=_STOCK_ID_OVERRIDES.get(symbol),
                exchange="NSE",
                exchange_segment="NSE_EQ",
                instrument_type="EQUITY",
                category="nifty50_stock_spot",
                data_source="tradingview_primary",
                tradingview_symbol=_tradingview_symbol_for(symbol),
            )
            for symbol in NIFTY_50_STOCKS
        ]
        return {
            "index_options": [
                Instrument(
                    symbol="NIFTY",
                    security_id=None,
                    exchange="NSE",
                    exchange_segment="IDX_I",
                    instrument_type="INDEX",
                    category="index_options",
                    data_source="dhan_primary",
                    tradingview_symbol=_tradingview_symbol_for("NIFTY"),
                ),
                Instrument(
                    symbol="BANKNIFTY",
                    security_id=None,
                    exchange="NSE",
                    exchange_segment="IDX_I",
                    instrument_type="INDEX",
                    category="index_options",
                    data_source="dhan_primary",
                    tradingview_symbol=_tradingview_symbol_for("BANKNIFTY"),
                ),
                Instrument(
                    symbol="SENSEX",
                    security_id=None,
                    exchange="BSE",
                    exchange_segment="IDX_I",
                    instrument_type="INDEX",
                    category="index_options",
                    data_source="dhan_primary",
                    tradingview_symbol=_tradingview_symbol_for("SENSEX", "BSE"),
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
                    tradingview_symbol=_tradingview_symbol_for("GOLD", "MCX"),
                ),
                Instrument(
                    symbol="SILVER",
                    security_id=None,
                    exchange="MCX",
                    exchange_segment="MCX_COMM",
                    instrument_type="FUTCOM",
                    category="commodity_options",
                    data_source="dhan_primary",
                    tradingview_symbol=_tradingview_symbol_for("SILVER", "MCX"),
                ),
                Instrument(
                    symbol="CRUDE OIL",
                    security_id=None,
                    exchange="MCX",
                    exchange_segment="MCX_COMM",
                    instrument_type="FUTCOM",
                    category="commodity_options",
                    data_source="dhan_primary",
                    tradingview_symbol=_tradingview_symbol_for("CRUDE OIL", "MCX"),
                ),
                Instrument(
                    symbol="NATURALGAS",
                    security_id=None,
                    exchange="MCX",
                    exchange_segment="MCX_COMM",
                    instrument_type="FUTCOM",
                    category="commodity_options",
                    data_source="dhan_primary",
                    tradingview_symbol=_tradingview_symbol_for("NATURALGAS", "MCX"),
                ),
            ],
            "nifty50_stock_options": stock_instruments,
            "nifty50_stock_spot": stock_spot_instruments,
            "crypto": [
                Instrument(
                    symbol="BTC",
                    security_id=None,
                    exchange="BINANCE",
                    exchange_segment="BINANCE",
                    instrument_type="SPOT",
                    category="crypto",
                    data_source="tradingview_primary",
                    tradingview_symbol=_tradingview_symbol_for("BTC", "BINANCE"),
                ),
                Instrument(
                    symbol="ETH",
                    security_id=None,
                    exchange="BINANCE",
                    exchange_segment="BINANCE",
                    instrument_type="SPOT",
                    category="crypto",
                    data_source="tradingview_primary",
                    tradingview_symbol=_tradingview_symbol_for("ETH", "BINANCE"),
                ),
            ],
        }

    def _find_security_id(
        self,
        symbol: str,
        exchange_segment: str,
        instrument_type: str | None = None,
    ) -> Optional[int]:
        """Find the current security ID for a universe underlying.

        Uses the shared, filtered security master (index -> IDX_I, NIFTY 50 stock
        -> NSE_EQ, commodity -> MCX future backing the traded option expiry).
        """
        underlying = self.resolve_underlying(symbol)
        if underlying is None:
            logger.warning(f"Could not find security_id for {symbol} on {exchange_segment}")
            return None
        if (exchange_segment and exchange_segment != underlying.exchange_segment) or (
            instrument_type and instrument_type != underlying.instrument_type
        ):
            logger.warning(
                f"Security master underlying for {symbol} is {underlying.exchange_segment}/"
                f"{underlying.instrument_type}, not the requested {exchange_segment}/{instrument_type}"
            )
            return None
        logger.info(
            f"✅ Found security_id={underlying.security_id} for {symbol} on {underlying.exchange_segment}"
        )
        return underlying.security_id
    
    def _lookup_instrument(self, symbol: str) -> Optional[Instrument]:
        for instruments in self._instrument_universe.values():
            for instrument in instruments:
                if instrument.symbol == symbol:
                    return instrument
        return None

    def _get_tradingview_fetcher(self):
        if self._tv_fetcher is None:
            from tradingview_fetcher import TradingViewFetcher

            self._tv_fetcher = TradingViewFetcher(cache_ttl_seconds=self.cache_ttl_seconds)
        return self._tv_fetcher
    
    def _read_cache(self, key: Tuple[str, str]) -> Optional[List[dict]]:
        item = self._cache.get(key)
        if not item:
            return None
        if time.time() - item["ts"] > self.cache_ttl_seconds:
            return None
        return item["candles"]
    
    def _write_cache(self, key: Tuple[str, str], candles: List[dict]) -> None:
        self._cache[key] = {"ts": time.time(), "candles": candles}


_shared_data_manager: Optional[DataManager] = None
_shared_data_manager_lock = threading.Lock()


def get_shared_data_manager() -> DataManager:
    """Process-wide DataManager so scanner components never duplicate caches."""
    global _shared_data_manager
    with _shared_data_manager_lock:
        if _shared_data_manager is None:
            _shared_data_manager = DataManager()
        return _shared_data_manager
