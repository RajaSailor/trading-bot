# 🚀 Render Deployment Guide for DhanHQ Trading Bot

Complete step-by-step guide to deploy the DhanHQ Trading Bot on Render.com

## 📋 Prerequisites

- Render.com account (https://render.com)
- GitHub repository connected to Render
- Telegram Bot Token (from @BotFather)
- DhanHQ API credentials
- Render environment configured

---

## 🎯 Phase 1: Render Account Setup

### Step 1: Create Render Account
1. Go to https://render.com
2. Sign up with GitHub account
3. Authorize GitHub connection
4. Verify email

### Step 2: Connect GitHub Repository
1. Go to Dashboard → New Web Service
2. Click "Connect Repository"
3. Select `RajaSailor/trading-bot`
4. Click "Connect"

---

## ⚙️ Phase 2: Service Configuration

### Step 1: Configure Build & Deploy

**In Render Dashboard:**

```
Name: trading-bot
Region: Virginia (US East)
Branch: main

Build Command:
pip install -r requirements.txt

Start Command:
gunicorn main:app --workers 1 --threads 4 --bind 0.0.0.0:$PORT

Instance Type: Starter (Free tier OK for testing)
Auto-Deploy: Enabled
```

### Step 2: One deployed scanner process

`main:app` is the Flask API and starts the background workers. Use exactly one
Gunicorn worker so scanner observations and the process-local quote budget are
not duplicated. Do not also start `unified_main.py` or a second scanner service.
Persistent storage is required for lifecycle/report recovery; free ephemeral
storage is not suitable for that requirement.

---

## 🔐 Phase 3: Environment Variables

### Step 1: Add Environment Variables in Render

1. Go to Settings tab in Render dashboard
2. Scroll to "Environment"
3. Click "Add Environment Variable"
4. Add these variables:

#### **REQUIRED - Telegram Configuration (finalized category migration)**
```
BOT_SERVICE_ALERTS_TOKEN = <IITS bot token in Render secrets>
BOT_TRADE_CONTROL_TOKEN = <separate execution bot token in Render secrets>
TELEGRAM_TEST_SECRET = your_private_telegram_test_secret
INDEX_OPTIONS_CHANNEL_ID = -1004402571102
COMMODITY_OPTIONS_CHANNEL_ID = -1004492725416
STOCK_OPTIONS_CHANNEL_ID = -1004496485914
CHANNEL_TRADE_CONTROL_ID = -1003704821776
CHANNEL_SERVICE_ALERTS_ID = -1004402571102
```

The destinations above are user configuration examples, not code defaults. Give
the **same IITS bot** posting permission in all three signal channels. Keep the
old `CHANNEL_SERVICE_ALERTS_ID` during migration; verify independent category
readiness in `/health` and a routing probe in each category, then remove it.
Only an **absent** `INDEX_OPTIONS_CHANNEL_ID` permits the old index fallback.
An empty/invalid new key fails closed. Both keys never cause a double send.
Missing stock/commodity routes never fall back to index or Trade Control.

Trade Control is reserved for actual real NIFTY fills and insufficient balance.
Paper controls, approval cards, practice probes, routine service alerts and
summaries cannot post there. Paper testing uses logs; `/telegram/paper` is
disabled. `/telegram/test` permits only signal routes with the test secret.

#### **REQUIRED - DhanHQ Configuration**
```
ACCESS_TOKEN = your_dhanhq_jwt_token
ACCESS_TOKEN_EXPIRES_AT = 2026-09-19T08:00:00+05:30
API_KEY = your_dhan_api_key
DHAN_CLIENT_ID = your_dhan_client_id
WEBHOOK_SECRET = your_random_webhook_secret
```

#### **OPTIONAL - Operating Mode**
```
PRACTICE_MODE = true
AUTO_TRADING_ENABLED = false
ENABLE_MARKET_SCANNER = false
ENABLE_DHAN_TOKEN_RENEWAL = true
LOG_LEVEL = INFO
TIMEZONE = Asia/Kolkata
TRADING_DB_PATH = /var/data/trading.db
SIGNAL_DB_PATH = /var/data/signals.db
PREMIUM_ALERT_STATE_FILE = /var/data/premium_alert_state.json
NIFTY_LIVE_ENABLED = false
LIVE_DB_PATH = /var/data/live_trading.db
```

Use persistent disk storage for both the signal SQLite database and anti-chop
state, and one scanner process. Signal/report outbox delivery retries with
backoff; Telegram has no idempotent-send primitive, so an ambiguous accepted
send followed by a crash may repeat the **same event ID**, never open a new
signal. Monitor undelivered events in `/health`. Do not deploy multiple scanner
workers via Gunicorn.

### Pre-live verification checklist

- [ ] Keep `PRACTICE_MODE=true`, `AUTO_TRADING_ENABLED=false`,
      `NIFTY_LIVE_ENABLED=false` during verification.
- [ ] Verify all three channel permissions, no cross-category fallback, and no
      Trade Control paper/service/report output.
- [ ] Verify fresh live quotes and completed 10-minute history during an open
      exchange session, including stale/out-of-order and 429 behavior.
- [ ] Restart with an unresolved signal; verify the original contract and
      single underlying lock survive, and reports/outbox do not reopen it.
- [ ] Verify current IST exchange holidays and MCX DST/half-day schedules;
      maintain `EXCHANGE_EXTRA_HOLIDAYS` and the calendar for future years.
- [ ] Independently verify official Dhan funds/margin and complete account
      order/trade/position schemas, one-lot units, static egress IP and token
      validity, with no real order during tests.
- [ ] Verify **every** protection leg is LIMIT-only. This release cannot prove
      Dhan Super Order stop-leg LIMIT execution and has no verified alternative
      protection adapter; `/health` therefore remains production-blocked.
- [ ] Verify partial/unknown orders block entry, actual fills determine 2R,
      broker app edits are respected, and a 15:25 IST LIMIT-only exit attempt
      never marks an unfilled position closed or creates a duplicate sell.

There is **no flag-only live activation in this release**. A future independently
verified adapter must resolve every reported capability blocker before the
user explicitly sets all three live flags (`PRACTICE_MODE=false`,
`AUTO_TRADING_ENABLED=true`, `NIFTY_LIVE_ENABLED=true`). Changing one or even all
flags now does not enable production. A LIMIT stop/exit may not fill in a gap;
monitoring and manual closure in the Dhan app remain necessary.

The subscription screenshot and updated Render token are not authenticated API
evidence. The [official data API](https://dhanhq.co/docs/v2/market-quote/) and
[rate limits](https://dhanhq.co/docs/v2/) still apply; an upgraded subscription
does not remove 429s or improve strategy win rate by itself. Reuse batched quotes
and existing bounded data retries; never log access tokens or full auth errors.

#### **OPTIONAL - Risk Management**
```
MAX_DAILY_LOSS = 5000
MAX_POSITION_SIZE = 100000
RISK_PER_TRADE = 2
```

### Step 2: Get Your Bot Token

1. Open Telegram
2. Search for `@BotFather`
3. Send `/newbot`
4. Follow prompts to create a new bot
5. Copy the token (format: `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`)
6. Paste into `TELEGRAM_BOT_TOKEN` in Render

### Step 3: Get Your Chat ID

1. Send any message to your bot in Telegram
2. Go to: `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
3. Look for `"chat":{"id":123456789}`
4. Copy that ID number
5. Paste into `TELEGRAM_CHAT_ID` in Render

---

## 🧪 Phase 4: Deploy & Test

### Step 1: Deploy

1. In Render dashboard, click "Manual Deploy"
2. Select branch: `main`
3. Click "Deploy latest commit"
4. Wait for build to complete

### Step 2: Monitor Deployment

1. Go to "Logs" tab
2. Watch deployment logs:
   ```
   Building Docker image...
   Installing dependencies...
   Starting service...
   ```
3. Once complete, you should see:
   ```
   ✅ Deployment successful
   ```

### Step 3: Test Health Endpoints

```bash
# Get your Render URL from dashboard
RENDER_URL=https://trading-bot-XXXX.onrender.com

# Test root endpoint
curl $RENDER_URL/

# Test health check
curl $RENDER_URL/health

# Test DhanHQ health
curl $RENDER_URL/dhan/health
```

You should get JSON responses from all endpoints.

### Step 4: Verify Telegram Connection

1. Send a message to your bot in Telegram
2. Check Render logs for message processing
3. Bot should respond with trading commands

---

## 🔄 Phase 5: Post-Deployment

### Step 1: Set Up Webhook (Optional)

If using TradingView or screener webhooks:

```
https://trading-bot-XXXX.onrender.com/dhan/postback
```

Add header:
```
X-Dhan-Signature: your_signature_here
```

### Step 2: Monitor Logs

- Go to Logs tab regularly
- Check for any errors
- Monitor performance metrics

### Step 3: Set Up Auto-Redeploy

1. Enable "Auto-Deploy" in Render dashboard
2. Choose: "Deploy on every push to main"
3. Now every merge to main auto-deploys

---

## 🐛 Troubleshooting

### Issue: "Service failed to deploy"

**Solution:**
1. Check Build Command: `pip install -r requirements.txt`
2. Check Start Command: `python main.py`
3. Check requirements.txt exists in repo
4. Check all dependencies are listed

### Issue: "Build times out"

**Solution:**
1. Render free tier has limited build time
2. Upgrade to Starter tier
3. Or split requirements into essential only

### Issue: "Connection timeout to Telegram"

**Solution:**
1. Verify `TELEGRAM_BOT_TOKEN` is correct
2. Verify `TELEGRAM_CHAT_ID` is correct
3. Check Telegram API isn't blocked in your region

### Issue: "DhanHQ connection failed"

**Solution:**
1. Verify `DHAN_API_KEY` is correct
2. Verify `DHAN_CLIENT_ID` is correct
3. Check DhanHQ API is online
4. Check practice mode is appropriate

### Issue: "dhanhq SDK is unavailable" / "TradingView websocket dependency is unavailable"

**Cause:** live scanner dependencies were not installed by the build.

**Solution:**
1. Confirm Build Command is `pip install -r requirements.txt` (it pins `dhanhq`, `pandas`, `websockets`)
2. Redeploy with "Clear build cache & deploy"
3. Check startup logs for `Market data dependency ... available` lines and `/health` → `market_scanner.dependencies`
4. Without `dhanhq`/`pandas` the scanner stays stopped (API keeps serving); without `websockets` it continues on DhanHQ candles
5. 10-minute breakouts use native TradingView 10-minute candles, or DhanHQ 5-minute candles aggregated into 10-minute candles (latest incomplete candle excluded)

### Issue: "No logs appearing"

**Solution:**
1. Check if service is actually running
2. Verify no startup errors in logs
3. Check `LOG_LEVEL` is not set to ERROR

---

## 📊 Deployment Checklist

```
✅ Pre-Deployment
  ├─ Phase 1 PR reviewed
  ├─ requirements.txt updated
  └─ All config files present

✅ Render Configuration
  ├─ Repository connected
  ├─ Build Command set
  ├─ Start Command UPDATED (python main.py)
  └─ Auto-Deploy enabled

✅ Environment Variables
  ├─ TELEGRAM_BOT_TOKEN set
  ├─ TELEGRAM_CHAT_ID set
  ├─ DHAN_API_KEY set
  ├─ DHAN_CLIENT_ID set
  └─ Other vars set (PRACTICE_MODE, etc.)

✅ Deployment
  ├─ Build succeeded
  ├─ Logs show successful startup
  ├─ /health endpoint responds
  ├─ /dhan/health endpoint responds
  └─ Telegram bot responds to messages

✅ Post-Deployment
  ├─ Logs being monitored
  ├─ Auto-deploy enabled
  ├─ Backup logs taken
  └─ Incident response plan ready
```

---

## 📞 Support

- Render Docs: https://render.com/docs
- DhanHQ Docs: https://github.com/Kalaiviswa/dhan-api-v2-docs
- Telegram Bot API: https://core.telegram.org/bots/api
- Python-Telegram-Bot: https://python-telegram-bot.readthedocs.io

---

## 🎉 Success!

If all steps complete successfully:
- ✅ Bot is live on Render
- ✅ Bot responds to Telegram messages
- ✅ DhanHQ connection active
- ✅ Auto-deploy configured
- ✅ Logs being collected

**Next Steps:**
1. Merge Phase 1.5 PR (Testing)
2. Merge Phase 2 PR (DhanHQ Integration)
3. Merge Phase 3 PR (Production Features)
4. Monitor deployment after each merge
