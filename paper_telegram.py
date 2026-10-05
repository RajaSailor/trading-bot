"""Authorized Telegram controls for the paper engine; no broker integration."""

import json
import math
import os
import re

import requests


class PaperTelegramController:
    def __init__(self, engine, token, chat_id, authorized_user_ids=None,
                 session=None, poll_timeout=20):
        self.engine = engine
        self._token = str(token or "")
        self.chat_id = str(chat_id)
        self.session = session if session is not None else requests.Session()
        self.authorized_user_ids = self._authorized_ids(
            os.getenv("TELEGRAM_AUTHORIZED_USER_IDS", "")
            if authorized_user_ids is None else authorized_user_ids
        )
        self.poll_timeout = max(0, min(50, int(poll_timeout)))
        self.offset = 0
        self._initialized = False
        self._last_update_id = -1

    @property
    def token(self):
        return self._token

    @staticmethod
    def _authorized_ids(value):
        if isinstance(value, str):
            value = value.split(",") if value.strip() else []
        try:
            ids = set()
            for item in value:
                if isinstance(item, bool) or not re.fullmatch(r"[1-9]\d*", str(item).strip()):
                    return frozenset()
                ids.add(int(str(item).strip()))
            return frozenset(ids)
        except (TypeError, ValueError):
            return frozenset()

    def _api(self, method, payload):
        if not self._token:
            raise RuntimeError("Telegram unavailable")
        try:
            response = self.session.post(
                f"https://api.telegram.org/bot{self._token}/{method}",
                json=payload, timeout=self.poll_timeout + 10,
            )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict) or data.get("ok") is not True:
                raise ValueError("API request failed")
            return data.get("result")
        except Exception:
            # Requests exceptions may contain the complete URL, including the token.
            raise RuntimeError("Telegram unavailable") from None

    def notify(self, message, buttons=None):
        text = str(message)
        chunks = [text[i:i + 2000] for i in range(0, len(text), 2000)]
        if not chunks:
            return False
        try:
            for index, chunk in enumerate(chunks):
                payload = {"chat_id": self.chat_id, "text": chunk}
                if buttons and index == len(chunks) - 1:
                    payload["reply_markup"] = (
                        buttons if isinstance(buttons, dict) else {"inline_keyboard": buttons}
                    )
                self._api("sendMessage", payload)
            return True
        except RuntimeError:
            return False

    def poll_once(self):
        """Discard old queued actions once per startup, then consume new updates."""
        if not self._initialized:
            updates = self._api("getUpdates", {
                "offset": -1, "timeout": 0,
                "allowed_updates": ["message", "callback_query"],
            })
            if not isinstance(updates, list):
                raise RuntimeError("Telegram unavailable")
            ids = [u.get("update_id") for u in updates if isinstance(u, dict)]
            ids = [i for i in ids if type(i) is int]
            if ids:
                self.offset = max(ids) + 1
                self._last_update_id = max(ids)
            self._initialized = True
            return 0
        updates = self._api("getUpdates", {
            "offset": self.offset, "timeout": self.poll_timeout,
            "allowed_updates": ["message", "callback_query"],
        })
        if not isinstance(updates, list):
            raise RuntimeError("Telegram unavailable")
        count = 0
        for update in updates:
            if not isinstance(update, dict) or type(update.get("update_id")) is not int:
                continue
            update_id = update["update_id"]
            if update_id < self.offset:
                continue
            self.handle_update(update)
            self.offset = update_id + 1
            count += 1
        return count

    def _answer(self, callback, text):
        callback_id = callback.get("id")
        if not isinstance(callback_id, str):
            return
        try:
            self._api("answerCallbackQuery", {"callback_query_id": callback_id,
                                              "text": text[:200]})
        except RuntimeError:
            pass

    def handle_update(self, update):
        if not isinstance(update, dict):
            return False
        update_id = update.get("update_id")
        if type(update_id) is int:
            if update_id <= self._last_update_id:
                return False
            self._last_update_id = update_id
        callback = update.get("callback_query")
        if callback is not None and not isinstance(callback, dict):
            return False
        event = callback if callback is not None else update.get("message")
        if not isinstance(event, dict):
            return False
        sender = event.get("from")
        message = event.get("message") if callback is not None else event
        chat = message.get("chat") if isinstance(message, dict) else None
        actor = sender.get("id") if isinstance(sender, dict) else None
        authorized = (type(actor) is int and actor in self.authorized_user_ids
                      and isinstance(chat, dict) and str(chat.get("id")) == self.chat_id)
        if not authorized:
            if callback is not None:
                self._answer(callback, "Not authorized.")
            return False
        try:
            if callback is not None:
                text = self._callback(callback.get("data"), actor)
            else:
                text = self._command(event.get("text"), actor)
                if text is None:
                    return False
        except (ValueError, TypeError, KeyError):
            text = "Invalid paper request. Check command arguments and trade state."
        except Exception:
            text = "Paper request failed. No live order was placed."
        buttons = None
        if isinstance(text, tuple):
            text, buttons = text
        if callback is not None:
            self._answer(callback, text)
        self.notify(text, buttons)
        return True

    @staticmethod
    def _number(value, positive=True):
        number = float(value)
        if not math.isfinite(number) or (positive and number <= 0):
            raise ValueError("Invalid number")
        return number

    @staticmethod
    def _order_type(value):
        if value not in {"limit", "market"}:
            raise ValueError("Invalid order type")
        return value

    @staticmethod
    def _boolean(value):
        if value.lower() not in {"on", "off", "true", "false"}:
            raise ValueError("Invalid boolean")
        return value.lower() in {"on", "true"}

    @staticmethod
    def _format(value):
        return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)

    @staticmethod
    def _guidance(trade_id):
        return (
            f"Modify paper trade with /modify {trade_id} key=value ...\n"
            "Allowed keys: entry_order_type=limit|market, entry_price_requested=PRICE, "
            "stop_loss=PRICE, target1=PRICE, target2=PRICE, trailing_enabled=on|off.\n"
            "Pending changes require final approval; open changes apply immediately.\n"
            f"Limit exit: /exit {trade_id} limit PRICE"
        )

    def _modified(self, trade_id, changes, actor):
        trade = self.engine.modify(trade_id, changes, actor=actor)
        if not trade:
            raise ValueError("No trade")
        status = trade.get("status", "") if isinstance(trade, dict) else getattr(trade, "status", "")
        text = f"Modified paper trade {trade_id}: {self._format(trade)}"
        if str(status).upper() in {"REQUESTED", "PENDING", "PENDING_APPROVAL"}:
            text += f"\nFinal confirmation required: /approve {trade_id} limit|market"
            return text, [[
                {"text": "Confirm limit", "callback_data": f"paper:{trade_id}:approve_limit"},
                {"text": "Confirm market", "callback_data": f"paper:{trade_id}:approve_market"},
            ]]
        return text

    def _callback(self, data, actor):
        if not isinstance(data, str):
            raise ValueError("Invalid callback")
        parts = data.split(":")
        if len(parts) != 3 or parts[0] != "paper" or not parts[1]:
            raise ValueError("Invalid callback")
        _, trade_id, action = parts
        if action in {"approve_limit", "approve_market"}:
            trade = self.engine.approve(trade_id, action.split("_")[1], actor=actor)
        elif action == "reject":
            trade = self.engine.reject(trade_id, actor=actor)
        elif action == "modify":
            return self._guidance(trade_id)
        elif action == "exit_limit":
            return f"Specify the paper exit price: /exit {trade_id} limit PRICE"
        elif action == "exit_market":
            trade = self.engine.exit(trade_id, order_type="market", actor=actor)
        elif action == "trailing":
            current = self.engine.get(trade_id)
            if not isinstance(current, dict):
                raise ValueError("No trade")
            return self._modified(trade_id, {
                "trailing": not current.get("trailing", False)
            }, actor)
        else:
            raise ValueError("Invalid callback")
        if not trade:
            raise ValueError("No trade")
        return f"Paper {action}: {self._format(trade)}"

    def _command(self, text, actor):
        if not isinstance(text, str) or not text.startswith("/"):
            return None
        parts = text.split()
        command = parts[0].split("@")[0].lower()
        args = parts[1:]
        if command == "/mode" and len(args) == 2 and args[0] == "approval":
            result = self.engine.configure({"approval_required": self._boolean(args[1])}, actor=actor)
        elif command == "/risk" and len(args) == 3 and args[0] == "pnl":
            profit = self._number(args[1])
            loss = self._number(args[2], positive=False)
            if loss >= 0:
                raise ValueError("Loss must be negative")
            result = self.engine.configure({"profit_cap": profit,
                                            "loss_cap": abs(loss)}, actor=actor)
        elif command == "/limits":
            compact = " ".join(args)
            match = re.fullmatch(r"max_open\s+([1-9]\d*)\s+max_day\s*([1-9]\d*)", compact)
            if not match:
                raise ValueError("Invalid limits")
            result = self.engine.configure({"max_open_positions": int(match[1]),
                                            "max_daily_trades": int(match[2])}, actor=actor)
        elif command == "/cutoff":
            match = re.fullmatch(r"index\s*(\d{2}:\d{2})\s+commodity\s*(\d{2}:\d{2})", " ".join(args))
            if not match:
                raise ValueError("Invalid cutoffs")
            for value in match.groups():
                hour, minute = map(int, value.split(":"))
                if hour > 23 or minute > 59:
                    raise ValueError("Invalid time")
            result = self.engine.configure({"index_cutoff": match[1],
                                            "commodity_cutoff": match[2]}, actor=actor)
        elif command == "/pending" and not args:
            result = (self.engine.trades(status="REQUESTED")
                      + self.engine.trades(status="ENTRY_PENDING"))
        elif command == "/positions" and not args:
            positions = (self.engine.trades(status="OPEN")
                         + self.engine.trades(status="EXIT_PENDING"))
            text = f"Paper positions: {self._format(self.engine.portfolio())}"
            buttons = []
            for trade in positions:
                trade_id = trade.get("id") or trade.get("trade_id")
                text += f"\n{self._format(trade)}"
                if not trade_id:
                    continue
                text += f"\n{self._guidance(trade_id)}"
                buttons.append([
                    {"text": f"{trade_id} market exit", "callback_data": f"paper:{trade_id}:exit_market"},
                    {"text": "Limit exit", "callback_data": f"paper:{trade_id}:exit_limit"},
                    {"text": "Trailing toggle", "callback_data": f"paper:{trade_id}:trailing"},
                ])
            return text, buttons
        elif command == "/approve" and len(args) in {1, 2}:
            result = self.engine.approve(args[0], self._order_type(args[1] if len(args) == 2 else "limit"), actor=actor)
        elif command == "/modify" and len(args) >= 2:
            changes = {}
            for item in args[1:]:
                key, sep, value = item.partition("=")
                if not sep or key in changes:
                    raise ValueError("Invalid modification")
                if key == "entry_order_type":
                    changes[key] = self._order_type(value)
                elif key == "trailing_enabled":
                    changes[key] = self._boolean(value)
                elif key in {"entry_price_requested", "stop_loss", "target1", "target2"}:
                    changes[key] = self._number(value)
                else:
                    raise ValueError("Invalid field")
            aliases = {"entry_price_requested": "entry", "target1": "target_1",
                       "target2": "target_2", "trailing_enabled": "trailing"}
            changes = {aliases.get(key, key): value for key, value in changes.items()}
            return self._modified(args[0], changes, actor)
        elif command == "/exit" and len(args) in {2, 3}:
            order_type = self._order_type(args[1])
            if (order_type == "limit") != (len(args) == 3):
                raise ValueError("Limit exits require a price")
            price = self._number(args[2]) if len(args) == 3 else None
            result = self.engine.exit(args[0], order_type=order_type, price=price, actor=actor)
        elif command == "/squareoff" and not args:
            result = self.engine.squareoff(reason="EMERGENCY", actor=actor)
        else:
            return ("Paper commands: /mode approval on|off; /risk pnl +30000 -10000; "
                    "/limits max_open 5 max_day20; /cutoff index15:25 commodity23:00; "
                    "/pending; /approve ID [limit|market]; /modify ID key=value ...; "
                    "/positions; /exit ID limit PRICE|market; /squareoff")
        return f"Paper {command[1:]}: {self._format(result)}"
