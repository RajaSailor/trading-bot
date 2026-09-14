# Trading Logic Guide

## Strategy Pipeline
1. Fetch candles/options premium data.
2. Evaluate breakout/breakdown conditions.
3. Validate risk rules (capital, limits, duplicate control).
4. Execute order and publish Telegram updates.

## Signal Processing
- Signals are normalized by category (index, commodity, stocks, intraday, crypto).
- Premium screener tracks ATM CE/PE premium breakout opportunities.

## Risk Management Rules
- Position sizing and max-loss checks gate execution.
- Trade control workflow supports approval paths and safe fallback behavior.
- Service alerts are emitted on risk breaches and critical processing faults.

## Order Execution Flow
1. Build order payload from signal + strike resolver.
2. Submit via DhanHQ client.
3. Persist/track positions and update P&L state.
4. Detect SL/Target hits and auto-close when triggered.
