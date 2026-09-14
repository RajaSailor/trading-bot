from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, Optional, Tuple
from uuid import uuid4


class OrderStatus(Enum):
    PENDING = "PENDING"
    ROUTED = "ROUTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


@dataclass
class Order:
    symbol: str
    side: str
    quantity: int
    price: float
    order_id: str = field(default_factory=lambda: str(uuid4()))
    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: int = 0
    route: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


class OrderExecutor:
    """Validates, routes and tracks order lifecycle."""

    def __init__(self) -> None:
        self.orders: Dict[str, Order] = {}

    def validate_order(self, order: Order) -> Tuple[bool, str]:
        if not order.symbol:
            return False, "Missing symbol"
        if order.side not in {"BUY", "SELL"}:
            return False, "Invalid side"
        if order.quantity <= 0:
            return False, "Invalid quantity"
        if order.price <= 0:
            return False, "Invalid price"
        return True, "OK"

    def route_order(self, order: Order) -> str:
        if "MCX" in order.symbol.upper():
            return "MCX_ROUTER"
        if "NIFTY" in order.symbol.upper():
            return "NSE_ROUTER"
        return "SMART_ROUTER"

    def execute_order(self, order: Order, available_liquidity: Optional[int] = None) -> Order:
        valid, reason = self.validate_order(order)
        if not valid:
            order.status = OrderStatus.REJECTED
            order.updated_at = datetime.utcnow().isoformat()
            self.orders[order.order_id] = order
            return order

        order.route = self.route_order(order)
        order.status = OrderStatus.ROUTED

        fill_qty = order.quantity if available_liquidity is None else min(order.quantity, max(0, available_liquidity))
        order.filled_quantity = fill_qty
        if fill_qty == order.quantity:
            order.status = OrderStatus.FILLED
        elif fill_qty > 0:
            order.status = OrderStatus.PARTIALLY_FILLED
        else:
            order.status = OrderStatus.ROUTED

        order.updated_at = datetime.utcnow().isoformat()
        self.orders[order.order_id] = order
        return order

    def handle_partial_fill(self, order_id: str, additional_fill: int) -> Order:
        order = self.orders[order_id]
        if order.status not in {OrderStatus.ROUTED, OrderStatus.PARTIALLY_FILLED}:
            return order
        order.filled_quantity = min(order.quantity, order.filled_quantity + max(0, additional_fill))
        if order.filled_quantity == order.quantity:
            order.status = OrderStatus.FILLED
        elif order.filled_quantity > 0:
            order.status = OrderStatus.PARTIALLY_FILLED
        order.updated_at = datetime.utcnow().isoformat()
        return order

    def cancel_order(self, order_id: str) -> Order:
        order = self.orders[order_id]
        if order.status not in {OrderStatus.FILLED, OrderStatus.REJECTED, OrderStatus.CANCELLED}:
            order.status = OrderStatus.CANCELLED
            order.updated_at = datetime.utcnow().isoformat()
        return order
