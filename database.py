from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional


class TradingDatabase:
    """SQLite-backed persistence for trades, orders, positions and signals."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        cursor = self.conn.cursor()
        cursor.executescript(
            """
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                price REAL NOT NULL,
                pnl REAL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT UNIQUE NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                price REAL NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS position_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                average_price REAL NOT NULL,
                snapshot_time TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy TEXT,
                symbol TEXT NOT NULL,
                action TEXT NOT NULL,
                payload TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS performance_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                metric_name TEXT NOT NULL,
                metric_value REAL NOT NULL,
                recorded_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        self.conn.commit()

    def log_trade(self, trade: Dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT INTO trades (symbol, side, quantity, price, pnl) VALUES (?, ?, ?, ?, ?)",
            (trade["symbol"], trade["side"], trade["quantity"], trade["price"], trade.get("pnl", 0.0)),
        )
        self.conn.commit()

    def save_order(self, order: Dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO orders (order_id, symbol, side, quantity, price, status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (order["order_id"], order["symbol"], order["side"], order["quantity"], order["price"], order["status"]),
        )
        self.conn.commit()

    def snapshot_position(self, snapshot: Dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO position_snapshots (symbol, quantity, average_price)
            VALUES (?, ?, ?)
            """,
            (snapshot["symbol"], snapshot["quantity"], snapshot["average_price"]),
        )
        self.conn.commit()

    def log_signal(self, signal: Dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT INTO signals (strategy, symbol, action, payload) VALUES (?, ?, ?, ?)",
            (
                signal.get("strategy"),
                signal["symbol"],
                signal["action"],
                str(signal),
            ),
        )
        self.conn.commit()

    def save_metric(self, name: str, value: float) -> None:
        self.conn.execute(
            "INSERT INTO performance_metrics (metric_name, metric_value) VALUES (?, ?)",
            (name, value),
        )
        self.conn.commit()

    def fetch_all(self, table: str) -> List[sqlite3.Row]:
        cursor = self.conn.execute(f"SELECT * FROM {table}")
        return list(cursor.fetchall())

    def close(self) -> None:
        self.conn.close()
