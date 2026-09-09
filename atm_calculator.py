from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Dict

INDEX_STRIKE_STEP = 100
STOCK_STRIKE_STEP = 50
INDEX_SYMBOLS = {"NIFTY", "BANKNIFTY", "SENSEX"}


def _round_half_up(value: float, step: int) -> int:
    scaled = Decimal(str(value)) / Decimal(str(step))
    rounded = scaled.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(rounded * Decimal(str(step)))


def get_strike_step(symbol: str | None) -> int:
    if not symbol:
        return INDEX_STRIKE_STEP
    return INDEX_STRIKE_STEP if symbol.upper() in INDEX_SYMBOLS else STOCK_STRIKE_STEP


def round_to_step(value: float, step: int) -> int | None:
    if value is None or value <= 0 or step <= 0:
        return None
    return _round_half_up(float(value), int(step))


def calculate_atm_strike(ltp: float, instrument_type: str) -> int:
    instrument = instrument_type.upper()
    step = STOCK_STRIKE_STEP if instrument in {"STOCK", "NIFTY50_STOCK"} else INDEX_STRIKE_STEP
    return _round_half_up(float(ltp), step)


def calculate_atm_strikes(symbol: str | None, ltp: float) -> Dict[str, int] | None:
    step = get_strike_step(symbol)
    atm_strike = round_to_step(ltp, step)
    if atm_strike is None:
        return None
    return {
        "atm_strike": atm_strike,
        "call_strike": atm_strike,
        "put_strike": atm_strike,
        "step": step,
    }


def get_option_premium(atm_strike: int, ltp: float) -> Dict[str, float]:
    base = max(float(ltp), float(atm_strike))
    call_premium = round(base * 0.02, 2)
    put_premium = round(base * 0.015, 2)
    return {
        "call_premium": call_premium,
        "put_premium": put_premium,
    }


def calculate_option_details(ltp: float, instrument_type: str) -> Dict[str, float]:
    atm = calculate_atm_strike(ltp, instrument_type)
    premiums = get_option_premium(atm, ltp)
    return {
        "call_strike": atm,
        "put_strike": atm,
        "call_premium": premiums["call_premium"],
        "put_premium": premiums["put_premium"],
    }


def get_premium_percent(volatility_percent: float | None) -> float:
    if volatility_percent is None:
        return 1.5
    if volatility_percent < 0.8:
        return 1.5
    if volatility_percent < 1.5:
        return 2.0
    return 2.5


def calculate_option_premium(ltp: float, premium_percent: float) -> float:
    if ltp is None or ltp <= 0:
        return 0.0
    return round(float(ltp) * (float(premium_percent) / 100.0), 2)
