from typing import Dict


INDEX_AND_COMMODITY_STEP = 100
STOCK_STEP = 50


def _round_half_up(value: float, step: int) -> int:
    quotient = value / step
    rounded = int(quotient)
    remainder = quotient - rounded
    if remainder >= 0.5:
        rounded += 1
    return rounded * step


def calculate_atm_strikes(symbol: str, ltp: float, asset_class: str) -> Dict[str, float]:
    if asset_class in {"INDEX", "COMMODITY"}:
        strike = _round_half_up(ltp, INDEX_AND_COMMODITY_STEP)
    else:
        strike = _round_half_up(ltp, STOCK_STEP)

    volatility_ratio = min(max((abs(ltp - strike) / max(ltp, 1.0)) * 10, 0.015), 0.025)
    premium = round(ltp * volatility_ratio, 2)

    return {
        "ce_strike": strike,
        "pe_strike": strike,
        "premium": premium,
        "premium_pct": round(volatility_ratio * 100, 2),
    }
