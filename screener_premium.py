from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, time as dt_time, timezone
from typing import Dict, Iterable, List, Optional
from zoneinfo import ZoneInfo

from atm_options_fetcher import ATMOptionsFetcher
from exchange_calendar import MCX, exchange_for_instrument, open_exchanges
from latency_tracker import now_mark
from live_signal_detector import LiveSignalDetector
from premium_strategy_engine import LOOKBACK_CANDLES, TRIGGER_LIVE, PremiumStrategyEngine
from telegram_handler import SCREENER_CATEGORIES, format_ist_timestamp


IST = ZoneInfo("Asia/Kolkata")
logger = logging.getLogger(__name__)

# Only these three segments are screened and alerted; crypto, NIFTY50 intraday 5X
# and NIFTY50 pay-later segments are retired.
SUPPORTED_SCREENER_CATEGORIES = SCREENER_CATEGORIES

INTERVAL = "10min"
BUCKET_SECONDS = 600
# Seconds after a 10-minute boundary before refreshing candles, so the
# just-completed candle is available from Dhan.
CANDLE_SETTLE_SECONDS = 5
# Retry a failed instrument refresh sooner than the next 10-minute boundary.
REFRESH_RETRY_SECONDS = 120


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


class PremiumScreener:
    """Monitor listed ITM+1 / ATM / OTM+1 CE and PE premiums for RED-high breakouts.

    * Candles (10-minute, incl. previous session) are refreshed once per
      completed 10-minute bucket per underlying, only while its exchange is open.
    * :meth:`live_check_once` polls batched LTPs for armed contracts and fires
      immediately when the premium crosses the RED candle high.
    """

    def __init__(
        self,
        data_manager,
        telegram_handler,
        position_manager,
        trade_control_handler=None,
        trade_control_bot=None,
        live_signal_detector: LiveSignalDetector | None = None,
        signal_callback=None,
        paper_trade_notifier=None,
    ) -> None:
        self.data_manager = data_manager
        self.telegram_handler = telegram_handler
        self.position_manager = position_manager
        self.trade_control_handler = trade_control_handler
        self.trade_control_bot = trade_control_bot
        self.fetcher = ATMOptionsFetcher(data_manager)
        self.engine = PremiumStrategyEngine(lookback=LOOKBACK_CANDLES)
        self.live_signal_detector = live_signal_detector or LiveSignalDetector(freshness_minutes=24 * 60)
        self.signal_callback = signal_callback
        self.paper_trade_notifier = paper_trade_notifier
        self.refresh_retry_seconds = _env_float("SCANNER_REFRESH_RETRY_SECONDS", REFRESH_RETRY_SECONDS)
        self._lock = threading.RLock()
        self._processed_signal_keys: set[str] = set()
        self._processed_signal_order: list[str] = []
        # security_id -> monitored contract state (contract, instrument, reference)
        self._monitored: Dict[int, dict] = {}
        # underlying symbol -> {"bucket": int, "retry_at": float}
        self._refresh_state: Dict[str, dict] = {}
        universe = self.data_manager.get_instruments()
        self.index_instruments = universe.get("index_options", [])
        self.commodity_instruments = universe.get("commodity_options", [])
        self.stock_instruments = universe.get("nifty50_stock_options", [])

    # ================================================================ scan
    def option_instruments(self) -> List:
        return [*self.commodity_instruments, *self.index_instruments, *self.stock_instruments]

    def run_once(self, now: datetime | None = None) -> int:
        now = self._as_ist(now)
        open_now = open_exchanges(now)
        self._prune_closed(open_now)
        if not open_now:
            logger.debug("All exchanges closed at %s; scan skipped", now.isoformat())
            return 0

        due = [
            instrument
            for instrument in self.option_instruments()
            if exchange_for_instrument(instrument) in open_now and self._refresh_due(instrument, now)
        ]
        alerts = self._scan_instruments(due, INTERVAL, now) if due else 0
        return alerts

    def _refresh_due(self, instrument, now: datetime) -> bool:
        state = self._refresh_state.get(instrument.symbol)
        bucket, seconds_in = self._bucket(instrument, now)
        if state is None:
            return True
        if state.get("retry_at") and time.time() >= state["retry_at"]:
            return True
        return bucket != state.get("bucket") and seconds_in >= CANDLE_SETTLE_SECONDS

    @staticmethod
    def _bucket(instrument, now: datetime) -> tuple[int, float]:
        # NSE/BSE sessions start 09:15 (buckets at :05/:15/...), MCX 09:00 (:00/:10/...).
        offset = 0 if exchange_for_instrument(instrument) == MCX else 300
        seconds = now.timestamp() - offset
        return int(seconds // BUCKET_SECONDS), seconds % BUCKET_SECONDS

    def _scan_instruments(self, instruments, interval: str, now: datetime | None = None) -> int:
        now = self._as_ist(now)
        spots = self._batch_spot_prices(instruments, now)
        alerts = 0
        for instrument in instruments:
            try:
                alerts += self._refresh_instrument(instrument, interval, now, spots.get(instrument.symbol))
            except Exception as exc:
                logger.error("❌ Premium screener failed for %s: %s", instrument.symbol, exc, exc_info=True)
                self._schedule_retry(instrument, now)
        return alerts

    def _batch_spot_prices(self, instruments, now: datetime) -> Dict[str, float]:
        """One batched LTP call for all underlyings; falls back to candles per symbol."""
        refs = {}
        for instrument in instruments:
            ref = self.data_manager.resolve_underlying(instrument.symbol, now.date())
            if ref is not None:
                refs[instrument.symbol] = ref
        if not refs:
            return {}
        request: Dict[str, List[int]] = {}
        for ref in refs.values():
            request.setdefault(ref.exchange_segment, []).append(int(ref.security_id))
        prices = self.data_manager.fetch_ltp(request) or {}
        return {
            symbol: float(prices[(ref.exchange_segment, int(ref.security_id))])
            for symbol, ref in refs.items()
            if prices.get((ref.exchange_segment, int(ref.security_id))) is not None
        }

    def _refresh_instrument(self, instrument, interval: str, now: datetime, spot: Optional[float] = None) -> int:
        bucket, _ = self._bucket(instrument, now)
        if spot is None:
            spot = self.fetcher.get_spot_price(instrument, interval)
        if spot is None:
            logger.warning("❌ [%s] No underlying price; retry later", instrument.symbol)
            self._schedule_retry(instrument, now)
            return 0

        contracts = []
        for side in ("CE", "PE"):
            contracts.extend(self.fetcher.resolve_strike_band(instrument, spot, side, now=now))
        if not contracts:
            self._schedule_retry(instrument, now)
            return 0

        keep_ids = {int(contract["security_id"]) for contract in contracts}
        with self._lock:
            for security_id in [
                sid for sid, state in self._monitored.items()
                if state["instrument"].symbol == instrument.symbol and sid not in keep_ids
            ]:
                self._monitored.pop(security_id, None)

        alerts = 0
        fetched = 0
        armed = 0
        for contract in contracts:
            candles = self.fetcher.fetch_option_candles(contract, interval, now)
            if not candles:
                logger.debug("[%s] No candles for %s", instrument.symbol, contract["option_symbol"])
                continue
            receive_mark = now_mark()
            fetched += 1
            reference = self.engine.find_reference(candles)
            contract["premium_ltp"] = round(float(candles[-1]["close"]), 2)
            state = {"contract": contract, "instrument": instrument, "reference": self._slim(reference)}
            signal = self.engine.evaluate_candles(
                instrument.symbol, candles, contract["option_type"], instrument.category
            )
            with self._lock:
                self._monitored[int(contract["security_id"])] = state
            armed += bool(reference and reference["armed"])
            del candles
            if signal and signal.get("breakout_is_latest"):
                alerts += self._emit(state, signal, {"receive": receive_mark, "evaluate": now_mark()}, now)

        if fetched:
            self._refresh_state[instrument.symbol] = {"bucket": bucket, "retry_at": None}
            logger.info(
                "✅ [%s] %s/%s band contracts refreshed (%s armed)",
                instrument.symbol,
                fetched,
                len(contracts),
                armed,
            )
        else:
            logger.warning("❌ [%s] No option candles fetched", instrument.symbol)
            self._schedule_retry(instrument, now)
        return alerts

    def _schedule_retry(self, instrument, now: datetime) -> None:
        bucket, _ = self._bucket(instrument, now)
        self._refresh_state[instrument.symbol] = {
            "bucket": bucket,
            "retry_at": time.time() + self.refresh_retry_seconds,
        }

    @staticmethod
    def _slim(reference: Optional[dict]) -> Optional[dict]:
        if reference is None:
            return None
        slim = {key: value for key, value in reference.items() if key != "window"}
        slim["candle"] = dict(reference["candle"])
        return slim

    def _prune_closed(self, open_now: Iterable[str]) -> None:
        open_now = set(open_now)
        with self._lock:
            for security_id in [
                sid for sid, state in self._monitored.items()
                if exchange_for_instrument(state["instrument"]) not in open_now
            ]:
                self._monitored.pop(security_id, None)
        by_symbol = {i.symbol: i for i in self.option_instruments()}
        for symbol in list(self._refresh_state):
            instrument = by_symbol.get(symbol)
            if instrument is None or exchange_for_instrument(instrument) not in open_now:
                self._refresh_state.pop(symbol, None)

    # ================================================================ live
    def armed_contracts(self) -> List[dict]:
        with self._lock:
            return [
                state for state in self._monitored.values()
                if state.get("reference") and state["reference"].get("armed")
            ]

    def live_check_once(self, now: datetime | None = None) -> int:
        """Poll batched LTPs for armed contracts and trigger on RED-high crosses."""
        now = self._as_ist(now)
        open_now = open_exchanges(now)
        paper_contracts = []
        if self.paper_trade_notifier is not None:
            get_contracts = getattr(self.paper_trade_notifier, "paper_option_contracts", None)
            if callable(get_contracts):
                paper_contracts = [
                    contract for contract in get_contracts()
                    if self._contract_exchange(contract) in open_now
                ]
        armed = [
            state for state in self.armed_contracts()
            if exchange_for_instrument(state["instrument"]) in open_now
        ]
        if not armed and not paper_contracts:
            return 0
        request: Dict[str, List[int]] = {}
        requested_ids: Dict[str, set[int]] = {}
        for state in armed:
            contract = state["contract"]
            segment = contract["exchange_segment"]
            security_id = int(contract["security_id"])
            if security_id not in requested_ids.setdefault(segment, set()):
                request.setdefault(segment, []).append(security_id)
                requested_ids[segment].add(security_id)
        for contract in paper_contracts:
            segment = contract["exchange_segment"]
            security_id = int(contract["security_id"])
            if security_id not in requested_ids.setdefault(segment, set()):
                request.setdefault(segment, []).append(security_id)
                requested_ids[segment].add(security_id)
        prices = self.data_manager.fetch_ltp(request) or {}
        receive_mark = now_mark()
        if not prices:
            return 0

        alerts = 0
        timestamp = now.isoformat(timespec="seconds")
        for state in armed:
            contract = state["contract"]
            price = prices.get((contract["exchange_segment"], int(contract["security_id"])))
            signal = self.engine.evaluate_live_price(
                state["instrument"].symbol,
                state["reference"],
                price,
                contract["option_type"],
                state["instrument"].category,
                timestamp=timestamp,
            )
            if signal is None:
                continue
            contract["premium_ltp"] = round(float(price), 2)
            alerts += self._emit(
                state, signal, {"receive": receive_mark, "evaluate": now_mark()}, now, require_armed=True
            )
        if paper_contracts and self.paper_trade_notifier is not None:
            update_trades = getattr(self.paper_trade_notifier, "update_paper_trades", None)
            if callable(update_trades):
                update_trades(prices, exit_time=now.isoformat(timespec="seconds"))
        return alerts

    @staticmethod
    def _contract_exchange(contract: dict) -> str:
        segment = str(contract.get("exchange_segment") or "").upper()
        if segment.startswith("MCX"):
            return "MCX"
        if segment.startswith("BSE"):
            return "BSE"
        return "NSE"

    # ================================================================ emit
    def _emit(
        self,
        state: dict,
        signal: dict,
        latency_marks: dict,
        now: datetime | None = None,
        require_armed: bool = False,
    ) -> int:
        instrument = state["instrument"]
        contract = state["contract"]
        signal_key = self._signal_key(instrument.category, {**signal, "option_symbol": contract["option_symbol"]})
        with self._lock:
            reference = state.get("reference")
            was_armed = bool(reference.get("armed")) if reference is not None else False
            if require_armed and not was_armed:
                # Another thread already consumed this reference.
                return 0
            if reference is not None:
                # Each RED reference fires at most once (live or candle).
                reference["armed"] = False
            if signal_key in self._processed_signal_keys:
                logger.debug("⚠️ [%s] Duplicate premium signal skipped: %s", instrument.symbol, signal_key)
                return 0
            if not self.live_signal_detector.should_emit(
                signal_key,
                str(signal.get("reference_timestamp", "")),
                str(signal.get("breakout_timestamp", "")),
                now=self._as_ist(now).astimezone(timezone.utc),
            ):
                if reference is not None:
                    reference["armed"] = was_armed
                logger.debug("⚠️ [%s] Non-live/historical signal skipped: %s", instrument.symbol, signal_key)
                return 0
            self._remember_signal_key(signal_key)

        self._enrich_signal(signal, instrument, contract)
        signal["idempotency_key"] = signal_key
        signal["latency_marks"] = {k: v for k, v in latency_marks.items() if v is not None}
        logger.info(
            "🚀 [%s] %s %s %s %s @ %.2f (%s)",
            instrument.category,
            signal["action_text"],
            instrument.symbol,
            contract["option_symbol"],
            contract.get("strike_band"),
            signal["entry"],
            signal.get("trigger"),
        )
        if self._deliver(instrument, signal, contract):
            return 1
        with self._lock:
            self._processed_signal_keys.discard(signal_key)
            forget = getattr(self.live_signal_detector, "forget", None)
            if callable(forget):
                forget(signal_key)
            if reference is not None:
                # Let the live trigger retry a reference whose alert was not delivered.
                reference["armed"] = was_armed
        logger.warning("⚠️ Alert rejected/skipped for %s %s", instrument.symbol, signal["signal"])
        return 0

    def _enrich_signal(self, signal: dict, instrument, contract: dict) -> None:
        now_ist = datetime.now(IST)
        signal.update(
            {
                "symbol": instrument.symbol,
                "underlying": instrument.symbol,
                "category": instrument.category,
                "premium_strategy": True,
                "timeframe": self._display_timeframe(INTERVAL),
                "action_text": "BUY CALL" if contract["option_type"] == "CE" else "BUY PUT",
                "option_symbol": contract["option_symbol"],
                "security_id": contract["security_id"],
                "exchange_segment": contract["exchange_segment"],
                "strike": contract.get("strike"),
                "strike_band": contract.get("strike_band"),
                "expiry": contract.get("expiry"),
                "lot_size": contract.get("lot_size"),
                "tick_size": contract.get("tick_size"),
                "instrument_type": contract.get("instrument_type"),
                "spot_ltp": contract.get("spot_ltp"),
                "premium_ltp": contract.get("premium_ltp"),
                "reference_time_ist": format_ist_timestamp(signal.get("reference_timestamp")),
                "breakout_time_ist": format_ist_timestamp(signal.get("breakout_timestamp")),
                "signal_time_ist": now_ist.strftime("%H:%M:%S"),
                "signal_date_ist": now_ist.strftime("%d:%m:%Y"),
            }
        )

    def _deliver(self, instrument, signal: dict, contract: dict) -> bool:
        if self.trade_control_handler and self.trade_control_bot:
            if self.trade_control_bot.create_and_send_request(signal, contract):
                return True

        if self.signal_callback and self.signal_callback(
            self._build_queue_payload(
                instrument=instrument,
                signal=signal,
                route_category=instrument.category,
                strategy_name="premium_screener",
                option_data=contract,
            )
        ):
            return True

        accepted = self.position_manager.add_position(
            symbol=instrument.symbol,
            side=signal["signal"],
            entry_price=signal["entry"],
            stop_loss=signal["stop_loss"],
            targets=signal["targets"],
        )
        if not accepted:
            return False
        telegram_payload = {
            "option_symbol": contract["option_symbol"],
            "strike_price": contract.get("strike"),
            "premium_ltp": contract.get("premium_ltp", signal["entry"]),
            "option_type": contract["option_type"],
        }
        if self._dispatch_option_alert(instrument.category, signal, telegram_payload):
            logger.info("✅ Alert sent for %s %s", instrument.symbol, signal["signal"])
            return True
        return False

    # ================================================================ helpers
    @staticmethod
    def _display_timeframe(interval: str) -> str:
        return f"{interval.replace('min', '')}-MINUTE BREAKOUT"

    def _dispatch_option_alert(self, category: str, signal: dict, telegram_payload: dict) -> bool:
        if category not in SUPPORTED_SCREENER_CATEGORIES:
            logger.warning("⚠️ Unsupported screener category '%s', alert skipped", category)
            return False

        # Single combined options screener channel: no fan-out to retired
        # NIFTY50 5X / pay-later routes.
        return self.telegram_handler.send_signal_alert(category, signal, telegram_payload)

    @staticmethod
    def _build_queue_payload(
        instrument,
        signal: dict,
        route_category: str,
        strategy_name: str,
        option_data: dict,
    ) -> dict:
        option_type = option_data.get("option_type") or signal.get("option_type")
        target = signal.get("target", (signal.get("targets") or [signal.get("entry")])[0])
        payload = {
            "symbol": instrument.symbol,
            # Queue contract: BUY = buy call premium, SELL = buy put premium (legacy).
            "action": "BUY" if signal.get("signal") == "CALL" else "SELL",
            "entry_price": signal.get("entry"),
            "target_price": target,
            "stop_loss": signal.get("stop_loss"),
            "quantity": 1,
            "strategy": strategy_name,
            "timestamp": signal.get("breakout_timestamp") or datetime.now(IST).isoformat(),
            "category": route_category,
            "reference_timestamp": signal.get("reference_timestamp"),
            "breakout_timestamp": signal.get("breakout_timestamp"),
            "metadata": {
                "source": "scanner",
                "route_category": route_category,
                "signal": signal.get("signal"),
                "timeframe": signal.get("timeframe"),
                "reference_timestamp": signal.get("reference_timestamp"),
                "breakout_timestamp": signal.get("breakout_timestamp"),
                "reference_time_ist": signal.get("reference_time_ist"),
                "breakout_time_ist": signal.get("breakout_time_ist"),
                "premium_strategy": bool(signal.get("premium_strategy")),
                "spot_strategy": bool(signal.get("spot_strategy")),
                "underlying": instrument.symbol,
                "category": route_category,
                "action_text": signal.get("action_text")
                or ("BUY CALL" if option_type == "CE" else "BUY PUT" if option_type == "PE" else None),
                "entry": signal.get("entry"),
                "stop_loss": signal.get("stop_loss"),
                "risk_points": signal.get("risk_points"),
                "target": target,
                "targets": list(signal.get("targets", [])),
                "trigger": signal.get("trigger"),
                "reference_high": signal.get("reference_high"),
                "reference_low": signal.get("reference_low"),
                "option_symbol": option_data.get("option_symbol"),
                "option_type": option_type,
                "strike": option_data.get("strike", option_data.get("atm_strike")),
                "strike_band": option_data.get("strike_band"),
                "expiry": option_data.get("expiry"),
                "security_id": option_data.get("security_id"),
                "exchange_segment": option_data.get("exchange_segment"),
                "lot_size": option_data.get("lot_size"),
                "tick_size": option_data.get("tick_size"),
                "instrument_type": option_data.get("instrument_type"),
                "premium_ltp": option_data.get("premium_ltp"),
                "spot_ltp": option_data.get("spot_ltp"),
            },
        }
        if signal.get("idempotency_key"):
            payload["idempotency_key"] = signal["idempotency_key"]
        if signal.get("latency_marks"):
            payload["metadata"]["latency_marks"] = dict(signal["latency_marks"])
        return payload

    @staticmethod
    def _signal_key(category: str, signal: dict) -> str:
        if signal.get("option_symbol"):
            # Option contract + RED reference: one alert per reference candle.
            return (
                f"{category}:{signal.get('symbol')}:{signal.get('option_symbol')}:"
                f"{signal.get('reference_timestamp')}"
            )
        return (
            f"{category}:{signal.get('symbol')}:{signal.get('signal')}:"
            f"{signal.get('option_type', '')}:{signal.get('reference_timestamp')}:"
            f"{signal.get('breakout_timestamp')}"
        )

    def _remember_signal_key(self, signal_key: str) -> None:
        with self._lock:
            self._processed_signal_keys.add(signal_key)
            self._processed_signal_order.append(signal_key)
            if len(self._processed_signal_order) > 2000:
                expired = self._processed_signal_order.pop(0)
                self._processed_signal_keys.discard(expired)

    def status(self) -> dict:
        with self._lock:
            return {
                "monitored_contracts": len(self._monitored),
                "armed_contracts": sum(
                    1 for s in self._monitored.values() if s.get("reference") and s["reference"].get("armed")
                ),
                "tracked_underlyings": len(self._refresh_state),
            }

    @staticmethod
    def _as_ist(now: datetime | None) -> datetime:
        if now is None:
            return datetime.now(IST)
        if now.tzinfo is None:
            return now.replace(tzinfo=IST)
        return now.astimezone(IST)

    @staticmethod
    def _in_window(now: dt_time, start: dt_time, end: dt_time) -> bool:
        return start <= now <= end
