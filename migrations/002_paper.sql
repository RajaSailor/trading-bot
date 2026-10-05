CREATE TABLE IF NOT EXISTS paper_trades (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    trading_day TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS paper_trades_status ON paper_trades(status);
CREATE INDEX IF NOT EXISTS paper_trades_day ON paper_trades(trading_day);
CREATE TABLE IF NOT EXISTS paper_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_daily_snapshots (
    trading_day TEXT PRIMARY KEY,
    date TEXT,
    opening_balance REAL,
    closing_balance REAL,
    total_trades INTEGER NOT NULL DEFAULT 0,
    winning_trades INTEGER NOT NULL DEFAULT 0,
    losing_trades INTEGER NOT NULL DEFAULT 0,
    open_positions INTEGER NOT NULL DEFAULT 0,
    payload TEXT,
    opening_equity REAL NOT NULL,
    equity REAL NOT NULL,
    realized_pnl REAL NOT NULL DEFAULT 0,
    unrealized_pnl REAL NOT NULL DEFAULT 0,
    daily_pnl REAL NOT NULL DEFAULT 0,
    trades_count INTEGER NOT NULL DEFAULT 0,
    halted INTEGER NOT NULL DEFAULT 0,
    halt_reason TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id TEXT,
    action TEXT NOT NULL,
    actor TEXT,
    old_value TEXT,
    new_value TEXT,
    created_at TEXT NOT NULL
);
