"""Dhan full-feed client and conservative, exact-contract minute candle cache."""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime
import json
import logging
import math
import os
import struct
import threading
import time
from urllib.parse import urlencode

import websockets

from exchange_calendar import IST, session_windows
from premium_indicators import completed_candles

log = logging.getLogger(__name__)
SEGMENTS = {0: "IDX_I", 1: "NSE_EQ", 2: "NSE_FNO", 4: "BSE_EQ", 5: "MCX_COMM", 8: "BSE_FNO"}
FULL = struct.Struct("<BHBIfHIfIIIIIIffff100s")
DEPTH = struct.Struct("<IIHHff")
MAX_BARS = 16000
MAX_AGE = 10


def _key(contract):
    segment = str(contract["exchange_segment"]).upper()
    security_id = int(contract["security_id"])
    if segment not in SEGMENTS.values() or security_id <= 0 or security_id > 0xFFFFFFFF:
        raise ValueError("invalid contract")
    return segment, security_id


def _epoch(now):
    return (now.replace(tzinfo=IST) if now.tzinfo is None else now).timestamp()


def _session(key, stamp):
    moment = datetime.fromtimestamp(stamp, IST)
    exchange = "MCX" if key[0] == "MCX_COMM" else "BSE" if key[0].startswith("BSE") else "NSE"
    clock = moment.time().replace(tzinfo=None)
    for start, end in session_windows(exchange, moment.date()):
        if start <= clock < end:
            return moment.date(), start, end
    return None


def _state():
    return {"bars": {}, "seeded": False, "registered_at": None, "quote": None,
            "trade": None, "trade_received": None, "volume": None, "session": None,
            "active": None, "backfill_from": None, "backfill_until": None, "gap_open": False}


class PremiumLiveFeed:
    """Single socket; snapshot/quotes are local-only and never perform network I/O."""

    def __init__(self, clock=time.time, access_token=None, client_id=None):
        self._clock = clock
        self._token = access_token if access_token is not None else os.getenv("ACCESS_TOKEN", "")
        self._client = client_id if client_id is not None else (os.getenv("DHAN_CLIENT_ID") or os.getenv("API_KEY", ""))
        self._lock = threading.RLock()
        self._desired = set()
        self._states = {}
        self._generation = 0
        self._connected = False
        self._last_receive = None
        self._counters = Counter()
        self._error = None
        self._thread = None
        self._loop = None
        self._task = None
        self._stop = threading.Event()
        self._server_disconnect = False
        # The library logs the authenticated request path at DEBUG level.
        self._socket_log = logging.Logger("premium_live_feed.socket", logging.CRITICAL + 1)

    def set_contracts(self, contracts):
        desired = {_key(c) for c in contracts}
        with self._lock:
            for key in self._desired - desired:
                self._states.pop(key, None)
            for key in desired - self._desired:
                self._states[key] = _state()
            self._desired = desired

    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            if not self._token or not self._client:
                self._error = "missing_credentials"
                self._counters["missing_credentials"] += 1
                log.error("Dhan live feed: missing credentials")
                return
            self._stop.clear()
            self._error = None
            self._thread = threading.Thread(target=self._thread_main, name="premium-live-feed", daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        with self._lock:
            loop, task, thread = self._loop, self._task, self._thread
        if loop and task:
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass
        if thread and thread is not threading.current_thread():
            thread.join(timeout=5)

    def status(self):
        with self._lock:
            return {"connected": self._connected, "running": bool(self._thread and self._thread.is_alive()),
                    "generation": self._generation, "contracts": len(self._desired),
                    "error": self._error, "counters": dict(self._counters)}

    def _thread_main(self):
        async def run():
            with self._lock:
                self._loop = asyncio.get_running_loop()
                self._task = asyncio.current_task()
            try:
                await self._run()
            finally:
                with self._lock:
                    self._loop = self._task = None
        try:
            asyncio.run(run())
        except asyncio.CancelledError:
            pass

    def _connection_open(self):
        with self._lock:
            self._connected = True
            self._generation += 1
            self._last_receive = self._clock()
            self._server_disconnect = False
            self._error = None
            self._counters["connections"] += 1

    def _connection_lost(self):
        with self._lock:
            if self._connected:
                self._counters["disconnects"] += 1
                self._generation += 1
                gap_start = self._last_receive if self._last_receive is not None else self._clock()
                for state in self._states.values():
                    if state["registered_at"] is not None:
                        self._mark_gap(state, gap_start)
            self._connected = False
            self._last_receive = None
            for state in self._states.values():
                self._invalidate(state)
                state["registered_at"] = None

    @staticmethod
    def _mark_gap(state, start):
        bucket = int(start // 60) * 60
        if state["backfill_from"] is None:
            state["backfill_from"] = bucket
        else:
            state["backfill_from"] = min(state["backfill_from"], bucket)
        state["gap_open"] = True

    @staticmethod
    def _invalidate(state):
        active = state["active"]
        if active is not None:
            item = state["bars"].get(active)
            if item and item[1] == "live" and not item[2]:
                del state["bars"][active]
        state["active"] = None
        state["quote"] = state["trade"] = state["trade_received"] = None
        state["volume"] = state["session"] = None

    def _coverage(self, now):
        if self._connected and self._last_receive is not None and now - self._last_receive > MAX_AGE:
            for state in self._states.values():
                if state["registered_at"] is not None:
                    self._mark_gap(state, self._last_receive)
                self._invalidate(state)
                if state["registered_at"] is not None:
                    state["registered_at"] = now
            self._last_receive = now
            self._counters["coverage_gaps"] += 1
            self._generation += 1
        for state in self._states.values():
            stamp = state["active"]
            if stamp is not None and stamp + 60 <= now:
                item = state["bars"].get(stamp)
                if item and item[1] == "live" and not item[2]:
                    state["bars"][stamp] = (item[0], "live", True)
                state["active"] = None

    async def _send_changes(self, ws, subscribed):
        with self._lock:
            desired = set(self._desired)
            reset = {key for key in subscribed & desired if self._states[key]["registered_at"] is None}
        for code, keys in ((22, sorted((subscribed - desired) | reset)),
                           (21, sorted((desired - subscribed) | reset))):
            for offset in range(0, len(keys), 100):
                batch = keys[offset:offset + 100]
                payload = {"RequestCode": code, "InstrumentCount": len(batch),
                           "InstrumentList": [{"ExchangeSegment": s, "SecurityId": str(i)} for s, i in batch]}
                await ws.send(json.dumps(payload))
                if code == 21:
                    with self._lock:
                        for key in batch:
                            if key in self._desired and key in self._states:
                                self._states[key]["registered_at"] = self._clock()
                    subscribed.update(batch)
                else:
                    subscribed.difference_update(batch)

    async def _run(self):
        delay = 1
        url = "wss://api-feed.dhan.co?" + urlencode(
            {"version": "2", "token": self._token, "clientId": self._client, "authType": "2"})
        while not self._stop.is_set():
            connected_at = None
            try:
                async with websockets.connect(url, open_timeout=5, close_timeout=2, ping_interval=20,
                                              ping_timeout=10, max_size=2**20, logger=self._socket_log) as ws:
                    self._connection_open()
                    subscribed = set()
                    connected_at = self._clock()
                    while not self._stop.is_set():
                        await self._send_changes(ws, subscribed)
                        try:
                            data = await asyncio.wait_for(ws.recv(), timeout=1)
                        except asyncio.TimeoutError:
                            with self._lock:
                                self._coverage(self._clock())
                            continue
                        if isinstance(data, bytes):
                            self.ingest(data)
                            if self._server_disconnect:
                                raise ConnectionError("feed disconnected")
                        else:
                            with self._lock:
                                self._counters["non_binary"] += 1
            except asyncio.CancelledError:
                raise
            except Exception:
                with self._lock:
                    self._error = "connection_error"
                    self._counters["connection_errors"] += 1
                log.warning("Dhan live feed connection failed; reconnecting")
            finally:
                self._connection_lost()
                if connected_at is not None and self._clock() - connected_at >= 30:
                    delay = 1
            if not self._stop.is_set():
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)

    def seed(self, contract, candles, now):
        """Merge REST bars completed by the request-start ``now``."""
        key, current = _key(contract), _epoch(now)
        # Validate independently so one bad row doesn't discard the rest of the history.
        valid = []
        for candle in candles:
            try:
                bars = completed_candles([candle], now if now.tzinfo else now.replace(tzinfo=IST),
                                         timeframe_seconds=60)
                if bars and bars[0]["_timestamp_epoch"] % 60 == 0 and _session(key, bars[0]["_timestamp_epoch"]):
                    valid.append(bars[0])
            except (ValueError, TypeError, OverflowError, OSError):
                continue
        with self._lock:
            state = self._states.get(key)
            if state is None:
                return
            self._coverage(current)
            changed = False
            for bar in valid:
                stamp = int(bar["_timestamp_epoch"])
                old = state["bars"].get(stamp)
                if old and old[1] == "live" and any(
                    not math.isclose(old[0][field], bar[field], rel_tol=1e-6, abs_tol=0.01)
                    for field in ("open", "high", "low", "close")
                ):
                    self._counters["history_mismatches"] += 1
                if not old or old[0] != bar or old[1] != "rest":
                    changed = True
                state["bars"][stamp] = (bar, "rest", True)
            if valid:
                state["seeded"] = True
                first = min(bar["_timestamp_epoch"] for bar in valid)
                last = max(bar["_timestamp_epoch"] for bar in valid)
                if (state["backfill_from"] is not None and not state["gap_open"]
                        and state["backfill_until"] is not None
                        and first <= state["backfill_from"] and last >= state["backfill_until"]):
                    state["backfill_from"] = state["backfill_until"] = None
                    changed = True
            if changed:
                self._generation += 1
            self._trim(state)

    @staticmethod
    def _trim(state):
        bars = state["bars"]
        if len(bars) > MAX_BARS:
            for stamp in sorted(bars)[:len(bars) - MAX_BARS]:
                del bars[stamp]

    def snapshot(self, contract, now, *, recent_only=False):
        """Return full history or the most recent ten minutes, without network I/O."""
        key, current = _key(contract), _epoch(now)
        with self._lock:
            self._coverage(current)
            state = self._states.get(key)
            if state is None:
                return {"one": [], "quote": {}, "ready": False, "generation": self._generation,
                        "reason": "unregistered", "needs_backfill": False}
            quote = self._fresh_quote(state, current)
            current_bucket = int(current // 60) * 60
            if recent_only:
                bars = [dict(state["bars"][stamp][0])
                        for stamp in range(current_bucket - 9 * 60, current_bucket + 1, 60)
                        if stamp in state["bars"]]
            else:
                bars = [dict(item[0]) for stamp, item in sorted(state["bars"].items())
                        if stamp <= current_bucket]
            live = state["bars"].get(current_bucket)
            needs_backfill = state["backfill_from"] is not None
            recent_complete = all(
                (item := state["bars"].get(current_bucket - i * 60))
                and item[1] == "live" and item[2]
                for i in range(1, 6)
            )
            ready = bool(self._connected and state["registered_at"] is not None and state["seeded"]
                         and live and live[1] == "live" and quote and _session(key, current)
                         and (not needs_backfill or (not state["gap_open"] and recent_complete)))
            reason = None if ready else ("disconnected" if not self._connected else
                                         "unregistered" if state["registered_at"] is None else
                                         "unseeded" if not state["seeded"] else
                                         "no_covered_current_bar" if not live or live[1] != "live" else
                                         "stale_quote" if not quote else
                                         "needs_backfill" if needs_backfill and not recent_complete else
                                         "outside_session")
            return {"one": bars, "quote": quote, "ready": ready, "generation": self._generation,
                    "reason": reason, "needs_backfill": needs_backfill}

    @staticmethod
    def _fresh_quote(state, now):
        quote, received = state["quote"], state["trade_received"]
        if not quote or received is None or not (-1 <= now - quote["trade_timestamp"] <= MAX_AGE
                                                   and -1 <= now - received <= MAX_AGE):
            return {}
        if not (0 < quote["bid"] <= quote["ask"] and quote["price"] > 0):
            return {}
        return dict(quote)

    def quotes(self, request, now):
        current = _epoch(now)
        result = {}
        with self._lock:
            self._coverage(current)
            if not self._connected:
                return result
            for segment, ids in request.items():
                for security_id in ids:
                    key = (str(segment).upper(), int(security_id))
                    state = self._states.get(key)
                    quote = self._fresh_quote(state, current) if state and state["registered_at"] is not None else {}
                    if quote:
                        result[key] = quote
        return result

    def ingest(self, packet, received_at=None):
        received = self._clock() if received_at is None else float(received_at)
        if not isinstance(packet, bytes):
            with self._lock:
                self._counters["malformed"] += 1
            return
        offset = 0
        with self._lock:
            self._coverage(received)
            while offset < len(packet):
                if len(packet) - offset < 8:
                    self._counters["malformed"] += 1
                    break
                code, length, segment, security_id = struct.unpack_from("<BHBI", packet, offset)
                if length < 8 or offset + length > len(packet):
                    self._counters["malformed"] += 1
                    break
                chunk = packet[offset:offset + length]
                offset += length
                if code == 50:
                    self._counters["server_disconnect"] += 1
                    self._server_disconnect = True
                    self._connection_lost()
                    continue
                if code != 8:
                    self._counters["other_packets"] += 1
                    continue
                if length != FULL.size or segment not in SEGMENTS:
                    self._counters["malformed"] += 1
                    continue
                self._last_receive = received
                for state in self._states.values():
                    if state["gap_open"] and state["registered_at"] is not None:
                        state["backfill_until"] = int(received // 60) * 60
                        state["gap_open"] = False
                fields = FULL.unpack(chunk)
                _, _, _, _, price, _ltq, stamp, _, volume, *_rest = fields
                bid, ask = DEPTH.unpack_from(fields[-1])[4:]
                self._ingest_trade((SEGMENTS[segment], security_id), price, stamp, volume, bid, ask, received)

    def _ingest_trade(self, key, price, stamp, volume, bid, ask, received):
        state = self._states.get(key)
        if not self._connected or not state or state["registered_at"] is None:
            return
        if stamp < state["registered_at"]:
            return
        if not all(math.isfinite(x) for x in (price, bid, ask)) or price <= 0 or not (-1 <= received - stamp <= MAX_AGE):
            self._counters["invalid_trade"] += 1
            return
        session = _session(key, stamp)
        if not session or _session(key, received) != session:
            return
        previous = state["trade"]
        event = (stamp, price, volume)
        if previous and (stamp < previous[0] or (state["session"] == session and volume < previous[2])):
            self._counters["out_of_order"] += 1
            return
        if event == previous:
            if state["quote"]:
                state["quote"]["bid"], state["quote"]["ask"] = bid, ask
            self._counters["duplicates"] += 1
            return
        if state["session"] != session:
            state["volume"] = None
        delta = volume - state["volume"] if state["volume"] is not None and volume >= state["volume"] else None
        state["volume"], state["session"], state["trade"] = volume, session, event
        state["trade_received"] = received
        state["quote"] = {"price": price, "bid": bid, "ask": ask, "trade_timestamp": stamp}
        bucket = stamp // 60 * 60
        if bucket < (int(state["registered_at"] // 60) + 1) * 60 or bucket != int(received // 60) * 60:
            return
        item = state["bars"].get(bucket)
        if item and (item[1] != "live" or item[2]):
            return
        if item is None:
            bar = {"timestamp": bucket, "open": price, "high": price, "low": price,
                   "close": price, "volume": delta}
        else:
            bar = item[0]
            bar["high"], bar["low"], bar["close"] = max(bar["high"], price), min(bar["low"], price), price
            bar["volume"] = bar["volume"] + delta if bar["volume"] is not None and delta is not None else None
        state["bars"][bucket] = (bar, "live", False)
        state["active"] = bucket
        self._trim(state)
