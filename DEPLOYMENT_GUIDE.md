# 🚀 Trading Bot Deployment Guide

## ✅ PRE-DEPLOYMENT CHECKLIST

### 1. Environment Variables (Critical)
Ensure these are set in **Render Dashboard → Environment**:

```bash
# DhanHQ API Credentials
API_KEY=your_dhan_client_id
ACCESS_TOKEN=your_dhan_access_token

# TradingView Login
TV_USERNAME=Sailor_raja12390
TV_PASSWORD=your_tv_password

# Telegram Bot
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_chat_id (for index options)
TELEGRAM_CHAT_ID_COMMODITY=your_commodity_chat_id (for MCX/Gold)
TELEGRAM_CHAT_ID_CRYPTO=your_crypto_chat_id (for crypto)

# Security
WEBHOOK_SECRET=your_secret_for_webhook_auth
FLASK_ENV=production
ENABLE_UNAUTHENTICATED_WEBHOOK_ADMIN=false

# Server
PORT=5000
```

### 2. DhanHQ Setup
✅ API Key & Access Token available? (Check DhanHQ dashboard)
✅ Security IDs mapped correctly:
  - NIFTY: 13
  - BANKNIFTY: 25
  - GOLD: 565901
  - RELIANCE: 1333 (approx)

### 3. TradingView Setup
✅ Account credentials working?
✅ tvDatafeed library installed?

### 4. Telegram Setup
✅ Bot token created from @BotFather?
✅ Chat IDs obtained?
✅ Bot added to channels/groups?

---

## 🚢 DEPLOYMENT STEPS

### Step 1: Verify Code Quality
```bash
# Check for syntax errors
python -m py_compile data_manager.py
python -m py_compile dhan_client.py
python -m py_compile screener_app.py
python -m py_compile main_screener.py
```

### Step 2: Test Locally (if possible)
```bash
# Install dependencies
pip install -r requirements.txt

# Run screener in test mode
python screener_app.py

# Check logs for:
# ✅ DhanHQ client initialized successfully
# ✅ Market monitor started
# ✅ Flask app running on 0.0.0.0:5000
```

### Step 3: Deploy to Render
1. Go to **Render Dashboard** → **trading-bot** service
2. Click **Manual Deploy** button
3. Wait 30-40 seconds for deployment
4. Check deployment logs

### Step 4: Verify Live
After deployment, test these endpoints:

```bash
# Health Check
curl https://your-render-url/health

# API Status
curl https://your-render-url/api/status

# Market Status
curl https://your-render-url/api/market/status

# Test Webhook (with auth)
curl -X POST https://your-render-url/api/webhook/test \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: your_webhook_secret" \
  -d '{
    "ticker": "NIFTY",
    "signal_type": "CALL",
    "entry_price": 18220,
    "stop_loss": 18180,
    "target_1": 18230,
    "target_2": 18240,
    "target_3": 18250,
    "timeframe": "5-MIN",
    "category": "INDEX_OPTIONS"
  }'
```

---

## 📊 EXPECTED LOGS (After Deployment)

```
🚀 TRADING BOT SCREENER APP STARTING
✅ Market monitor started
🟢 Market opened - Starting screener
✅ DhanHQ client initialized successfully
✅ DhanHQ fetch successful for NIFTY: 12 candles (5min)
✅ DhanHQ fetch successful for GOLD: 8 candles (5min)
Screener running - 0 alerts triggered
...
Signal detected! → Sending Telegram alert
Position recorded: NIFTY 18220 (SL: 18180, T1: 18230)
```

---

## 🔧 TROUBLESHOOTING

### Issue: 400 Bad Request from DhanHQ
**Cause:** Wrong `instrument_type` parameter
**Solution:** Already fixed in `data_manager.py`
- NIFTY: `instrument_type="FUT"`
- GOLD: `instrument_type="FUT"`
- RELIANCE: `instrument_type="EQUITY"`

### Issue: No Alerts Received
**Check:**
1. Market status: Is market open? `/api/market/status`
2. Screener running? `/api/status` → `running: true`
3. TradingView webhook connected?
4. Telegram bot token valid?

### Issue: "DhanHQ client not initialized"
**Cause:** Missing `ACCESS_TOKEN` environment variable
**Solution:** 
1. Go to Render dashboard
2. Environment tab
3. Add `ACCESS_TOKEN=your_token`
4. Redeploy

### Issue: Position Management Issues
**Check:**
1. Max positions limit (5): `/api/positions`
2. Position manager state: `screener_state.json`
3. Telegram alerts received?

---

## 📈 LIVE TRADING WORKFLOW

```
1. Market Opens (09:15 IST)
   ↓
2. Market Monitor starts screener
   ↓
3. Screeners run continuously (5, 15, 30 min)
   ↓
4. Breakout Detected!
   ↓
5. Telegram Alert Sent
   Entry: 18220 | SL: 18180 | T1: 18230 | T2: 18240 | T3: 18250
   ↓
6. Position Recorded (max 5 open)
   ↓
7. Monitor P&L (use `/api/positions`)
   ↓
8. Market Closes (15:30 IST)
   ↓
9. Screener stops automatically
```

---

## 📊 SUPPORTED MARKETS

### NSE Equity Options
- Symbols: NIFTY_50 stocks
- Data: DhanHQ (primary), TradingView (fallback)
- Timeframes: 5-min, 15-min, 30-min

### NSE Futures & Options
- Symbols: NIFTY, BANKNIFTY
- Data: DhanHQ
- Timeframes: 5-min, 15-min, 30-min

### MCX Commodities (🆕)
- Symbols: GOLD, SILVER, CRUDE OIL, NATURAL GAS
- Data: DhanHQ
- Timeframes: 5-min, 15-min, 30-min
- Status: ✅ LIVE on DhanHQ only

### Crypto (TradingView)
- Symbols: BTC/USD, ETH/USD
- Data: TradingView
- Timeframes: 5-min, 15-min, 30-min

---

## 🎯 API ENDPOINTS

### Status & Monitoring
- `GET /health` - Health check
- `GET /api/status` - Screener status
- `GET /api/stats` - Statistics
- `GET /api/positions` - Open positions
- `GET /api/alerts` - Alert history
- `GET /api/market/status` - Market open/closed

### Control
- `POST /api/control/start` - Start screener
- `POST /api/control/stop` - Stop screener
- `POST /api/control/pause` - Pause screener
- `POST /api/control/resume` - Resume screener

### Webhooks
- `POST /webhook/tradingview` - TradingView alerts
- `POST /api/webhook/test` - Test webhook
- `GET /api/webhook/history` - Webhook history
- `GET /health/webhook` - Webhook health

---

## 🆘 EMERGENCY STOP

If something goes wrong:

```bash
# Stop screener via API
curl -X POST https://your-render-url/api/control/stop

# Or exit all positions
curl https://your-render-url/api/control/stop

# Check logs in Render dashboard
```

---

## 📞 SUPPORT

Check these logs for issues:
1. **Render Dashboard** → Logs tab
2. **Local logs**: `screener_state.json`
3. **API responses**: Check status endpoints

---

**DEPLOYMENT READY!** ✅ 🚀

