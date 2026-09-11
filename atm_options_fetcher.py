from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Dict, List, Optional

from atm_calculator import calculate_atm_strikes
from data_manager import DataManager, Instrument, IST, _apply_rate_limit, _normalize_stock_symbol
from nse_symbol_mapping import NSESymbolMapper


logger = logging.getLogger(__name__)


class ATMOptionsFetcher:
    """Fetch ATM option premium candles for the current underlying price."""

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

        atm = calculate_atm_strikes(instrument.symbol, underlying_candles[-1]["close"])
        if not atm:
            logger.debug("Could not calculate ATM strike for %s", instrument.symbol)
            return [], None

        contract = self._resolve_option_contract(instrument, atm["atm_strike"], option_type)
        if contract is None:
            logger.warning("Could not resolve %s ATM option for %s", option_type, instrument.symbol)
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
        atm_strike: int,
        option_type: str,
    ) -> Optional[dict]:
        securities = self.data_manager._fetch_security_master_with_cache()
        if not securities:
            return None

        option_side = option_type.upper()
        candidate_symbols = {
            self._normalized_symbol(name)
            for name in NSESymbolMapper.get_possible_dhan_names(instrument.symbol)
        }
        target_symbol = self._normalized_symbol(instrument.symbol)
        candidate_symbols.add(target_symbol)
        preferred_expiry = None
        best_match = None

        for security in securities:
            if not self._matches_option_symbol(instrument, security, candidate_symbols):
                continue

            exchange_id = str(security.get("SEM_EXM_EXCH_ID", "")).upper()
            if not self._matches_option_exchange(instrument, exchange_id):
                continue

            security_option_type = str(security.get("SEM_OPTION_TYPE", "")).upper()
            if security_option_type != option_side:
                continue

            instrument_type = self._option_instrument_type(security)
            if "OPT" not in instrument_type:
                continue

            strike = self._parse_float(security.get("SEM_STRIKE_PRICE"))
            if strike is None or round(strike) != int(atm_strike):
                continue

            expiry_date = self._parse_expiry(security.get("SEM_EXPIRY_DATE"))
            if expiry_date is None:
                continue

            candidate = {
                "security_id": int(security["SEM_SMST_SECURITY_ID"]),
                "option_symbol": str(
                    security.get("SEM_TRADING_SYMBOL")
                    or security.get("SEM_CUSTOM_SYMBOL")
                    or f"{instrument.symbol}-{atm_strike}-{option_side}"
                ),
                "exchange_segment": self._option_exchange_segment(instrument),
                "instrument_type": instrument_type,
                "atm_strike": int(atm_strike),
                "option_type": option_side,
                "expiry": expiry_date.strftime("%d%b%Y").upper(),
            }

            if preferred_expiry is None or expiry_date < preferred_expiry:
                preferred_expiry = expiry_date
                best_match = candidate

        return best_match

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
