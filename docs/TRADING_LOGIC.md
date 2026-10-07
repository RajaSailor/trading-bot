# Trading Logic Guide

## Strategy Pipeline
1. Load completed 10-minute premium history for listed ITM+1 CE/PE contracts.
2. Trigger on the first observed fresh live crossing from premium ≤ reference high to premium > reference high, using the most recent RED candle within the last 20 completed 10-minute candles.
3. Validate risk rules (capital, limits, duplicate control).
4. Route eligible signals through the mode-specific approval/execution workflow and publish Telegram updates. A service alert is an observation, not an order or fill.

## Signal Processing
- The premium screener supports index, commodity and stock options, using only listed ITM+1 CE/PE contracts with expiry strictly after today.
- History loading is silent. Completed highs/closes never generate service alerts or seed a monitored crossing. Startup above the reference does not trigger: a fresh observation at/below the high must precede an observation strictly above it. No candle close, second above-high observation, or continuation confirmation is required.
- EMA9, MACD, RSI and volume have no role in premium alert eligibility or presentation. Legacy range/sideways gates remain removed.
- After successful alert delivery at observed price P, the next trigger within [P×0.95, P×1.05] is blocked (inclusive). The deterministic key is `category:underlying:CE|PE`, shared across strikes and expiries, without daily reset. Only a fresh crossing outside that band can qualify; blocked crossings are consumed, not deferred until price drifts out.
- `PREMIUM_ALERT_STATE_FILE` stores successful trigger prices and per-reference dedup keys using atomic StateManager saves. It defaults to `premium_alert_state.json` beside `STATE_FILE`; mount persistent storage and run a single scanner process. Queue acceptance alone or failed delivery does not advance the last successful price.
- Live breakout evidence must be at most 10 seconds old. A separate last-trade timestamp, when present, must be fresh; a fresh executable book timestamp cannot disguise stale LTP.
- Entry is the red high, SL is 5% below the red low, and the strategy target is 2R. Alerts retain 1R/2R levels, actual observed breakout price/time (including seconds), contract details and spot without an indicator diagnostics block. Paper alerts retain the practice label; other alerts explicitly say SIGNAL ONLY, with mode unverified when unknown. They never imply a broker entry or LIVE fill.
- Internal payload/logging retains `reference_timestamp`, `reference_high`, `first_crossing_timestamp`, `breakout_price`, `detected_at`, and `sent_at`. Delivery retry keeps the original crossing price/time and detection deadline, never relabels a later above-high quote, and expires after 60 seconds. Retries require a fresh current quote and an open session. Transport uncertainty after Telegram accepts a message can still cause an at-least-once delivery; this is not a new strategy trigger.
- Isolated live-execution controls are unchanged: price-only scanner alerts cannot satisfy that engine's existing indicator-evidence gate, and the production broker adapter remains fail-closed. This update does not enable real orders.

## Risk Management Rules
- Position sizing and max-loss checks gate execution.
- Trade control workflow supports approval paths and safe fallback behavior.
- Service alerts are emitted on risk breaches and critical processing faults.

## Order Execution Flow
1. Build order payload from signal + strike resolver.
2. Apply the selected mode's authorization and risk checks. Paper trading simulates execution; live submission uses the isolated broker-backed workflow.
3. Persist/track positions and update P&L state.
4. Detect SL/Target hits and auto-close when triggered.
