from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import date, datetime
from typing import Dict, List, Optional

from data_manager import DataManager, Instrument, IST, _apply_rate_limit, _normalize_stock_symbol
from nse_symbol_mapping import NSESymbolMapper


logger = logging.getLogger(__name__)

REASON_ATM = "ATM"
REASON_EXPIRY_ITM_PLUS_1 = "EXPIRY_ITM_PLUS_1"


class ATMOptionsFetcher:
    """Fetch option premium candles for a listed strike selected from the security master.

    Strike selection is contract-driven: the nearest listed strike to the spot
    price (ATM) of the nearest active expiry is used, except on that expiry's
    day (IST) where the strike one listed step in-the-money (ITM+1) is used.
    No premium-value thresholds are applied to the selection.
    """

    def __init__(self, data_manager: Optional[DataManager] = None) -> None:
        self.data_manager = data_manager or DataManager()

    def fetch_atm_premium_candles(
        self,
        instrument: Instrument,
        option_type: str,
        interval: str = "10min",
    ) -> tuple[List[dict], Optional[dict]]:
        underlying_candles = self.data_manager.fetch_candles(instrument, interval)
        if not underlying_candles:
            logger.debug("No underlying candles available for %s", instrument.symbol)
            return [], None

        spot_price = self._parse_float(underlying_candles[-1].get("close"))
        if spot_price is None or spot_price <= 0:
            logger.debug("Invalid underlying price for %s", instrument.symbol)
            return [], None

        contract = self._resolve_option_contract(instrument, spot_price, option_type)
        if contract is None:
            logger.warning(
                "Could not resolve listed %s option contract for %s (spot=%.2f)",
                option_type.upper(),
                instrument.symbol,
                spot_price,
            )
            return [], None

        _apply_rate_limit()
        today = datetime.now(IST).strftime("%Y-%m-%d")
        candles = self.data_manager._fetch_dhan_intraday_data(
            security_id=contract["security_id"],
            exchange_segment=contract["exchange_segment"],
            instrument_type=contract["instrument_type"],
            from_date=today,
            to_date=today,
            interval=int(interval.replace("min", "")),
            symbol=contract["option_symbol"],
        )

        if not candles:
            return [], contract

        contract["premium_ltp"] = round(float(candles[-1]["close"]), 2)
        return candles, contract

    def _resolve_option_contract(
        self,
        instrument: Instrument,
        spot_price: float,
        option_type: str,
        now: Optional[datetime] = None,
    ) -> Optional[dict]:
        securities = self.data_manager._fetch_security_master_with_cache()
        if not securities:
            return None

        option_side = option_type.upper()
        today = self._ist_date(now)
        candidates = self._collect_option_candidates(instrument, securities, option_side, today)
        if not candidates:
            return None

        selected_expiry = min(candidates)
        contracts_by_strike = candidates[selected_expiry]
        strikes = sorted(contracts_by_strike)
        atm_strike = self._nearest_listed_strike(strikes, spot_price)

        selected_strike = atm_strike
        reason = REASON_ATM
        if selected_expiry == today:
            itm_strike = self._itm_plus_one_strike(strikes, atm_strike, option_side)
            if itm_strike is not None:
                selected_strike = itm_strike
                reason = REASON_EXPIRY_ITM_PLUS_1
            else:
                logger.info(
                    "[%s] %s expiry day but no ITM strike listed beyond ATM %s; using ATM",
                    instrument.symbol,
                    option_side,
                    self._display_strike(atm_strike),
                )

        security, instrument_type = contracts_by_strike[selected_strike]
        strike_value = self._display_strike(selected_strike)
        contract = {
            "security_id": int(security["SEM_SMST_SECURITY_ID"]),
            "option_symbol": str(
                security.get("SEM_TRADING_SYMBOL")
                or security.get("SEM_CUSTOM_SYMBOL")
                or f"{instrument.symbol}-{strike_value}-{option_side}"
            ),
            "exchange_segment": self._option_exchange_segment(instrument),
            "instrument_type": instrument_type,
            "atm_strike": strike_value,
            "selected_strike": strike_value,
            "listed_atm_strike": self._display_strike(atm_strike),
            "strike_reason": reason,
            "spot_ltp": round(float(spot_price), 2),
            "option_type": option_side,
            "expiry": selected_expiry.strftime("%d%b%Y").upper(),
        }
        logger.info(
            "🎯 [%s] %s contract selected | spot=%.2f | expiry=%s | strike=%s | reason=%s | symbol=%s",
            instrument.symbol,
            option_side,
            float(spot_price),
            contract["expiry"],
            strike_value,
            reason,
            contract["option_symbol"],
        )
        return contract

    def _collect_option_candidates(
        self,
        instrument: Instrument,
        securities: List[dict],
        option_side: str,
        today: date,
    ) -> Dict[date, Dict[float, tuple[dict, str]]]:
        candidate_symbols = {
            self._normalized_symbol(name)
            for name in NSESymbolMapper.get_possible_dhan_names(instrument.symbol)
        }
        candidate_symbols.add(self._normalized_symbol(instrument.symbol))
        candidates: Dict[date, Dict[float, tuple[dict, str]]] = {}

        for security in securities:
            if str(security.get("SEM_OPTION_TYPE", "")).upper() != option_side:
                continue

            exchange_id = str(security.get("SEM_EXM_EXCH_ID", "")).upper()
            if not self._matches_option_exchange(instrument, exchange_id):
                continue

            instrument_type = self._option_instrument_type(security)
            if "OPT" not in instrument_type:
                continue

            if not self._matches_option_symbol(instrument, security, candidate_symbols):
                continue

            strike = self._parse_float(security.get("SEM_STRIKE_PRICE"))
            if strike is None or strike <= 0:
                continue

            expiry_date = self._parse_expiry(security.get("SEM_EXPIRY_DATE"))
            if expiry_date is None or expiry_date.date() < today:
                continue

            try:
                int(security["SEM_SMST_SECURITY_ID"])
            except (KeyError, TypeError, ValueError):
                continue

            by_strike = candidates.setdefault(expiry_date.date(), {})
            by_strike.setdefault(strike, (security, instrument_type))

        return candidates

    @staticmethod
    def _nearest_listed_strike(strikes: List[float], spot_price: float) -> float:
        # Ties resolve to the higher strike (half-up), matching prior rounding behaviour.
        return min(strikes, key=lambda strike: (abs(strike - spot_price), -strike))

    @staticmethod
    def _strike_step(strikes: List[float]) -> Optional[float]:
        diffs = [round(b - a, 6) for a, b in zip(strikes, strikes[1:]) if b > a]
        if not diffs:
            return None
        counts = Counter(diffs)
        return min(counts, key=lambda diff: (-counts[diff], diff))

    @classmethod
    def _itm_plus_one_strike(
        cls,
        strikes: List[float],
        atm_strike: float,
        option_side: str,
    ) -> Optional[float]:
        step = cls._strike_step(strikes)
        if step is None:
            return None

        # CE moves ITM to lower strikes; PE moves ITM to higher strikes.
        direction = -1 if option_side == "CE" else 1
        target = atm_strike + direction * step
        tolerance = step * 1e-6
        for strike in strikes:
            if abs(strike - target) <= tolerance:
                return strike

        further_itm = [
            strike for strike in strikes
            if (strike - target) * direction > 0
        ]
        if not further_itm:
            return None
        return min(further_itm, key=lambda strike: abs(strike - target))

    @staticmethod
    def _display_strike(strike: float) -> float | int:
        return int(strike) if float(strike).is_integer() else strike

    @staticmethod
    def _ist_date(now: Optional[datetime]) -> date:
        if now is None:
            return datetime.now(IST).date()
        if now.tzinfo is None:
            return now.date()
        return now.astimezone(IST).date()

    @classmethod
    def _matches_option_symbol(
        cls,
        instrument: Instrument,
        security: dict,
        candidate_symbols: set[str],
    ) -> bool:
        if instrument.category != "commodity_options":
            security_symbol = cls._normalized_symbol(str(security.get("SM_SYMBOL_NAME", "")))
            return security_symbol in candidate_symbols

        for symbol_value in cls._security_symbol_values(security):
            normalized_symbol = cls._normalized_symbol(symbol_value)
            if normalized_symbol in candidate_symbols:
                return True

            commodity_root = cls._commodity_symbol_root(normalized_symbol)
            if commodity_root and commodity_root in candidate_symbols:
                return True

        return False

    @staticmethod
    def _security_symbol_values(security: dict) -> tuple[str, ...]:
        return (
            str(security.get("SM_SYMBOL_NAME", "")),
            str(security.get("SEM_TRADING_SYMBOL", "")),
            str(security.get("SEM_CUSTOM_SYMBOL", "")),
        )

    @staticmethod
    def _commodity_symbol_root(symbol: str) -> str:
        match = re.match(r"[A-Z]+", symbol)
        return match.group(0) if match else ""

    @staticmethod
    def _option_exchange_segment(instrument: Instrument) -> str:
        if instrument.category == "commodity_options":
            return "MCX_COMM"
        if instrument.symbol == "SENSEX":
            return "BSE_FNO"
        return "NSE_FNO"

    @staticmethod
    def _option_instrument_type(security: dict) -> str:
        return str(
            security.get("SEM_EXCH_INSTRUMENT_TYPE")
            or security.get("SEM_INSTRUMENT_NAME")
            or ""
        ).upper()

    @staticmethod
    def _matches_option_exchange(instrument: Instrument, exchange_id: str) -> bool:
        if instrument.category == "commodity_options":
            return "MCX" in exchange_id
        if instrument.symbol == "SENSEX":
            return "BSE" in exchange_id
        return "NSE" in exchange_id

    @staticmethod
    def _parse_expiry(value: object) -> Optional[datetime]:
        text = str(value or "").strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
        return None

    @staticmethod
    def _parse_float(value: object) -> Optional[float]:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _normalized_symbol(symbol: str) -> str:
        normalized = _normalize_stock_symbol(symbol.upper())
        return "".join(ch for ch in normalized if ch.isalnum())
