"""Completed-candle sideways filter for one exact option contract."""

from decimal import Decimal

from premium_indicators import IndicatorDataError, completed_candles


def rejection_reason(five, one, price, stamp, now):
    bars = [bar for bar in completed_candles(five, now, timeframe_seconds=300)
            if bar["_timestamp_epoch"] + 300 <= stamp]
    if len(bars) < 2:
        return "sideways_history_missing"
    if stamp - (bars[-1]["_timestamp_epoch"] + 300) > 375:
        return "sideways_history_stale"
    count, sideways, breakout = 1, False, None
    for previous, current in zip(bars, bars[1:]):
        if current["_timestamp_epoch"] - previous["_timestamp_epoch"] != 300:
            count, sideways, breakout = 1, False, None
            continue
        midpoint = (Decimal(str(previous["high"])) + Decimal(str(previous["low"]))) / 2
        lower, upper = midpoint * Decimal(".94"), midpoint * Decimal("1.06")
        close = Decimal(str(current["close"]))
        breakout = None
        if lower <= close <= upper:
            count += 1
            sideways = sideways or count >= 2
        elif close > upper:
            if sideways:
                breakout = current
            count, sideways = 1, False
        else:
            # A downward escape is not permission to buy inside the old range.
            count = 1
    if sideways:
        return "sideways_range"
    if breakout is None:
        return None
    end = breakout["_timestamp_epoch"] + 300
    if stamp - end > 375:
        return "sideways_breakout_stale"
    minutes = [bar for bar in completed_candles(one, now, timeframe_seconds=60)
               if end <= bar["_timestamp_epoch"] and bar["_timestamp_epoch"] + 60 <= stamp]
    if not minutes or stamp - (minutes[-1]["_timestamp_epoch"] + 60) > 75:
        return "sideways_one_minute_confirmation_missing"
    if minutes[-1]["close"] <= breakout["high"] or price <= breakout["high"]:
        return "sideways_breakout_high_not_cleared"
    return None


def signal_rejection(metadata, now):
    """Apply route-specific confirmation, rather than a second universal breakout."""
    try:
        from premium_structure import STRUCTURE_POLICY
        from premium_patterns import CANDLESTICK_PATTERN_NAMES, PATTERN_POLICY

        evidence = metadata.get("structure_evidence")
        if evidence is not None:
            five, one = evidence["five"], evidence["one"]
            if metadata.get("entry_policy") == STRUCTURE_POLICY:
                # The unchanged order-block touch route still needs its range gate.
                if evidence["candidate"]["route"] != "order_block_retest":
                    return None
        else:
            five, one = metadata["entry_candles"], metadata["entry_one_minute_candles"]
            if metadata.get("entry_policy") == PATTERN_POLICY:
                pattern = metadata["premium_pattern"]
                if pattern["name"] not in CANDLESTICK_PATTERN_NAMES:
                    return None
                bars = completed_candles(five, now, timeframe_seconds=300)
                end = pattern["confirmed_end"]
                prior = [bar for bar in bars if bar["_timestamp_epoch"] + 300 < end]
                if len(prior) < 2:
                    return "sideways_history_missing"
                if pattern["boundary"] <= max(bar["high"] for bar in prior[-2:]):
                    return "pattern_range_not_cleared"
                return None
        return rejection_reason(five, one, float(metadata["breakout_price"]),
                                float(metadata["breakout_timestamp"]), now)
    except IndicatorDataError as exc:
        return exc.reason
    except (KeyError, TypeError, ValueError, OverflowError):
        return "sideways_evidence_missing"
