"""Authenticated webhook controls and retryable notifications for paper trading."""

import hmac
import html
import math
import os
import re
import time
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

import requests


def _escape(value):
    return html.escape(str(value), quote=True)


def _text_size(value):
    return len(value.encode("utf-16-le")) // 2


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
            if _text_size(current) + _text_size(escaped) + 1 > 3900:
                chunks.append(current.rstrip())
                current = ""
            current += escaped + "\n"
    if current:
        chunks.append(current.rstrip())
    return chunks


def format_portfolio(snapshot):
    """Return HTML-safe messages including every position and the full trade log."""
    return _chunks(["PAPER portfolio (practice only)", *_lines(snapshot)])


def _price(value):
    return "—" if value is None else str(value)


def _ist(value):
    try:
        if isinstance(value, str):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Kolkata"))
        else:
            parsed = datetime.fromtimestamp(float(value), ZoneInfo("Asia/Kolkata"))
        return parsed.astimezone(ZoneInfo("Asia/Kolkata")).strftime("%d %b %Y %H:%M:%S IST")
    except (ValueError, TypeError, OverflowError):
        return "—"


def position_lines(position):
    return [
        f"Position ID: {position.get('id', '—')}",
        f"Contract: {position.get('option_symbol', position.get('symbol', '—'))}",
        f"Exchange: {position.get('exchange_segment', '—')} | Security ID: {position.get('security_id', '—')}",
        f"Expiry: {position.get('expiry', '—')} | Strike: {position.get('strike', '—')} | Option: {position.get('option_type', '—')}",
        f"Lots: {position.get('lots', 1)} | Units: {position.get('quantity', '—')} | Lot size: {position.get('lot_size', '—')}",
        f"Entry: {_price(position.get('entry_price', position.get('fill_price')))} | Mark: {_price(position.get('mark_price'))}",
        f"Tick size: {_price(position.get('tick_size'))} | Mark evidence: {_ist(position.get('mark_timestamp', position.get('quote_timestamp')))}",
        f"Effective SL: {_price(position.get('stop_loss'))} ({position.get('stop_kind', 'initial_stop')}; source: {position.get('stop_source', 'initial')})",
        f"T1: {_price(position.get('target_1'))} ({'custom' if position.get('custom_target_1') else 'default'}; {'reached' if position.get('t1_reached') else 'milestone'})",
        f"T2: {_price(position.get('target_2'))} ({'custom' if position.get('custom_target_2') else 'default'}; full one-lot exit)",
        f"Trailing: {'ON' if position.get('trailing_enabled', True) else 'OFF (effective SL frozen)'}",
    ]


def format_event(kind, payload, timestamp=None):
    headings = {
        "entry_filled": ("🟨 ENTRY FILLED", "#PAPER #ENTRY"),
        "order_awaiting_approval": ("🟨 PENDING APPROVAL", "#PAPER #APPROVAL"),
        "approval_request": ("🟨 PENDING APPROVAL", "#PAPER #APPROVAL"),
        "approval_requested": ("🟨 PENDING APPROVAL", "#PAPER #APPROVAL"),
        "order_pending": ("ENTRY PENDING", "#PAPER #ENTRY_PENDING"),
        "order_approved": ("ENTRY APPROVED — PENDING SIMULATION", "#PAPER #ENTRY_PENDING"),
        "order_modified": ("ENTRY REQUEST MODIFIED", "#PAPER #APPROVAL"),
        "order_rejected": ("ENTRY REJECTED", "#PAPER #REJECTED"),
        "order_cancelled": ("ENTRY CANCELLED", "#PAPER #CANCELLED"),
        "order_expired": ("ENTRY EXPIRED", "#PAPER #EXPIRED"),
        "exit_requested": ("EXIT PENDING", "#PAPER #EXIT_PENDING"),
        "exit_escalated": ("EXIT PENDING — PROTECTION OVERRIDE", "#PAPER #EXIT_PENDING"),
        "exit_pending": ("EXIT PENDING", "#PAPER #EXIT_PENDING"),
        "exit_order_pending": ("EXIT PENDING", "#PAPER #EXIT_PENDING"),
        "stop_tightened": ("SL UPDATED", "#PAPER #SL_UPDATE"),
        "sl_updated": ("SL UPDATED", "#PAPER #SL_UPDATE"),
        "target_updated": ("TARGET UPDATED", "#PAPER #TARGET_UPDATE"),
        "trailing_updated": ("TRAILING UPDATED", "#PAPER #TRAIL_UPDATE"),
        "target_1": ("TARGET 1 MILESTONE", "#PAPER #TARGET1 #SL_UPDATE"),
        "position_detail": ("OPEN POSITION", "#PAPER"),
    }
    if kind == "exit_filled":
        realized = Decimal(str(payload["realized_pnl"])).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP)
        heading, tags = (
            ("🟩 EXIT PROFIT", "#PAPER #EXIT #PROFIT") if realized > 0 else
            ("🟥 EXIT LOSS", "#PAPER #EXIT #LOSS") if realized < 0 else
            ("◻️ EXIT BREAKEVEN", "#PAPER #EXIT #BREAKEVEN"))
    else:
        heading, tags = headings.get(kind, ("PAPER EVENT", "#PAPER"))
    reason = payload.get("exit_reason", payload.get("reason"))
    reason_tags = {
        "target_2": "#TARGET2", "time_cutoff": "#CUTOFF",
        "daily_profit_limit": "#RISK #PROTECTION",
        "daily_loss_limit": "#RISK #PROTECTION",
        "initial_stop": "#STOP #PROTECTION",
        "breakeven_stop": "#STOP #PROTECTION",
        "trailing_stop": "#STOP #PROTECTION #TRAIL",
        "manual_close": "#MANUAL",
    }
    if reason in reason_tags:
        tags += (" #STOP #PROTECTION #SL_UPDATE" if reason == "trailing_stop"
                 and payload.get("stop_source") == "manual" else " " + reason_tags[reason])
    if kind in headings or kind == "exit_filled":
        lines = position_lines(payload)
        if kind in {"entry_filled", "exit_filled"}:
            entry = Decimal(str(payload.get("entry_price", 0)))
            exit_price = Decimal(str(payload.get("exit_price", entry)))
            lines += [
                f"Actual simulated {'exit' if kind == 'exit_filled' else 'entry'}: {_price(payload.get('exit_price' if kind == 'exit_filled' else 'entry_price'))}",
                f"Reason: {reason or 'entry'}",
                f"P&L points: {float(exit_price - entry)}",
                f"P&L INR: {(realized if realized else Decimal('0')) if kind == 'exit_filled' else Decimal('0.00')}",
                "INR is the ledger result (units/multiplier applied once). Precision: INR 0.01.",
                "Fees: not modeled; simulated P&L excludes fees.",
            ]
        elif kind in {"exit_requested", "exit_escalated", "exit_pending", "exit_order_pending"}:
            lines += ["Exit request submitted — NOT a completed simulated fill.",
                      f"Exit order: {payload.get('id')} | Position: {payload.get('position_id')}",
                      f"Type: {payload.get('order_type')} | Limit: {_price(payload.get('limit_price'))}",
                      f"Reason: {reason or '—'}"]
        elif kind in {"order_awaiting_approval", "approval_request", "approval_requested"}:
            lines += ["Awaiting entry approval — NOT filled.",
                      f"Limit: {_price(payload.get('limit_price'))}",
                      f"Reason: {payload.get('reason', '—')}"]
        elif kind.startswith("order_"):
            lines += [f"Status: {payload.get('status', kind.removeprefix('order_'))}",
                      f"Limit: {_price(payload.get('limit_price'))}",
                      f"Reason: {payload.get('reason', '—')}",
                      "This is an entry request update, not a new simulated fill."]
        if "old" in payload:
            lines += list(_lines({"old": payload["old"], "new": payload.get("new")}))
        event_time = (payload.get("closed_at") if kind == "exit_filled" else
                      payload.get("opened_at") if kind == "entry_filled" else
                      timestamp or payload.get("timestamp"))
        lines.append(f"Time: {_ist(event_time)}")
    else:
        lines = [f"Event: {kind}", *_lines(payload)]
    chunks = _chunks(lines)
    header = f"<b>{_escape(heading)}</b>\n{tags}"
    if chunks and _text_size(header) + _text_size(chunks[0]) + 1 <= 3900:
        chunks[0] = header + "\n" + chunks[0]
    else:
        chunks.insert(0, header)
    return chunks


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
            if response.status_code != 200:
                return False
            body = response.json()
            return (body.get("result") or True) if body.get("ok") is True else False
        except Exception:
            # Transport exceptions may contain the token-bearing URL; never log them.
            return False

    def _send(self, messages, reply_markup=None, position_id=None):
        for index, message in enumerate(messages):
            payload = {"chat_id": self.chat_id, "text": message,
                       "parse_mode": "HTML"}
            if reply_markup and index == len(messages) - 1:
                payload["reply_markup"] = reply_markup
            result = self._api("sendMessage", payload)
            if not result:
                return False
            if "reply_markup" in payload and position_id and isinstance(result, dict):
                message_id = result.get("message_id")
                if type(message_id) is int:
                    self.engine.remember_control_message(position_id, self.chat_id, message_id)
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

    def _position_buttons(self, position):
        if position.get("status") != "open" or position.get("exit_reason"):
            return None
        identifier = str(position.get("id", ""))
        version = int(position.get("control_version", 0))
        expires = int(self.engine.clock() + 300)
        choices = [
            ("Exit Market", "em"), ("Exit Limit", "el"),
            ("Modify SL", "sl"), ("Modify Target 1", "t1"),
            ("Modify Target 2", "t2"),
            ("Trailing OFF" if position.get("trailing_enabled", True) else "Trailing ON",
             "tr0" if position.get("trailing_enabled", True) else "tr1"),
            ("Refresh", "rf"),
        ]
        buttons = [{"text": label,
                    "callback_data": f"pos:{identifier}:{version}:{expires}:{action}"}
                   for label, action in choices]
        if not identifier or ":" in identifier or any(
                len(button["callback_data"].encode()) > 64 for button in buttons):
            return None
        return {"inline_keyboard": [buttons[i:i + 2] for i in range(0, len(buttons), 2)]}

    def _disable_position_buttons(self, identifier):
        disabled = True
        for message in self.engine.control_messages(identifier):
            disabled = bool(self._api("editMessageReplyMarkup", {
                "chat_id": message["chat_id"], "message_id": message["message_id"],
                "reply_markup": {"inline_keyboard": []},
            })) and disabled
        marker = getattr(self.engine, "disable_control_messages", None)
        if disabled and callable(marker):
            marker(identifier)

    def _send_position(self, position):
        return self._send(format_event("position_detail", position),
                          self._position_buttons(position), position.get("id"))

    def _workflow(self, result, message_id=None):
        if not result.get("ok", True):
            self._send(["Paper action rejected: expired, stale, invalid or protected. Refresh /positions."])
            return
        if result.get("stage") == "cancelled":
            self._send(["Paper edit cancelled."])
            return
        token = result.get("token")
        position = result.get("position", {})
        if not token or result.get("stage") not in {"input", "confirm"}:
            if position.get("status") == "open" and position.get("exit_reason"):
                self._send(_chunks([
                    "EXIT PENDING #PAPER #EXIT_PENDING",
                    "Exit request submitted — NOT a completed simulated fill.",
                    *position_lines(position),
                ]))
            elif position.get("status") == "open":
                self._send_position(position)
            else:
                self._send(["Paper action processed. Exit fills are reported separately."])
            return
        base = f"pc:{token}:"
        cancel = {"text": "Cancel", "callback_data": base + "cancel"}
        if result["stage"] == "input":
            rows = [[{"text": digit, "callback_data": base + "d" + digit}
                     for digit in row] for row in ("123", "456", "789", "0.")]
            rows += [[{"text": "⌫", "callback_data": base + "back"},
                      {"text": "Preview", "callback_data": base + "preview"}, cancel]]
            lines = ["Enter a tick-aligned price using the keypad, then Preview.",
                     f"Input: {result.get('input', '') or '—'}",
                     f"Alternative in a group: /input {token} <price>",
                     f"Cancel: /cancel {token}"]
        else:
            rows = [[{"text": "Confirm", "callback_data": base + "confirm"}, cancel]]
            lines = ["CONFIRM PAPER CHANGE (not submitted yet)",
                     f"Action: {result.get('action')} | Old: {result.get('old', '—')} | New: {result.get('value', 'MARKET')}",
                     "Exit orders use simulated execution; a request is not a fill.",
                     f"/confirm {token} or /cancel {token}"]
        lines += position_lines(position)
        lines += [f"Expires: {_ist(result.get('expires_at'))}",
                  "Protection remains automatic and may invalidate this preview."]
        messages = _chunks(lines)
        markup = {"inline_keyboard": rows}
        edited = (len(messages) == 1 and type(message_id) is int and
                  self._api("editMessageText", {
                      "chat_id": self.chat_id, "message_id": message_id,
                      "text": messages[0], "parse_mode": "HTML",
                      "reply_markup": markup,
                  }))
        if not edited:
            self._send(messages, markup, position.get("id"))
        if result["stage"] == "input" and message_id is None:
            self._send(_chunks([f"Paper price input: /input {token} <price>"]),
                       {"force_reply": True, "selective": True})

    def send_event(self, event):
        """Return false on any failed chunk so the persistent outbox can retry.

        Retrying a partially delivered event may duplicate earlier Telegram messages.
        """
        started = time.monotonic()
        success = False
        try:
            kind = str(event.get("type", event.get("kind", event.get("event", "event"))))
            payload = event.get("payload", event.get("data", event))
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
            if identifier and kind in {
                "entry_filled", "position_detail", "stop_tightened", "sl_updated",
                "target_updated", "trailing_updated", "target_1",
            }:
                current = next((p for p in self.engine.snapshot().get("positions", [])
                                if p.get("id") == identifier), None)
                if current:
                    if kind != "entry_filled":
                        self._disable_position_buttons(identifier)
                    buttons = self._position_buttons(current)
            if identifier and kind == "exit_filled":
                self._disable_position_buttons(identifier)
            elif kind in {"exit_requested", "exit_escalated"} and payload.get("position_id"):
                self._disable_position_buttons(payload["position_id"])
            messages = format_event(kind, payload, event.get("timestamp"))
            success = self._send(messages, buttons, identifier if not needs_approval else None)
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
        if command == "/help" and len(args) == 1:
            return "help", {}
        if command == "/position" and len(args) == 2:
            return "refresh", {"position_id": args[1]}
        if command in {"/sl", "/target1", "/target2"} and len(args) in {2, 3}:
            values = {"position_id": args[1]}
            if len(args) == 3:
                values["value"] = self._number(args[2])
            return {"/sl": "sl", "/target1": "target_1", "/target2": "target_2"}[command], values
        if command == "/trailing" and len(args) == 3 and args[2].lower() in {"on", "off"}:
            return "trailing", {"position_id": args[1], "value": args[2].lower() == "on"}
        if command in {"/input", "/confirm", "/cancel"} and len(args) == (3 if command == "/input" else 2):
            values = {"token": args[1]}
            if command == "/input":
                values["value"] = self._number(args[2])
            return command[1:], values
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
                         "/sl <id> [price], /target1|/target2 <id> [price], "
                         "/trailing <id> on|off, /position <id>, "
                         "/input <token> <price>, /confirm|/cancel <token>, "
                         "/limits <profit> <loss>, /trades [YYYY-MM-DD]")

    def _handle_position_callback(self, data, actor):
        parts = data.split(":")
        if parts[0] == "pos" and len(parts) == 5:
            _, identifier, version, expires, code = parts
            if not version.isdigit() or not expires.isdigit():
                raise ValueError("Invalid callback")
            now = self.engine.clock()
            if int(expires) > now + 300 or (code != "rf" and int(expires) < now):
                raise ValueError("Expired callback")
            actions = {"em": "exit_market", "el": "exit_limit", "sl": "sl",
                       "t1": "target_1", "t2": "target_2", "tr0": "trailing",
                       "tr1": "trailing", "rf": "refresh"}
            if code not in actions:
                raise ValueError("Invalid action")
            kwargs = {"version": int(version)}
            if code in {"tr0", "tr1"}:
                kwargs["value"] = code == "tr1"
            return self.engine.position_control(
                identifier, actions[code], actor=actor, chat_id=self.chat_id, **kwargs)
        if parts[0] == "pc" and len(parts) == 3:
            _, token, action = parts
            kwargs = {}
            if re.fullmatch(r"d[0-9.]", action):
                kwargs["value"] = action[1:]
                action = "digit"
            elif action == "back":
                action, kwargs = "digit", {"value": "back"}
            elif action not in {"preview", "confirm", "cancel"}:
                raise ValueError("Invalid action")
            return self.engine.position_control(
                None, action, actor=actor, chat_id=self.chat_id, token=token, **kwargs)
        raise ValueError("Invalid callback")

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
                if data.startswith(("pos:", "pc:")):
                    result = self._handle_position_callback(data, actor["id"])
                    self._workflow(result, message.get("message_id") if data.startswith("pc:") else None)
                    self._api("answerCallbackQuery", {"callback_query_id": callback["id"],
                                                      "text": ("Paper control processed" if result.get("ok", True)
                                                               else "Action rejected. Refresh positions.")})
                    return {"ok": True, "result": result}, 200
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
                if not text.startswith("/"):
                    reply = message.get("reply_to_message", {})
                    match = re.fullmatch(r"Paper price input: /input ([a-f0-9]{16}) <price>",
                                         reply.get("text", ""))
                    if not match:
                        return {"ok": True, "ignored": True}, 200
                    text = f"/input {match[1]} {text.strip()}"
                action, kwargs = self._command(text)
            if action == "help":
                result = {"help": (
                    "/positions, /portfolio, /position <id>\n"
                    "/sl <id> [price], /target1 <id> [price], /target2 <id> [price]\n"
                    "/trailing <id> on|off\n"
                    "/close <id> [market|limit <price>]\n"
                    "/input <token> <price>, /confirm <token>, /cancel <token>\n"
                    "/pending, /approve <id> [limit|market], /modify <id> <limit> [SL], /reject <id>\n"
                    "/mode approval on|off, /orders, /trades [YYYY-MM-DD], /limits <profit> <loss>\n"
                    "All position changes require preview/confirmation. Keypads work in channels; "
                    "commands/replies require a group message from an authorized numeric user. "
                    "No quantities editable. Paper only.")}
                self._send(_chunks([result["help"]]))
                return {"ok": True, "result": result}, 200
            if action in {"refresh", "sl", "target_1", "target_2", "trailing",
                          "input", "confirm", "cancel", "close"}:
                identifier = kwargs.pop("position_id", kwargs.pop("request_id", None))
                if action == "close":
                    action = "exit_limit" if kwargs.pop("order_type", "MARKET") == "LIMIT" else "exit_market"
                    if "limit_price" in kwargs:
                        kwargs["value"] = kwargs.pop("limit_price")
                result = self.engine.position_control(
                    identifier, action, actor=actor["id"], chat_id=self.chat_id, **kwargs)
                if action == "refresh" and result.get("position"):
                    self._send_position(result["position"])
                else:
                    self._workflow(result)
                return {"ok": True, "result": result}, 200
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
                    for position in snapshot.get("positions", []):
                        if position.get("status") == "open" and not position.get("exit_reason"):
                            self._send_position(position)
                else:
                    result = snapshot
                    self._send(format_portfolio(snapshot))
                    for position in snapshot.get("positions", []):
                        if position.get("status") == "open" and not position.get("exit_reason"):
                            self._send_position(position)
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
                summary = {key: result[key] for key in (
                    "ok", "error", "reason", "enabled", "approval_required",
                    "profit_limit", "loss_limit") if key in result}
                self._send(_chunks([
                    "PAPER command acknowledged", f"Action: {action}",
                    f"Request ID: {target_id or 'account'}", *_lines(summary),
                    "Entry/exit lifecycle receipts are reported separately.",
                ]))
            if callback is not None:
                self._api("answerCallbackQuery", {"callback_query_id": callback["id"],
                                                 "text": "Paper action processed"})
            return {"ok": True, "result": result}, 200
        except ValueError:
            error = "Invalid paper command or request"
            auditor = getattr(self.engine, "audit_control_attempt", None)
            if callable(auditor):
                try:
                    auditor(actor["id"], {
                        "action": "telegram_control", "old": None, "new": None,
                        "outcome": "rejected", "update_id": update["update_id"],
                    })
                except Exception:
                    pass
            self._send([error])
            if callback is not None:
                self._api("answerCallbackQuery", {"callback_query_id": callback["id"],
                                                  "text": "Expired or invalid action. Refresh /positions."})
            return {"ok": False, "error": error}, 400
        except Exception:
            return {"ok": False, "error": "Paper control unavailable"}, 503
