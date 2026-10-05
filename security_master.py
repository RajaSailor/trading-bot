"""Memory-safe, filtered Dhan security master.

The full Dhan scrip master has ~200k rows. Loading it with ``pandas.read_csv``
and converting every row into a dict (the previous implementation) costs
hundreds of MB and was the main cause of Render 512MB OOM restarts.

This module instead:
  * streams the compact CSV line by line (never holds the whole file),
  * reads only the columns the scanner needs,
  * keeps only rows for the configured universe (index, NIFTY 50 stock and
    MCX commodity underlyings) and, per underlying + CE/PE, only the option
    contracts of the single expiry the scanner trades (the nearest expiry
    strictly after today - same-day expiry is skipped),
  * stores the survivors as small slotted dataclasses,
  * is shared process-wide (one instance) and rebuilt at most once per IST day.
"""

from __future__ import annotations

import csv
import gc
import logging
import math
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable, Dict, Iterable, Iterator, List, Mapping, Optional, Tuple

import requests

from memory_diagnostics import log_rss

logger = logging.getLogger(__name__)

COMPACT_CSV_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"

NEEDED_COLUMNS = (
    "SEM_EXM_EXCH_ID",
    "SEM_SEGMENT",
    "SEM_SMST_SECURITY_ID",
    "SEM_INSTRUMENT_NAME",
    "SEM_TRADING_SYMBOL",
    "SEM_LOT_UNITS",
    "SEM_TICK_SIZE",
    "SEM_CUSTOM_SYMBOL",
    "SEM_EXPIRY_DATE",
    "SEM_STRIKE_PRICE",
    "SEM_OPTION_TYPE",
    "SEM_EXCH_INSTRUMENT_TYPE",
    "SEM_SERIES",
    "SM_SYMBOL_NAME",
)
_REQUIRED_COLUMNS = ("SEM_EXM_EXCH_ID", "SEM_SMST_SECURITY_ID", "SEM_INSTRUMENT_NAME", "SEM_TRADING_SYMBOL")

KIND_INDEX = "index"
KIND_STOCK = "stock"
KIND_COMMODITY = "commodity"

_OPTION_SEGMENT = {"NSE": "NSE_FNO", "BSE": "BSE_FNO", "MCX": "MCX_COMM"}

# "NIFTY-Oct2026-24500-CE", "GOLD-05Oct2026-FUT", "BAJAJ-AUTO-Oct2026-9000-PE"
_DERIVATIVE_ROOT = re.compile(r"^(?P<root>.+?)-(?:\d{1,2})?[A-Za-z]{3}\d{4}(?:-|$)")


def normalize_root(name: object) -> str:
    return "".join(ch for ch in str(name or "").upper() if ch.isalnum())


def _parse_date(value: object) -> Optional[date]:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _parse_float(value: object) -> Optional[float]:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _parse_int(value: object) -> Optional[int]:
    number = _parse_float(value)
    if number is None:
        return None
    return int(number)


def row_root_candidates(row: Mapping[str, object]) -> Tuple[str, ...]:
    """Underlying root names for a security-master row (most reliable first).

    NSE derivative rows carry an empty ``SM_SYMBOL_NAME`` and SENSEX options
    carry ``BSXOPT``, so the trading symbol prefix is the primary source.
    """
    trading = str(row.get("SEM_TRADING_SYMBOL") or "").strip()
    match = _DERIVATIVE_ROOT.match(trading)
    if match:
        return (match.group("root"),)
    # Only exact names: display names like "Reliance Power" or "NIFTY MIDCAP 150"
    # must never be truncated into a universe root.
    return tuple(value for value in (trading, str(row.get("SM_SYMBOL_NAME") or "").strip()) if value)


@dataclass(frozen=True, slots=True)
class OptionContract:
    security_id: int
    trading_symbol: str
    strike: float
    option_type: str
    expiry: date
    exchange_segment: str
    instrument_type: str
    lot_size: float = 0.0
    tick_size: Optional[float] = None


@dataclass(frozen=True, slots=True)
class UnderlyingRef:
    security_id: int
    exchange_segment: str
    instrument_type: str
    trading_symbol: str
    expiry: Optional[date] = None


@dataclass(frozen=True, slots=True)
class UniverseEntry:
    symbol: str
    root: str
    exchange: str
    kind: str


class UniverseSpec:
    """Configured scanner universe keyed by normalized root symbol."""

    def __init__(self, entries: Iterable[UniverseEntry], aliases: Optional[Mapping[str, str]] = None) -> None:
        self.entries: Dict[str, UniverseEntry] = {entry.root: entry for entry in entries}
        self.aliases: Dict[str, str] = {
            normalize_root(alias): normalize_root(target)
            for alias, target in (aliases or {}).items()
            if normalize_root(target) in self.entries
        }
        self.key = tuple(sorted((entry.root, entry.exchange, entry.kind) for entry in self.entries.values()))

    def resolve(self, name: object) -> Optional[UniverseEntry]:
        root = normalize_root(name)
        root = self.aliases.get(root, root)
        return self.entries.get(root)

    def __len__(self) -> int:
        return len(self.entries)


@dataclass
class SecurityMasterIndex:
    as_of: date
    universe_key: tuple
    chains: Dict[Tuple[str, str], Tuple[date, Tuple[OptionContract, ...]]] = field(default_factory=dict)
    underlyings: Dict[str, UnderlyingRef] = field(default_factory=dict)
    rows_scanned: int = 0

    @property
    def contract_count(self) -> int:
        return sum(len(contracts) for _expiry, contracts in self.chains.values())

    def option_chain(self, root: str, option_type: str) -> Optional[Tuple[date, Tuple[OptionContract, ...]]]:
        return self.chains.get((root, option_type.upper()))

    def underlying(self, root: str) -> Optional[UnderlyingRef]:
        return self.underlyings.get(root)

    @classmethod
    def build(cls, rows: Iterable[Mapping[str, object]], universe: UniverseSpec, today: date) -> "SecurityMasterIndex":
        best: Dict[Tuple[str, str], Tuple[date, Dict[float, OptionContract]]] = {}
        index_refs: Dict[str, UnderlyingRef] = {}
        equity_refs: Dict[str, UnderlyingRef] = {}
        futures: Dict[str, List[UnderlyingRef]] = {}
        scanned = 0

        for row in rows:
            scanned += 1
            exchange = str(row.get("SEM_EXM_EXCH_ID") or "").strip().upper()
            if exchange not in _OPTION_SEGMENT:
                continue
            entry = None
            for candidate in row_root_candidates(row):
                entry = universe.resolve(candidate)
                if entry is not None:
                    break
            if entry is None or entry.exchange != exchange:
                continue

            security_id = _parse_int(row.get("SEM_SMST_SECURITY_ID"))
            if security_id is None or security_id <= 0:
                continue
            instrument = str(row.get("SEM_INSTRUMENT_NAME") or "").strip().upper()
            trading_symbol = str(row.get("SEM_TRADING_SYMBOL") or "").strip()

            if instrument.startswith("OPT"):
                option_type = str(row.get("SEM_OPTION_TYPE") or "").strip().upper()
                if option_type not in {"CE", "PE"}:
                    continue
                strike = _parse_float(row.get("SEM_STRIKE_PRICE"))
                expiry = _parse_date(row.get("SEM_EXPIRY_DATE"))
                # Expiry-day rule: never trade the contract that expires today.
                if strike is None or strike <= 0 or expiry is None or expiry <= today:
                    continue
                key = (entry.root, option_type)
                tick_size = _parse_float(row.get("SEM_TICK_SIZE"))
                if tick_size is not None and (not math.isfinite(tick_size) or tick_size <= 0):
                    tick_size = None
                current = best.get(key)
                if current is not None and expiry > current[0]:
                    continue
                if current is None or expiry < current[0]:
                    current = (expiry, {})
                    best[key] = current
                current[1].setdefault(
                    strike,
                    OptionContract(
                        security_id=security_id,
                        trading_symbol=trading_symbol or f"{entry.symbol}-{strike:g}-{option_type}",
                        strike=strike,
                        option_type=option_type,
                        expiry=expiry,
                        exchange_segment=_OPTION_SEGMENT[exchange],
                        instrument_type=instrument,
                        lot_size=_parse_float(row.get("SEM_LOT_UNITS")) or 0.0,
                        tick_size=tick_size,
                    ),
                )
            elif instrument == "INDEX" and entry.kind == KIND_INDEX:
                index_refs.setdefault(
                    entry.root, UnderlyingRef(security_id, "IDX_I", "INDEX", trading_symbol or entry.symbol)
                )
            elif instrument == "EQUITY" and entry.kind == KIND_STOCK:
                series = str(row.get("SEM_SERIES") or "").strip().upper()
                if series and series != "EQ":
                    continue
                equity_refs.setdefault(
                    entry.root, UnderlyingRef(security_id, "NSE_EQ", "EQUITY", trading_symbol or entry.symbol)
                )
            elif instrument.startswith("FUT") and entry.kind == KIND_COMMODITY:
                expiry = _parse_date(row.get("SEM_EXPIRY_DATE"))
                if expiry is None or expiry <= today:
                    continue
                futures.setdefault(entry.root, []).append(
                    UnderlyingRef(security_id, "MCX_COMM", instrument or "FUTCOM", trading_symbol, expiry)
                )

        chains = {
            key: (expiry, tuple(by_strike[strike] for strike in sorted(by_strike)))
            for key, (expiry, by_strike) in best.items()
        }
        underlyings: Dict[str, UnderlyingRef] = {}
        for root, entry in universe.entries.items():
            if entry.kind == KIND_INDEX and root in index_refs:
                underlyings[root] = index_refs[root]
            elif entry.kind == KIND_STOCK and root in equity_refs:
                underlyings[root] = equity_refs[root]
            elif entry.kind == KIND_COMMODITY and futures.get(root):
                underlyings[root] = _commodity_underlying(futures[root], chains, root)
        return cls(
            as_of=today,
            universe_key=universe.key,
            chains=chains,
            underlyings=underlyings,
            rows_scanned=scanned,
        )


def _commodity_underlying(
    futures: List[UnderlyingRef],
    chains: Mapping[Tuple[str, str], Tuple[date, Tuple[OptionContract, ...]]],
    root: str,
) -> UnderlyingRef:
    """MCX options settle into a futures contract: pick the nearest future that
    expires on/after the traded option expiry (else the nearest future)."""
    ordered = sorted(futures, key=lambda ref: ref.expiry or date.max)
    option_expiries = [chain[0] for (chain_root, _side), chain in chains.items() if chain_root == root]
    if option_expiries:
        target = min(option_expiries)
        for ref in ordered:
            if ref.expiry is not None and ref.expiry >= target:
                return ref
    return ordered[0]


def iter_needed_columns(lines: Iterable[str]) -> Iterator[Dict[str, str]]:
    """Parse CSV lines, yielding only :data:`NEEDED_COLUMNS` per row."""
    reader = csv.reader(lines)
    header = next(reader, None)
    if not header:
        raise ValueError("Security master CSV is empty")
    header = [name.strip().lstrip("\ufeff") for name in header]
    positions = {name: header.index(name) for name in NEEDED_COLUMNS if name in header}
    missing = [name for name in _REQUIRED_COLUMNS if name not in positions]
    if missing:
        raise ValueError(f"Security master CSV missing columns: {', '.join(missing)}")
    for values in reader:
        if not values:
            continue
        width = len(values)
        yield {name: (values[pos] if pos < width else "") for name, pos in positions.items()}


def stream_compact_security_master(url: Optional[str] = None, timeout: tuple = (10, 120)) -> Iterator[Dict[str, str]]:
    """Stream the Dhan compact scrip master over HTTP without buffering the file."""
    url = url or os.getenv("DHAN_SECURITY_MASTER_URL", COMPACT_CSV_URL)
    with requests.get(url, stream=True, timeout=timeout) as response:
        response.raise_for_status()
        response.encoding = "utf-8"
        lines = response.iter_lines(chunk_size=64 * 1024, decode_unicode=True)
        yield from iter_needed_columns(lines)


class SecurityMasterCache:
    """Process-wide filtered security master, rebuilt at most once per IST day."""

    def __init__(
        self,
        row_source: Optional[Callable[[], Iterable[Mapping[str, object]]]] = None,
        retry_seconds: float = 300.0,
    ) -> None:
        self._row_source = row_source or stream_compact_security_master
        self._retry_seconds = retry_seconds
        self._lock = threading.Lock()
        self._index: Optional[SecurityMasterIndex] = None
        self._failed_at: Optional[float] = None
        self._failed_for: Optional[tuple] = None
        self.loads = 0

    def get_index(self, universe: UniverseSpec, today: date) -> Optional[SecurityMasterIndex]:
        with self._lock:
            current = self._index
            if current is not None and current.as_of == today and current.universe_key == universe.key:
                return current
            request_key = (today, universe.key)
            if (
                self._failed_for == request_key
                and self._failed_at is not None
                and time.monotonic() - self._failed_at < self._retry_seconds
            ):
                return None

            # Drop the previous day's index before building the new one so the
            # two never coexist in memory.
            self._index = None
            current = None
            started = time.monotonic()
            rows = None
            try:
                rows = self._row_source()
                index = SecurityMasterIndex.build(rows, universe, today)
            except Exception as exc:
                self._failed_at = time.monotonic()
                self._failed_for = request_key
                logger.error("❌ Security master load failed: %s: %s", exc.__class__.__name__, exc)
                return None
            finally:
                rows = None
                gc.collect()

            if not index.chains and not index.underlyings:
                self._failed_at = time.monotonic()
                self._failed_for = request_key
                logger.error(
                    "❌ Security master loaded %s rows but none matched the scanner universe",
                    index.rows_scanned,
                )
                return None

            self._index = index
            self._failed_at = None
            self._failed_for = None
            self.loads += 1
            logger.info(
                "✅ Filtered security master for %s: kept %s option contracts in %s chains "
                "+ %s underlyings from %s rows in %.1fs",
                today.isoformat(),
                index.contract_count,
                len(index.chains),
                len(index.underlyings),
                index.rows_scanned,
                time.monotonic() - started,
            )
            log_rss("after security master load")
            return index

    def release(self) -> bool:
        with self._lock:
            had_index = self._index is not None
            self._index = None
        if had_index:
            gc.collect()
            log_rss("after security master release")
        return had_index

    def status(self) -> dict:
        # Lock-free on purpose: get_index() holds the lock during the download and
        # /health must stay responsive. Diagnostic counters may lag by one load.
        index = self._index
        return {
            "loaded": index is not None,
            "as_of": index.as_of.isoformat() if index else None,
            "option_contracts": index.contract_count if index else 0,
            "underlyings": len(index.underlyings) if index else 0,
            "loads": self.loads,
        }


_shared_cache: Optional[SecurityMasterCache] = None
_shared_lock = threading.Lock()


def get_shared_security_master() -> SecurityMasterCache:
    global _shared_cache
    with _shared_lock:
        if _shared_cache is None:
            _shared_cache = SecurityMasterCache()
        return _shared_cache
