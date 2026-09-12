from __future__ import annotations

import asyncio
import json
import logging
import random
import string
import threading
import time
from typing import Dict, List, Tuple

import websockets


logger = logging.getLogger(__name__)


class TradingViewFetcher:
    """Fetch TradingView candles over websocket with a small in-memory cache."""

    WS_URL = "wss://data.tradingview.com/socket.io/websocket"
    INTERVAL_MAP = {
        "1min": "1",
        "5min": "5",
        "10min": "10",
        "15min": "15",
        "30min": "30",
        "60min": "60",
    }

    def __init__(self, cache_ttl_seconds: int = 8, timeout_seconds: int = 10) -> None:
        self.cache_ttl_seconds = cache_ttl_seconds
        self.timeout_seconds = timeout_seconds
        self._cache: Dict[Tuple[str, str], dict] = {}

    def fetch_candles(self, symbol: str, interval: str, limit: int = 120) -> List[dict]:
        cache_key = (symbol, interval)
        cached = self._read_cache(cache_key)
        if cached is not None:
            return cached

        candles = self._run(self._fetch_history(symbol, interval, limit))
        if candles:
            self._write_cache(cache_key, candles)
        return candles

    def _run(self, coroutine):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coroutine)

        result: dict = {}

        def runner():
            result["value"] = asyncio.run(coroutine)

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
        thread.join(timeout=self.timeout_seconds + 2)
        return result.get("value", [])

    async def _fetch_history(self, symbol: str, interval: str, limit: int) -> List[dict]:
        tv_interval = self.INTERVAL_MAP.get(interval, interval.replace("min", ""))
        chart_session = self._session_id("cs")
        quote_session = self._session_id("qs")
        resolve_payload = json.dumps(
            {
                "symbol": symbol,
                "adjustment": "splits",
                "session": "24x7" if symbol.startswith("BINANCE:") else "regular",
            },
            separators=(",", ":"),
        )

        try:
            async with websockets.connect(self.WS_URL, open_timeout=self.timeout_seconds) as websocket:
                messages = [
                    self._message("set_auth_token", ["unauthorized_user_token"]),
                    self._message("chart_create_session", [chart_session, ""]),
                    self._message("quote_create_session", [quote_session]),
                    self._message(
                        "quote_set_fields",
                        [
                            quote_session,
                            "lp",
                            "open_price",
                            "high_price",
                            "low_price",
                            "volume",
                            "ch",
                            "chp",
                        ],
                    ),
                    self._message("quote_add_symbols", [quote_session, symbol, {"flags": ["force_permission"]}]),
                    self._message("resolve_symbol", [chart_session, "symbol_1", resolve_payload]),
                    self._message("create_series", [chart_session, "s1", "s1", "symbol_1", tv_interval, limit]),
                ]
                for message in messages:
                    await websocket.send(message)

                deadline = time.time() + self.timeout_seconds
                candles: List[dict] = []
                while time.time() < deadline:
                    raw = await asyncio.wait_for(websocket.recv(), timeout=self.timeout_seconds)
                    for payload in self._payloads(raw):
                        if payload.startswith("~h~"):
                            await websocket.send(self._frame_raw(payload))
                            continue

                        parsed = self._decode_payload(payload)
                        if not parsed:
                            continue

                        if parsed.get("m") == "timescale_update":
                            candles = self._extract_candles(parsed)
                        if parsed.get("m") == "series_completed" and candles:
                            return candles

                return candles
        except Exception as exc:
            logger.warning("TradingView websocket fetch failed for %s: %s", symbol, exc)
            return []

    @staticmethod
    def _session_id(prefix: str) -> str:
        return f"{prefix}_{''.join(random.choices(string.ascii_lowercase, k=12))}"

    @staticmethod
    def _message(method: str, params: list) -> str:
        payload = json.dumps({"m": method, "p": params}, separators=(",", ":"))
        return TradingViewFetcher._frame_raw(payload)

    @staticmethod
    def _frame_raw(payload: str) -> str:
        return f"~m~{len(payload)}~m~{payload}"

    @staticmethod
    def _payloads(raw_message: str) -> List[str]:
        if not raw_message.startswith("~m~"):
            return [raw_message]

        payloads: List[str] = []
        remaining = raw_message
        while remaining.startswith("~m~"):
            try:
                remaining = remaining[3:]
                length_str, remaining = remaining.split("~m~", 1)
                payload = remaining[: int(length_str)]
                payloads.append(payload)
                remaining = remaining[int(length_str) :]
            except (ValueError, IndexError):
                break
        return payloads

    @staticmethod
    def _decode_payload(payload: str) -> dict | None:
        if not payload or payload.startswith("~h~"):
            return None
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _extract_candles(message: dict) -> List[dict]:
        payload = message.get("p", [])
        if len(payload) < 2 or not isinstance(payload[1], dict):
            return []

        series = payload[1].get("s1")
        if not isinstance(series, dict):
            return []

        candles = []
        for item in series.get("s", []):
            values = item.get("v", [])
            if len(values) < 6:
                continue
            candles.append(
                {
                    "timestamp": values[0],
                    "open": float(values[1]),
                    "high": float(values[2]),
                    "low": float(values[3]),
                    "close": float(values[4]),
                    "volume": float(values[5] or 0),
                }
            )
        return candles

    def _read_cache(self, key: Tuple[str, str]) -> List[dict] | None:
        item = self._cache.get(key)
        if not item:
            return None
        if time.time() - item["ts"] > self.cache_ttl_seconds:
            return None
        return item["candles"]

    def _write_cache(self, key: Tuple[str, str], candles: List[dict]) -> None:
        self._cache[key] = {"ts": time.time(), "candles": candles}
