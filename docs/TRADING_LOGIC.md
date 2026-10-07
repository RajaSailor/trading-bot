# Trading Logic Guide

## Strategy Pipeline
1. Load completed 10-minute premium history for listed ITM+1 CE/PE contracts.
2. Evaluate actual fresh live option premium strictly above the most recent RED candle high within the last 20 completed candles.
3. Validate risk rules (capital, limits, duplicate control).
4. Route eligible signals through the mode-specific approval/execution workflow and publish Telegram updates. A service alert is an observation, not an order or fill.

## Signal Processing
- The premium screener supports index, commodity and stock options, using only listed ITM+1 CE/PE contracts with expiry strictly after today.
- History loading is silent. Completed highs/closes never generate service alerts or masquerade as live prices. Failed indicator candidates remain eligible for reevaluation; delivered signals are deduplicated by contract and reference candle.
- All three premium indicators must pass: actual live price above EMA9 or an honestly observed current-bucket EMA9 straddle; MACD(12,26,9) line strictly above signal; Wilder RSI(14) strictly above 25 and above the preceding completed RSI.
- EMA and MACD use SMA seeds; RSI uses Wilder smoothing. Previous-session history may supply warmup, and provisional live candles never mutate completed history. Volume is optional.
- Live breakout evidence must be at most 10 seconds old. A separate last-trade timestamp, when present, must be fresh; a fresh executable book timestamp cannot disguise stale LTP.
- Entry is the red high, SL is 5% below the red low, and the strategy target is 2R. Alerts retain 1R/2R levels, actual observed breakout price/time (including seconds), contract details and spot without an indicator diagnostics block. Paper alerts retain the practice label; other alerts explicitly say SIGNAL ONLY, with mode unverified when unknown. They never imply a broker entry or LIVE fill.

## Risk Management Rules
- Position sizing and max-loss checks gate execution.
- Trade control workflow supports approval paths and safe fallback behavior.
- Service alerts are emitted on risk breaches and critical processing faults.

## Order Execution Flow
1. Build order payload from signal + strike resolver.
2. Apply the selected mode's authorization and risk checks. Paper trading simulates execution; live submission uses the isolated broker-backed workflow.
3. Persist/track positions and update P&L state.
4. Detect SL/Target hits and auto-close when triggered.
