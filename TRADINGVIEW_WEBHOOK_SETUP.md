# TradingView Webhook Setup

## 1. Enable the webhook receiver

Set these Render environment variables:

```env
ENABLE_TRADINGVIEW_WEBHOOK=true
WEBHOOK_SECRET=optional-shared-secret
TRADINGVIEW_WEBHOOK_TIMEOUT=30
WEBHOOK_RETRY_ATTEMPTS=3
```

Webhook URL:

```text
https://your-render-app.com/webhook/tradingview
```

## 2. Add the Pine Script to TradingView

1. Open a TradingView chart.
2. Open **Pine Editor**.
3. Copy the contents of `/home/runner/work/trading-bot/trading-bot/pine_script_template.txt`.
4. Update `signal_category` and `signal_timeframe` if needed.
5. Click **Add to chart**.

## 3. Create the alert

1. Click **Create Alert** in TradingView.
2. Choose the strategy added from the template.
3. Enable **Webhook URL** and paste your Render URL.
4. Use the script-generated JSON alert message.
5. If `WEBHOOK_SECRET` is enabled, add `"secret":"<value>"` to the JSON body.

## 4. Test the integration

Use the built-in test endpoint:

```bash
curl -X POST https://your-render-app.com/api/webhook/test \
  -H "Content-Type: application/json"
```

Check:

- `GET /health/webhook`
- `GET /api/webhook/history`
- Telegram channel delivery

## 5. Troubleshooting

- **400 Invalid webhook secret**: make sure the JSON body or `X-Webhook-Secret` header matches `WEBHOOK_SECRET`.
- **400 Missing required webhook fields**: confirm the TradingView alert includes every required JSON key from the template.
- **Rate limit exceeded**: the receiver allows up to 100 alerts per minute.
- **No Telegram alert**: verify the category matches one of `INDEX_OPTIONS`, `NIFTY50_STOCK_OPTIONS`, `COMMODITY_OPTIONS`, `INTRADAY_5X`, `PAY_LATER`, or `CRYPTO`.
- **DhanHQ outage fallback**: recent webhook signals are cached and used as a lightweight fallback candle source when DhanHQ data fetches fail.
