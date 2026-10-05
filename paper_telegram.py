"""Authenticated webhook controls and retryable notifications for paper trading."""

import hmac
import html
import math
import os
import re
import time
from datetime import date

import requests


def _escape(value):
    return html.escape(str(value), quote=True)


def _lines(value, prefix=""):
    if isinstance(value, dict):
        for key, item in value.items():
            label = {
                "pnl_points": "P&L points",
                "pnl_inr": "P&L INR",
                "realized_pnl": "Realized P&L INR",
                "unrealized_pnl": "Unrealized P&L INR",
                "stop_loss": "Stop loss",
            }.get(key, key)
            yield from _lines(item, f"{prefix}{label}: ")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value, 1):
            yield from _lines(item, f"{prefix}{index}. ")
        if not value:
            yield prefix + "None"
    else:
        yield prefix + str(value)


def _chunks(lines):
    """Escape before measuring, without cutting HTML entities or tags."""
    chunks, current = [], ""
    for line in lines:
        for start in range(0, max(1, len(line)), 500):
            escaped = _escape(line[start:start + 500])
            if len(current) + len(escaped) + 1 > 3900:
                chunks.append(current.rstrip())
                current = ""
            current += escaped + "\n"
    if current:
        chunks.append(current.rstrip())
    return chunks


def format_portfolio(snapshot):
    """Return HTML-safe messages including every position and the full trade log."""
    return _chunks(["PAPER portfolio (practice only)", *_lines(snapshot)])


class PaperTelegramControl:
    def __init__(self, engine, telegram_handler, allowed_user_ids=None,
                 chat_id=None, webhook_secret=None):
        self.engine = engine
        self.telegram_handler = telegram_handler
        raw_ids = (os.getenv("PAPER_TELEGRAM_ALLOWED_USER_IDS", "")
                   if allowed_user_ids is None else allowed_user_ids)
        if isinstance(raw_ids, str):
            raw_ids = re.split(r"[\s,]+", raw_ids.strip())
        try:
            self.allowed_user_ids = {
                int(value) for value in raw_ids
                if str(value).isdigit() and int(value) > 0
            }
        except TypeError:
            self.allowed_user_ids = set()
        configured_chat = (os.getenv("CHANNEL_TRADE_CONTROL_ID", "")
                           if chat_id is None else chat_id)
        try:
            self.chat_id = int(configured_chat)
        except (TypeError, ValueError):
            self.chat_id = None
        self.webhook_secret = (os.getenv("PAPER_TELEGRAM_WEBHOOK_SECRET", "")
                               if webhook_secret is None else webhook_secret)
        self.last_delivery = None

    def _api(self, method, payload):
        try:
            token, routed_chat = self.telegram_handler._get_bot_for_category("trade_control")
            if not token or not self.chat_id or int(routed_chat) != self.chat_id:
                return False
            session_factory = getattr(self.telegram_handler, "_http_session", None)
            transport = session_factory() if callable(session_factory) else requests
            response = transport.post(
                f"https://api.telegram.org/bot{token}/{method}",
                json=payload, timeout=10,
            )
            return response.status_code == 200 and response.json().get("ok") is True
        except Exception:
            # Transport exceptions may contain the token-bearing URL; never log them.
            return False

    def _send(self, messages, reply_markup=None):
        for index, message in enumerate(messages):
            payload = {"chat_id": self.chat_id, "text": message,
                       "parse_mode": "HTML"}
            if reply_markup and index == len(messages) - 1:
                payload["reply_markup"] = reply_markup
            if not self._api("sendMessage", payload):
                return False
        return True

    @staticmethod
    def _buttons(identifier):
        if not identifier:
            return None
        buttons = []
        for label, action in (("Approve Limit", "approve_limit"),
                              ("Approve Market", "approve_market"),
                              ("Modify", "modify"), ("Reject", "reject")):
            data = f"paper:{identifier}:{action}"
            if len(data.encode("utf-8")) > 64 or ":" in str(identifier):
                return None
            buttons.append({"text": label, "callback_data": data})
        return {"inline_keyboard": [buttons[:2], buttons[2:]]}

    def send_event(self, event):
        """Return false on any failed chunk so the persistent outbox can retry.

        Retrying a partially delivered event may duplicate earlier Telegram messages.
        """
        started = time.monotonic()
        success = False
        try:
            kind = str(event.get("type", event.get("kind", event.get("event", "event"))))
            payload = event.get("payload", event.get("data", event))
            if isinstance(payload, dict) and kind in {"entry_filled", "exit_filled"}:
                payload = dict(payload)
                try:
                    entry = float(payload["entry_price"])
                    exit_price = float(payload.get("exit_price", entry))
                    if math.isfinite(entry) and math.isfinite(exit_price):
                        payload.setdefault("pnl_points", exit_price - entry)
                        payload.setdefault("pnl_inr", payload.get("realized_pnl", 0))
                except (KeyError, TypeError, ValueError):
                    pass
            identifier = (payload.get("id", payload.get("signal_id"))
                          if isinstance(payload, dict) else None)
            needs_approval = (
                isinstance(payload, dict)
                and kind.lower() in {"order_awaiting_approval", "approval_request",
                                     "approval_requested"}
                and str(payload.get("side", payload.get("transaction_type", "BUY"))).upper() != "SELL"
                and payload.get("approval_required") is not False
            )
            buttons = (self._buttons(identifier)
                       if needs_approval else None)
            messages = _chunks([f"PAPER {kind} (practice only)", *_lines(payload)])
            success = self._send(messages, buttons)
            return success
        except Exception:
            return False
        finally:
            self.last_delivery = {
                "event_id": (event.get("event_id", event.get("id"))
                             if isinstance(event, dict) else None),
                "duration_ms": (time.monotonic() - started) * 1000,
                "success": success,
            }
            recorder = getattr(self.engine, "record_delivery", None)
            if callable(recorder) and self.last_delivery["event_id"] is not None:
                try:
                    recorder(**self.last_delivery)
                except Exception:
                    pass

    def _authorized(self, actor, message, is_callback=False):
        return (
            isinstance(actor, dict) and type(actor.get("id")) is int
            and actor["id"] in self.allowed_user_ids
            and not actor.get("is_bot", False)
            and isinstance(message, dict)
            and isinstance(message.get("chat"), dict)
            and type(message["chat"].get("id")) is int
            and self.chat_id is not None
            and message["chat"]["id"] == self.chat_id
            and not any(key in message for key in
                        ("forward_origin", "forward_from", "forward_from_chat",
                         "forward_date"))
            and (is_callback or "sender_chat" not in message)
        )

    @staticmethod
    def _number(value):
        number = float(value)
        if not math.isfinite(number) or number <= 0:
            raise ValueError("Expected a positive finite number")
        return number

    def _command(self, text):
        args = text.split()
        if not args:
            raise ValueError("Missing command")
        command = args[0].split("@", 1)[0].lower()
        if command in {"/pending", "/portfolio", "/trades", "/orders", "/positions"} and len(args) == 1:
            return command[1:], {}
        if command == "/trades" and len(args) == 2:
            day = date.fromisoformat(args[1])
            if day.isoformat() != args[1]:
                raise ValueError("Use /trades [YYYY-MM-DD]")
            return "trades", {"day": day.isoformat()}
        if command == "/mode" and len(args) == 3 and args[1].lower() == "approval":
            if args[2].lower() not in {"on", "off"}:
                raise ValueError("Use /mode approval on|off")
            return "mode", {"enabled": args[2].lower() == "on"}
        if command == "/approve" and len(args) in {2, 3}:
            order_type = args[2].lower() if len(args) == 3 else "limit"
            if order_type not in {"limit", "market"}:
                raise ValueError("Use /approve <id> [limit|market]")
            return f"approve_{order_type}", {"request_id": args[1]}
        if command in {"/reject", "/close"} and len(args) == 2:
            return command[1:], {"request_id": args[1]}
        if command == "/close" and len(args) == 3 and args[2].lower() == "market":
            return "close", {"request_id": args[1], "order_type": "MARKET"}
        if command == "/close" and len(args) == 4 and args[2].lower() == "limit":
            return "close", {"request_id": args[1], "order_type": "LIMIT",
                             "limit_price": self._number(args[3])}
        if command == "/modify" and len(args) in {3, 4}:
            values = {"request_id": args[1], "limit_price": self._number(args[2])}
            if len(args) == 4:
                values["stop_loss"] = self._number(args[3])
            return "modify", values
        if command == "/limits" and len(args) == 3:
            return "limits", {"profit_limit": self._number(args[1]),
                              "loss_limit": self._number(args[2])}
        raise ValueError("Commands: /mode approval on|off, /pending, "
                         "/approve <id> [limit|market], "
                         "/modify <id> <limit_price> [stop_loss], /reject <id>, "
                         "/portfolio, /orders, /positions, "
                         "/close <id> [market|limit <limit_price>], "
                         "/limits <profit> <loss>, /trades [YYYY-MM-DD]")

    def handle_update(self, update, secret):
        configured = self.webhook_secret
        if (not isinstance(configured, str) or len(configured) < 32
                or not isinstance(secret, str)
                or not hmac.compare_digest(configured.encode(), secret.encode())):
            return {"ok": False, "error": "Forbidden"}, 403
        if not isinstance(update, dict) or type(update.get("update_id")) is not int:
            return {"ok": False, "error": "Invalid update"}, 400
        callback = update.get("callback_query")
        if callback is not None:
            if not isinstance(callback, dict):
                return {"ok": False, "error": "Invalid callback"}, 400
            actor, message = callback.get("from"), callback.get("message")
        else:
            message = update.get("message")
            actor = message.get("from") if isinstance(message, dict) else None
        if not self._authorized(actor, message, is_callback=callback is not None):
            return {"ok": False, "error": "Forbidden"}, 403
        if callback is not None and (not isinstance(callback.get("id"), str)
                                     or not callback["id"]):
            return {"ok": False, "error": "Invalid callback"}, 400
        try:
            if not self.engine.record_update(f"telegram:update:{update['update_id']}"):
                return {"ok": True, "duplicate": True}, 200
            request_id = f"telegram:update:{update['update_id']}"
            if callback is not None:
                request_id = f"telegram:callback:{callback['id']}"
                if not self.engine.record_update(request_id):
                    return {"ok": True, "duplicate": True}, 200
                data = callback.get("data", "")
                if not isinstance(data, str) or len(data.encode("utf-8")) > 64:
                    raise ValueError("Invalid callback")
                parts = data.split(":")
                if len(parts) != 3 or parts[0] != "paper" or not parts[1]:
                    raise ValueError("Invalid callback")
                action, identifier = parts[2], parts[1]
                if action not in {"approve_limit", "approve_market", "modify", "reject"}:
                    raise ValueError("Invalid callback")
                if action == "modify":
                    result = {"instruction": f"/modify {identifier} <limit_price> [stop_loss]"}
                    self._send(_chunks(_lines(result)))
                    self._api("answerCallbackQuery", {"callback_query_id": callback["id"],
                                                     "text": "Use /modify to enter prices"})
                    return {"ok": True, "result": result}, 200
                kwargs = {"request_id": identifier}
            else:
                text = message.get("text")
                if not isinstance(text, str):
                    return {"ok": True, "ignored": True}, 200
                action, kwargs = self._command(text)
            if action in {"pending", "portfolio", "trades", "orders", "positions"}:
                snapshot = self.engine.snapshot(**kwargs)
                if action == "pending":
                    pending = snapshot.get("pending", snapshot.get("pending_signals", []))
                    entries = pending.values() if isinstance(pending, dict) else pending
                    for entry in entries:
                        kind = ("order_awaiting_approval"
                                if entry.get("status") == "awaiting_approval" else "order_pending")
                        self.send_event({"type": kind, "payload": entry})
                    exits = snapshot.get("pending_exit_orders", [])
                    result = {"pending": pending, "pending_exit_orders": exits}
                    if exits:
                        self._send(_chunks(["PAPER pending exit orders (automatic; no approval)",
                                            *_lines(exits)]))
                    if not pending and not exits:
                        self._send(["PAPER pending: None"])
                elif action == "trades":
                    result = {"trades": snapshot.get("trades", snapshot.get(
                        "trade_log", snapshot.get("executions", []))),
                        "closed_positions": snapshot.get("closed_positions", [])}
                    self._send(format_portfolio(result))
                elif action == "orders":
                    result = {"orders": snapshot.get("orders", []),
                              "exit_orders": snapshot.get(
                                  "exit_orders", snapshot.get("pending_exit_orders", []))}
                    self._send(format_portfolio(result))
                elif action == "positions":
                    result = {key: snapshot.get(key, [] if key == "positions" else {})
                              for key in ("account", "daily", "positions")}
                    self._send(format_portfolio(result))
                else:
                    result = snapshot
                    self._send(format_portfolio(snapshot))
            else:
                target_id = kwargs.pop("request_id", None)
                if action == "mode":
                    approval_required = kwargs.pop("enabled")
                    account = self.engine.snapshot().get("account", {})
                    kwargs.update(enabled=bool(account.get("enabled", True)),
                                  approval_required=approval_required)
                result = self.engine.action(action, request_id=target_id,
                                            actor=actor["id"],
                                            telegram_request_id=request_id, **kwargs)
                self._send(_chunks(["PAPER command result", *_lines(result)]))
            if callback is not None:
                self._api("answerCallbackQuery", {"callback_query_id": callback["id"],
                                                 "text": "Paper action processed"})
            return {"ok": True, "result": result}, 200
        except ValueError:
            error = "Invalid paper command or request"
            self._send([error])
            return {"ok": False, "error": error}, 400
        except Exception:
            return {"ok": False, "error": "Paper control unavailable"}, 503
