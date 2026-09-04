from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional


@dataclass
class Position:
    position_id: str
    symbol: str
    side: str
    entry_price: float
    stop_loss: float
    targets: List[float]
    entry_time: str
    targets_hit: List[float] = field(default_factory=list)
    sl_triggered: bool = False
    sl_miss_alerted: bool = False


class PositionManager:
    def __init__(self, max_positions: int = 5) -> None:
        self.max_positions = max_positions
        self._positions: Dict[str, Position] = {}
        self._counter = 0

    def add_position(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        stop_loss: float,
        targets: List[float],
        confirm_sixth: Optional[Callable[[dict], bool]] = None,
    ) -> Optional[Position]:
        if len(self._positions) >= self.max_positions:
            if not confirm_sixth or not confirm_sixth(
                {
                    "symbol": symbol,
                    "side": side,
                    "entry_price": entry_price,
                    "stop_loss": stop_loss,
                    "targets": targets,
                }
            ):
                return None

        self._counter += 1
        position_id = f"POS-{self._counter:05d}"
        position = Position(
            position_id=position_id,
            symbol=symbol,
            side=side,
            entry_price=float(entry_price),
            stop_loss=float(stop_loss),
            targets=[float(target) for target in targets],
            entry_time=datetime.utcnow().isoformat(),
        )
        self._positions[position_id] = position
        return position

    def update_price(
        self,
        position_id: str,
        ltp: float,
        sl_miss_callback: Optional[Callable[[Position, float], None]] = None,
    ) -> Optional[Position]:
        position = self._positions.get(position_id)
        if not position:
            return None

        price = float(ltp)
        for target in position.targets:
            if target not in position.targets_hit:
                if position.side == "CALL" and price >= target:
                    position.targets_hit.append(target)
                elif position.side == "PUT" and price <= target:
                    position.targets_hit.append(target)

        if not position.sl_miss_alerted:
            sl_missed = (position.side == "CALL" and price < position.stop_loss) or (
                position.side == "PUT" and price > position.stop_loss
            )
            if sl_missed:
                position.sl_miss_alerted = True
                if sl_miss_callback:
                    sl_miss_callback(position, price)

        sl_hit = (position.side == "CALL" and price <= position.stop_loss) or (
            position.side == "PUT" and price >= position.stop_loss
        )
        if sl_hit:
            position.sl_triggered = True

        return position

    def close_position(self, position_id: str) -> None:
        self._positions.pop(position_id, None)

    def get_open_positions(self) -> List[dict]:
        return [
            {
                "position_id": p.position_id,
                "symbol": p.symbol,
                "side": p.side,
                "entry_price": p.entry_price,
                "entry_time": p.entry_time,
                "targets": p.targets,
                "targets_hit": p.targets_hit,
                "stop_loss": p.stop_loss,
                "sl_triggered": p.sl_triggered,
                "sl_miss_alerted": p.sl_miss_alerted,
            }
            for p in self._positions.values()
        ]
