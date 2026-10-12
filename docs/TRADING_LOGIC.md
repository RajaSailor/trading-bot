# Trading Logic Guide

## Strategy Pipeline
1. Load completed 10-minute premium history for listed ITM+1 CE/PE contracts.
2. Trigger on the first observed fresh live crossing from premium ≤ reference high to premium > reference high, using the most recent RED candle within the last 20 completed 10-minute candles.
3. Validate NIFTY auto-entry risk rules (fresh funds/margin, one metadata lot, two filled entries per IST day, one open position, and persisted deduplication).
4. Only NIFTY index options may be considered by the real auto route. Stock, commodity, and other-index options remain category-channel signals and never fall back to real execution.

## Signal Processing
- The premium screener supports index, commodity and stock options, using only listed ITM+1 CE/PE contracts with expiry strictly after today.
- History loading is silent. Completed highs/closes never generate service alerts or seed a monitored crossing. Startup above the reference does not trigger: a fresh observation at/below the high must precede an observation strictly above it. No candle close, second above-high observation, or continuation confirmation is required.
- EMA9, MACD, RSI and volume have no role in premium alert eligibility or presentation. Legacy range/sideways gates remain removed.
- The deterministic anti-chop key is `index_options:NIFTY:CE|PE`, shared across strikes/expiries and persisted with live intents. After an acted/filled trigger at P, the inclusive band [0.95P, 1.05P] is blocked; only a new crossing strictly outside it qualifies.
- `PREMIUM_ALERT_STATE_FILE` retains legacy anti-chop and per-reference dedup state. In the deployed lifecycle path, a successfully acted trigger means the **atomic durable signal opening and outbox acceptance**, not Telegram delivery or a broker fill. This deliberately supersedes the old delivery-success-only policy: a transport retry must not open another CE/PE signal while the first is unresolved. Queue acceptance alone never advances this state. Mount persistent storage and run one scanner process.
- Live breakout evidence must be at most 10 seconds old. A separate last-trade timestamp, when present, must be fresh; a fresh executable book timestamp cannot disguise stale LTP.
- The compact signal alert has no EMA/MACD/RSI or volume lines. The fixed stop is `0.95 × red-candle low` (rounded upward to the contract tick); after the actual fill E, R is `E − SL` and the full target is `E + 2R`. No trailing or breakeven adjustment is enabled.
- Real auto sizing is exactly one lot from contract metadata and uses the carry-forward `MARGIN` product enum. Insufficient or unverifiable funds/margin, stale evidence, an occupied position, or the two-filled-entry IST-day cap blocks entry.
- Internal payload/logging retains `reference_timestamp`, `reference_high`, `first_crossing_timestamp`, `breakout_price`, `detected_at`, and `sent_at`. Delivery retry keeps the original crossing price/time and detection deadline, never relabels a later above-high quote, and expires after 60 seconds. Retries require a fresh current quote and an open session. Transport uncertainty after Telegram accepts a message can still cause an at-least-once delivery; this is not a new strategy trigger.
- Production execution remains fail-closed: there is no verified Dhan Super Order adapter, funds/margin wire parser, authoritative account reconciler, or broker-managed protection worker in this checkout. Runtime mode is automatic only, denies approval updates, and creates no approval cards or generic-order substitute. The old approval library is retained only for fake-adapter simulation tests. Dhan carry-forward semantics, static egress IP, and exit behavior must be independently verified before a real deployment.
- The official [Super Order API](https://dhanhq.co/docs/v2/super-order/) and [Dhan SDK implementation](https://github.com/dhan-oss/DhanHQ-py/blob/main/src/dhanhq/_super_order.py) expose entry `orderType` and `stopLossPrice`, but no independent stop-leg LIMIT price/type. The precise production blocker is: **Super Order stop leg has no independently configurable LIMIT price/type; LIMIT entry does not verify LIMIT stop execution**. No alternative OCO/protection route has been verified safe with no hidden market execution, so there is no substitute route.
- `live_route.status()` exposes sanitized actual execution account/intent status, LIMIT-only policy, disabled trailing, and production blockers; it does not expose Telegram secrets, credentials, or arbitrary adapter/proposal data.

## Risk Management Rules
- One NIFTY lot only; fresh funds/margin checks precede any entry, with a maximum of two filled entries per IST day and one managed open position.
- LIMIT is mandatory for every leg: entry, target, stop, manual exit, and time exit. No MARKET fallback is allowed, even after rejection or confirmed non-submission. An ambiguous write is reconciled and never blindly resent. A LIMIT order may remain unfilled; neither fills nor protective execution are guaranteed.
- Trade Control messages are restricted to confirmed real entry fills (including available balance), completed exits (actual fills and realized P&L), and deduplicated insufficient-balance alerts. Signals and daily reports use only their respective category channels. Paper output and routine diagnostics use logs.

## Advisory signal lifecycle and daily reports

- There is one SQLite-enforced OPEN signal per underlying, across CE/PE,
  strikes and expiries. Different underlyings are independent. The selected
  contract, entry, stop and **one 2R target** remain frozen until a fresh observed
  target/stop outcome or session square-off. ITM+1 reselection cannot replace an open signal.
- Telegram text includes contract, CE/PE, strike, expiry, entry, stop, target,
  risk points, lot size and observed premium. Signal/event IDs remain internal
  in SQLite/outbox deduplication, not in Telegram text. For example:

  ```text
  POSITION OPEN TRIGGER
  NIFTY | NIFTY-Oct2026-22550-PE
  PE | Strike 22550 | Expiry 2026-10-13
  Entry trigger: 134.95 | Stop: 93.45 | Target: 217.95
  Risk points: 41.50 | Lot size: 75
  Observed breakout premium: 134.95
  ```

  `TARGET ACHIEVED`, `STOP LOSS TRIGGERED` and `SESSION CLOSE EXIT` also show
  exit premium and, for example, `P&L (1 lot x 75): +₹6,225.00`.
  OPEN delivery persists Telegram's message ID; exits reply to it in the same
  category channel, with `allow_sending_without_reply=true` if the original was
  deleted. No signal or report is sent to Trade Control.
- `SIGNAL_DB_PATH` stores signals, underlying locks, anti-chop entries, terminal
  events, report cursors and a durable outbox. Opening and terminal notifications
  are atomically enqueued with state transitions. Telegram delivery is
  at-least-once: ambiguous sends may repeat a notification, not a signal opening.
  Retries preserve event ordering within each category without blocking healthy
  categories. Exits wait for OPEN acknowledgment and its message ID. After
  `SIGNAL_NOTIFICATION_MAX_ATTEMPTS` (default `25`), a permanently failed OPEN
  releases the delivery dependency, allowing an unthreaded exit. Failed events
  remain in the outbox with `delivered=-1`. Large reports use bounded parts with
  internal stable IDs.
- At `SIGNAL_SQUAREOFF_TIME_NSE` (default `15:25` IST), still-OPEN NSE/BSE signals
  close as `SESSION_CLOSE` and release their underlying locks. Optional
  `SIGNAL_SQUAREOFF_TIME_BSE` overrides BSE. MCX defaults to the existing calendar's
  session close minus five minutes (DST and half-day aware); optional
  `SIGNAL_SQUAREOFF_TIME_MCX=HH:MM` overrides it. Configured times are clamped to
  the session window. No new signals open at/after square-off.
- Square-off uses the last fresh valid observed premium, fetching frozen contracts
  in one batched request through the existing shared quote pacing and bounded
  HTTP 429 backoff. If unavailable, use the persisted last valid premium and mark
  `(last quote HH:MM)`. The validated opening observation is also a valid fallback.
  Legacy signals without any persisted valid premium show `NO QUOTE`, count as
  closed, but are excluded from rupee totals. Restart closes previous-day OPEN
  signals using their last known premium and original day's square-off time;
  persistent terminal state prevents duplicate closure. No broker order is placed.
- Category reports run after that category's square-off completes:
  index NSE/BSE, stocks NSE, commodities MCX.
  Calendar holidays/weekends are skipped and report cursors survive restarts.
  Reported openings belong to their opening IST day; target/stop outcomes belong
  to their observation IST day, and recovered session exits to their original day.
- Hypothetical long-option P&L is `(exit premium − entry trigger) × lot size`,
  using the validated security-master lot size frozen at opening. By default,
  target/stop exits use the defined target/stop level, not a jumping observed
  premium; the observed premium is still displayed. Set
  `SIGNAL_PNL_USE_OBSERVED_PREMIUM=true` to use observed exit premiums instead
  (default `false`). Each exit persists the choice so later env changes cannot
  change its report. Missing lot sizes are flagged and excluded from rupee totals.
- Per-underlying rows and totals show opened, targets, stops, session-close exits
  (profit/loss counts), win rate and net rupee P&L. Win rate is
  `(targets + profitable session exits) / all closed signals × 100`, or `N/A`
  with no closed signals; flat/no-quote session exits are not wins. Totals also
  show gross profit, gross loss (positive magnitude), average P&L per included
  closed signal, max single loss and max single win. One daily-report footer reads:
  "Hypothetical signal results (1 lot per signal), not broker fills."
  These are not realized broker results or guaranteed performance.
- No pullback/rebound, five-minute closing confirmation, five-point entry
  buffer, extra indicators, second target, trailing or breakeven is used.

## Order Execution Flow
1. Validate the NIFTY ITM+1 contract and its lot/tick metadata.
2. Recheck fresh quote, funds, margin, daily cap, one-position guard, and deterministic re-entry band.
3. Use Dhan Super Order entry/target/stop management only after its API capabilities are verified; reconcile actual fills, not acknowledgments.
4. Persist entry/exit lifecycle evidence and realized P&L. Missing broker protection or uncertain outcomes fail closed.

> **Risk disclaimer:** Options trading can result in rapid, substantial loss, including loss of the full premium. A 1:2 planned reward/risk is not a win-rate, fill, or profit guarantee. Do not trade funds you cannot afford to lose.
