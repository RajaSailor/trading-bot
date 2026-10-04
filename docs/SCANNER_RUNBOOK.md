# Options Scanner Runbook (Render 512 MB)

This runbook covers the premium breakout scanner that sends option alerts to the
`SERVICE_ALERTS` Telegram channel.

## Strategy rules (as implemented)

| Rule | Implementation |
|---|---|
| Expiry | Nearest listed expiry **strictly after today**. On expiry day the same-day contract is never used; the next expiry is used from the start of the day. |
| Strikes | Taken from the listed Dhan contracts (no hard-coded strike step). For every underlying the scanner monitors CE and PE at **ITM+1, ATM, OTM+1** (6 contracts). CE ITM = lower strike, PE ITM = higher strike. |
| Reference candle | Most recent **RED** 10-minute candle (`close < open`; dojis are *not* red) within the last **20** ten-minute candles. Previous-session candles are fetched so the window is full from the open. |
| Trigger | Premium price crosses **above the red candle HIGH**. Checked on every completed 10-minute candle and, between candles, on the live LTP (`LIVE_TRIGGER_POLL_SECONDS`, default 1 s). |
| Entry | Red candle high |
| Stop-loss | Red candle low × 0.95 (5 % below the low) |
| Risk points | Entry − Stop-loss |
| Target | Entry + 2 × Risk (single 2R target) |
| Duplicates | One alert per option contract per reference red candle. |

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
| `PRACTICE_MODE` | `true` | Alerts only, no real orders. Keep `true` for validation. |
| `AUTO_TRADING_ENABLED` | `false` | Ignored while `PRACTICE_MODE=true`. |
| `ENABLE_MARKET_SCANNER` | `false` | Set `true` to run the scanner (also needs `ACCESS_TOKEN`). |
| `ACCESS_TOKEN` | – | Dhan access token (candles, LTP). |
| `DHAN_CLIENT_ID` (or `API_KEY`) | – | Dhan client id, required by the LTP endpoint used for live triggers. |
| `CHANNEL_SERVICE_ALERTS_ID`, `BOT_SERVICE_ALERTS_TOKEN` | – | Telegram alert channel / bot. |
| `SCANNER_IDLE_SECONDS` | `300` | Max sleep while all exchanges are closed. |
| `LIVE_TRIGGER_ENABLED` | `true` | Live LTP breakout checks between candles. |
| `LIVE_TRIGGER_POLL_SECONDS` | `1` | Live LTP poll interval (Dhan LTP limit is 1 request/s). |
| `SCANNER_REFRESH_RETRY_SECONDS` | `120` | Retry delay after a failed candle refresh. |
| `EXCHANGE_EXTRA_HOLIDAYS` | – | Extra closures, e.g. `ALL:2027-01-26,MCX:2026-12-31`. Required for 2027+ until the calendar is updated. |
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
   - `🎯 [SYMBOL] CE band | ... | ITM+1=.. ATM=.. OTM+1=..` per underlying,
   - on expiry day the `expiry=` shown is the **next** expiry.
3. For each Telegram alert verify: underlying/category, option symbol, strike,
   CE/PE, BUY CALL/BUY PUT, expiry, band, entry, stop-loss, risk, 2R target,
   timeframe and IST reference/breakout times. Cross-check entry/SL/target
   against the red candle on the Dhan chart.
4. Latency: `⏱️ Latency <stage> (n=..): p50/p95/p99` log lines report internal timings
   from price receipt to evaluate, queue and Telegram send start.
5. Only after several sessions of correct alerts consider changing
   `PRACTICE_MODE`.

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
