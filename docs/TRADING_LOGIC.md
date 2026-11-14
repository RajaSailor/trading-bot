# Trading Logic Guide

## Premium-only options strategy

`INDEX_SWING_STRATEGY_ENABLED=true` enables the premium-only advisory routes for
NIFTY, BANKNIFTY and SENSEX index options, configured NIFTY 50 stock options,
and supported MCX commodity options. The default remains `false`. All use ATM
contracts and retain their category's existing alert channel. These alerts
never place broker or paper orders; keep `NIFTY_LIVE_ENABLED=false`,
`AUTO_TRADING_ENABLED=false`, and `PRACTICE_MODE=true`.

### Independent premium-only entry routes

Both bought CALL and bought PUT contracts use the same bullish-premium rules.
The scanner does **not** require spot direction, opening-range breaks, spot
patterns, spot support/resistance or option volume. Spot is needed only for ATM contract selection, not entry
confirmation. Each candidate uses its own contract's same-session candles;
neither the opposite option nor another strike supplies its structure.

Five-minute pivots need one completed neighbour on either side. Use the
latest contiguous block of today's completed five-minute candles; an overnight
gap or a missing candle cannot manufacture a pivot across the gap.

1. **Repeated support / W recovery:** a confirmed five-minute low defines
   support. There must also be a subsequently confirmed swing high (the
   intervening rebound) before the new five-minute retest begins. A completed
   five-minute close more than 0.5% below support invalidates that candidate.
   The latest pair of five-minute candles must test within +/-0.5% of support;
   the latest close must exceed both support and the preceding candle high.
   The target is the nearest already-confirmed intervening swing high.
   Example: 70 -> 47 -> 60 -> approximately 47 -> recovery. Entry can occur
   near support after confirmation, without waiting to break the 60 neckline.
2. **Average support reclaim:** at least two confirmed five-minute swing highs
   within 1% define repeated resistance. At least two confirmed pullback lows
   after the first rejection and before the latest matching rejection define
   an arithmetic mean support level. Only already-confirmed lows participate:
   today's current retest is not included in its own average. The initial stop
   is the lowest contributing five-minute support candle's low; the fixed
   target is the lowest matching resistance high. Example: pullbacks of 90 and
   100 between repeated rejections at 120 give mean support 95.
3. **Previous-session level recovery:** use confirmed five-minute pivots and
   the available session high/low from the immediately previous trading session
   of this exact security ID, strike, expiry and option side. Never splice in
   the previous ATM contract or an expired series. A prior support, or prior
   resistance reclaimed as support, may qualify after a test within +/-0.5%
   and the same completed five-minute recovery. One recovery is enough; do not
   require a second retest or a same-day W. Select the next higher known prior
   resistance as target, and the source five-minute candle low as initial stop.
   Skip if that target is already passed. This requires two completed same-day
   five-minute candles. Missing prior-session data disables only this
   route. Gaps cannot manufacture prior-session pivot points.

Support, compression, FVG, OB, BOS and pattern routes are **OR** alternatives,
not cumulative requirements. Process support recovery and compression first,
then independently qualifying zones/BOS and patterns, including when another
signal is already open. Support/average levels retain their original definitions,
but their new confirmation is five-minute, not the old one-minute recovery.
There is no established universal count of market patterns; this implementation
supports the following six explicitly defined bullish-premium structures.
Tolerances are trial settings, not empirically optimized probabilities.

### Compression, FVG/OB and BOS

These are explicit deterministic definitions of discretionary chart concepts,
not proof that an institutional order or imbalance exists.

| Route | Confirmation / entry | Initial stop | Fixed target |
| --- | --- | --- | --- |
| Compression | At least two consecutive completed 5m candles; combined high minus low <=10% of combined low. Extend backwards through consecutive candles while this remains true. After a confirmed prior decline, first completed 1m close above the frozen range high. No earlier post-range 1m close may already break the range. | Range low | Nearest confirmed pre-range swing high above the range |
| Bullish FVG | Three consecutive completed 5m candles; third low > first high, middle candle green with body >=50% of its high-low range. Gap is [first high, third low], known only after the third closes above it. First fresh live premium in that range alerts; no close confirmation. | Entry 5m candle's low observed so far | Nearest confirmed 5m swing high above entry, plus one metadata tick |
| Bullish order block | A completed bullish 5m candle first closes above the latest already-confirmed swing high; the last red candle within its preceding five candles defines the full [low, high] block. Breakout close must be above the block. First fresh live retest alerts. | Entry 5m candle's low observed so far | Nearest confirmed 5m swing high above entry, plus one metadata tick |
| BOS/retest | Latest two confirmed 5m highs form a lower high. A completed 5m close breaks it from below, followed by a completed wick rejection within +/-0.5% of that level. No subsequent completed close may break below the band. Then a fresh live crossing from at/below the lower high to above it (within +0.5%) alerts, without another 5m close. | Entry 5m candle's low observed so far | The preceding higher swing high plus one metadata tick |

Compression example: prior confirmed high 150, decline to a 75-82 range held
for two or more completed 5m candles, then a completed 1m close above 82.
Initial stop is 75; target is 150 if that is the nearest confirmed pre-range
high. It does not require five or ten range candles or another 5m breakout.
The confirming candle need not be green; the required event is its close above
the range. Support recovery similarly requires the close above the previous
5m high, not a separate candle-colour gate.

Use only today's latest contiguous 5m block for zones, BOS and compression;
range/structure cannot bridge missing candles. Latest zone/BOS history must
end within 375 seconds; completed support/1m compression triggers within
75 seconds. FVG/OB zones already touched by a subsequent completed candle
are not replayed as untouched zones. When multiple zones overlap, scan-time
entry uses the most recently formed eligible zone for that route.
Zone entries are polled on the existing live-quote loop between minute scans,
not delayed until the next candle closes. BOS live crossing uses a fresh
previous observed quote (<=10 seconds earlier) or an already-completed
one-minute close (<=75 seconds earlier); both must follow the rejection close.

At a live touch, the final entry-candle low is unknowable. Freeze only the
low actually observed from current contract candles/live quotes at entry.
If no current-candle evidence exists, or its low is not below the premium,
skip with an explicit reason rather than invent a stop. A quote below the
zone is not a touch; a quote far above the BOS band is not an at-level entry.
No known target above entry means rejection, not a fabricated measured move.
All routes retain positive bid/ask, <=2% spread, source quote age <=10 seconds,
target/stop ordering and calendar/square-off admission.

### Completed bullish premium patterns

Scan **2, 3, 5, 10, 15, 30 and 60 minutes** for index options, and **5, 10,
15, 30, 60, 120, 240 minutes and 1 day** for stock and commodity options.
Each timeframe is evaluated independently for each ATM CALL/PUT.
A bullish PUT-premium pattern is a bought PUT candidate; bearish CALL-premium
patterns are not converted into PUT signals, and no pattern places a sell order.
The pattern route does **not** require a support retest, one-minute recovery,
agreement between timeframes, or spot confirmation.

Every pivot has one completed neighbouring candle on either side. The entire
structure must already be observable before the breakout candle starts.
Use alternating unambiguous pivots (an outside bar that is both a high and low
is excluded); retain the more extreme of consecutive same-kind pivots.
Examine the most recent appropriate pivot sequence in the last 80 full bars.

| Pattern | Required structure | Initial stop | Fixed measured-move target |
| --- | --- | --- | --- |
| Double bottom | Low-high-low; lows within 0.5%; close above intervening high | Lower bottom | Boundary + boundary-to-bottom height |
| Triple bottom | Low-high-low-high-low; all lows within 0.5%; close above both intervening highs | Lowest bottom | Boundary + boundary-to-bottom height |
| Inverse head-and-shoulders | Low-high-low-high-low; shoulders within 1%; head at least 0.5% below both; close above both neckline highs | Right shoulder low | Boundary + boundary-to-head height |
| Ascending triangle | Three highs within 0.5%; two or three intervening lows rising by more than 0.5% each; close above all three highs | Latest pivot low | Boundary + height above first low |
| Falling wedge | Three falling highs and three falling lows; declining upper line is steeper than lower; positive narrowing channel; intermediate points within 1% of their lines | Latest pivot low | Breakout boundary + initial channel width |
| Bullish flag | Three falling highs/lows in a channel; negative slopes differ by at most 25%; preceding up-pole within eight bars is at least three channel widths and 5% of pole-high premium; pullback is no more than half the pole | Latest pivot low | Breakout boundary + pole height |

The latest candle must be green and produce the **first completed close**
strictly above the neckline/upper boundary after the structure, with the
preceding close at/below its boundary. No intervening close may already have
broken the boundary or invalidated the stop. For sloped boundaries, compare at
each candle index, not against an invented flat line. The confirmation must
end today and be no more than 75 seconds old. Current fresh premium must still
be above the confirmed boundary and stop, and below target. Merely forming
patterns, continuation above an old breakout, and bearish patterns do not alert.

Build intraday timeframes from exact-contract one-minute OHLC, anchored to each
exchange session. Require every constituent minute, reject partial buckets and
never aggregate across session boundaries or missing data. Daily stock and
commodity patterns use completed exact-contract daily OHLC; during a live
session, confirmation must be from the previous trading session. Multi-session
intraday patterns are allowed only across consecutive trading sessions, with
no missing full buckets; closures/holidays are not missing bars.
At least seven full contiguous bars are needed even to evaluate a timeframe;
some structures need more. Request up to ten prior trading sessions plus today,
without synthesizing data predating a contract's listing. Long timeframes may
often lack enough history for short-lived weekly contracts.

The previous-session/current one-minute fetch remains separate from the older
history, which is cached per contract/date range in a bounded 24-entry cache.
This avoids repeatedly downloading/caching ten-session histories every minute.
History or API failures can still leave timeframes unavailable; they are not
interpreted as “no pattern.”

Each confirmed pattern has its own contract/timeframe/name/geometry/breakout
identity. Repeated scans and restarts cannot reopen the same signal. Different
confirmed patterns or timeframes may produce separate alerts; support acceptance
does not suppress them. The chart freezes the completed detection candles,
draws pattern vertices and neckline/trendlines, names the exact timeframe, and
shows entry, initial stop and target. Pattern evidence is revalidated at durable
lifecycle admission, not inferred from a label alone.

### Support recovery entry, expiry and target

For each side, select the listed **ATM** contract nearest to spot, using the
nearest listed expiry strictly after today. On expiry day, same-day contracts
are skipped and the next available expiry is selected.

After support and target are known, examine the latest two consecutive completed
five-minute option candles. Their test must be within +/-0.5% of support.
The latest closes above support and the previous 5m high, with confirmation
ending no more than 75 seconds ago. Support and target must already be known
before the preceding 5m candle starts. There is no additional 1m check.
Source last-trade evidence must be no more than 10 seconds old, with positive
bid/ask and spread <=2%. The current quote must remain above support, below
target and above stop. The alert labels the actual confirming close separately
from the fresh observed premium: it cannot guarantee a past candle-close fill.

Recovery targets come only from the same-session or explicitly labelled
previous-session premium structure described above.
If there is no target above entry, skip the alert rather than invent a target.
The target remains fixed after entry. Stop/target alerts use observed premium;
they are not broker orders or guaranteed execution prices.

### Re-entry, protection and frequency

- Repeated FVG/OB touches alert once per exact-contract zone; BOS once per
  lower-high break and rejection. Candle-based routes require a new confirmation.
  Idempotency persists across scans/restarts, not just the current open position.
- There is no daily entry-alert cap. Multiple index signals can be open at once;
  each has its own signal ID, contract, entry, stop, target and lifecycle.
- Initial recovery stop is the five-minute option support candle low, rounded to
  tick size. Do not delay the initial advisory stop until a profitable candle.
  Pattern entries instead use the pattern invalidation low in the table above.
- **New index signals freeze `completed_5m_low_v1`.** Each fully post-entry
  completed 5m candle can raise the advisory stop to its low, tick-rounded down.
  Never use a partial candle or lower the existing stop, and never move the
  target. No 20% profit threshold or 5%-below-quote ladder is applied to these
  new signals. A fresh quote must cover the completed candle; if that quote is
  already below the newly raised stop, close at the observed premium rather
  than claim a guaranteed stop fill. Repeated candles cannot repeat MODIFY.
- Stops, targets, exits and re-entries are informational only. Repeated
  stop-outs can accumulate losses; there is no guaranteed fill, loss limit or
  profitable outcome.
- Existing session calendar and square-off remain. Stocks and commodities keep
  their prior lifecycle behavior. Already-open old-policy signals retain their
  frozen stop logic.

Every threshold and support-structure rule is an experimental starting point, not
an optimized or historically validated strategy. Compare alerts with the exact
ATM contract and contemporaneous candles; record missed/late alerts, fees,
spread, drawdown and losses across multiple sessions before considering changes.
No new market-data vendor is added. Full spot history is no longer requested
for index entry checks, but premium and quote requests may still meet Dhan rate
limits. A missing ATM selection price still prevents selecting a contract.

Back up existing persistent state before deployment. Check the health endpoint's
`workers.market_scanner.screener.index_swing_strategy` and rejection counters.
`recent_index_evaluations` retains the last 50 ATM-side/route evaluations with
zone ranges, entry/stop/target and explicit rejection reasons.
`ACCEPTED` means durable signal/outbox acceptance,
not confirmed Telegram delivery. `QUALIFIED_NOT_ACCEPTED` distinguishes
duplicate/admission rejection from a setup failure.
Pattern diagnostics list all nine timeframes, available/contiguous bar counts,
gap-excluded counts and `INSUFFICIENT_HISTORY`, `AWAITING_FRESH_CLOSE`,
`AWAITING_TODAY_CLOSE`, `NO_CONFIRMED_PATTERN` or `CONFIRMED`; quote/data errors
are recorded separately. Pattern entry rejections include price outside the
boundary/stop/target range. Health exposes the six pattern names, timeframe list,
history budget, previous-session route and support-first priority.
`index_underlying_status` retains each NIFTY/BANKNIFTY/SENSEX configuration,
scan timestamp, resolved CALL/PUT sides, and per-side route results beyond
the rolling list. Missing ATM prices, missing listed sides, scan exceptions
and not-yet-evaluated instruments are explicit; no alerts alone does not prove
there were no setups. Index validation now includes time spent scanning earlier
instruments, avoiding erroneous future-quote rejection on later indices.
This clock correction is not a retrospective diagnosis of a particular session.
Leave live execution disabled, review the first sessions, and change the flag
to `false` to stop new revised index entries. Do not reset or delete lifecycle
state during rollout.

### Public-channel disclaimer

All three categories' signal alerts, lifecycle events (OPEN, MODIFY, TARGET,
STOP and SESSION_CLOSE), chart captions and every daily-report part carry this
shared one-line notice:

> Educational information only - not investment advice or a recommendation; I am not a SEBI-registered investment adviser or research analyst; trading involves risk, and decisions and losses are your responsibility.

The registration statement must remain factually accurate. This is a risk
disclosure, **not legal clearance, immunity or a waiver of applicable SEBI
requirements**. Labels/disclaimers do not determine the legal nature of public
entry/stop/target signals. Obtain Indian securities-law advice before public
distribution. Existing hypothetical-result labels remain; historical stored
messages are not rewritten.

## Legacy strategy pipeline (stocks/commodities; indices when trial is off)
1. Load completed 10-minute premium history for listed ITM+1 CE/PE contracts.
2. Trigger on the first observed fresh live crossing from premium ≤ reference high to premium > reference high, using the most recent RED candle within the last 20 completed 10-minute candles.
3. Validate NIFTY auto-entry risk rules (fresh funds/margin, one metadata lot, two filled entries per IST day, one open position, and persisted deduplication).
4. Only NIFTY index options may be considered by the real auto route. Stock, commodity, and other-index options remain category-channel signals and never fall back to real execution.

## Signal Processing
- The premium screener supports index, commodity and stock options, using only listed ITM+1 CE/PE contracts with expiry strictly after today.
- History loading is silent. Completed highs/closes never generate service alerts or seed a monitored crossing. Startup above the reference does not trigger: a fresh observation at/below the high must precede an observation strictly above it. No candle close, second above-high observation, or continuation confirmation is required.
- Option-premium EMA9, MACD, RSI and volume have no role in premium alert eligibility or presentation. Legacy range/sideways gates remain removed. Optional NIFTY underlying EMA20 and VWAP/TWAP gates are described below.
- The deterministic anti-chop key is `index_options:NIFTY:CE|PE`, shared across strikes/expiries and persisted with live intents. After an acted/filled trigger at P, the inclusive band [0.95P, 1.05P] is blocked; only a new crossing strictly outside it qualifies.
- `PREMIUM_ALERT_STATE_FILE` retains legacy anti-chop and per-reference dedup state. In the deployed lifecycle path, a successfully acted trigger means the **atomic durable signal opening and outbox acceptance**, not Telegram delivery or a broker fill. This deliberately supersedes the old delivery-success-only policy: a transport retry must not open another CE/PE signal while the first is unresolved. Queue acceptance alone never advances this state. Mount persistent storage and run one scanner process.
- Live breakout evidence must be at most 10 seconds old. A separate last-trade timestamp, when present, must be fresh; a fresh executable book timestamp cannot disguise stale LTP.
- The compact signal alert has no EMA/MACD/RSI or volume lines. The initial stop is `0.95 × red-candle low` (rounded upward to the contract tick); after the actual fill E, R is `E − SL` and the full target is `E + 2R`. Live trailing remains disabled; opt-in hypothetical signal trailing is documented in [Lifecycle charts](LIFECYCLE_CHARTS.md).
- Real auto sizing is exactly one lot from contract metadata and uses the carry-forward `MARGIN` product enum. Insufficient or unverifiable funds/margin, stale evidence, an occupied position, or the two-filled-entry IST-day cap blocks entry.
- Internal payload/logging retains `reference_timestamp`, `reference_high`, `first_crossing_timestamp`, `breakout_price`, `detected_at`, and `sent_at`. Delivery retry keeps the original crossing price/time and detection deadline, never relabels a later above-high quote, and expires after 60 seconds. Retries require a fresh current quote and an open session. Transport uncertainty after Telegram accepts a message can still cause an at-least-once delivery; this is not a new strategy trigger.
- Production execution remains fail-closed: there is no verified Dhan Super Order adapter, funds/margin wire parser, authoritative account reconciler, or broker-managed protection worker in this checkout. Runtime mode is automatic only, denies approval updates, and creates no approval cards or generic-order substitute. The old approval library is retained only for fake-adapter simulation tests. Dhan carry-forward semantics, static egress IP, and exit behavior must be independently verified before a real deployment.
- The official [Super Order API](https://dhanhq.co/docs/v2/super-order/) and [Dhan SDK implementation](https://github.com/dhan-oss/DhanHQ-py/blob/main/src/dhanhq/_super_order.py) expose entry `orderType` and `stopLossPrice`, but no independent stop-leg LIMIT price/type. The precise production blocker is: **Super Order stop leg has no independently configurable LIMIT price/type; LIMIT entry does not verify LIMIT stop execution**. No alternative OCO/protection route has been verified safe with no hidden market execution, so there is no substitute route.
- `live_route.status()` exposes sanitized actual execution account/intent status, LIMIT-only policy, disabled trailing, and production blockers; it does not expose Telegram secrets, credentials, or arbitrary adapter/proposal data.

## Optional NIFTY spot direction and support/resistance gates

These additive phase-1 gates apply only to `index_options:NIFTY`, before durable
opening or live-route callbacks. They do not relax premium crossing/freshness,
deduplication, risk or broker guards, enable execution, or change channel routing.
Other indices, stocks and commodities retain their existing behavior.

| Environment variable | Default | Meaning |
| --- | --- | --- |
| `NIFTY_DIRECTION_FILTER_ENABLED` | `false` | Enable underlying regime gate |
| `NIFTY_DIRECTION_HTF_MINUTES` | `15` | Completed spot candle interval: 5 or 15 |
| `NIFTY_DIRECTION_NEUTRAL_POLICY` | `block` | `block` both sides, or `stronger` |
| `NIFTY_DIRECTION_NEUTRAL_BREAKOUT_PCT` | `0.5` | Neutral `stronger` requires premium ≥ reference high × 1.005 |
| `NIFTY_SR_FILTER_ENABLED` | `false` | Enable immediate underlying S/R gate independently |
| `NIFTY_SR_BUFFER_PCT` | `0.10` | Spot-relative distance, in percent (0.10 means 0.1%, not 10%) |

Enable either or both flags explicitly; no deployment environment is changed by
this feature. Invalid numeric settings revert to defaults (S/R range 0–5%,
neutral premium buffer 0.01–10%); unknown neutral policies block.

The shared Dhan candle path resolves the **NIFTY spot index**, fetching the
previous NSE trading day through today. Requests are coalesced once per
session-anchored HTF bucket, with failed, empty or delayed latest-bar fetches
retried after 120 seconds.
These explicit retries refresh the shared candle cache; ordinary reads retain
bucket caching and all broker request pacing remains in force.
No external data source or new dependency is introduced.

Only valid completed, non-future OHLC bars are used. Latest completion must be
today and no older than one HTF interval plus 60 seconds. EMA20 is seeded with
20 completed closes, including previous-session history; 21 bars are needed
for its slope. Bullish means latest spot close is strictly above both session
VWAP and EMA20, with rising EMA20; bearish is the strict inverse. CE requires
bullish and PE requires bearish. Equality/disagreement is neutral. Missing
history or stale/invalid data blocks enabled gates, not a neutral exception.

VWAP uses session typical price `(H+L+C)/3` weighted by positive volume.
Spot indices often have no meaningful volume: if any session bar lacks positive
volume, an explicitly labeled **TWAP** (equal-weight session typical prices)
is used instead, never invented volume. No separate 5-minute confirmation or
strength/probability score is added in phase 1.

S/R includes previous-day high/low, classic pivot `P=(H+L+C)/3`,
`R1=2P−L`, `R2=P+(H−L)`, `S1=2P−H`, `S2=P−(H−L)` and strict local
swing highs/lows among the last 20 completed session bars (one neighbor on
either side). The pivot can act as either support or resistance. CE is blocked
when a resistance is at/above spot within `spot × buffer_pct / 100`; PE is
blocked when support is at/below spot within that buffer. Equality blocks.
Missing previous-session data blocks S/R. Already-broken levels do not block.
For example, spot 25,000 and resistance 25,020 blocks CE with a 0.10% buffer;
support 24,980 similarly blocks PE.

Rejected crossings consume that RED reference rather than creating a delayed
entry when regime changes. Health `indicator_rejections` adds
`direction_filter_reject` and `sr_filter_reject` (direction evaluated first);
`nifty_market_filter` exposes regime, VWAP/TWAP basis and as-of telemetry.
Existing rejection categories remain intact. These are coarse, lagging
confirmations, not forecasts: HTF spot closes can lag current price, gaps can
jump levels, and enabling filters can reduce signal count substantially.

## Risk Management Rules
- One NIFTY lot only; fresh funds/margin checks precede any entry, with a maximum of two filled entries per IST day and one managed open position.
- LIMIT is mandatory for every leg: entry, target, stop, manual exit, and time exit. No MARKET fallback is allowed, even after rejection or confirmed non-submission. An ambiguous write is reconciled and never blindly resent. A LIMIT order may remain unfilled; neither fills nor protective execution are guaranteed.
- Trade Control messages are restricted to confirmed real entry fills (including available balance), completed exits (actual fills and realized P&L), and deduplicated insufficient-balance alerts. Signals and daily reports use only their respective category channels. Paper output and routine diagnostics use logs.

## Advisory signal lifecycle and daily reports

- There is one SQLite-enforced OPEN signal per underlying, across CE/PE,
  strikes and expiries. Different underlyings are independent. The selected
  contract and entry remain frozen until a fresh observed target/stop outcome
  or session square-off. Stop and **one initial 2R target** remain frozen unless
  advisory trailing is explicitly enabled for that signal. ITM+1 reselection
  cannot replace an open signal.
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
- Per-strategy rows pool symbols only within their category/channel and show
  entries, closed/open counts, targets, stops (including profitable trailing
  stops), wins/losses/flat, win rate, average and weighted premium return, and
  P&L. Per-underlying rows and totals show opened, targets, stops, session-close exits
  (profit/loss counts), win rate and net rupee P&L. Win rate is
  `(targets + profitable session exits) / all closed signals × 100`, or `N/A`
  with no closed signals; flat/no-quote session exits are not wins. Totals also
  show gross profit, gross loss (positive magnitude), average P&L per included
  closed signal, max single loss and max single win. One daily-report footer reads:
  "Hypothetical signal results (1 lot per signal), not broker fills."
  These are not realized broker results or guaranteed performance.
- No pullback/rebound, five-minute closing confirmation, five-point entry
  buffer, second target or breakeven is used. NIFTY underlying filters and
  advisory trailing are opt-in; defaults retain the original signal rules.

## Order Execution Flow
1. Validate the NIFTY ITM+1 contract and its lot/tick metadata.
2. Recheck fresh quote, funds, margin, daily cap, one-position guard, and deterministic re-entry band.
3. Use Dhan Super Order entry/target/stop management only after its API capabilities are verified; reconcile actual fills, not acknowledgments.
4. Persist entry/exit lifecycle evidence and realized P&L. Missing broker protection or uncertain outcomes fail closed.

> **Risk disclaimer:** Options trading can result in rapid, substantial loss, including loss of the full premium. A 1:2 planned reward/risk is not a win-rate, fill, or profit guarantee. Do not trade funds you cannot afford to lose.
