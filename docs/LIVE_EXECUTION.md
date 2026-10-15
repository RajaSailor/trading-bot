# NIFTY live execution (Dhan v2 Super Order) — design, readiness and runbook

> **Status of this release:** the production Dhan wire adapter, computed
> readiness checks, reconciliation, Super Order management and Trade Control
> fill messages are implemented and tested against **mocked** Dhan responses
> only. No order has been sent by the authors or tests. Two behaviors cannot be
> established from Dhan documentation and stay **explicit blockers** until an
> operator validates them externally (see "External validation"). Defaults
> keep execution off. Nothing here guarantees fills, slippage limits or profit.

## Components

| File | Role |
| --- | --- |
| `dhan_live_adapter.py` | Dhan v2 HTTP wire adapter. Validates wire schemas strictly and returns the normalized schemas used by `LiveBroker`. Read-only readiness probes. |
| `live_broker.py` | Fail-closed boundary: master/ITM+1/lot/tick/funds/margin/exposure checks before any write; readiness computed only from the wire adapter's checks. |
| `live_execution.py` | Durable SQLite intents (`LIVE_DB_PATH`), correlation ids, reconciliation, per-leg exit accounting, 15:25 square-off, operational alerts outbox. |
| `live_runtime.py` | Route (NIFTY `index_options` only), `LiveWorker` (probes, reconciliation, manage, Trade Control delivery), message formatter, health. |

### Normalized vs wire schemas

Wire names are Dhan's JSON fields (`availabelBalance` — Dhan's documented
spelling —, `filledQty`, `averageTradedPrice`, `legDetails`, `netQty` …).
They are validated in `dhan_live_adapter.py` only. `LiveBroker` and
`LiveExecution` consume normalized dicts (`available`, `filled_quantity`,
`average_price`, `legs`, `exit_fills`, `positions{security_id: qty}`) that
always carry `verified=True` and a receive-time `timestamp`.

## Endpoint evidence

From the DhanHQ v2 documentation (https://dhanhq.co/docs/v2/ — authentication,
orders, super-order, portfolio, funds, market-quote and annexure pages) with
request shapes cross-checked against the official `dhanhq==2.2.0` SDK
(`_super_order.py`, `_funds.py`, `dhan_http.py`):

| Use | Endpoint | Fields relied on |
| --- | --- | --- |
| Auth probe | `GET /profile` | `dhanClientId`, `tokenValidity` (`dd/mm/yyyy HH:MM`), `activeSegment` |
| Static IP (read only) | `GET /ip/getIP` | `primaryIP`, `secondaryIP` — `setIP`/`modifyIP` are **never** called |
| Funds | `GET /fundlimit` | `availabelBalance`, `sodLimit`, `utilizedAmount` |
| Margin | `POST /margincalculator` | `totalMargin`, `availableBalance`, `insufficientBalance`, `brokerage` |
| Entry | `POST /super/orders` | `correlationId`, `transactionType=BUY`, `exchangeSegment=NSE_FNO`, `productType`, `orderType=LIMIT`, `securityId`, `quantity`, `price`, `targetPrice`, `stopLossPrice`, `trailingJump=0` → `orderId`, `orderStatus` |
| Square-off | `PUT /super/orders/{id}` | `legName=TARGET_LEG`, `targetPrice` |
| Cancel unfilled entry | `DELETE /super/orders/{id}/ENTRY_LEG` | cancelling the entry cancels all legs (so partial entries are never auto-cancelled) |
| Reconciliation | `GET /super/orders`, `GET /orders`, `GET /trades`, `GET /positions` | statuses `TRANSIT/PENDING/PART_TRADED/TRADED/CLOSED/CANCELLED/REJECTED/EXPIRED/TRIGGERED`, `filledQty`, `averageTradedPrice`, `legDetails[]`, `tradedQuantity`, `tradedPrice`, `exchangeTime`, `netQty` |
| Errors | annexure | `DH-901/902` auth, `903` account, `904` rate limit, `905` input, `906` order, `908` internal, `909` network, `911` invalid IP |

Release notes state that all order APIs need a whitelisted static IP and that
API MARKET orders are converted to LIMIT with market price protection. Order API
rate limit is 10/s; the adapter paces calls at ≥100 ms.

## Readiness checks (all must be true; health shows each one)

| Check | How it is computed |
| --- | --- |
| `broker_adapter` | wire adapter constructed (`DHAN_CLIENT_ID` + `ACCESS_TOKEN`) |
| `authentication_verified` | read-only `/profile` matches client id and token validity not expired |
| `funds_margin_schema_verified` | `/fundlimit` parsed with the documented schema (margin is validated on every entry) |
| `reconciliation_verified` | authoritative snapshot (orders + super orders + trades + positions) within 60 s |
| `product_type_verified` | `LIVE_PRODUCT_TYPE` ∈ {INTRADAY, MARGIN} and profile shows Derivative segment |
| `static_ip_verified` | `LIVE_STATIC_EGRESS_IP` is an exact address equal to Dhan's registered primary/secondary IP **and** three samples from `LIVE_EGRESS_IP_CHECK_URL` all equal it. CIDRs/ranges never match. |
| `super_order_verified`, `production_adapter_verified` | operator attestation `LIVE_EXTERNAL_VALIDATION_REVISION` equals the deployed 40-hex revision (`RENDER_GIT_COMMIT`/`GITHUB_SHA`) |

Attribute flags on an adapter are ignored. A successful funds call never marks
the static IP or order authorization as verified.

## External validation still required (cannot be proven from docs)

1. **Stop-loss leg execution type and leg fills**: Dhan support describes the
   Super Order stop as a market exit (the operator accepted this); the docs do
   not specify it, nor how leg fills appear in `/trades`. Exit fills are
   attributed by matching trade `orderId` to the super order or `legDetails`
   order ids; if Dhan reports leg trades differently, exits will be treated as
   unattributed reductions (operational alert + halt, no false EXIT message).
2. **Order endpoint acceptance from the deployment egress**: Dhan allows at most
   two registered IPs. Render's shared outbound ranges do not establish one
   stable source address; do not assume a `.33` (or any) address covers a CIDR.
   Use a dedicated static-egress setup and confirm with Dhan.
3. **Product type** for NIFTY Super Orders on this account.

Only after these are validated for a specific deployed revision should the
operator set `LIVE_EXTERNAL_VALIDATION_REVISION` to that revision. A new deploy
changes the revision and re-blocks execution until re-attested.

## Execution policy (enforced)

- NIFTY NSE_FNO OPTIDX, nearest expiry after today, listed ITM+1 CE/PE, exactly
  one master lot, tick-valid prices. Only `index_options` scanner `live_ltp`
  signals; stock/other-index/commodity signals are never routed.
- One Super Order per signal: LIMIT entry (lifecycle entry = breakout LTP rounded
  up to tick), LIMIT target = entry + 2R, stop = 95 % of RED low (tick-rounded),
  `trailingJump=0`. Market behavior is accepted **only** for Dhan's stop leg.
- Max 2 filled entries per IST day, one managed position, no entries at/after
  15:25 IST. Pending/partial/unknown/exit-pending states, halts and unknown
  exposure block entries.
- Funds: `/fundlimit` available ≥ max(margin, premium×qty) and
  `insufficientBalance == 0`; otherwise the entry is skipped with one Trade
  Control operational alert per IST day.
- Ambiguous writes (timeouts, 5xx, unparseable acks) stay `UNKNOWN` and are
  never retried; only reconciliation resolves them. Documented DH-90x rejections
  resolve to `REJECTED` only after a later authoritative snapshot (≥60 s) lacks
  the correlation id.
- Unfilled entries are cancelled after `entry_timeout_seconds` (60 s). Partial
  entries are **not** cancelled (it would cancel the protection legs) — manual
  review alert.
- 15:25 IST: the TARGET_LEG is moved to the tick-floored bid (LTP if no bid) up
  to 5 times, 30 s apart. No independent SELL is placed, so the stop leg is
  never cancelled and the position cannot be oversold. If still open: manual
  action alert; the position is reported open, not closed.
- Manual leg edits, trailing, a cancelled stop leg while open, or a manual exit
  with active legs halt entries with an operational alert. Halts are never
  cleared automatically; after reviewing the Dhan account, an operator calls
  `LiveExecution.clear_halt("reason")` on the live database from a maintenance
  shell while `AUTO_TRADING_ENABLED=false`.

## Trade Control messages

- `✅ NIFTY LIVE ENTRY EXECUTED` / `🏁 NIFTY LIVE EXIT EXECUTED` only from
  authoritative broker fills (quantity, average prices, reason, **gross** P&L
  before brokerage/taxes — net is not available).
- `⚠️ OPERATIONAL ALERT — this is NOT an order execution` for funds, unknown
  writes, halts, partial entries and square-off failures.
- Delivery is at-least-once from the durable outbox; a crash after Telegram
  accepted a message may resend it.

## Environment keys

| Key | Default | Meaning |
| --- | --- | --- |
| `NIFTY_LIVE_ENABLED` | `false` | construct the live route/adapter |
| `PRACTICE_MODE` | `true` | must be `false` for entries |
| `AUTO_TRADING_ENABLED` | `false` | must be `true` for entries |
| `DHAN_CLIENT_ID`, `ACCESS_TOKEN` | — | existing Dhan credentials (never logged or shown in health) |
| `LIVE_DB_PATH` | `live_trading.db` | persistent SQLite, separate from `PAPER_DB_PATH` |
| `LIVE_PRODUCT_TYPE` | `INTRADAY` | `INTRADAY` or `MARGIN` |
| `LIVE_STATIC_EGRESS_IP` | — | exact registered static IP |
| `LIVE_EGRESS_IP_CHECK_URL` | — | plain-text "what is my IP" URL used to sample egress |
| `LIVE_EXTERNAL_VALIDATION_REVISION` | — | 40-hex revision the operator externally validated |
| `LIVE_PROBE_SECONDS` | `300` | read-only probe interval |
| `LIVE_RECONCILE_SECONDS` | `15` | worker interval (must stay < 60 s) |

## Persistence and restart safety

`LIVE_DB_PATH` holds intents, correlation ids, fill accounting, halts and the
outbox. **Render's default filesystem is ephemeral**: attach a persistent disk
and point `LIVE_DB_PATH` (and `SIGNAL_DB_PATH`, which also holds chart jobs) to
it, or do not enable live execution. Losing this file loses ownership history;
the engine would then halt on "unknown external BUY activity". Never delete it
to "reset". Startup performs read-only reconciliation only.

## Safe practice test

1. Keep `PRACTICE_MODE=true`, `AUTO_TRADING_ENABLED=false`, set
   `NIFTY_LIVE_ENABLED=true` with a persistent `LIVE_DB_PATH`.
2. Deploy; `/health → nifty_live` shows `mode=blocked`, each check and
   `adapter_diagnostics.probe_errors` (categories only). This performs **reads
   only** (profile, getIP, fundlimit, order/trade/position books).
3. Fix failing checks; complete external validation; set
   `LIVE_EXTERNAL_VALIDATION_REVISION`.

## Activation and rollback

Activate only when every check is true while still in practice mode: then set
`PRACTICE_MODE=false` and `AUTO_TRADING_ENABLED=true` and monitor `/health`,
Dhan and Trade Control. **Rollback:** set `AUTO_TRADING_ENABLED=false` (and
`PRACTICE_MODE=true`). The worker keeps reconciling and still performs the
15:25 square-off for an open managed position while `NIFTY_LIVE_ENABLED=true`;
setting `NIFTY_LIVE_ENABLED=false` stops all monitoring, so first close/verify
positions in Dhan. Rollback never cancels broker orders by itself.

Signal/lifecycle rupee results and charts are hypothetical signal accounting;
only broker-fill messages describe actual executions.
