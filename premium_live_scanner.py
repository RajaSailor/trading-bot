"""Independent candle bootstrap and timely premium entry evaluation."""

from __future__ import annotations

from collections import OrderedDict
from datetime import timedelta
import logging
import threading
import time

from exchange_calendar import exchange_for_instrument, open_exchanges
from index_swing_strategy import aggregate_five_minutes
from premium_indicators import IndicatorDataError
from premium_patterns import PATTERN_LOOKBACK_SESSIONS
from premium_structure import CANDLE_STOP_POLICY, evaluate_structure

logger = logging.getLogger(__name__)


def _key(contract):
    return contract["exchange_segment"], int(contract["security_id"])


class PremiumLiveScanner:
    """Keep slow REST/history and pattern work off the live structure loop."""

    def __init__(self, screener, feed):
        self.scanner = screener
        self.feed = feed
        self._states = {}
        self._history = {}
        self._registry_at = 0
        self._last_pattern_at = None
        self._patterns = OrderedDict()
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._pattern_thread = None
        self._last_error = None
        self._last_live_at = None
        self._max_cycle_seconds = 0.0
        self._history_jobs = 0
        self._pattern_drops = 0

    def start(self):
        self.feed.start()
        with self._lock:
            if self._pattern_thread is None or not self._pattern_thread.is_alive():
                self._stop.clear()
                self._pattern_thread = threading.Thread(
                    target=self._pattern_loop, daemon=True, name="premium-live-patterns",
                )
                self._pattern_thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()
        self.feed.stop()
        if self._pattern_thread and self._pattern_thread is not threading.current_thread():
            self._pattern_thread.join(timeout=5)

    def _open_contracts(self):
        lifecycle = self.scanner.signal_lifecycle
        if lifecycle is None:
            return []
        return [
            {**position.get("metadata", {}), **{
                key: position[key] for key in ("exchange_segment", "security_id", "instrument_type")
                if key in position
            }}
            for position in lifecycle.open_positions()
        ]

    def run_once(self, now):
        self.start()
        if now.timestamp() - self._registry_at >= 60:
            self._refresh_registry(now)
        # One bounded job per cycle: cold/recovery history has priority over
        # optional older patterns, and neither runs on the live quote thread.
        self._warm_one(now)
        return 0

    def _refresh_registry(self, now):
        scanner = self.scanner
        opened = open_exchanges(now)
        instruments = [
            instrument for instrument in scanner.option_instruments()
            if scanner._uses_index_swing(instrument)
            and exchange_for_instrument(instrument) in opened
        ]
        spots = scanner._batch_spot_prices(instruments, now)
        selected = {}
        for instrument in instruments:
            spot = spots.get(instrument.symbol)
            status = {"evaluated_at": now.isoformat(), "result": "ATM_SELECTION_UNAVAILABLE"}
            if spot is not None:
                try:
                    contracts = [
                        contract for side in ("CE", "PE")
                        for contract in scanner.fetcher.resolve_strike_band(
                            instrument, spot, side, now=now, bands=("ATM",),
                        )
                    ]
                    status.update(result="LIVE_WARMING" if contracts else "NO_LISTED_CONTRACT",
                                  resolved_sides=[contract["option_type"] for contract in contracts])
                    for contract in contracts:
                        key = _key(contract)
                        with self._lock:
                            state = self._states.get(key)
                        if state is None:
                            state = {"instrument": instrument, "contract": contract,
                                     "zones": [], "five": [], "one": [], "prepared_at": 0}
                        state["selected_at"] = now.timestamp()
                        selected[key] = state
                except (KeyError, TypeError, ValueError) as exc:
                    status.update(result="ATM_SELECTION_ERROR", reason=exc.__class__.__name__)
                    logger.exception("Live ATM selection failed for %s", instrument.symbol)
            with scanner._lock:
                old = scanner._index_scan_status.get(instrument.symbol, {})
                scanner._index_scan_status[instrument.symbol] = {**old, **status}
        open_contracts = self._open_contracts()
        subscribed = {_key(item): item for item in open_contracts}
        subscribed.update({key: state["contract"] for key, state in selected.items()})
        self.feed.set_contracts(list(subscribed.values()))
        with self._lock, scanner._lock:
            self._states = selected
            scanner._index_zone_states = dict(selected)
            self._history = {key: value for key, value in self._history.items() if key in subscribed}
            self._patterns = OrderedDict(
                (key, task) for key, task in self._patterns.items() if key in selected
            )
        self._registry_at = now.timestamp()

    def _warm_one(self, now):
        with self._lock:
            contracts = {_key(item): item for item in self._open_contracts()}
            contracts.update({key: state["contract"] for key, state in self._states.items()})
            due = []
            for key, contract in contracts.items():
                history = self._history.get(key, {})
                if now.timestamp() < history.get("retry_at", 0):
                    continue
                snapshot = self.feed.snapshot(contract, now, recent_only=True)
                if (snapshot.get("needs_backfill")
                        and now.timestamp() - history.get("recent_at", 0) >= 60):
                    due.append((0, history.get("recent_at", 0), key, contract, 1))
                elif now.timestamp() - history.get("recent_at", 0) >= 300:
                    due.append((1, history.get("recent_at", 0), key, contract, 1))
                elif now.timestamp() - history.get("extended_at", 0) >= 1800:
                    due.append((2, history.get("recent_at", 0), key, contract,
                                PATTERN_LOOKBACK_SESSIONS))
            if not due:
                return
            _, _, key, contract, sessions = min(due, key=lambda item: (item[0], item[1], item[2]))
        try:
            candles = self.scanner.fetcher.fetch_option_candles(
                contract, "1min", now, lookback_sessions=sessions,
            )
            if not candles:
                raise IndicatorDataError("live_history_unavailable")
            self.feed.seed(contract, candles, now)
            with self._lock:
                state = self._history.setdefault(key, {})
                state.update(recent_at=now.timestamp(), retry_at=0)
                if sessions > 1:
                    state["extended_at"] = now.timestamp()
                self._history_jobs += 1
        except Exception as exc:
            with self._lock:
                self._history.setdefault(key, {})["retry_at"] = now.timestamp() + 60
                self._last_error = exc.__class__.__name__
            logger.exception("Live history bootstrap failed for %s", contract.get("option_symbol"))

    def fetch_quotes(self, instruments):
        """Lifecycle maintenance must not compete with the feed through REST."""
        now = self.scanner._as_ist(self.scanner.clock())
        return self.feed.quotes(instruments, now)

    def fetch_contract_candles(self, security_id, exchange_segment, instrument_type,
                               from_date, to_date, interval=1, symbol=None, now=None):
        if interval != 1:
            raise ValueError("Live lifecycle candles require the one-minute interval")
        observed = self.scanner._as_ist(self.scanner.clock())
        snapshot = self.feed.snapshot(
            {"exchange_segment": exchange_segment, "security_id": security_id}, observed,
        )
        return snapshot["one"] if snapshot["ready"] else []

    def live_check_once(self, now):
        started = time.monotonic()
        cycle_at = now
        scanner = self.scanner
        opened = open_exchanges(now)
        with self._lock:
            states = list(self._states.values())
        alerts = 0
        request = {}
        for contract in [*(state["contract"] for state in states), *self._open_contracts()]:
            request.setdefault(contract["exchange_segment"], []).append(int(contract["security_id"]))
        quotes = self.feed.quotes(request, now)
        if scanner.signal_lifecycle is not None and quotes:
            scanner.signal_lifecycle.observe_quotes(quotes, now)
        for state in states:
            now = cycle_at + timedelta(seconds=time.monotonic() - started)
            if exchange_for_instrument(state["instrument"]) not in opened:
                continue
            if now.timestamp() - state["selected_at"] > 120:
                scanner._count_indicator_reason("live_atm_selection_stale")
                continue
            contract = state["contract"]
            key = _key(contract)
            quote = quotes.get(key)
            if not quote:
                scanner._count_indicator_reason("index_quote_missing")
                self._record_wait(state, now, "index_quote_missing")
                continue
            recent = self.feed.snapshot(contract, now, recent_only=True)
            if not recent["ready"]:
                scanner._count_indicator_reason("live_candles_warming")
                self._record_wait(state, now, "live_candles_warming")
                continue
            # Refresh candle snapshots once per minute. Price-only conditions
            # still run on each fresh quote, without copying ten sessions/sec.
            bucket = int(now.timestamp() // 60)
            if (state.get("snapshot_bucket") != bucket
                    or state.get("generation") != recent["generation"]):
                snapshot = self.feed.snapshot(contract, now)
                if not snapshot["ready"]:
                    scanner._count_indicator_reason("live_candles_warming")
                    continue
                one = snapshot["one"]
                today = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
                five = aggregate_five_minutes(
                    [bar for bar in one if float(bar["timestamp"]) >= today], now,
                )
                state.update(one=one, five=five, snapshot_bucket=bucket,
                             prepared_at=now.timestamp(), generation=recent["generation"])
                self._queue_patterns(state, one, five, bucket)
                if scanner.signal_lifecycle is not None:
                    scanner.signal_lifecycle.observe_index_candles(
                        key[1], key[0], one, now,
                    )
            else:
                one, five = state["one"], state["five"]
            # Routes only use today's structure; long pattern history remains
            # available to the isolated pattern worker.
            today = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
            merged = {float(bar["timestamp"]): bar for bar in one
                      if float(bar["timestamp"]) >= today}
            merged.update({float(bar["timestamp"]): bar for bar in recent["one"]})
            recent_one = [merged[stamp] for stamp in sorted(merged)]
            recent_five = [bar for bar in five if float(bar["timestamp"]) >= today]
            try:
                signals, diagnostics, zones = evaluate_structure(
                    state["instrument"].symbol, contract["option_type"], recent_five,
                    recent_one, quote, now, contract["tick_size"], state["instrument"].category,
                )
                state["zones"] = zones
                for signal in signals:
                    admitted_at = cycle_at + timedelta(seconds=time.monotonic() - started)
                    alerts += scanner._accept_index_signal(
                        state["instrument"], contract, {"armed": True}, recent_five,
                        signal, admitted_at, zone_state=state,
                    )
                with scanner._lock:
                    status = scanner._index_scan_status.setdefault(state["instrument"].symbol, {})
                    status.update(result="LIVE_EVALUATED", evaluated_at=now.isoformat())
                    side = status.setdefault("sides", {}).setdefault(contract["option_type"], {})
                    side.pop("reason", None)
                    side.update(result="EVALUATED", option_symbol=contract.get("option_symbol"),
                                routes={item["route"]: {
                                    key: item[key] for key in ("result", "reason") if key in item
                                } for item in diagnostics})
                for diagnostic in diagnostics:
                    if diagnostic.get("reason"):
                        scanner._count_indicator_reason(diagnostic["reason"])
            except IndicatorDataError as exc:
                scanner._count_indicator_reason(exc.reason)
        self._last_live_at = now.isoformat()
        self._max_cycle_seconds = max(self._max_cycle_seconds, time.monotonic() - started)
        return alerts

    def _record_wait(self, state, now, reason):
        scanner = self.scanner
        with scanner._lock:
            status = scanner._index_scan_status.setdefault(state["instrument"].symbol, {})
            status.update(evaluated_at=now.isoformat(), result="LIVE_WAITING")
            side = status.setdefault("sides", {}).setdefault(state["contract"]["option_type"], {})
            side.update(result="WAITING", reason=reason,
                        option_symbol=state["contract"].get("option_symbol"))

    def _queue_patterns(self, state, one, five, bucket):
        with self._lock:
            key = _key(state["contract"])
            if key not in self._patterns and len(self._patterns) >= 256:
                self._pattern_drops += 1
                self._last_error = "pattern_queue_full"
                logger.error("Live pattern queue full; skipping contract %s", key)
                return
            self._patterns[key] = state, one, five, bucket
        self._wake.set()

    def _pattern_loop(self):
        while not self._stop.is_set():
            self._wake.wait(1)
            self._wake.clear()
            while not self._stop.is_set():
                with self._lock:
                    if not self._patterns:
                        break
                    key, (state, one, five, bucket) = self._patterns.popitem(last=False)
                    if self._states.get(key) is not state:
                        continue
                now = self.scanner._as_ist(self.scanner.clock())
                if (int(now.timestamp() // 60) - bucket > 1
                        or now.timestamp() - state["selected_at"] > 120
                        or exchange_for_instrument(state["instrument"]) not in open_exchanges(now)):
                    self.scanner._count_indicator_reason("live_pattern_task_expired")
                    continue
                try:
                    self._evaluate_patterns(state, one, five, now)
                except Exception as exc:
                    self._last_error = exc.__class__.__name__
                    logger.exception("Live pattern evaluation failed for %s", key)

    def _evaluate_patterns(self, state, one, five, now):
        scanner, contract = self.scanner, state["contract"]
        key = _key(contract)
        quote = self.feed.quotes({key[0]: [key[1]]}, now).get(key, {})
        try:
            signals, diagnostics = scanner.index_engine.evaluate_patterns(
                state["instrument"].symbol, contract["option_type"], one, quote,
                now, state["instrument"].category, five_candles=five,
                tick=contract["tick_size"],
            )
            for signal in signals:
                signal["requested_stop_policy"] = CANDLE_STOP_POLICY
                # Admission time is real wall time, never when slow work began.
                admitted_at = scanner._as_ist(scanner.clock())
                scanner._accept_index_signal(
                    state["instrument"], contract, {"armed": True},
                    signal["chart_candles"], signal, admitted_at, zone_state=state,
                )
            with scanner._lock:
                status = scanner._index_scan_status.setdefault(state["instrument"].symbol, {})
                side = status.setdefault("sides", {}).setdefault(contract["option_type"], {})
                side["patterns"] = {"pattern_timeframes": diagnostics}
            self._last_pattern_at = now.isoformat()
        except IndicatorDataError as exc:
            scanner._count_indicator_reason(exc.reason)

    def status(self):
        with self._lock:
            return {
                "mode": "websocket", "feed": self.feed.status(),
                "selected_contracts": len(self._states), "history_jobs": self._history_jobs,
                "pending_patterns": len(self._patterns), "pattern_drops": self._pattern_drops,
                "pattern_worker_running": bool(self._pattern_thread and self._pattern_thread.is_alive()),
                "last_live_evaluation_at": self._last_live_at,
                "last_pattern_evaluation_at": self._last_pattern_at,
                "max_live_cycle_seconds": self._max_cycle_seconds, "last_error": self._last_error,
            }
