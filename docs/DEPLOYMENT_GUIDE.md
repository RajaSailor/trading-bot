# Deployment Guide

## System Requirements
- Python 3.9+
- Network access to DhanHQ + Telegram APIs
- Environment variables in `.env`

## Installation
```bash
pip install -r requirements.txt
python main.py
```

## Configuration
- Required credentials: Dhan client/token, Telegram bot/chat IDs.
- Optional monitoring credentials: Slack webhook, SMTP, service alert bot/channel IDs.
- For protected webhook admin routes, set `WEBHOOK_SECRET`.

## Backup and Restore
- Persist bot state files and deployment env config.
- Keep periodic backups of runtime JSON/state artifacts.
- Restore by redeploying code, restoring env vars, then state files.

## NIFTY live route: BLOCKED — do not activate

This change is substantive but incomplete for production. The isolated engine
and Telegram controls are exercised with injected **mock/simulation adapters
only**. No real order adapter is connected to the runtime. Flags cannot bypass
this barrier; `/health` distinguishes paper, configured, and blocked states.
Never interpret an outbound alert or broker acknowledgment as a real fill.

Policy checks exercised only with isolated mock adapters include NIFTY-only
ITM+1 validation, exactly one metadata lot, fresh test funds/margin reads, two
filled entries per IST day, one-open-position enforcement, acknowledgment-versus-
fill separation, and normalized snapshot reconciliation. The test ledger uses a
fixed stop, one target, no trailing/breakeven, and LIMIT-only exits. Its `MARGIN`
product field is an unverified intent, not evidence of Dhan endpoint support.

Missing runtime capabilities include the Dhan Super Order and read adapters,
verified funds/margin and product schemas, startup/ongoing authoritative
reconciliation for a real account, the automatic execution/notification worker,
verified exchange-side protection, and the 15:25 LIMIT-only square-off service.
The current runtime reports these checks as blockers and cannot place a real
order. A locally calculated stop/target is not broker protection; an unfilled
square-off must remain pending and be closed manually in Dhan. These are
implementation gaps, not deployment flags.

Known normalized-snapshot limitation: order fills can lead the broker's position
snapshot. The mocked engine conservatively treats a lower position as an
external reduction; a later catch-up can appear to be a manual addition and
latch an entry halt. Production needs an attribution/lag recovery protocol
before use. Do not delete or edit the ledger to bypass that halt.

### Source evidence and remaining verification

Primary sources:

- [Dhan Orders v2](https://dhanhq.co/docs/v2/orders/), Place Order,
  Modify Order, Cancel Order, Order Book, Order Detail, correlation-ID lookup,
  Trade Book and trades for an order. The supplied parent-session evidence
  verifies `POST /orders`, `PUT /orders/{id}`, `DELETE /orders/{id}`,
  `GET /orders`, `GET /orders/{id}`, `GET /orders/external/{correlation-id}`,
  `GET /trades`, and `GET /trades/{id}`. Correlation IDs are at most 30 characters.
  Placement fields include `dhanClientId`, `correlationId`, `transactionType`,
  `exchangeSegment`, `productType`, `orderType`, `validity`, `securityId`,
  `quantity`, `price`, `triggerPrice`, and `afterMarketOrder`.
  `orderId`/`orderStatus=PENDING` is acknowledgment, **not a fill**.
  Static-IP whitelisting is required for placement, modification and cancellation.
- [Dhan Funds v2](https://dhanhq.co/docs/v2/funds/): `GET /fundlimit` and
  margin-calculator request/response schemas require re-verification before
  implementing production adapters. Normalized test dictionaries are **not**
  verified Dhan wire schemas.
- [Dhan Authentication v2](https://dhanhq.co/docs/v2/authentication/):
  verify token validity/renewal, trading and market-data permissions and actual
  account entitlement. Token expiry or authorization failure must block entries.

The official URLs failed DNS resolution in this implementation sandbox.
Only the order facts explicitly supplied by the parent session are treated as
verified. Portfolio, quotes, order updates, annexure/instrument semantics, funds,
margin, snapshot ordering and supported broker stop behavior remain production
verification gaps. Do not invent postback authentication or exchange-side
protection guarantees from legacy modules.

### Activation prerequisites and go-live runbook (not satisfied by default)

Safe defaults are `PRACTICE_MODE=true`, `AUTO_TRADING_ENABLED=false`, and
`NIFTY_LIVE_ENABLED=false`. `LIVE_DB_PATH` must be a persistent file on a mounted
volume, separate from `PAPER_DB_PATH`. With `NIFTY_LIVE_ENABLED=true` and Dhan
credentials, `live_runtime.py` now builds the documented Dhan v2 wire adapter
(`dhan_live_adapter.py`) and computes each check from read-only probes;
`/health` exposes the adapter, Super Order, funds/margin schema, product type,
reconciliation, authentication, and static-IP checks; any false check blocks
entry. Exact keys, endpoint evidence and the remaining external validations are
in [LIVE_EXECUTION.md](LIVE_EXECUTION.md).

Do not enable real trading until all of the following are implemented and
independently verified against the current official Dhan API and the actual
deployment account. Items 1, 4, 6, 7 and 8 are now implemented in code and
covered by mocked tests only; the Super Order stop-leg/leg-fill behavior (2),
product type (3) and order-endpoint static-IP acceptance (5) still need
external validation before `LIVE_EXTERNAL_VALIDATION_REVISION` is set:

1. Authenticated order, funds/margin, contract-master, quote, order/trade-book,
   and position adapters with validated response schemas, bounded request
   retries, and no logging of credentials or raw sensitive responses.
2. Verified Super Order request/response and manual edit/exit reconciliation.
   The user-approved stop leg is market-executed when triggered and can slip.
   Verify the endpoint's support for one target, LIMIT entry, and the stop
   behavior. No trailing or breakeven modifications are allowed.
3. Verify the configured `LIVE_PRODUCT_TYPE` (default `INTRADAY`; `MARGIN`
   allowed) is supported by that endpoint for this account.
4. Startup and ongoing authoritative reconciliation, including pending/partial/
   unknown orders, manual Dhan edits/exits, externally-created exposure, and
   restart recovery. Reconciliation failure or ambiguity must block entries.
5. Confirm Dhan auth and static-IP authorization from actual outbound order
   requests. A funds/read call is not evidence that the order endpoint accepts
   the source IP; shared Render egress must be treated as uncertain until
   confirmed.
6. Implement a durable execution worker and Trade Control notification outbox.
   Only broker-confirmed fills may generate ENTRY EXECUTED or EXIT EXECUTED;
   alerts also include actual fill details and available balance/realized P&L.
   Deduplicate/rate-limit insufficient-funds alerts.
7. Implement 15:25 IST entry cutoff and LIMIT-only square-off. If that exit is
   not filled, retain OPEN/EXIT-PENDING state and block further entries. There
   is no market fallback or blind resend; the operator must close any remaining
   position manually in Dhan and confirm it by reconciliation.
8. Keep the global cap at two filled entries per IST day and one managed open
   position. All non-NIFTY indices, stocks, commodities, and other instruments
   remain signal-only.
9. Use a separate live Telegram bot/webhook design that does not replace the
   paper bot's webhook. Review secret storage, numeric actor/chat allowlists,
   persistent state backup/restore, and one process per broker account.
10. Pass focused tests for fills, caps, reconciliation, manual exits, cutoff,
    routing, funds, alert gating, and health blockers. Complete human readiness
    review before deployment.

**Go-live procedure after all prerequisites above are actually satisfied:**

1. Back up and restore-test the dedicated live database. Verify `/health` shows
   every required check healthy, reconciliation complete, zero unknown orders,
   and expected account limits. A missing adapter or IP/auth/schema error is a
   hard stop.
2. Verify the connected Dhan client/account, static-IP behavior on order
   endpoints, product support, instrument lot/tick metadata, funds, and Telegram
   routing using a separately approved test plan. Never send a test order just
   to discover whether the IP is authorized.
3. Only then set `NIFTY_LIVE_ENABLED=true`, `PRACTICE_MODE=false`, and
   `AUTO_TRADING_ENABLED=true` in deployment secrets/configuration. Restart one
   worker and monitor `/health`, broker state, and Trade Control. Stop immediately
   on any uncertain order, stale/unavailable reconciliation, or mismatched fill.

**Rollback:** set `AUTO_TRADING_ENABLED=false` and `NIFTY_LIVE_ENABLED=false`,
restore `PRACTICE_MODE=true`, and verify the live health status shows entries
disabled. Do not assume this cancels existing broker orders or closes positions.
Inspect positions, orders, and trades in Dhan; manually manage outstanding risk;
then reconcile before any later restart. Never delete the live database or blindly
resend/cancel an ambiguous order.

### Engine policy and operator handling

The isolated route validates only NIFTY NSE_FNO OPTIDX ITM+1 CE/PE contracts and
one lot using security-master lot/tick metadata. It applies a two-filled-entry
IST-day cap and one-managed-position guard. At/after 15:25 IST it rejects new
entries. Real positions can be created only when every readiness check passes
and all three activation flags are set (see LIVE_EXECUTION.md). Never infer
execution from a signal, a local preview, or a broker acknowledgment.

Trade Control must remain reserved for confirmed execution lifecycle messages
and insufficient-balance alerts; routine signals and reports stay on their
existing service-alert routes. An unresolved exit is not closed and must be
managed manually in Dhan. Unknown exposure or broker errors block new entries.

**Risk disclaimer:** Options can lose the entire premium quickly. Planned 1:2
reward/risk, stop loss, target, and trailing do not guarantee execution, a win
rate, or profit. Never trade money you cannot afford to lose.

### Quote freshness diagnosis

The reported `stale_quote=97795` is a rejection counter, not verified HTTP request
volume. Existing quote calls share batching, cache, pacing and 429 backoff.
The parser accepts epoch seconds/milliseconds, ISO offsets/UTC, and Dhan-style
`DD/MM/YYYY HH:MM:SS` as IST. Cache reads retain source timestamps.
Fresh bid/ask snapshots no longer turn stale last-traded premium into fresh live
breakout evidence: `trade_timestamp` is separate from executable-book timestamp.
Source trade freshness is checked at HTTP response receipt, preserving trades
that genuinely occurred during the request. Executable-book age remains bounded
by request start; receipt time does not refresh book evidence or loosen limits.
Missing/stale source trade time blocks live LTP signals even with fresh depth.
Do not increase polling, bypass cooldowns, or loosen freshness to force entries.
Actual account 429 cause/permissions must be checked from broker/deployment
telemetry; logs supplied here do not establish the exact remote cause.
