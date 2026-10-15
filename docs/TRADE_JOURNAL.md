# Durable Excel trade journal

The journal is a report, not a trading control. Its source of truth is the
existing persistent SQLite signal lifecycle (`SIGNAL_DB_PATH`) and live execution
database (`LIVE_DB_PATH`). Export failures, a disconnected laptop, or an open
Excel/OneDrive file do not block signal processing or order management. The
exporter replays those databases after restart and coalesces workbook rebuilds
on a background worker. It does not change `PRACTICE_MODE`,
`AUTO_TRADING_ENABLED`, `NIFTY_LIVE_ENABLED`, readiness checks, or order behavior.

## Workbook and accounting

The generated `.xlsx` has exactly four worksheets, in this order: **Index**,
**Stocks**, **Commodity**, and **Live Trades**. Each sheet has frozen headers,
filters, IST date/time and duration formats, closed-result summaries, and a
stable `Row ID` used for safe review-field merging.

- The first three sheets contain hypothetical lifecycle signal outcomes, one
  validated metadata lot per signal. They are not paper or broker executions;
  order types are `N/A — no order submitted`. Open signals have no exit time,
  exit price, duration, or realized outcome.
- **Live Trades** contains only entries with a broker-confirmed fill. Cumulative
  partial fills use the broker's average price and filled quantity. A row is
  closed only after the complete entry quantity has a confirmed, attributed exit.
  Acknowledgements, proposed orders, unknown fills, and signal-only NIFTY rows
  are not live trades.
- `P&L (Points)` is a premium-point difference. Signal monetary amounts require
  a positive `premium_multiplier` explicitly present in validated contract
  metadata. Without it, INR amounts and gross P&L are blank, with the missing
  conversion explained in the accounting basis; lot size is not assumed to be
  a universal currency multiplier. Confirmed NIFTY live gross P&L uses the
  existing broker average-fill and quantity accounting.
- Actual commissions/taxes/charges are not currently imported from contract
  notes or a verified ledger. Live charges remain pending and net P&L / net win
  rate remain unavailable; no zero-fee claim is made. Signal fees are
  inapplicable because no order was submitted.
- Win rate is wins divided by priced closed wins, losses, and break-even trades.
  Break-even is distinct and included in the denominator; open and unpriced
  trades are excluded. Sheets show running and daily gross rates. The workbook
  never combines hypothetical signal outcomes and live totals.
- Unknown text is escaped against spreadsheet formula injection. Bot-owned
  columns are authoritative outputs and must not be edited as a source of trade
  data. `Notes` and `Review` are user-owned fields; the local helper preserves
  those fields by stable row ID and refuses conflicting/corrupt workbooks rather
  than silently dropping review text.

The cloud workbook is generated as `.xlsx`; OOXML data is never written with an
`.xls` extension. The user's requested path was
`C:\Users\A  Bharathiraja\OneDrive\Desktop\Trading Bot related files\Trading Log File through bot .xls`.
The recommended modern path is the same directory with the filename
`Trading Log File through bot.xlsx`. The helper never renames or overwrites an
existing `.xls`. Confirm the extension change and OneDrive directory with the
user before configuring a destination. No workbook is created on the user's
laptop merely by deploying this code.

## Cloud configuration

Set these Render environment variables. Use a persistent disk mount and keep
each SQLite database and the generated workbook on that disk; ephemeral
filesystem data is not durable.

```text
SIGNAL_DB_PATH=/var/data/signals.db
LIVE_DB_PATH=/var/data/live_trading.db
TRADE_JOURNAL_XLSX_PATH=/var/data/trade-journal.xlsx
TRADE_JOURNAL_EXPORT_SECONDS=5
TRADE_JOURNAL_EXPORT_TOKEN=<independently generated random secret, 32+ characters>
```

`LIVE_DB_PATH` is where the existing live execution code persists state. Keep
the current configured path if it is already on persistent storage. Before
moving an existing SQLite database to a new mount, stop the service, make a
verified backup, copy the database (and any required WAL/SHM state using a
SQLite-consistent backup), then configure the supported path. A disk mount does
not migrate data by itself. Keep one scanner/executor process as required by
[`RENDER_SETUP.md`](RENDER_SETUP.md).

Generate the export token on a trusted machine, for example:

```powershell
py -c "import secrets; print(secrets.token_urlsafe(48))"
```

Put the result only in Render's secret environment configuration and in the
Windows user's protected environment configuration for the optional helper.
Never place it in a URL, query string, source code, command-line argument, or
logs. The endpoint is `GET /trade-journal.xlsx` and requires the
`X-Trade-Journal-Token` header. Missing/short server tokens disable download.
The response is `no-store`; there is no unauthenticated trade-download route.
Health exposes only last event/export timestamps, pending/exported row counts,
and an exception class name—not workbook contents, paths, or credentials.

## Optional Windows sync

The server does not have access to the laptop's `C:` drive. Install the project
dependencies on the laptop, configure the three environment variables below
for the Windows user/task account, and run the helper from the repository root:

```powershell
py -m pip install requests==2.31.0 openpyxl==3.1.5
$env:TRADE_JOURNAL_URL = "https://<your-service-host>/trade-journal.xlsx"
$env:TRADE_JOURNAL_DESTINATION = "C:\Users\A  Bharathiraja\OneDrive\Desktop\Trading Bot related files\Trading Log File through bot.xlsx"
python tools\sync_trade_journal.py
```

Set `TRADE_JOURNAL_TOKEN` in the Windows user's protected environment; do not
paste it into the script or URL. For scheduled syncing, configure Windows Task
Scheduler to run the helper under the same account and environment that has
OneDrive configured. The helper uses HTTPS and the secret header, caps download
size, validates the workbook, takes a versioned backup, uses an inter-process
file lock and atomic replacement, retries transient Excel file locks, and
preserves `Notes`/`Review` by stable ID. If download, validation, review merge,
or replacement fails, the previous workbook stays in place and the helper
returns an error. Backups are limited to the latest ten versions.

Local syncing requires the laptop and helper to be online and able to reach the
cloud service. Trading does not depend on that laptop or helper. The signed-in
OneDrive sync client may upload the local file after it is written; this feature
does **not** connect the bot to OneDrive or Microsoft Graph. A future Graph
integration would require explicit OAuth consent, least-privilege permissions,
secure refresh-token storage, and concurrency/eTag handling.

## Operational checks

Check `/health` for `trade_journal.running`, `last_event_at`, `last_export_at`,
`export_pending`, `pending_rows`, `exported_rows`, and `export_error`. A non-null
export error is diagnostic only and does not stop trading. The generated workbook is a
reproducible view of SQLite, not a substitute for backups. Do not commit
workbooks, SQLite databases, or export credentials.
