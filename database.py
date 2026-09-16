from __future__ import annotations

import sqlite3
import json
import os
from typing import Any, Dict, List, Optional


class TradingDatabase:
    """SQLite-backed persistence for trades, orders, positions and signals."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        migration_path = os.path.join(os.path.dirname(__file__), "migrations", "001_init.sql")
        with open(migration_path, "r", encoding="utf-8") as handle:
            schema_sql = handle.read()
        cursor = self.conn.cursor()
        cursor.executescript(schema_sql)
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
            INSERT INTO orders (order_id, symbol, side, quantity, price, status)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(order_id) DO UPDATE SET
                symbol = excluded.symbol,
                side = excluded.side,
                quantity = excluded.quantity,
                price = excluded.price,
                status = excluded.status
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
                json.dumps(signal),
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
        allowed_tables = {
            "trades",
            "orders",
            "position_snapshots",
            "signals",
            "performance_metrics",
        }
        if table not in allowed_tables:
            raise ValueError(f"Unsupported table: {table}")
        cursor = self.conn.execute(f"SELECT * FROM {table}")
        return list(cursor.fetchall())

    def fetch_latest_positions(self) -> List[sqlite3.Row]:
        cursor = self.conn.execute(
            """
            SELECT ps.*
            FROM position_snapshots ps
            INNER JOIN (
                SELECT symbol, MAX(id) AS max_id
                FROM position_snapshots
                GROUP BY symbol
            ) latest ON latest.max_id = ps.id
            ORDER BY ps.symbol
            """
        )
        return list(cursor.fetchall())

    def close(self) -> None:
        self.conn.close()
