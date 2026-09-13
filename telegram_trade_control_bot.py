from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, List, Optional

from trade_control_handler import TradeControlHandler, TradeRequest


class TelegramTradeControlBot:
    """Builds and routes semi-automated trade approval messages."""

    def __init__(self, telegram_handler, trade_control: TradeControlHandler) -> None:
        self.telegram_handler = telegram_handler
        self.trade_control = trade_control

    def create_and_send_request(self, signal_data: dict, option_data: dict) -> Optional[TradeRequest]:
        request = self.trade_control.create_trade_request(signal_data, option_data)
        if self.send_pre_approval_message(request):
            return request
        self.trade_control.cancel_trade(request.trade_id)
        return None

    def send_pre_approval_message(self, request: TradeRequest) -> bool:
        return self.telegram_handler.send_to_channel("trade_control", self.build_pre_approval_message(request))

    def send_approval_confirmation(self, request: TradeRequest) -> bool:
        return self.telegram_handler.send_to_channel("trade_control", self.build_approved_message(request))

    def send_execution_confirmation(self, request: TradeRequest) -> bool:
        return self.telegram_handler.send_to_channel("trade_control", self.build_execution_message(request))

    def handle_callback(self, callback_data: str) -> dict:
        parts = callback_data.split(":")
        if len(parts) < 3 or parts[0] != "trade":
            return {"ok": False, "error": "invalid_callback"}

        _, trade_id, action, *params = parts

        if action == "approve":
            existing = self.trade_control.get(trade_id)
            if not existing or existing.status != "PENDING":
                return {"ok": False, "trade": asdict(existing) if existing else None}
            request = self.trade_control.approve_trade(trade_id)
            if request:
                self.send_approval_confirmation(request)
            return {"ok": bool(request and request.status == "APPROVED"), "trade": asdict(request) if request else None}

        if action == "reject":
            existing = self.trade_control.get(trade_id)
            if not existing or existing.status != "PENDING":
                return {"ok": False, "trade": asdict(existing) if existing else None}
            request = self.trade_control.reject_trade(trade_id, "Rejected from Telegram")
            return {"ok": bool(request and request.status == "REJECTED"), "trade": asdict(request) if request else None}

        if action == "cancel":
            existing = self.trade_control.get(trade_id)
            if not existing or existing.status not in {"PENDING", "APPROVED"}:
                return {"ok": False, "trade": asdict(existing) if existing else None}
            request = self.trade_control.cancel_trade(trade_id)
            return {"ok": bool(request and request.status == "CANCELLED"), "trade": asdict(request) if request else None}

        if action in {"intraday", "carry", "regular", "bracket", "cover", "order"}:
            return self._handle_preference_change(trade_id, action, params)

        return {"ok": False, "error": "unknown_action"}

    def _handle_preference_change(self, trade_id: str, action: str, params: List[str]) -> dict:
        order_type = validity = mode = None
        if action == "intraday":
            validity = "INTRADAY"
        elif action == "carry":
            validity = "CARRY_FORWARD"
        elif action == "regular":
            mode = "REGULAR"
        elif action == "bracket":
            mode = "BRACKET"
            order_type = "BRACKET_ORDER"
        elif action == "cover":
            mode = "COVER"
            order_type = "COVER_ORDER"
        elif action == "order" and params:
            order_type = params[0]

        existing = self.trade_control.get(trade_id)
        if not existing or existing.status != "PENDING":
            return {"ok": False, "trade": asdict(existing) if existing else None}
        before = (existing.order_type, existing.validity, existing.mode)
        request = self.trade_control.set_order_preferences(
            trade_id,
            order_type=order_type,
            validity=validity,
            mode=mode,
        )
        after = (request.order_type, request.validity, request.mode) if request else before
        return {"ok": bool(request and request.status == "PENDING" and before != after), "trade": asdict(request) if request else None}

    @staticmethod
    def build_pre_approval_message(request: TradeRequest) -> str:
        rr = TelegramTradeControlBot._risk_reward(request)
        t1, t2, t3 = (request.targets + [request.entry + 20, request.entry + 40, request.entry + 60])[:3]
        return (
            "🤖 SEMI-AUTOMATED TRADE REQUEST\n"
            "AWAITING YOUR APPROVAL\n\n"
            "📊 TRADE DETAILS:\n"
            f"Trade ID: {request.trade_id}\n"
            f"Symbol: {request.symbol}\n"
            f"Signal Type: {'🚀 CALL (BUY)' if request.signal == 'CALL' else '📉 PUT (BUY)'}\n"
            f"Entry: ₹{request.entry:.2f} | Target: ₹{t1:.2f} | SL: ₹{request.stop_loss:.2f}\n"
            f"Risk:Reward = 1:{rr:.2f}\n\n"
            "Order Config:\n"
            f"Type: {request.order_type}\n"
            f"Validity: {'Intraday (MIS)' if request.validity == 'INTRADAY' else 'Carry Forward'}\n"
            f"Mode: {request.mode}\n"
            "Trail SL: Every 5 points\n\n"
            "Post-Entry Strategy:\n"
            "- Move SL to C2C after entry\n"
            "- Trail Stop: +5 points every move\n"
            f"- Exit Targets: T1({t1:.2f}), T2({t2:.2f}), T3({t3:.2f})\n"
            "- Auto-Exit: On Target/SL breach\n\n"
            "[✅ APPROVE] [❌ REJECT]\n"
            "[📋 INTRADAY] [📦 CARRY FWD]\n"
            "[🎯 REGULAR] [🔒 BRACKET] [🛡️ COVER]\n"
            "[❌ CANCEL REQUEST]"
        )

    @staticmethod
    def build_approved_message(request: TradeRequest) -> str:
        t1, t2, t3 = (request.targets + [request.entry + 20, request.entry + 40, request.entry + 60])[:3]
        return (
            "✅ TRADE APPROVED\n"
            "READY FOR AUTOMATED EXECUTION\n\n"
            f"Symbol: {request.symbol}\n"
            f"Type: {request.signal}\n"
            f"Entry: ₹{request.entry:.2f}\n"
            f"Target: ₹{t1:.2f}\n"
            f"SL: ₹{request.stop_loss:.2f}\n"
            f"Quantity: {request.quantity} lot\n"
            f"Validity: {request.validity}\n"
            f"Order Type: {request.order_type}\n\n"
            "⚠️ AUTO-EXECUTION STRATEGY:\n"
            f"1️⃣ Place entry order at ₹{request.entry:.2f}\n"
            "2️⃣ Once filled, move SL to C2C\n"
            "3️⃣ Set Trail: +5 points every target\n"
            "4️⃣ Target Zones:\n"
            f"   T1: ₹{t1:.2f} (+20 pts)\n"
            f"   T2: ₹{t2:.2f} (+40 pts)\n"
            f"   T3: ₹{t3:.2f} (+60 pts)\n"
            "5️⃣ Exit on Target/SL hit (auto)\n\n"
            "Awaiting EXECUTION..."
        )

    @staticmethod
    def build_execution_message(request: TradeRequest) -> str:
        t1, t2, t3 = (request.targets + [request.entry + 20, request.entry + 40, request.entry + 60])[:3]
        return (
            "🚀 TRADE EXECUTED\n\n"
            f"Symbol: {request.symbol}\n"
            f"Type: {request.signal}\n"
            f"Order ID: {request.order_id or 'N/A'}\n"
            f"Entry Price: ₹{request.entry:.2f}\n"
            "Status: FILLED\n\n"
            "Trail Stop-Loss Active:\n"
            f"Current SL: ₹{request.entry:.2f} (C2C)\n"
            "Trail Interval: +5 points\n"
            f"Targets: T1({t1:.2f}) T2({t2:.2f}) T3({t3:.2f})\n\n"
            "Real-time monitoring active..."
        )

    @staticmethod
    def build_inline_buttons(trade_id: str) -> List[List[Dict[str, Any]]]:
        return [
            [
                {"text": "✅ APPROVE", "callback_data": f"trade:{trade_id}:approve"},
                {"text": "❌ REJECT", "callback_data": f"trade:{trade_id}:reject"},
            ],
            [
                {"text": "📋 INTRADAY", "callback_data": f"trade:{trade_id}:intraday"},
                {"text": "📦 CARRY FWD", "callback_data": f"trade:{trade_id}:carry"},
            ],
            [
                {"text": "🎯 REGULAR", "callback_data": f"trade:{trade_id}:regular"},
                {"text": "🔒 BRACKET", "callback_data": f"trade:{trade_id}:bracket"},
                {"text": "🛡️ COVER", "callback_data": f"trade:{trade_id}:cover"},
            ],
            [
                {"text": "❌ CANCEL REQUEST", "callback_data": f"trade:{trade_id}:cancel"},
            ],
        ]

    @staticmethod
    def _risk_reward(request: TradeRequest) -> float:
        if not request.targets:
            return 0.0
        risk = max(0.01, abs(request.entry - request.stop_loss))
        reward = abs(request.targets[0] - request.entry)
        return reward / risk
