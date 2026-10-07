# Trading Logic Guide

## Strategy Pipeline
1. Load completed 10-minute premium history for listed ITM+1 CE/PE contracts.
2. Trigger on the first observed fresh live crossing from premium ≤ reference high to premium > reference high, using the most recent RED candle within the last 20 completed 10-minute candles.
3. Validate NIFTY auto-entry risk rules (fresh funds/margin, one metadata lot, two filled entries per IST day, one open position, and persisted deduplication).
4. Only NIFTY index options may be considered by the real auto route. Stock, commodity, and other-index options remain service-channel signals and never fall back to real execution.

## Signal Processing
- The premium screener supports index, commodity and stock options, using only listed ITM+1 CE/PE contracts with expiry strictly after today.
- History loading is silent. Completed highs/closes never generate service alerts or seed a monitored crossing. Startup above the reference does not trigger: a fresh observation at/below the high must precede an observation strictly above it. No candle close, second above-high observation, or continuation confirmation is required.
- EMA9, MACD, RSI and volume have no role in premium alert eligibility or presentation. Legacy range/sideways gates remain removed.
- The deterministic anti-chop key is `index_options:NIFTY:CE|PE`, shared across strikes/expiries and persisted with live intents. After an acted/filled trigger at P, the inclusive band [0.95P, 1.05P] is blocked; only a new crossing strictly outside it qualifies.
- `PREMIUM_ALERT_STATE_FILE` stores successful trigger prices and per-reference dedup keys using atomic StateManager saves. It defaults to `premium_alert_state.json` beside `STATE_FILE`; mount persistent storage and run a single scanner process. Queue acceptance alone or failed delivery does not advance the last successful price.
- Live breakout evidence must be at most 10 seconds old. A separate last-trade timestamp, when present, must be fresh; a fresh executable book timestamp cannot disguise stale LTP.
- The compact signal alert has no EMA/MACD/RSI or volume lines. The initial stop is `0.95 × red-candle low` (rounded upward to the contract tick); after the actual fill E, R is `E − SL` and the full target is `E + 2R`. At each +5 premium points, move SL to breakeven on the first step, then raise it by 5 points per additional step. Stops are monotonic and tick-aligned.
- Real auto sizing is exactly one lot from contract metadata and uses the carry-forward `MARGIN` product enum. Insufficient or unverifiable funds/margin, stale evidence, an occupied position, or the two-filled-entry IST-day cap blocks entry.
- Internal payload/logging retains `reference_timestamp`, `reference_high`, `first_crossing_timestamp`, `breakout_price`, `detected_at`, and `sent_at`. Delivery retry keeps the original crossing price/time and detection deadline, never relabels a later above-high quote, and expires after 60 seconds. Retries require a fresh current quote and an open session. Transport uncertainty after Telegram accepts a message can still cause an at-least-once delivery; this is not a new strategy trigger.
- Production execution remains fail-closed: there is no verified Dhan Super Order adapter, funds/margin wire parser, authoritative account reconciler, or broker-managed protection worker in this checkout. Auto mode creates no approval cards and does not submit a generic-order substitute. Dhan Super Order support, carry-forward semantics, static egress IP, and exit behavior must be independently verified before a real deployment.

## Risk Management Rules
- One NIFTY lot only; fresh funds/margin checks precede any entry, with a maximum of two filled entries per IST day and one managed open position.
- LIMIT is preferred. MARKET fallback is permitted only after a broker-confirmed non-submission/rejection; an ambiguous write is reconciled and never blindly resent. A market order is not an execution guarantee.
- Trade-control auto-mode messages are restricted to confirmed entry fills (capital details) and completed exits (realized P&L). Signals remain in the options-screener channel.

## Order Execution Flow
1. Validate the NIFTY ITM+1 contract and its lot/tick metadata.
2. Recheck fresh quote, funds, margin, daily cap, one-position guard, and deterministic re-entry band.
3. Use Dhan Super Order entry/target/stop management only after its API capabilities are verified; reconcile actual fills, not acknowledgments.
4. Persist entry/exit lifecycle evidence and realized P&L. Missing broker protection or uncertain outcomes fail closed.

> **Risk disclaimer:** Options trading can result in rapid, substantial loss, including loss of the full premium. A 1:2 planned reward/risk and step trailing are not a win-rate, fill, or profit guarantee. Do not trade funds you cannot afford to lose.
