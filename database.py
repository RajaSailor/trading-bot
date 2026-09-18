from __future__ import annotations

import sqlite3
import json
import os
import threading
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

from timezone_utils import now_local_iso


class TradingDatabase:
    """SQLite-backed persistence for trades, orders, positions and signals."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self._lock = threading.RLock()
        self._local = threading.local()
        self._connections: list[sqlite3.Connection] = []
        self._uri = db_path == ":memory:"
        self.db_path = db_path if not self._uri else f"file:trading-db-{id(self)}?mode=memory&cache=shared"
        if not self._uri and self.db_path not in {"", ":memory:"}:
            os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        self.conn = self._connect()
        self._connections.append(self.conn)
        self._initialize_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, uri=self._uri, check_same_thread=False, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _connection(self):
        conn = self.conn if self._uri else getattr(self._local, "conn", None)
        if conn is None:
            conn = self._connect()
            if not self._uri:
                self._local.conn = conn
            self._connections.append(conn)
        try:
            yield conn
            conn.commit()
        finally:
            pass

    def _initialize_schema(self) -> None:
        migration_path = os.path.join(os.path.dirname(__file__), "migrations", "001_init.sql")
        with open(migration_path, "r", encoding="utf-8") as handle:
            schema_sql = handle.read()
        with self._lock:
            with self._connection() as conn:
                cursor = conn.cursor()
                cursor.executescript(schema_sql)

    def log_trade(self, trade: Dict[str, Any]) -> None:
        with self._lock:
            with self._connection() as conn:
                conn.execute(
                    "INSERT INTO trades (symbol, side, quantity, price, pnl, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        trade["symbol"],
                        trade["side"],
                        trade["quantity"],
                        trade["price"],
                        trade.get("pnl", 0.0),
                        trade.get("created_at", now_local_iso()),
                    ),
                )

    def save_order(self, order: Dict[str, Any]) -> None:
        with self._lock:
            with self._connection() as conn:
                conn.execute(
                    """
                    INSERT INTO orders (order_id, symbol, side, quantity, price, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(order_id) DO UPDATE SET
                        symbol = excluded.symbol,
                        side = excluded.side,
                        quantity = excluded.quantity,
                        price = excluded.price,
                        status = excluded.status
                    """,
                    (
                        order["order_id"],
                        order["symbol"],
                        order["side"],
                        order["quantity"],
                        order["price"],
                        order["status"],
                        order.get("created_at", now_local_iso()),
                    ),
                )

    def snapshot_position(self, snapshot: Dict[str, Any]) -> None:
        with self._lock:
            with self._connection() as conn:
                conn.execute(
                    """
                    INSERT INTO position_snapshots (symbol, quantity, average_price, snapshot_time)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        snapshot["symbol"],
                        snapshot["quantity"],
                        snapshot["average_price"],
                        snapshot.get("snapshot_time", now_local_iso()),
                    ),
                )

    def log_signal(self, signal: Dict[str, Any]) -> None:
        with self._lock:
            with self._connection() as conn:
                conn.execute(
                    "INSERT INTO signals (strategy, symbol, action, payload, created_at) VALUES (?, ?, ?, ?, ?)",
                    (
                        signal.get("strategy"),
                        signal["symbol"],
                        signal["action"],
                        json.dumps(signal),
                        signal.get("timestamp", now_local_iso()),
                    ),
                )

    def save_metric(self, name: str, value: float) -> None:
        with self._lock:
            with self._connection() as conn:
                conn.execute(
                    "INSERT INTO performance_metrics (metric_name, metric_value, recorded_at) VALUES (?, ?, ?)",
                    (name, value, now_local_iso()),
                )

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
        with self._lock:
            with self._connection() as conn:
                cursor = conn.execute(f"SELECT * FROM {table}")
                return list(cursor.fetchall())

    def fetch_latest_positions(self) -> List[sqlite3.Row]:
        with self._lock:
            with self._connection() as conn:
                cursor = conn.execute(
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

    def fetch_latest_position(self, symbol: str) -> Optional[sqlite3.Row]:
        with self._lock:
            with self._connection() as conn:
                cursor = conn.execute(
                    """
                    SELECT *
                    FROM position_snapshots
                    WHERE symbol = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (symbol,),
                )
                return cursor.fetchone()

    def order_exists(self, order_id: str) -> bool:
        with self._lock:
            with self._connection() as conn:
                cursor = conn.execute(
                    "SELECT 1 FROM orders WHERE order_id = ? LIMIT 1",
                    (order_id,),
                )
                return cursor.fetchone() is not None

    def claim_order(self, order: Dict[str, Any]) -> bool:
        with self._lock:
            with self._connection() as conn:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO orders (order_id, symbol, side, quantity, price, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        order["order_id"],
                        order["symbol"],
                        order["side"],
                        order["quantity"],
                        order["price"],
                        order.get("status", "PROCESSING"),
                        order.get("created_at", now_local_iso()),
                    ),
                )
                return cursor.rowcount == 1

    def delete_order(self, order_id: str) -> None:
        with self._lock:
            with self._connection() as conn:
                conn.execute("DELETE FROM orders WHERE order_id = ?", (order_id,))

    def close(self) -> None:
        closed = set()
        for conn in self._connections:
            if id(conn) in closed:
                continue
            conn.close()
            closed.add(id(conn))
