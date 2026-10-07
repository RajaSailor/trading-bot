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

Implemented policy and isolated tests: NIFTY-only ITM+1 validation, exactly one
metadata lot, carry-forward product intent (`MARGIN`), fresh funds/margin checks,
two filled entries per IST day, one-open-position guard, five-percent inclusive
re-entry band, acknowledgment-versus-fill separation, conservative normalized
snapshot reconciliation, fill-based 2R target/step-trailing calculations, and
auto-mode suppression of approval and non-execution messages. Auto mode remains
fail-closed; these controls do not constitute a connected execution service.

Missing runtime capabilities: verified Dhan Super Order placement/read adapters
and wire schemas, paced production funds/margin cache, an automatic execution and
notification worker, authenticated broker-update ingress, authoritative
reconciliation/restart recovery, verified Super Order target/stop modification
and attribution, protection scheduler, cutoff/emergency exit automation, and a
production readiness attestation. A locally calculated stop/target is not broker
protection. Auto-mode proposals fail closed and no generic-order substitute is
sent. These are implementation gaps, not merely deployment environment settings.

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

### Activation prerequisites (not satisfied by this release)

1. Implement and review verified production funds/margin/quote/master adapters,
   with shared account pacing, bounded balance caching and fresh submission
   rechecks. Unavailable/stale funds or quote evidence blocks entry.
2. Implement authoritative orderbook/tradebook/positions reconciliation, including
   external same-account NIFTY entries, manual additions, modifications, partial
   exits, pending SELL quantities, delayed BUY fills, snapshot lag and restart
   recovery. Attribute only managed orders; never cancel unrelated protection.
   Unknown submissions must reconcile by persisted correlation/trades, never
   blind resend. Local reservation accounting is not proof of broker execution.
3. Complete and verify actual-fill protection, Super Order stop/target behavior,
   partial-fill handling, monotonic step trailing (first +5 points to breakeven,
   then +5 SL for each additional +5 favorable move), 2R target, and session
   cutoffs. Client-side protection cannot protect during outages; it is not an
   exchange-side stop.
4. Verify static IP against **actual deployment egress**, not inbound host IP;
   confirm Dhan trading/data permissions, token expiry and real contract metadata.
5. Use separate persistent `LIVE_DB_PATH` and `PAPER_DB_PATH` on mounted storage,
   with backups. One app process/worker per account; do not use replicas or
   Gunicorn preloading. Never delete the ledger to clear unknown exposure/caps.
6. Configure a separate live Telegram bot/webhook or a reviewed multiplexing
   design. Telegram allows one webhook per bot: registering `/telegram/live`
   on the paper bot replaces `/telegram/paper`. Existing paper endpoint remains
   unchanged. Numeric user IDs, exact numeric chat ID and a random >=32-character
   secret are mandatory. Register HTTPS `setWebhook` with `secret_token` and
   callback/message updates using deployment secret tooling. Outbound delivery
   configuration does not prove inbound webhook registration.
7. Human readiness review is mandatory. Keep `NIFTY_LIVE_ENABLED=false`,
   `PRACTICE_MODE=true`, `AUTO_TRADING_ENABLED=false` until blockers are fixed.
   Even deliberately changing all three cannot activate this release's adapter.

### Engine safety policy and operator handling

The isolated route admits only canonical NIFTY INDEX listed NSE_FNO OPTIDX
ITM+1 CE/PE long BUY contracts, revalidated against the master at proposal and
write boundaries. Size is exactly one lot using current lot-size metadata; no
multi-lot control is exposed. Entry prefers LIMIT; a MARKET fallback may only
follow a broker-confirmed non-submission/rejection. An uncertain result is
reconciled, never blindly resent. The intended Dhan carry-forward product enum
is `MARGIN`; verify the Super Order endpoint's accepted product/order enums from
current official Dhan documentation before connecting an adapter.

Two first-filled logical entries per IST day is the cap, independent of
win/loss and lots. Partial fills count once; pending/unknown intents reserve
capacity and cash. Reservations survive restart and day rollover until verified
zero-fill cancellation/rejection. One open/reserved managed position is a required safety limit.
No live daily profit/loss threshold is introduced; paper thresholds are unchanged.
Entry kill switches/caps must not disable exit reconciliation or protection.
Auto mode has no approval cards or manual control callbacks. In this mode the
trade-control bot is reserved for two lifecycle message types only: confirmed
entry fills (including capital committed and available-funds evidence) and
completed exits (realized P&L). All other option signals stay on the service
alerts bot and non-NIFTY signals never enter real or paper submission paths.

Manage the actual account in Dhan. On unknown exposure, outages or unexpected
manual changes, disable **new entries**, inspect actual positions, open orders and
trades in Dhan, and handle emergency risk there. Reconcile the durable ledger
before resuming; do not repeatedly press approval or infer closure from an alert.
Direct Dhan changes cannot be promised error-free or instant synchronization.
This blocked release does not provide a production auto-entry, managed exit, or
emergency-exit service.

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
