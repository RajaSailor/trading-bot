# Options Scanner Runbook (Render 512 MB)

This runbook covers the premium breakout scanner that sends option alerts to the
`SERVICE_ALERTS` Telegram channel.

## Strategy rules (as implemented)

| Rule | Implementation |
|---|---|
| Expiry | Nearest listed expiry **strictly after today**. On expiry day the same-day contract is never used; the next expiry is used from the start of the day. |
| Strikes | Entry scanning, alerts, and approval requests use only the **listed ITM+1 CE and PE** contracts. CE ITM is the next lower listed strike; PE ITM is the next higher listed strike. There is no ATM/OTM or synthetic fallback; a missing ITM+1 contract is skipped before option-candle/LTP requests. Existing paper positions and pending orders in any band remain independently quoted and protected by the paper portfolio worker. |
| Reference candle | Most recent **RED** 10-minute candle (`close < open`; dojis are *not* red) within the last **20** ten-minute candles. Previous-session candles are fetched so the window is full from the open. |
| Confirmations | None: no EMA9, MACD, RSI, volume, legacy sideways or continuation gate. |
| Trigger | Previous observed fresh premium ≤ reference high, current observed fresh premium > reference high. Equality never triggers. Startup already above the high needs a later observed crossing. No second above-high tick or candle-close wait. |
| Re-entry | After successful alert at observed P, block [P×0.95, P×1.05] inclusive, keyed by `category:underlying:CE|PE` across strikes/expiries. Only a new crossing outside the band qualifies. P=100 blocks 95–105; 94.99 and 105.01 qualify. |
| Entry | Red candle high |
| Stop-loss | Red candle low × 0.95 (5 % below the low) |
| Risk points | Entry − Stop-loss |
| Target | Entry + 2 × Risk (single 2R target) |
| Duplicates | One alert per option contract per reference red candle. |

### Crossing and timing evidence

- No indicator warmup is required. Valid completed OHLC determines the reference;
  fresh observed quotes establish the crossing, never historical highs/closes.
- Same-reference candle refresh preserves the live baseline. A different reference
  or contract starts without crossing evidence. Out-of-order, stale, invalid and
  future quotes cannot establish the baseline or trigger.
- Internal timing records retain reference time/high, first-crossing time/price,
  detection time and successful send time. A failed send retries the original
  evidence for less than 60 seconds, with fresh current quotes and an open session.
  Queue acceptance is not successful Telegram delivery.
- Rejected candidates are counted in `/health` → scanner `indicator_rejections`
  (e.g. `reentry_band`, `stale_quote`, `insufficient_history`); they are not
  sent to Telegram. History loading is silent, including at startup.
- ITM+1 is selected before batched candle and live-quote requests; the existing
  shared pacing/cache/429 cooldown remains in use. Signals do not guarantee returns.
- Persist `PREMIUM_ALERT_STATE_FILE` (default `premium_alert_state.json` beside
  `STATE_FILE`) on a mounted volume. It retains the last successfully alerted price
  per category/underlying/side and emitted reference keys across restarts, without
  daily reset. Use one scanner process; do not delete it to bypass re-entry limits.

## Memory / stability design

- The Dhan compact security master is **streamed** (`security_master.py`), only the
  needed columns are parsed and only universe contracts for the nearest valid
  expiry are kept (~6–7k contracts instead of ~200k rows). No pandas DataFrame is built.
- The filtered index is loaded **once per IST day**, shared process-wide
  (`get_shared_security_master()` / `get_shared_data_manager()`), and released
  when every exchange is closed.
- `initialize_app()` is idempotent, so the scanner and caches are created once.
- RSS is logged after security-master load/release and when the scanner starts
  (`🧠 Memory [...]: RSS=... MB` log lines). `/health` → `workers.market_scanner` also reports RSS.

## Market session / holiday gating

| Underlyings | Calendar | Session (IST) |
|---|---|---|
| NIFTY, BANKNIFTY, NIFTY 50 stocks | NSE | 09:15–15:30 |
| SENSEX | BSE | 09:15–15:30 |
| GOLD, SILVER, CRUDE OIL, NATURALGAS | MCX | 09:00–23:30 (23:55 during US winter time) |

Weekends and the 2026 exchange holidays (`exchange_calendar.py`) are skipped.
When all exchanges are closed the scanner idles (no Dhan calls) and only the
Flask app, including `/health`, keeps running.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `PRACTICE_MODE` | `true` | Persistent simulated portfolio, no real orders. Main enforces practice mode. |
| `AUTO_TRADING_ENABLED` | `false` | Conscious isolated live guard; paper remains simulated and production is blocked. Keep `false`. |
| `NIFTY_LIVE_ENABLED` | `false` | Separate opt-in NIFTY route, no duplicate paper approval when configured. Production adapter remains blocked; see deployment guide. |
| `ENABLE_MARKET_SCANNER` | `false` | Set `true` to run the scanner (also needs `ACCESS_TOKEN`). |
| `ACCESS_TOKEN` | – | Dhan access token (candles, LTP). |
| `DHAN_CLIENT_ID` (or `API_KEY`) | – | Dhan client id, required by the LTP endpoint used for live triggers. |
| `CHANNEL_SERVICE_ALERTS_ID`, `BOT_SERVICE_ALERTS_TOKEN` | – | Telegram alert channel / bot. |
| `SCANNER_IDLE_SECONDS` | `300` | Max sleep while all exchanges are closed. |
| `LIVE_TRIGGER_ENABLED` | `true` | Live LTP breakout checks between candles. |
| `LIVE_TRIGGER_POLL_SECONDS` | `1` | Local live-check cadence; shared cache/pacing/backoff controls HTTP requests, not this counter alone. |
| `SCANNER_REFRESH_RETRY_SECONDS` | `120` | Retry delay after a failed candle refresh. |
| `PREMIUM_ALERT_STATE_FILE` | Beside `STATE_FILE`: `premium_alert_state.json` | Persistent alert re-entry/dedup state; single scanner process. |
| `EXCHANGE_EXTRA_HOLIDAYS` | – | Extra closures, e.g. `ALL:2027-01-26,MCX:2026-12-31`; MCX half days via `MCX_MORNING:<date>` / `MCX_EVENING:<date>`. Required for 2027+ until the calendar is updated. |
| `DHAN_SECURITY_MASTER_URL` | Dhan compact CSV | Override the security master source. |

## Running in PRACTICE_MODE

1. In Render → Environment set `PRACTICE_MODE=true`, `AUTO_TRADING_ENABLED=false`,
   `ENABLE_MARKET_SCANNER=true`, a fresh `ACCESS_TOKEN` and `DHAN_CLIENT_ID`.
2. Start command: `python main.py`.
3. Check `GET /health`: `workers.market_scanner.running` is `true`; outside
   market hours the logs show `😴 All exchanges closed ...; scanner idle. Next session: ...`.

## Validating alerts before go-live

1. Run `pytest` locally – all tests must pass.
2. During market hours, confirm in the logs:
   - `🧠 Memory [after security master load]: RSS=...` stays well below 512 MB,
   - `🎯 [SYMBOL] CE band | ... | ITM+1=..` per underlying; no ATM/OTM entry
     contracts should be fetched,
   - on expiry day the `expiry=` shown is the **next** expiry.
3. For each Telegram alert verify: underlying/category, option symbol, strike,
   CE/PE, BUY CALL/BUY PUT, expiry, ITM+1 band, entry, stop-loss, risk, 2R target,
   observed breakout price and IST quote time, underlying spot and practice mode.
   No indicators, volume, reference or analysis sections appear in service alerts.
   Cross-check entry/SL/target against the red candle on the Dhan chart.
4. Latency: `⏱️ Latency <stage> (n=..): p50/p95/p99` log lines report internal timings
   from price receipt to evaluate, queue and Telegram send start.
5. Verify the paper approval pipeline below. Do not enable live trading as a
   workaround for missing approvals.

## Persistent paper approval pipeline

Service alerts describe a **strategy observation**, not an accepted entry or fill.
The independent flow is scanner → queue → fresh shared contract quotes →
persistent portfolio validation → durable outbox → trade-control Telegram receipt.
Both BUY CALL (CE) and BUY PUT (PE) are **long BUY** option entries. Explicit
SELL/SHORT/EXIT intents are never rewritten into BUY. Position exits use the
existing authorized close/position controls.

Execution metadata uses `10min`; `10-MINUTE BREAKOUT` is presentation only.
Reference and breakout timestamps remain candle/trigger evidence. `detected_at`
is the first actual evaluation time, `queue_received_at` is queue receipt, and
`created_at` is portfolio submission. The approval deadline is at most 60 seconds
after original detection by default—not 60 seconds after the candle's start or
the worker's next retry. Submit, approval and simulated fill enforce it.
`/pending`, duplicate requests, restart and retries do not extend it.

Only actual live quotes at most 10 seconds old at detection are eligible.
When quotes expose a separate last-trade timestamp, that timestamp must be fresh;
a fresh depth/receipt timestamp cannot turn stale option LTP into breakout evidence.
Startup/history refresh does not replay completed breakouts; previous-session
RED references may still arm a **new live** observation.
Do not increase freshness or substitute historical breakout prices for execution
quotes to force approvals.
Execution needs a fresh shared quote (default 10 seconds), exact listed security
ID/segment, positive metadata lot size and INR tick size. One real metadata lot,
max 5 occupied/reserved slots, max 20 daily entries, cash/risk limits, exchange
cutoffs and unresolved-exit blocks still apply.

### Deployment verification checklist (operator, not automatically performed)

1. Compare Render's deployed Git SHA with the **merged fix commit**. `/health`
   exposes `revision` from `RENDER_GIT_COMMIT` or `GITHUB_SHA` when available;
   `version: 1.0.0` alone does not identify deployed code. An absent revision
   requires checking the deployment dashboard, not assuming the fix is live.
2. Keep practice mode ON and auto/live trading OFF. Verify persistent
   `TRADING_DB_PATH` storage and a single shared quote process. Do not delete the
   database to clear pending requests: accounting, deadlines, rejection dedup and
   control/update idempotency are stored there.
3. Inspect `/health`:
   - `workers.queue_consumer.queue_size`, `outcomes`, `last_outcome`, `last_reason`;
     `processed_signals` means consumed business requests, **not fills**.
     Queue acceptance means queued, not portfolio approval.
   - `paper_submissions`: account `enabled`, `approval_required`, durable
     submission counts and rejection reasons. Both account switches should be
     true for an enabled approval-gated portfolio.
   - `workers.paper_portfolio.running` and `delivery_running`: protection and
     outbound delivery run independently.
   - `paper_outbox`: notifier configured, pending backlog, oldest age, failed
     attempts and latest sanitized failure. Health reads do not send or freshen
     requests.
4. During market hours, an eligible ITM+1 CE **and** PE fresh crossing outside the
   successful-entry re-entry band should produce
   `#PAPER #APPROVAL`, exact contract/one-lot units, and **Approve Limit / Approve
   Market / Modify / Reject** buttons in trade_control, with no position or
   simulated execution beforehand. Verify `/orders` and `/positions`, then an
   authorized approval with fresh quotes should produce only a PAPER fill.
   Limit approval may remain pending until a fresh executable quote reaches
   its limit; approval is not a fill guarantee.
5. Rejected requests produce `#PAPER #REJECTED`, original signal key, known exact
   contract and a fixed reason, not a fabricated trade/fill ID. Re-submit of the
   same key does not create another business rejection or approval. Examples:
   `missing_lot_size`, `missing_tick_size`, `missing_security_id`,
   `invalid_metadata`, `invalid_intent`, `invalid_side`, `invalid_timeframe`, `invalid_timestamp`,
   `invalid_expiry`, `stale_signal`, `no_price`, plus existing cash/risk/cutoff
   reasons. Correct upstream metadata/quote availability for **new** signals;
   rejected or expired evidence must not be made fresh.
6. Delivery is at-least-once: failed events remain durable and are retried in
   bounded, fair batches, without blocking subsequent receipts. A transport
   timeout after Telegram accepted a send can produce a duplicate receipt;
   persisted request/update/callback idempotency prevents duplicate execution.
   A late-delivered keyboard cannot revive an expired request. Database failures
   are worker failures/retries, not silently recorded as business rejections.

### Outbound Telegram versus inbound controls

- `/health.paper_telegram.outbound_ready` checks local token/chat routing
  configuration only, **not** Telegram permission or network reachability.
  Verify trade-control bot membership/post permission in the intended chat.
  `last_api_status`/outbox diagnostics report safe reason codes and numeric
  HTTP/API errors: 401 suggests invalid outbound bot credentials, 403 bot/chat
  permissions, 429 rate limiting; `transport_error` indicates network/timeout.
  No token-bearing URL or Telegram error description is exposed.
- Inbound callbacks separately require `PAPER_TELEGRAM_ALLOWED_USER_IDS`
  (positive numeric IDs), explicit `CHANNEL_TRADE_CONTROL_ID`, and
  `PAPER_TELEGRAM_WEBHOOK_SECRET` (at least 32 characters). Missing configuration
  is diagnosed and remains deny-default. `inbound_ready` is configuration
  readiness, not proof that Telegram has registered or can reach the webhook.
  Operators must separately verify their existing webhook points to
  `/telegram/paper` with the matching secret header. This fix does **not**
  register/change webhooks, request pasted credentials, or change live flags.
- A successfully delivered approval receipt includes its keyboard even if
  inbound webhook/allowlist configuration is absent. Webhook configuration
  affects clicks reaching authorized callbacks, not keyboard creation.
- If trade_control is a **Telegram channel**, use its inline callback buttons:
  callbacks carry the clicking numeric user identity and the configured channel
  chat. Channel-post slash commands are **not supported** by the current
  message-only identity validation. `/portfolio`, `/pending`, `/positions`,
  `/orders` and authorized configuration commands work in a configured
  trade-control **group** with an allowlisted human sender, not anonymous
  channel posts. Do not weaken actor/chat/secret checks to enable channel posts.

Code/fake-data tests do not validate Render's deployed SHA, persistent disk,
Telegram membership, webhook registration, or live market-data availability.
Complete this checklist after an operator-controlled merge/deployment; this
change does not merge or deploy itself.

## Known limitations

- Latency is measured inside the bot only (until the Telegram request starts).
  Delivery to the phone depends on Telegram and the network; **50 ms end-to-end
  delivery cannot be guaranteed**. Live triggers are polled once per second
  (Dhan LTP rate limit), not tick-by-tick.
- Holiday lists cover 2026; later years need `EXCHANGE_EXTRA_HOLIDAYS` or a code update.
  Special sessions (e.g. Muhurat trading) are not handled.
- Each 10-minute refresh fetches candles for all monitored contracts at Dhan's
  historical rate limit (1 call/s), so a full refresh of all underlyings takes
  several minutes; the live LTP trigger covers breakouts in between.
- Symbols with no listed options in the security master (e.g. renamed stocks) are skipped.
