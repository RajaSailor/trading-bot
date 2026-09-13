from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class TrailingStopState:
    side: str
    entry_price: float
    current_stop_loss: float
    trail_interval: float = 5.0
    c2c_moved: bool = False
    next_trigger_price: float = 0.0
    targets: List[float] | None = None
    exit_reason: Optional[str] = None
    exited: bool = False


class TrailingStopManager:
    """C2C move + 5-point trailing stop and auto-exit checks."""

    def initialize(self, side: str, entry_price: float, stop_loss: float, targets: List[float] | None = None) -> TrailingStopState:
        side_up = side.upper()
        entry = float(entry_price)
        state = TrailingStopState(
            side=side_up,
            entry_price=entry,
            current_stop_loss=float(stop_loss),
            next_trigger_price=entry + 5 if side_up == "CALL" else entry - 5,
            targets=targets or [entry + 20, entry + 40, entry + 60],
        )
        return state

    def on_entry_filled(self, state: TrailingStopState, fill_price: float) -> TrailingStopState:
        entry = float(fill_price)
        state.entry_price = entry
        state.next_trigger_price = entry + state.trail_interval if state.side == "CALL" else entry - state.trail_interval
        return state

    def evaluate(self, state: TrailingStopState, ltp: float) -> dict:
        if state.exited:
            return self._snapshot(state, ltp)

        price = float(ltp)
        self._apply_trailing(state, price)
        self._check_target_or_sl(state, price)
        return self._snapshot(state, price)

    def _apply_trailing(self, state: TrailingStopState, price: float) -> None:
        if state.side == "CALL":
            while price >= state.next_trigger_price:
                if not state.c2c_moved:
                    state.current_stop_loss = state.entry_price
                    state.c2c_moved = True
                else:
                    state.current_stop_loss += state.trail_interval
                state.next_trigger_price += state.trail_interval
        else:
            while price <= state.next_trigger_price:
                if not state.c2c_moved:
                    state.current_stop_loss = state.entry_price
                    state.c2c_moved = True
                else:
                    state.current_stop_loss -= state.trail_interval
                state.next_trigger_price -= state.trail_interval

    def _check_target_or_sl(self, state: TrailingStopState, price: float) -> None:
        if state.side == "CALL":
            if any(price >= target for target in (state.targets or [])):
                state.exit_reason = "TARGET_HIT"
                state.exited = True
                return
            if price <= state.current_stop_loss:
                state.exit_reason = "STOP_LOSS_HIT"
                state.exited = True
                return
        else:
            if any(price <= target for target in (state.targets or [])):
                state.exit_reason = "TARGET_HIT"
                state.exited = True
                return
            if price >= state.current_stop_loss:
                state.exit_reason = "STOP_LOSS_HIT"
                state.exited = True
                return

    @staticmethod
    def _snapshot(state: TrailingStopState, price: float) -> dict:
        return {
            "price": round(price, 2),
            "current_stop_loss": round(state.current_stop_loss, 2),
            "next_trigger_price": round(state.next_trigger_price, 2),
            "c2c_moved": state.c2c_moved,
            "exited": state.exited,
            "exit_reason": state.exit_reason,
        }
