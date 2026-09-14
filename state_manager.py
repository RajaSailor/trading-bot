from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from typing import Any, Dict, Optional
from copy import deepcopy


class StateManager:
    """Persists and restores application runtime state."""

    def __init__(self, state_file: str = "bot_state.json") -> None:
        self.state_file = state_file
        self.state: Dict[str, Any] = {
            "bot_status": "stopped",
            "configuration": {},
            "session": {"session_id": None, "started_at": None},
            "error_state": {"active": False, "message": None, "updated_at": None},
            "updated_at": datetime.utcnow().isoformat(),
        }
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.state_file):
            return
        try:
            with open(self.state_file, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
        except (json.JSONDecodeError, OSError):
            return
        if isinstance(loaded, dict):
            for key, value in loaded.items():
                if isinstance(value, dict) and isinstance(self.state.get(key), dict):
                    self.state[key].update(value)
                else:
                    self.state[key] = value

    def save(self) -> None:
        self.state["updated_at"] = datetime.utcnow().isoformat()
        state_dir = os.path.dirname(self.state_file) or "."
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=state_dir, delete=False) as handle:
            json.dump(self.state, handle, indent=2)
            temp_path = handle.name
        os.replace(temp_path, self.state_file)

    def set_bot_status(self, status: str) -> None:
        self.state["bot_status"] = status
        self.save()

    def set_config(self, key: str, value: Any) -> None:
        self.state.setdefault("configuration", {})[key] = value
        self.save()

    def get_config(self, key: str, default: Optional[Any] = None) -> Any:
        return self.state.get("configuration", {}).get(key, default)

    def start_session(self, session_id: str) -> None:
        self.state["session"] = {"session_id": session_id, "started_at": datetime.utcnow().isoformat()}
        self.save()

    def end_session(self) -> None:
        self.state["session"] = {"session_id": None, "started_at": None}
        self.save()

    def set_error(self, message: str) -> None:
        self.state["error_state"] = {
            "active": True,
            "message": message,
            "updated_at": datetime.utcnow().isoformat(),
        }
        self.save()

    def clear_error(self) -> None:
        self.state["error_state"] = {"active": False, "message": None, "updated_at": datetime.utcnow().isoformat()}
        self.save()

    def recover(self) -> Dict[str, Any]:
        self._load()
        return deepcopy(self.state)
