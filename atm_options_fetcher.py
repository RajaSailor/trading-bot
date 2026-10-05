from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Iterable, List, Optional, Sequence

from data_manager import DataManager, Instrument, IST, _apply_rate_limit, _parse_interval_minutes, get_shared_data_manager
from exchange_calendar import BSE, MCX, NSE, previous_trading_day
from security_master import OptionContract


logger = logging.getLogger(__name__)

BAND_ITM_PLUS_1 = "ITM+1"
BAND_ATM = "ATM"
BAND_OTM_PLUS_1 = "OTM+1"
STRIKE_BAND = (BAND_ITM_PLUS_1, BAND_ATM, BAND_OTM_PLUS_1)

# Backward-compatible name for the ATM selection reason.
REASON_ATM = BAND_ATM

_SEGMENT_EXCHANGE = {"NSE_FNO": NSE, "BSE_FNO": BSE, "MCX_COMM": MCX}


class ATMOptionsFetcher:
    """Resolve listed option contracts and fetch their premium candles.

    Contract rules (driven entirely by the Dhan security master, no strike-step
    assumptions):
      * expiry: nearest listed expiry strictly after today (IST). On an expiry
        day the same-day contract is skipped and the next expiry is used from the
        start of the day.
      * strikes: ATM = listed strike nearest to spot; ITM+1 / OTM+1 are the
        adjacent listed strikes (CE: ITM is lower, PE: ITM is higher).
    """

    def __init__(self, data_manager: Optional[DataManager] = None) -> None:
        self.data_manager = data_manager or get_shared_data_manager()

    # ------------------------------------------------------------------ spot
    def get_spot_price(self, instrument: Instrument, interval: str = "10min") -> Optional[float]:
        candles = self.data_manager.fetch_candles(instrument, interval)
        if not candles:
            logger.debug("No underlying candles available for %s", instrument.symbol)
            return None
        spot = self._parse_float(candles[-1].get("close"))
        if spot is None or spot <= 0:
            logger.debug("Invalid underlying price for %s", instrument.symbol)
            return None
        return spot

    # ------------------------------------------------------------ contracts
    def resolve_strike_band(
        self,
        instrument: Instrument,
        spot_price: float,
        option_type: str,
        now: Optional[datetime] = None,
        bands: Sequence[str] = STRIKE_BAND,
    ) -> List[dict]:
        """Listed CE/PE contracts for the ITM+1 / ATM / OTM+1 band around spot."""
        option_side = option_type.upper()
        today = self._ist_date(now)
        chain = self._option_chain(instrument, option_side, today)
        if chain is None:
            return []
        expiry, contracts = chain
        strikes = [contract.strike for contract in contracts]
        atm_index = self._nearest_index(strikes, spot_price)
        itm_step = -1 if option_side == "CE" else 1
        offsets = {BAND_ITM_PLUS_1: itm_step, BAND_ATM: 0, BAND_OTM_PLUS_1: -itm_step}

        resolved: List[dict] = []
        for band in bands:
            position = atm_index + offsets[band]
            if position < 0 or position >= len(contracts):
                logger.debug(
                    "[%s] %s %s strike not listed beyond the chain edge", instrument.symbol, option_side, band
                )
                continue
            resolved.append(
                self._contract_dict(instrument, contracts[position], band, contracts[atm_index].strike, spot_price)
            )

        if resolved:
            logger.info(
                "🎯 [%s] %s band | spot=%.2f | expiry=%s | %s",
                instrument.symbol,
                option_side,
                float(spot_price),
                expiry.strftime("%d%b%Y").upper(),
                " ".join(f"{item['strike_band']}={item['strike']}" for item in resolved),
            )
        return resolved

    def _resolve_option_contract(
        self,
        instrument: Instrument,
        spot_price: float,
        option_type: str,
        now: Optional[datetime] = None,
    ) -> Optional[dict]:
        """ATM contract of the tradable expiry (kept for backward compatibility)."""
        band = self.resolve_strike_band(instrument, spot_price, option_type, now=now, bands=(BAND_ATM,))
        return band[0] if band else None

    def _option_chain(self, instrument: Instrument, option_side: str, today: date):
        index = self.data_manager.security_index(today)
        if index is None:
            return None
        entry = self.data_manager.universe_spec.resolve(instrument.symbol)
        if entry is None:
            logger.warning("[%s] Not part of the configured option universe", instrument.symbol)
            return None
        chain = index.option_chain(entry.root, option_side)
        if not chain or not chain[1]:
            logger.warning("[%s] No listed %s contracts after %s", instrument.symbol, option_side, today.isoformat())
            return None
        return chain

    def _contract_dict(
        self,
        instrument: Instrument,
        contract: OptionContract,
        band: str,
        atm_strike: float,
        spot_price: float,
    ) -> dict:
        strike_value = self._display_strike(contract.strike)
        lot_size = contract.lot_size
        if isinstance(lot_size, float) and lot_size > 0 and lot_size.is_integer():
            lot_size = int(lot_size)
        return {
            "security_id": int(contract.security_id),
            "option_symbol": contract.trading_symbol,
            "exchange_segment": contract.exchange_segment,
            "instrument_type": contract.instrument_type,
            "underlying": instrument.symbol,
            "category": instrument.category,
            "strike": strike_value,
            "selected_strike": strike_value,
            # Historical field name: the strike actually traded.
            "atm_strike": strike_value,
            "listed_atm_strike": self._display_strike(atm_strike),
            "strike_band": band,
            "strike_reason": band,
            "spot_ltp": round(float(spot_price), 2),
            "option_type": contract.option_type,
            "expiry": contract.expiry.strftime("%d%b%Y").upper(),
            "expiry_date": contract.expiry.isoformat(),
            "lot_size": lot_size,
            "tick_size": contract.tick_size,
        }

    # --------------------------------------------------------------- candles
    def fetch_option_candles(self, contract: dict, interval: str = "10min", now: Optional[datetime] = None) -> List[dict]:
        """Premium candles for ``contract`` including the previous session for lookback."""
        today = self._ist_date(now)
        exchange = _SEGMENT_EXCHANGE.get(str(contract.get("exchange_segment", "")).upper(), NSE)
        from_date = previous_trading_day(exchange, today).strftime("%Y-%m-%d")
        return self.data_manager.fetch_contract_candles(
            security_id=contract["security_id"],
            exchange_segment=contract["exchange_segment"],
            instrument_type=contract["instrument_type"],
            from_date=from_date,
            to_date=today.strftime("%Y-%m-%d"),
            interval=_parse_interval_minutes(interval),
            symbol=contract["option_symbol"],
            now=now,
        )

    def fetch_atm_premium_candles(
        self,
        instrument: Instrument,
        option_type: str,
        interval: str = "10min",
    ) -> tuple[List[dict], Optional[dict]]:
        spot_price = self.get_spot_price(instrument, interval)
        if spot_price is None:
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

        candles = self.fetch_option_candles(contract, interval)
        if not candles:
            return [], contract

        contract["premium_ltp"] = round(float(candles[-1]["close"]), 2)
        return candles, contract

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _nearest_index(strikes: List[float], spot_price: float) -> int:
        # Ties resolve to the higher strike (half-up), matching prior rounding behaviour.
        return min(range(len(strikes)), key=lambda i: (abs(strikes[i] - spot_price), -strikes[i]))

    @staticmethod
    def _nearest_listed_strike(strikes: Iterable[float], spot_price: float) -> float:
        return min(strikes, key=lambda strike: (abs(strike - spot_price), -strike))

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

    @staticmethod
    def _parse_float(value: object) -> Optional[float]:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
