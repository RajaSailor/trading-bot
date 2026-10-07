"""Authenticated live-only approval controller; never sends network requests.

The webhook route must pass the Telegram secret header as `secret`, not a body
field. Allowlist entries are decimal Telegram actor/chat identifiers. Delivery
is injected separately via the engine's durable notification outbox.
"""

import hmac
import re

from live_broker import LiveBlocked


class LiveTelegram:
    def __init__(self, execution, *, secret, allowed_actors, allowed_chats):
        self.execution = execution
        self.secret = str(secret or "")
        self.allowed_actors = self._ids(allowed_actors, actor=True)
        self.allowed_chats = self._ids(allowed_chats)

    @staticmethod
    def _ids(values, actor=False):
        values = {str(value) for value in values}
        pattern = r"[1-9]\d*" if actor else r"-?[1-9]\d*"
        return values if values and all(re.fullmatch(pattern, value) for value in values) else set()

    def readiness(self):
        return {"authorization_configured": bool(len(self.secret) >= 32
                and self.allowed_actors and self.allowed_chats),
                "production_ready": False}

    def _allowed(self, actor, chat):
        if (isinstance(actor, bool) or isinstance(chat, bool)
                or not re.fullmatch(r"[1-9]\d*", str(actor))
                or not re.fullmatch(r"-?[1-9]\d*", str(chat))
                or str(actor) not in self.allowed_actors or str(chat) not in self.allowed_chats):
            raise LiveBlocked("actor/chat not allowlisted")

    def proposal_card(self, proposal, *, actor, chat_id):
        if self.execution.auto_mode:
            raise LiveBlocked("approval cards are disabled in NIFTY auto mode")
        self._allowed(actor, chat_id)
        item = self.execution.bind(proposal["id"], actor, chat_id)
        context = self.execution.approval_context(item["id"])
        prefix = f"live:{item['id']}:{item['version']}:"
        buttons = [
            [{"text": "Approve " + item["order_type"].title(), "callback_data": prefix + "approve"},
             {"text": "Reject", "callback_data": prefix + "reject"}],
            [{"text": "Modify to Market", "callback_data": prefix + "market"}],
        ]
        return {
            "chat_id": str(chat_id),
            "text": (f"LIVE SIMULATION ONLY — production BLOCKED\n"
                     f"{item['side']} NIFTY {item['option_type']} {item['strike']} {item['expiry']}\n"
                     f"Security {item['security_id']} | {item['quantity']} units | "
                     f"lot {item['lot_size']} | tick {item['tick_size']}\n"
                     f"Lots {item['lots']} | current premium {context['premium']}\n"
                     f"Balance {context['balance']} | as of {context['balance_asof']}\n"
                     f"Estimated funds {context['estimated_funds']} | reserved {context['reserved_cash']}\n"
                     f"Filled entries today {context['filled_entries_today']}/2 (IST)\n"
                     f"{item['order_type']} {item.get('limit_price')} | version {item['version']}\n"
                     f"Expires {item['expires_at']} | {item['id']}\n"
                     "Approval is not a fill. SL/T1/T2 monitoring is local; "
                     "automatic exchange protection unavailable.\n"
                     "Modifications create a new preview; approve its new version separately.\n"
                     f"Modify to Limit: /live_limit {item['id']} {item['version']} PRICE"),
            "reply_markup": {"inline_keyboard": buttons},
            "proposal": item, "context": context,
        }

    def handle_update(self, payload, secret):
        try:
            if len(self.secret) < 32 or not isinstance(secret, str) or not hmac.compare_digest(
                    self.secret.encode(), secret.encode()):
                raise LiveBlocked("invalid webhook secret")
            if not isinstance(payload, dict):
                raise LiveBlocked("invalid update")
            if self.execution.auto_mode:
                raise LiveBlocked("manual trade controls are disabled in NIFTY auto mode")
            update_id = payload.get("update_id")
            if isinstance(update_id, bool) or not isinstance(update_id, int):
                raise LiveBlocked("missing update id")
            callback = payload.get("callback_query")
            if callback:
                source = callback
                message = callback.get("message", {})
                callback_id = callback.get("id")
                if not isinstance(callback_id, str) or not callback_id:
                    raise LiveBlocked("missing callback id")
                parts = str(callback.get("data", "")).split(":")
                if len(parts) not in (4, 5) or parts[0] != "live":
                    raise LiveBlocked("not a live control")
                identifier, version, action = parts[1:4]
                value = parts[4] if len(parts) == 5 else None
            else:
                callback_id = None
                source = message = payload.get("message", {})
                parts = str(message.get("text", "")).split()
                if not parts or not parts[0].startswith("/live_"):
                    raise LiveBlocked("not a live control")
                action = parts[0][6:]
                identifier = parts[1] if len(parts) > 1 else None
                version = parts[2] if len(parts) > 2 else None
                value = parts[3] if len(parts) > 3 else None
            actor_data = source.get("from", {})
            actor = actor_data.get("id")
            chat = message.get("chat", {}).get("id")
            if actor_data.get("is_bot") is True:
                raise LiveBlocked("bots cannot approve")
            if message.get("sender_chat") or any(key in message for key in
                    ("forward_origin", "forward_from", "forward_from_chat", "forward_date")):
                raise LiveBlocked("forwarded or sender_chat controls forbidden")
            self._allowed(actor, chat)
            if not self.execution.record_telegram_update(update_id, callback_id):
                return {"ok": True, "duplicate": True}
            if action == "status":
                return {"ok": True, "result": self.execution.status()}
            if action == "exit":
                result = self.execution.request_exit(identifier)
                if result.get("id"):
                    result = self.proposal_card(result, actor=actor, chat_id=chat)
                return {"ok": True, "result": result}
            version = int(version)
            args = (identifier, version, actor, chat)
            if action == "approve":
                result = self.execution.approve(*args)
            elif action == "reject":
                result = self.execution.reject(*args)
            elif action == "lots":
                result = self.execution.modify(*args, lots=int(value))
            elif action == "market":
                result = self.execution.modify(*args, order_type="MARKET")
            elif action == "limit":
                result = self.execution.modify(*args, order_type="LIMIT", limit_price=float(value))
            else:
                raise LiveBlocked("unsupported live control")
            if result["status"] == "AWAITING":
                result = self.proposal_card(result, actor=actor, chat_id=chat)
            return {"ok": True, "result": result}
        except (LiveBlocked, ValueError, TypeError, KeyError, AttributeError) as exc:
            return {"ok": False, "error": str(exc)}
