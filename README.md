# DhanHQ Trading Bot - Comprehensive README

**Production-Ready Automated Trading System** | Telegram + DhanHQ Integration | Real-time Position Monitoring

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9+-brightgreen.svg)](https://www.python.org/downloads/)
[![Status: Production Ready](https://img.shields.io/badge/Status-Production%20Ready-success.svg)]()

---

## 📋 Quick Start

### 30-Second Setup

```bash
# Clone & setup
git clone https://github.com/RajaSailor/trading-bot.git
cd trading-bot

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Add your credentials to .env (see Configuration section)

# Run on Render or local
python main.py
```

**✅ Webhook URL:** `https://trading-bot-0p7j.onrender.com/dhan/postback`

**✅ Whitelisted IPs in DhanHQ:**
- `106.200.21.44` (Render server 1)
- `74.220.52.33` (Render server 2)

---

## 🎯 Features Overview

### **Telegram Integration**
- ✅ Trade signal reception via Telegram (TRADE_CONTROL channel)
- ✅ Real-time position monitoring
- ✅ P&L tracking & alerts
- ✅ SL/Target hit notifications
- ✅ Market close routine automation
- ✅ Status commands & health checks

### **DhanHQ Trading**
- ✅ Market order execution (Official DhanHQ v2 API)
- ✅ Position tracking (real-time P&L)
- ✅ SL/Target hit detection (BUY/SELL logic)
- ✅ Auto-close on triggers
- ✅ Risk management (max loss, position size limits)
- ✅ Account balance & margin monitoring

### **Production Ready**
- ✅ Webhook postback handling (HMAC-SHA256 signature verification)
- ✅ State persistence (JSON)
- ✅ Comprehensive error recovery
- ✅ Full logging & monitoring
- ✅ Docker deployment
- ✅ Supervisor/systemd process management
- ✅ Render cloud ready
- ✅ 20+ integration tests

---

## 📈 Monitoring & Analytics

- `monitoring/metrics.py` — trade/order/API/error/system metrics collection
- `monitoring/alerts.py` — critical/performance/connection/risk alert workflows
- `monitoring/dashboard.py` — Flask dashboard endpoints for metrics and P&L charts
- `monitoring/swagger_ui.py` — OpenAPI/Swagger UI bootstrap for API exploration
- `tools/performance_analyzer.py` — win rate, drawdown, sharpe and trade stats
- `tools/log_analyzer.py` — log error pattern and latency trend analysis

## 📚 Documentation Hub

- [Architecture](docs/ARCHITECTURE.md)
- [API Reference](docs/API_REFERENCE.md)
- [Trading Logic](docs/TRADING_LOGIC.md)
- [Deployment Guide](docs/DEPLOYMENT_GUIDE.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Contributing](docs/CONTRIBUTING.md)
- [OpenAPI Spec](docs/openapi.yaml)

---

## 📊 System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    TELEGRAM CHANNELS                         │
│  ├─ TRADE_CONTROL (Primary - Trade signals & control)      │
│  ├─ COMMODITY, INDEX, OPTIONS (Alert channels)             │
│  └─ SERVICE_ALERTS (System notifications)                  │
└─────────────────┬───────────────────────────────────────────┘
                  │
        ┌─────────▼──────────────────┐
        │  DhanTelegramBridge        │
        │  ├─ Signal Parser          │
        │  ├─ Commands (/positions)  │
        │  └─ Alerts Dispatcher      │
        └─────────┬──────────────────┘
                  │
     ┌────────────┴────────────┐
     │                         │
┌────▼──────┐          ┌──────▼─────┐
│  Order    │          │  Position  │
│  Manager  │          │  Tracker   │
│ (validate)│          │ (P&L, SL/T)│
└────┬──────┘          └──────┬─────┘
     │                        │
     └────────────┬───────────┘
                  │
          ┌───────▼────────┐
          │  DhanHQ API    │
          │  (Official v2) │
          │  ├─ Place Order│
          │  ├─ Get Pos    │
          │  └─ Account    │
          └───────┬────────┘
                  │
     ┌────────────┴────────────┐
     │                         │
┌────▼──────────┐    ┌────────▼───┐
│ Order Updates │    │ Postbacks   │
│ (Execution)   │    │ (Webhook)   │
└────┬──────────┘    └────────┬───┘
     │                        │
     └────────────┬───────────┘
                  │
          ┌───────▼────────────┐
          │  Telegram Alerts   │
          │  ├─ Confirmations  │
          │  ├─ SL/Target Hits │
          │  └─ P&L Updates    │
          └────────────────────┘
```

---

## 🚀 Installation & Setup

### Prerequisites

```bash
# System requirements
- Python 3.9 or higher
- 2GB RAM minimum
- 5GB disk space
- Static IP (whitelisted in DhanHQ)
  → 106.200.21.44 (Render server 1)
  → 74.220.52.33 (Render server 2)
```

### Step 1: Clone Repository

```bash
git clone https://github.com/RajaSailor/trading-bot.git
cd trading-bot
```

### Step 2: Create Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate  # Linux/Mac
# or
venv\Scripts\activate  # Windows
```

### Step 3: Install Dependencies

```bash
pip install -r requirements.txt
```

### Step 4: Configure Environment

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

```env
# ============================================================================
# DHANHQ TRADING (From your DhanHQ account)
# ============================================================================
ACCESS_TOKEN=eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJ1c2VyUmVnaW9uIjoiUjEiLCJpc3MiOiJkaGFuIiwicGFydG5lcklkIjoiIiwiZXhwIjoxNzg5MzE5MDY4LCJpYXQiOjE3ODkyMzI2NjgsInRva2VuQ29uc3VtZXJUeXBlIjoiU0VMRiIsIndlYmhvb2tVcmwiOiIiLCJkaGFuQ2xpZW50SWQiOiIxMTExNzc4NDQyIn0.tc6_YQVBnZeiep_Kg5js_bQjGNoiIsHX-e3V0LM2qGSlQtUhyWbqmyT5rgBpaZG9dmweD2dTXqe4E9PBTMINew
DHAN_CLIENT_ID=1111778442
API_KEY=f6c12cb2
DHAN_PIN=8144444
DHAN_TOTP_SECRET=APATWXGV4LDO2XBLEJQDEFTQKNSXMM3K
DHAN_WEBHOOK_SECRET=7kJ9mL2pQ5xR8tV1wY3nB6cD9hF2jG5kL8mN

# ============================================================================
# TELEGRAM - TRADE CONTROL CHANNEL (PRIMARY - For trading)
# ============================================================================
# Use ONE bot for all trading + alerts (see "Do I need separate bot?" FAQ)
TELEGRAM_BOT_TOKEN=8654404135:AAGHqdH81h1t1_RzjfqBSsbRk8O5l-ozRdc
TELEGRAM_CHAT_ID=-1003966854994
CHANNEL_TRADE_CONTROL_ID=-1003704821776

# ============================================================================
# TELEGRAM - ALERT CHANNELS (Optional - Secondary notification channels)
# ============================================================================
CHANNEL_COMMODITY_ID=-1004403277287
CHANNEL_CRYPTO_ID=-1004482078964
CHANNEL_INDEX_ID=-1003966854994
CHANNEL_NIFTY50_5X_ID=-1004466883026
CHANNEL_NIFTY50_OPTIONS_ID=-1003804613787
CHANNEL_NIFTY50_PAY_LATER_ID=-1003814243881
CHANNEL_SERVICE_ALERTS_ID=-1004402571102

# ============================================================================
# TRADING PARAMETERS
# ============================================================================
PRACTICE_MODE=true                # Set to false for REAL trading
MAX_LOSS_PER_TRADE=500           # Maximum loss per trade (₹)
MAX_POSITION_SIZE=5              # Maximum position size (lots)
MIN_RR_RATIO=1.0                 # Minimum risk-reward ratio

# ============================================================================
# SERVER CONFIGURATION
# ============================================================================
FLASK_ENV=production
PORT=5000
SERVER_HOST=0.0.0.0
WEBHOOK_SECRET=7kJ9mL2pQ5xR8tV1wY3nB6cD9hF2jG5kL8mN

# ============================================================================
# TIME & TIMEZONE
# ============================================================================
TIMEZONE=Asia/Kolkata
MARKET_START_TIME=09:00
MARKET_END_TIME=23:30

# ============================================================================
# STATE & PERSISTENCE
# ============================================================================
STATE_FILE=dhan_state.json
LOG_LEVEL=INFO
```

### Step 5: Test Connection

```bash
# Test DhanHQ API
python -c "
from dhan_api_client import DhanAPIClient
client = DhanAPIClient(
    access_token='YOUR_TOKEN',
    client_id='YOUR_CLIENT_ID'
)
success, msg, account = client.get_account_info()
print(f'DhanHQ: {\"✅\" if success else \"❌\"} {msg}')
"

# Test Telegram Bot
python -c "
from telegram import Bot
import asyncio

async def test():
    bot = Bot(token='YOUR_BOT_TOKEN')
    await bot.send_message(
        chat_id=YOUR_CHAT_ID,
        text='✅ Bot Connected!'
    )

asyncio.run(test())
"
```

---

## 📱 Telegram Trading Guide

### **Single Bot for All Trading & Alerts**

✅ **Use ONE Telegram Bot for:**
- Trade signal reception
- Trade confirmations
- Position status updates
- SL/Target hit alerts
- P&L notifications
- System health checks

**NO need for separate bot!** All alerts go through your TRADE_CONTROL_CHANNEL.

### **Commands**

```
/start          - Initialize bot
/status         - Show current trading status
/positions      - List open positions
/pnl            - Daily P&L summary
/close          - Close a position manually
/help           - Show help & signal format
```

### **Trade Signal Format**

Send trade signals in this format to the TRADE_CONTROL channel:

```
BUY NIFTY50 1 19400 SL:19300 TARGET:19600

SELL BANKNIFTY 2 45000 SL:45100 TARGET:44900

BUY GOLD 1 5500 SL:5450 TARGET:5650
```

**Format Breakdown:**
```
[TYPE] [SYMBOL] [QUANTITY] [ENTRY_PRICE] SL:[SL_PRICE] TARGET:[TARGET_PRICE]

TYPE: BUY or SELL
SYMBOL: NIFTY50, BANKNIFTY, GOLD, SILVER, CRUDE, BTC, ETH, etc.
QUANTITY: Number of lots
ENTRY_PRICE: Order entry price (₹)
SL:[PRICE]: Stop Loss price
TARGET:[PRICE]: Target price
```

### **Example Signals**

```
BUY NIFTY50 1 19400 SL:19300 TARGET:19600
SELL BANKNIFTY 1 45000 SL:45100 TARGET:44900
BUY GOLD 2 5500 SL:5450 TARGET:5650 support_bounce
SELL SILVER 1 68000 SL:68500 TARGET:67000 resistance_break
```

---

## 🎯 Trading Features

### **1. Order Management**

Orders are validated BEFORE placement:
```
✅ SL must be on correct side of entry (BUY: SL < Entry, SELL: SL > Entry)
✅ Target must be on correct side of entry (BUY: Target > Entry, SELL: Target < Entry)
✅ Position size limit enforcement (max 5 lots per trade)
✅ Maximum loss per trade validation (max ₹500 per trade)
✅ Risk-reward ratio validation (min 1:1)
```

### **2. Position Tracking (Real-time)**

```
✅ Current price updates (every 5 seconds)
✅ Live P&L calculation
✅ SL/Target hit detection (using market price comparison)
✅ Auto-close on triggers
✅ Position history tracking (dhan_state.json)
```

### **3. Auto-Close Logic**

```
Priority Order:
1. Target Hit → Close at target price ✅
2. SL Hit → Close at SL price 🚨
3. Manual Close → Close at specified price 📝
4. Market Close → Close all positions at market 🏁
```

### **4. Risk Management**

```python
MAX_LOSS_PER_TRADE = 500          # ₹500 max loss per trade
MAX_POSITION_SIZE = 5             # 5 lots max per trade
MIN_RR_RATIO = 1.0                # 1:1 risk-reward minimum

Example:
Entry: 19400
SL: 19300 (Loss: 100 per lot)
For 1 lot: Risk = ₹100
Max lots = 500 / 100 = 5 lots ✅
```

---

## 🔌 DhanHQ API Reference

### **Official DhanHQ v2 Documentation**

All endpoints follow official DhanHQ v2 API:
📖 https://github.com/Kalaiviswa/dhan-api-v2-docs

### **API Client Usage**

```python
from dhan_api_client import DhanAPIClient, ExchangeSegment, OrderType, TransactionType

# Initialize client
client = DhanAPIClient(
    access_token='YOUR_TOKEN',
    client_id='YOUR_CLIENT_ID',
    practice_mode=True  # Paper trading
)

# Place Market Order
success, msg, order_id = client.place_order(
    symbol="NIFTY50",
    exchange_segment=ExchangeSegment.NSE_FO,
    transaction_type=TransactionType.BUY,
    quantity=1,
    order_type=OrderType.MARKET,
    price=19400.0
)
# Returns: (True, "Order placed", "ORD_12345")

# Get Positions
success, msg, positions = client.get_positions()
# Returns: (True, "Positions fetched", [...])

# Get Account Info
success, msg, account = client.get_account_info()
# Returns: (True, "Account info fetched", {...})

# Get Funds/Margin
success, msg, funds = client.get_funds()
# Returns: (True, "Funds fetched", {...})
```

---

## 🌐 Webhook Postback Handling

### **Postback Events (Official Format)**

DhanHQ sends postbacks to: `https://trading-bot-0p7j.onrender.com/dhan/postback`

#### **1. ORDER_EXECUTION Postback**
```json
{
    "eventType": "ORDER_EXECUTION",
    "orderId": "ORD_12345",
    "symbol": "NIFTY50",
    "exchangeSegment": "NSE_FO",
    "orderStatus": "EXECUTED",
    "executedQuantity": 1,
    "executedPrice": 19400.0,
    "transactionType": "BUY",
    "timestamp": "2024-01-15T10:30:00Z"
}
```

#### **2. POSITION_UPDATE Postback**
```json
{
    "eventType": "POSITION_UPDATE",
    "positionId": "POS_12345",
    "symbol": "NIFTY50",
    "quantity": 1,
    "currentPrice": 19500.0,
    "entryPrice": 19400.0,
    "pnl": 100.0,
    "pnlPercentage": 0.52,
    "mtm": 100.0,
    "timestamp": "2024-01-15T10:35:00Z"
}
```

#### **3. ACCOUNT_UPDATE Postback**
```json
{
    "eventType": "ACCOUNT_UPDATE",
    "dhanClientId": "1111778442",
    "ledgerBalance": 100000.0,
    "marginAvailable": 80000.0,
    "marginUsed": 20000.0,
    "timestamp": "2024-01-15T10:40:00Z"
}
```

### **Signature Verification**

```python
# DhanHQ sends: X-Dhan-Signature header
# Algorithm: HMAC-SHA256
# Key: DHAN_WEBHOOK_SECRET from .env
# Message: Raw postback JSON body

import hmac
import hashlib

expected_sig = hmac.new(
    secret.encode(),
    payload.encode(),
    hashlib.sha256
).hexdigest()

assert expected_sig == received_signature  # ✅ Valid
```

---

## 📁 Project Structure

```
trading-bot/
│
├── PHASE 1: Core Telegram Bot (2,200 lines)
│   ├── telegram_bot.py              # Main bot handler
│   ├── alert_handler.py             # Alert routing
│   ├── state_manager.py             # State persistence
│   ├── message_formatter.py         # Message formatting
│   ├── bot_coordinator.py           # Bot coordination
│   ├── error_recovery.py            # Error handling
│   └── logging_setup.py             # Logging config
│
├── PHASE 1.5: Testing & Automation (830 lines)
│   ├── test_suite.py                # 12 comprehensive tests
│   ├── mock_webhooks.py             # Mock DhanHQ API
│   └── manual_test_scenarios.py     # Manual test cases
│
├── PHASE 2: DhanHQ Integration (1,420 lines)
│   ├── dhan_api_client.py           # Official DhanHQ API client
│   ├── dhan_order_manager.py        # Order validation & mgmt
│   ├── dhan_position_tracker.py     # Real-time position tracking
│   └── dhan_integration.py          # Master orchestrator
│
├── PHASE 3: Production Deployment (1,310 lines)
│   ├── dhan_postback_handler.py     # Webhook postback processor
│   ├── dhan_integration_tests.py    # 20+ integration tests
│   ├── dhan_telegram_bridge.py      # Telegram ↔ DhanHQ bridge
│   ├── dhan_deployment_guide.md     # Deployment guide
│   ├── Dockerfile                   # Docker image
│   ├── docker-compose.yml           # Docker compose
│   ├── requirements.txt             # Python dependencies
│   ├── .env.example                 # Environment template
│   ├── main.py                      # Application entry point
│   └── README.md                    # This file
│
└── Runtime Files (not committed)
    ├── dhan_state.json              # Position state
    ├── .env                         # Credentials
    └── logs/                        # Application logs
```

**Total: 19 Files | 5,760+ Lines | Production-Ready**

---

## 🐳 Docker Deployment

### **Build & Run with Docker**

```bash
# Build image
docker build -t trading-bot:latest .

# Run container
docker run -d \
  --name trading-bot \
  -p 5000:5000 \
  --env-file .env \
  trading-bot:latest

# Check logs
docker logs -f trading-bot

# Stop container
docker stop trading-bot
```

### **Docker Compose**

```bash
# Start all services
docker-compose up -d

# Stop services
docker-compose down

# View logs
docker-compose logs -f
```

---

## ☁️ Deploy on Render

### **Step 1: Connect GitHub Repository**

1. Push code to GitHub: `git push origin main`
2. Go to https://render.com
3. Click "New +" → "Web Service"
4. Connect your GitHub repository

### **Step 2: Configure Service**

```
Name: trading-bot
Environment: Python
Build Command: pip install -r requirements.txt
Start Command: python main.py
```

### **Step 3: Add Environment Variables**

In Render dashboard → Environment (add all from .env):

```
ACCESS_TOKEN=eyJ0eXA...
DHAN_CLIENT_ID=1111778442
TELEGRAM_BOT_TOKEN=8654404135:AAG...
DHAN_WEBHOOK_SECRET=7kJ9mL2pQ5xR8tV1wY3nB6cD9hF2jG5kL8mN
PRACTICE_MODE=true
# ... (add all from .env)
```

### **Step 4: Add Webhook to DhanHQ**

In DhanHQ account settings:

```
1. Go to Settings → Webhooks
2. Webhook URL: https://trading-bot-0p7j.onrender.com/dhan/postback
3. Webhook Secret: 7kJ9mL2pQ5xR8tV1wY3nB6cD9hF2jG5kL8mN
4. Enable Events: ✅ Order Execution, ✅ Position Update, ✅ Account Update
5. Save & Test
```

### **Step 5: Whitelist IPs in DhanHQ**

In DhanHQ account settings → API → IP Whitelist:

```
106.200.21.44    (Render server 1) ✅
74.220.52.33     (Render server 2) ✅
```

---

## 🔒 Security Checklist

```
✅ Use environment variables for all secrets
✅ Never commit .env file to git
✅ Enable HTTPS only (Render handles this)
✅ Verify webhook signatures (HMAC-SHA256)
✅ Whitelist IP addresses in DhanHQ
✅ Use strong webhook secret (32+ chars)
✅ Rotate tokens regularly
✅ Monitor API usage & costs
✅ Enable logging & alerting
✅ Regular backups of state.json
✅ Use .env.example (no secrets)
```

---

## 📊 Monitoring & Logs

### **View Live Logs**

```bash
# Local
tail -f logs/trading-bot.log

# Render
render logs trading-bot

# Docker
docker logs -f trading-bot
```

### **Health Check**

```bash
# Webhook health
curl https://trading-bot-0p7j.onrender.com/dhan/health
# Response: {"status": "healthy", "timestamp": "..."}
```

### **Monitor Positions**

```python
from dhan_integration import get_dhan_integration

dhan = get_dhan_integration()
dhan.print_summary()

# Output:
# ======================================================
# DHAN INTEGRATION SUMMARY
# ======================================================
# Open Positions: 2
# Closed Positions: 5
# Wins: 4 | Losses: 1
# Win Rate: 80.0%
# Daily P&L: ₹2,500.00
```

---

## 🧪 Testing

### **Run Integration Tests**

```bash
# Install test dependencies
pip install pytest pytest-asyncio

# Run all tests (20+ tests)
pytest dhan_integration_tests.py -v

# Run specific test
pytest dhan_integration_tests.py::TestOrderManager -v

# With coverage
pytest --cov=. dhan_integration_tests.py
```

### **Manual Testing**

See `manual_test_scenarios.py` for 12+ test cases including:
- Order placement
- Position tracking
- SL/Target detection
- Postback handling
- Telegram commands

---

## 🆘 Troubleshooting

### **Problem: Orders not being placed**

```bash
# Check 1: Verify practice mode
grep PRACTICE_MODE .env
# Should be: false (for real trading) or true (for paper)

# Check 2: Verify market hours
# DhanHQ trading: 9:15 AM - 3:30 PM IST (M-F)
# MCX trading: 9:00 AM - 11:30 PM

# Check 3: Check margin/funds
python -c "
from dhan_integration import get_dhan_integration
dhan = get_dhan_integration()
print(dhan.get_funds())
"

# Check 4: Check logs for errors
tail -f logs/trading-bot.log | grep ERROR
```

### **Problem: Webhook not receiving postbacks**

```bash
# Check 1: Verify IP whitelisting
# DhanHQ Settings → API → IP Whitelist
# Should include: 106.200.21.44 or 74.220.52.33

# Check 2: Verify webhook URL
# DhanHQ Settings → Webhooks
# Should be: https://trading-bot-0p7j.onrender.com/dhan/postback

# Check 3: Verify webhook secret matches
grep DHAN_WEBHOOK_SECRET .env
# Must match DhanHQ settings exactly

# Check 4: Test manually
curl -X POST http://localhost:5000/dhan/postback \
  -H "Content-Type: application/json" \
  -d '{"eventType": "ORDER_EXECUTION", "orderId": "TEST_001"}'
```

### **Problem: Telegram bot not responding**

```bash
# Check 1: Verify bot token is correct
python -c "
from telegram import Bot
import asyncio
async def test():
    bot = Bot(token='YOUR_BOT_TOKEN')
    me = await bot.get_me()
    print(f'Bot: {me.username}')
asyncio.run(test())
"

# Check 2: Verify chat ID is correct
# Format should be NEGATIVE for groups/channels
# e.g., -1003966854994 (NOT +1003966854994)

# Check 3: Check logs
tail -f logs/trading-bot.log | grep telegram
```

---

## 📈 Performance Tips

```
1. Monitor P&L regularly (/pnl command)
2. Start with PRACTICE_MODE=true for paper trading
3. Begin with small position sizes (1 lot)
4. Test SL/Target triggers thoroughly
5. Review closed positions daily
6. Adjust MAX_LOSS_PER_TRADE based on results
7. Keep emergency manual override ready (/kill)
8. Check logs for warnings
9. Verify postbacks are being received
10. Monitor account margin usage
```

---

## 🔄 Updating & Maintenance

```bash
# Pull latest code
git pull origin main

# Reinstall dependencies
pip install -r requirements.txt --upgrade

# Restart bot (Render)
render restart trading-bot

# Restart bot (Docker)
docker-compose restart

# Restart bot (Supervisor)
sudo supervisorctl restart dhan-bot:*
```

---

## ❓ FAQ

### **Q: Do I need a separate Telegram bot for DhanHQ trading?**
**A:** ✅ **NO!** Use your existing **TRADE_CONTROL_CHANNEL** bot. All trading signals, alerts, and status updates go through this single bot. No need for separate bots!

### **Q: Is my data safe on Render?**
**A:** ✅ Yes. Render uses encrypted storage. Keep your .env file secure and never share tokens.

### **Q: Can I run multiple instances?**
**A:** ❌ No. The system uses a single state file (dhan_state.json). Run only one instance per account.

### **Q: What happens if the bot crashes?**
**A:** ✅ State is automatically persisted to `dhan_state.json`. On restart, all positions are recovered.

### **Q: Can I run in PRACTICE_MODE?**
**A:** ✅ Yes! Set `PRACTICE_MODE=true` to paper trade without real money. Perfect for testing.

### **Q: What if DhanHQ is down?**
**A:** ✅ The bot has automatic retry logic (exponential backoff). Check DhanHQ status at https://status.dhan.co

### **Q: How do I know if webhook is working?**
**A:** Check `/dhan/health` endpoint or review logs. Should see postback entries.

### **Q: Can I trade multiple symbols?**
**A:** ✅ Yes! Send multiple signals via Telegram (one per message).

---

## 📞 Support

- **DhanHQ Support:** support@dhan.co
- **DhanHQ Community:** https://t.me/dhan_community
- **DhanHQ Docs:** https://github.com/Kalaiviswa/dhan-api-v2-docs
- **GitHub Issues:** [Create Issue](https://github.com/RajaSailor/trading-bot/issues)
- **Email:** er.bharathirajaathikkannan@gmail.com

---

## 📄 License

GNU General Public License v3.0 - See LICENSE file for details

This project is inspired by and integrates with:
- **DhanHQ Official API v2:** https://github.com/Kalaiviswa/dhan-api-v2-docs
- **Python Telegram Bot:** https://python-telegram-bot.org/
- **Flask Framework:** https://flask.palletsprojects.com/

---

## 🎉 What You Get

- ✅ **19 Production-Ready Files**
- ✅ **5,760+ Lines of Code**
- ✅ **Official DhanHQ v2 API Integration** (Reference: https://github.com/Kalaiviswa/dhan-api-v2-docs)
- ✅ **Real-time Position Tracking**
- ✅ **Telegram Trading Interface**
- ✅ **20+ Integration Tests**
- ✅ **Docker Deployment Ready**
- ✅ **Render Cloud Ready**
- ✅ **Supervisor Process Management**
- ✅ **Complete Documentation**
- ✅ **24/7 Monitoring & Alerts**

---

**🚀 Ready to Trade?**

1. Start with `PRACTICE_MODE=true` (paper trading)
2. Follow the setup guide above
3. Send test signals via Telegram
4. Monitor P&L with `/pnl` command
5. When confident, set `PRACTICE_MODE=false` for real trading

**⚠️ DISCLAIMER:** This is automated trading software. Only trade with capital you can afford to lose. Start small, test thoroughly, and monitor constantly.

---

**Made with ❤️ by RajaSailor**

*Last Updated: September 2026 | Status: ✅ Production Ready*
