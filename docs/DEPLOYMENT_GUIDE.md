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

## Automatic NIFTY-only Super Order runbook

Real trading is default OFF. Keep these defaults until an operator has verified
the actual Dhan account, instrument/product eligibility and deployment:

```text
NIFTY_LIVE_ENABLED=false
PRACTICE_MODE=true
AUTO_TRADING_ENABLED=false
LIVE_DB_PATH=/persistent/live_trading.db
PAPER_DB_PATH=/persistent/paper_trading.db
```

Activation requires `NIFTY_LIVE_ENABLED=true`, `PRACTICE_MODE=false` and
`AUTO_TRADING_ENABLED=true` together, plus all adapter capability gates.
Use Dhan credentials only through deployment secrets. Confirm valid trading and
market-data permissions, token validity, Super Order availability for NIFTY
options and the supported carry-forward enum before any real-money rollout.
Static outbound IP whitelisting is required for broker writes; whitelist actual
egress, not the service's inbound IP. Auth/IP/permission errors fail closed.

Mount separate persistent live/paper ledgers and scanner state, back them up,
and run one process per account (`gunicorn --workers 1`, no replicas/preload).
On restart, reconciliation must precede new exposure. Never delete the ledger to
clear an unknown order, reservation, daily cap or halt.

Auto-live accepts only NIFTY INDEX NSE_FNO OPTIDX ITM+1 CE/PE long entries, one
lot from current security-master metadata. Other categories are signal-only.
Funds and required margin are read before each entry; insufficient, invalid,
stale or unavailable evidence skips entry. ₹25,000 does not guarantee sufficiency.
Two first-filled logical entries per IST day (partial fills count once) and one
open/reserved position are the maximum.

Entry is preferably LIMIT with broker-managed target/stop via Super Order.
MARKET fallback is bounded to authoritative zero-fill rejection, after
reconciliation; timeouts/unknown acknowledgments never justify blind resend.
MARKET may be protected/converted by Dhan and may remain unfilled.
SL is tick-rounded `0.95 × red low`; target after fill E is `E + 2 × (E − SL)`.
First +5 favorable points moves SL to entry; each subsequent +5 raises it 5
points, monotonically. Super Order stop/target semantics and modification
constraints are broker-controlled, not an exchange-side or guaranteed-fill claim.

Configure `BOT_SERVICE_ALERTS_TOKEN` / `CHANNEL_SERVICE_ALERTS_ID` for IITS
Scalping Options signals and `BOT_TRADE_CONTROL_TOKEN` /
`CHANNEL_TRADE_CONTROL_ID` for real NIFTY entry/exit messages only. No approval
webhook is required for auto entry. Paper controls/workers and generic Trade
Control notifications are suppressed in auto-live mode. Diagnostics go to logs.
Delivery uncertainty can cause duplicate Telegram delivery; four messages for
two completed trades is the intended lifecycle set, not a transport guarantee.

On API outage, unknown exposure, unexpected position changes or failed protection
modification, stop new entries, inspect positions/orders/trades directly in Dhan
and manage emergency risk there. Continue reconciliation; do not assume an alert
or acknowledgment proves entry, exit or protection. No automatic execution or
backtest guarantees profit; **options trading can lose your capital**.

## Legacy isolated simulation route: production BLOCKED

The following describes the retained approval simulation only, not the automatic
Super Order route above. Its controls and blockers cannot authorize production
orders and are kept for compatibility and paper/simulation testing.

This change is substantive but incomplete for production. The isolated engine
and Telegram controls are exercised with injected **mock/simulation adapters
only**. No real order adapter is connected to the runtime. Flags cannot bypass
this barrier; `/health` distinguishes paper, configured, and blocked states.
Never interpret an outbound alert or broker acknowledgment as a real fill.

Implemented in isolated mocked tests: master/ITM+1 validation, 1..5 lot proposals,
version/actor/chat-bound approval and limit/market previews, durable intents and
reservations, acknowledgment-versus-fill separation, monotonic normalized
snapshot reconciliation, conservative exit quantity checks and notification
outbox retries. `/telegram/live` is an authenticated separate controller;
configured production signals are blocked without paper fallback.

Missing runtime capabilities: verified Dhan HTTP placement/read adapters and
wire schemas, paced production funds/margin cache, production live-card delivery
worker, live broker-update ingress, reconciliation/protection scheduler, existing
broker order modification/attributable protection cancellation, full live
SL/target/trailing position controls, cutoff/emergency exit automation and a
production readiness attestation. Mocked protection previews are not automatic
real protection; card delivery is not connected while production is blocked.
These are implementation gaps, not merely deployment environment settings.

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
3. Complete and verify fill-based partial-fill protection, entry-remainder
   cancellation before exits, monotonic completed-option-candle trailing,
   T1 tightening, T2 full exit and session cutoffs. Client-side protection cannot
   protect during outages; this is a readiness gap, not an exchange-side stop.
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
ITM+1 CE/PE long BUY contracts, revalidated against the master at proposal,
approval and write boundaries. Proposal lots default to one; +1/-1 controls
and edits apply only before submission, integer 1..5, with dynamic lot/tick
metadata. Modification invalidates older approval versions. MARKET is not a
synthetic fill and LIMIT never silently converts after five seconds.

Two first-filled logical entries per IST day is the cap, independent of
win/loss and lots. Partial fills count once; pending/unknown intents reserve
capacity and cash. Reservations survive restart and day rollover until verified
zero-fill cancellation/rejection. One open/reserved managed position is a
conservative implementation policy, **not a user-confirmed quantity limit**.
No live daily profit/loss threshold is introduced; paper thresholds are unchanged.
Entry kill switches/caps must not disable exit reconciliation or protection.
Approval cards distinguish estimated funds from actual broker evidence and show
balance as-of or explicitly unavailable. No funds or new deposits automatically
replay rejected/expired proposals.

Manage the actual account in Dhan. On unknown exposure, outages or unexpected
manual changes, disable **new entries**, inspect actual positions, open orders and
trades in Dhan, and handle emergency risk there. Reconcile the durable ledger
before resuming; do not repeatedly press approval or infer closure from an alert.
Direct Dhan changes cannot be promised error-free or instant synchronization.
This blocked release does not provide a production emergency-exit service.

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
