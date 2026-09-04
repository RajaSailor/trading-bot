from __future__ import annotations

from typing import Dict


def _round_half_up(value: float, step: int) -> int:
    return int(((value + (step / 2)) // step) * step)


def calculate_atm_strike(ltp: float, instrument_type: str) -> int:
    instrument = instrument_type.upper()
    step = 50 if instrument in {"STOCK", "NIFTY50_STOCK"} else 100
    return _round_half_up(float(ltp), step)


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
