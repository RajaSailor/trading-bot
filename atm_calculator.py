"""
ATM strike calculation utilities.
"""

INDEX_STRIKE_STEP = 100
STOCK_STRIKE_STEP = 50
INDEX_SYMBOLS = {"NIFTY", "BANKNIFTY", "SENSEX"}


def get_strike_step(symbol):
    """Return strike step based on instrument symbol."""
    if not symbol:
        return INDEX_STRIKE_STEP
    return INDEX_STRIKE_STEP if symbol.upper() in INDEX_SYMBOLS else STOCK_STRIKE_STEP


def round_to_step(value, step):
    """Round numeric value to nearest strike step."""
    if value is None or value <= 0 or step <= 0:
        return None
    return int(round(float(value) / step) * step)


def calculate_atm_strikes(symbol, ltp):
    """
    Calculate ATM strike and return CE/PE ATM strikes.
    For ATM options, both CE and PE strike are the same ATM level.
    """
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


def get_premium_percent(volatility_percent):
    """Return premium percent between 1.5% and 2.5% based on volatility."""
    if volatility_percent is None:
        return 1.5
    if volatility_percent < 0.8:
        return 1.5
    if volatility_percent < 1.5:
        return 2.0
    return 2.5


def calculate_option_premium(ltp, premium_percent):
    """Calculate option premium estimate from LTP and premium percent."""
    if ltp is None or ltp <= 0:
        return 0.0
    return round(float(ltp) * (float(premium_percent) / 100.0), 2)
