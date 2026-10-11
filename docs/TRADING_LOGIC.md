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
  target/stop outcome. ITM+1 reselection cannot replace an open signal.
- `POSITION OPEN TRIGGER` includes signal/event identity, contract, levels and
  timestamp. Its entry is a tick-valid advisory level derived from the observed
  crossing premium, **not a broker fill**. Terminal `TARGET ACHIEVED` or
  `STOP LOSS TRIGGERED` records observed premium, including jumps; it never
  invents a fill at the crossed level. Missing/stale/out-of-order quotes cannot
  close a signal.
- `SIGNAL_DB_PATH` stores signals, underlying locks, anti-chop entries, terminal
  events, report cursors and a durable outbox. Opening and terminal notifications
  are atomically enqueued with state transitions. Telegram delivery is
  at-least-once: ambiguous sends may repeat an event ID, not a signal opening.
  Retries preserve event ordering within each category without blocking healthy
  categories. Large reports use bounded, stable-ID parts.
- Category reports run after the last applicable calendar session close:
  index NSE/BSE, stocks NSE, commodities MCX (DST and half-day aware).
  Calendar holidays/weekends are skipped and report cursors survive restarts.
  Reported openings belong to their opening IST day; target/stop outcomes belong
  to their observation IST day, including older signals closed today.
- Unresolved counts are as of that session close and include earlier-day
  openings. **No expiry/overnight auto-loss or unlock policy exists**; expired
  contracts with no valid outcome remain unresolved and need operational review.
- Per-underlying rows and consolidated totals show opened, target, stop and
  unresolved. Closed-signal win rate is `target/(target+stop)*100`, or `N/A`
  without closed outcomes. It is not realized rupee P&L, guaranteed performance
  or evidence that a subscription upgrade improves results.
- No pullback/rebound, five-minute closing confirmation, five-point entry
  buffer, extra indicators, second target, trailing or breakeven is used.

## Order Execution Flow
1. Validate the NIFTY ITM+1 contract and its lot/tick metadata.
2. Recheck fresh quote, funds, margin, daily cap, one-position guard, and deterministic re-entry band.
3. Use Dhan Super Order entry/target/stop management only after its API capabilities are verified; reconcile actual fills, not acknowledgments.
4. Persist entry/exit lifecycle evidence and realized P&L. Missing broker protection or uncertain outcomes fail closed.

> **Risk disclaimer:** Options trading can result in rapid, substantial loss, including loss of the full premium. A 1:2 planned reward/risk is not a win-rate, fill, or profit guarantee. Do not trade funds you cannot afford to lose.
