# Lifecycle option-premium charts

Signal lifecycle alerts can attach PNG charts for OPEN, TARGET, STOP, and SESSION_CLOSE events. Charts show actual option-contract intraday candles, not underlying candles: green/red candlesticks, entry/stop/target levels, shaded risk/reward zones, reference/breakout markers when available, and the event premium marker.

## Data source and timing

Candles come from `DataManager.fetch_contract_candles`, backed by Dhan `/v2/charts/intraday`. Only completed candles in IST are plotted; partial and future candles are excluded. The default interval is 10 minutes to match the premium strategy timeframe.

## Settings

| Env key | Default |
| --- | --- |
| `LIFECYCLE_CHARTS_ENABLED` | `true` |
| `LIFECYCLE_CHART_WINDOW` | `80` |
| `LIFECYCLE_CHART_INTERVAL_MINUTES` | `10` |
| `LIFECYCLE_CHART_MAX_FETCHES_PER_MINUTE` | `6` |
| `LIFECYCLE_CHART_MAX_ATTEMPTS` | `5` |
| `LIFECYCLE_CHART_TTL_HOURS` | `48` |
| `LIFECYCLE_CHART_MAX_QUEUE` | `200` |
| `LIFECYCLE_CHART_MAX_BYTES` | `900000` |

## Resource budget

One daemon worker sends charts serially. Fetches are capped per minute and coalesced in memory so OPEN/exit jobs for the same contract bucket reuse data. PNG output is capped by `LIFECYCLE_CHART_MAX_BYTES`. Matplotlib uses the Agg backend; expect transient memory use while rendering.

## Persistence and delivery

Jobs live in the same SQLite file as signal lifecycle state. Use persistent storage; Render's filesystem is ephemeral unless a persistent disk is attached. Delivery is at-least-once: retries after ambiguous network failures can duplicate a photo. Exactly-once photo delivery is not promised.

## Failure modes

Diagnostics are stored as short categories such as `no_completed_candles`, `wrong_contract`, `fetch_budget_exhausted`, `rate_limited`, or `destination_changed`. Text alerts are sent first and are not blocked by chart fetch, render, or Telegram photo failures.

Charts and rupee figures are hypothetical signal results, not broker fills. They do not guarantee performance, profit, or execution quality.
