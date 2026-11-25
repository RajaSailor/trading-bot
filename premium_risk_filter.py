"""Minimum entry-to-stop and entry-to-target distances for premium alerts."""

from decimal import Decimal, InvalidOperation


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
