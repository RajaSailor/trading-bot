"""Minimum entry-to-stop and entry-to-target distances for premium alerts."""

from decimal import Decimal, InvalidOperation, ROUND_FLOOR


def minimum_distance_stop(entry, candle_low, tick):
    """Keep wider candle stops; round a minimum 5% distance away from entry."""
    entry, candle_low, tick = (Decimal(str(value)) for value in (entry, candle_low, tick))
    if not all(value.is_finite() and value > 0 for value in (entry, candle_low, tick)):
        raise ValueError("invalid_stop_levels")
    if candle_low >= entry:
        raise ValueError("candle_stop_not_below_entry")
    stop = (min(candle_low, entry * Decimal(".95")) / tick).to_integral_value(
        rounding=ROUND_FLOOR) * tick
    if stop <= 0:
        raise ValueError("invalid_stop_levels")
    return float(stop)


def rejection_reason(entry, stop, target):
    try:
        entry, stop, target = (Decimal(str(value)) for value in (entry, stop, target))
        if not all(value.is_finite() for value in (entry, stop, target)):
            return "invalid_levels"
        if not 0 < stop < entry < target:
            return "invalid_levels"
        risk = entry - stop
        reward = target - entry
        if risk < entry * Decimal("0.05"):
            return "stop_distance_below_5_percent"
        if reward < entry * Decimal("0.10"):
            return "target_distance_below_10_percent"
        if reward < risk:
            return "reward_below_1r"
    except (InvalidOperation, ValueError, TypeError):
        return "invalid_levels"
    return None
