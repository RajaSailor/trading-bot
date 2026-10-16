# Lifecycle option-premium charts

OPEN, TARGET, STOP, and SESSION_CLOSE normally send **one photo with the complete event message as its caption**, not a text message followed by a chart. The caption includes the contract, entry/stop/target, observed premium and, for exits, exit premium and one-lot P&L. Exit photos and text fallbacks reply to the original OPEN message, whether OPEN was a photo or text. Advisory MODIFY messages also reply to OPEN; daily reports remain text.

Charts show actual option-contract intraday candles, never underlying candles or fabricated future prices: green/red candlesticks, entry/stop/target levels, shaded risk/reward zones, reference/breakout markers when available, and the observed event premium marker. A clearly labelled empty projection area extends at least four candle intervals beyond the event so the levels are visible. It is a level projection, **not a price forecast or future candle series**.

## Data source and timing

Candles come from `DataManager.fetch_contract_candles`, backed by Dhan `/v2/charts/intraday`, using the signal's exact security ID, segment and instrument type. Only completed candles as of the event in IST are plotted; partial and future candles are excluded, including when an event is retried later. The default interval is 10 minutes to match the premium strategy timeframe. Missing or stale historical coverage is not supplemented with synthetic candles; delayed data is labelled.

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
| `SIGNAL_NOTIFICATION_MAX_ATTEMPTS` | `25` |
| `SIGNAL_ADVISORY_TRAILING_ENABLED` | `false` |
| `SIGNAL_ADVISORY_TRAILING_GAIN_PERCENT` | `20` |
| `SIGNAL_ADVISORY_TRAILING_STOP_PERCENT` | `10` |
| `SIGNAL_ADVISORY_TRAILING_TARGET_PERCENT` | `15` |

Chart integers are clamped: window 20–150, interval 1–60 minutes, fetches 1–120/minute, attempts 1–20, TTL 1–336 hours, queue 1–5000, PNG 100000–5000000 bytes. Trailing percentages must be finite and positive; gain and target must be at most 100, and stop must not exceed gain. Invalid trailing configuration disables enrollment of new signals. Percentages are percentage points (`20` means 20%, not `0.20`).

## Resource budget

The lifecycle outbox drain is the sole delivery owner. Chart preparation and one bounded transport attempt run inline in its SQLite write transaction; no competing chart delivery worker is used for integrated lifecycle events. Concurrent lifecycle drains serialize through SQLite. The injected candle provider must have a bounded transport timeout; Telegram photo requests use a 20-second timeout. Slow fetch/render/send work can delay other lifecycle database writers; this is not an asynchronous hard wall-clock deadline.

Fetches are capped per service process per minute and coalesced in memory for the same contract/time bucket. At most 300 fetched candles are snapshotted; rendering selects at most the configured window. A full chart queue, exhausted fetch budget, unavailable renderer, missing completed candles, or an overlength caption falls back immediately to one complete text message. Captions are never truncated to fit Telegram's 1024 UTF-16-unit limit. PNG output is capped by `LIFECYCLE_CHART_MAX_BYTES`. Matplotlib uses Agg; expect transient rendering memory use. A configured instance should have one lifecycle/chart-service pair; budgets are not global across multiple deployments.

## Persistence and delivery

Jobs live in the same SQLite file as lifecycle state. Event-time position snapshots freeze captions and overlays, so a delayed OPEN cannot acquire a later trailing stop or exit. A fetched candle snapshot is persisted with retry state and reused after restart. Successful photo IDs are saved in the event-message mapping and OPEN ID column. Outbox acceptance, event snapshots and advisory state updates are atomic; chart schema checks do not commit the caller's transaction.

Telegram 429 responses defer the outbox by at least `retry_after` (clamped to 1–300 seconds), without an immediate text send. Other transient photo failures use bounded exponential retries. Chart send attempts default to 5; the lifecycle outbox also caps overall attempts at `SIGNAL_NOTIFICATION_MAX_ATTEMPTS`. Definite photo-only refusals (for example a Telegram HTTP 400) can fall back to text. Ambiguous failures (timeout, connection loss or missing accepted-message ID) allow one ambiguous retry, then fail without sending an additional text copy. Destination changes, disabled/unknown categories and trade-control destinations also fail closed without text rerouting.

Delivery is at-least-once: a crash after Telegram accepts a message but before SQLite commits can repeat an event. No exactly-once guarantee is possible. Per-category outbox ordering preserves OPEN-first threading. If OPEN exhausts retries without a message ID, later events may be unthreaded rather than blocked forever. Legacy two-argument text callbacks remain supported but cannot capture IDs or provide threading.

Use persistent storage; Render's filesystem is ephemeral unless a persistent disk is attached. Existing terminal chart jobs are eligible for cleanup after the configured TTL; cleanup discards terminal snapshots. Deduplication remains in the lifecycle outbox even after chart-job cleanup.

## Advisory trailing rules (opt-in, no live order support)

Trailing is **off by default**. This feature changes only hypothetical lifecycle stop/target levels and emits a `MODIFY` notification. It does **not** amend, cancel or place broker orders. Existing live-route authorization, execution and safety gates are unchanged; broker trailing/order modification is unsupported. Do not enable this expecting live positions to be protected by the advisory stop. Lifecycle diagnostics expose `advisory_trailing.enabled_for_new_signals` and `live_broker_modifications_supported: false`.

Configuration is frozen into each **new** OPEN. Existing signals without a frozen trailing configuration are not enrolled by a restart or environment change. With entry `E`, initial stop `S0`, initial target `T0`, observed premium `P`, tick `t`, and configured percentage values `g`, `s`, `u`:

```text
step = floor((P - E) / (E * g / 100))
stop(step)   = ceil_to_tick(S0 + step * E * s / 100, t)
target(step) = ceil_to_tick(T0 + step * E * u / 100, t)
```

Only a step greater than the persisted highest step applies. Defaults advance the stop by 10% **of entry** and target by 15% **of entry** for each additional 20% entry gain; they do not compound and the first stop is not automatically moved to entry. Stops and targets never decrease. A quote jumping several thresholds produces one atomic modification for the highest reached step (`signal_id:MODIFY:step`), not a burst of intermediate messages. Duplicate/replayed/stale quotes cannot repeat a step. Quote validation and session cutoffs still apply.

**An existing stop/target hit is evaluated before trailing.** A gap through the current target closes at that target (or the observed premium if explicitly configured for P&L), rather than moving the target away. Later stop/target exits use the revised advisory levels. Reports use those hypothetical outcomes; a trailed stop can realize a positive P&L and is still classified as a STOP event. Projected remaining risk is shown as zero when the stop rises above entry.

## Migration

Schema updates are additive: lifecycle outbox `position` snapshots and chart-job `delivery_mode` are added automatically. Old outbox rows without snapshots use their persisted signal data; the original event-time snapshot cannot be reconstructed retrospectively. Existing message IDs and deduplication records are retained. Pending old text-first chart jobs are marked `SKIPPED` with `legacy_text_already_sent`, preventing a second photo for an already delivered text event. Standalone legacy queue APIs remain available outside a lifecycle database, but cannot compete with integrated delivery.

## Failure modes

Diagnostics are stored as short categories such as `no_completed_candles`, `wrong_contract`, `fetch_budget_exhausted`, `rate_limited`, `expired`, or `destination_changed`. Preparation failures never suppress the event's text fallback. A text transport failure still uses the durable lifecycle retry policy; ambiguous photo delivery intentionally does not trigger an immediate text duplicate. Disabling charts restores text-only lifecycle delivery.

Charts and rupee figures are hypothetical signal results, not broker fills. They do not guarantee performance, profit, or execution quality.
