from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional


@dataclass
class Position:
    id: str
    symbol: str
    timeframe: str
    side: str
    entry: float
    stop_loss: float
    target_1: float
    target_2: float
    target_3: float
    entry_time: str
    status: str = "OPEN"
    targets_hit: List[str] = field(default_factory=list)


class PositionManager:
    def __init__(self, max_positions: int = 5):
        self.max_positions = max_positions
        self.open_positions: Dict[str, Position] = {}
        self.pending_confirmation: Optional[dict] = None

    def can_open_position(self) -> bool:
        return len(self.open_positions) < self.max_positions

    def _insert_position(self, signal: dict) -> None:
        position_id = signal["position_id"]
        self.open_positions[position_id] = Position(
            id=position_id,
            symbol=signal["symbol"],
            timeframe=signal["timeframe"],
            side=signal["side"],
            entry=signal["entry"],
            stop_loss=signal["stop_loss"],
            target_1=signal["target_1"],
            target_2=signal["target_2"],
            target_3=signal["target_3"],
            entry_time=signal.get("entry_time", datetime.now(timezone.utc).isoformat()),
        )

    def register_signal(self, signal: dict) -> dict:
        if not self.can_open_position():
            self.pending_confirmation = signal
            return {"requires_confirmation": True, "open_positions": len(self.open_positions)}

        self._insert_position(signal)
        return {"requires_confirmation": False, "open_positions": len(self.open_positions)}

    def confirm_pending_position(self) -> bool:
        return self.confirm_pending_signal() is not None

    def confirm_pending_signal(self) -> Optional[dict]:
        if not self.pending_confirmation:
            return None
        pending = self.pending_confirmation
        self.pending_confirmation = None
        pending["position_id"] = pending.get("position_id") or f"{pending['symbol']}-{pending['breakout_time']}-{pending['side']}"
        self._insert_position(pending)
        return pending

    def update_price(self, symbol: str, timeframe: str, ltp: float) -> List[dict]:
        events = []
        for position in self.open_positions.values():
            if position.symbol != symbol or position.timeframe != timeframe or position.status != "OPEN":
                continue

            if position.side == "PUT":
                target_1_hit = ltp <= position.target_1
                target_2_hit = ltp <= position.target_2
                target_3_hit = ltp <= position.target_3
                sl_hit = ltp >= position.stop_loss
            else:
                target_1_hit = ltp >= position.target_1
                target_2_hit = ltp >= position.target_2
                target_3_hit = ltp >= position.target_3
                sl_hit = ltp <= position.stop_loss

            if target_1_hit and "T1" not in position.targets_hit:
                position.targets_hit.append("T1")
                events.append({"type": "TARGET_HIT", "target": "T1", "position_id": position.id})
            if target_2_hit and "T2" not in position.targets_hit:
                position.targets_hit.append("T2")
                events.append({"type": "TARGET_HIT", "target": "T2", "position_id": position.id})
            if target_3_hit and "T3" not in position.targets_hit:
                position.targets_hit.append("T3")
                position.status = "CLOSED"
                events.append({"type": "TARGET_HIT", "target": "T3", "position_id": position.id})

            if sl_hit and position.status == "OPEN":
                position.status = "SL_MISSED"
                events.append({"type": "SL_MISSED", "position_id": position.id})

        return events

    def open_position_count(self) -> int:
        return sum(1 for position in self.open_positions.values() if position.status == "OPEN")
