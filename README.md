# DhanHQ Trading Bot - Comprehensive README

**Production-Ready Automated Trading System** | Telegram + DhanHQ Integration | Real-time Position Monitoring

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9+-brightgreen.svg)](https://www.python.org/downloads/)
[![Status: Production Ready](https://img.shields.io/badge/Status-Production%20Ready-success.svg)]()

---

## Persistent PRACTICE portfolio (`main.py`)

The application runtime is **PRACTICE-only**, regardless of `PRACTICE_MODE` or
`AUTO_TRADING_ENABLED`. It never sends paper orders to Dhan. Existing broker
integration modules are not the paper execution path. Compact strategy alerts
remain on `service_alerts`; approvals, simulated fills, protection, and account
reports go to `trade_control`.

### Persistence and controls

- Set `PAPER_DB_PATH` (defaults to `TRADING_DB_PATH`, then `trading.db`) to a
  persistent, backed-up SQLite file on a mounted volume. An ephemeral filesystem
  is not restart-safe. Database failure stops startup instead of resetting an
  in-memory account. Do not delete the database to restart the bot.
- INR 500,000 is seeded once. Each position buys exactly **one metadata lot**,
  whose lot size is the number of units; premium and P&L multiply by units once.
  Dhan master tick sizes are converted from paise to INR once at ingestion.
  Missing/invalid lot or tick metadata, capital, quotes, or session eligibility
  cause rejection, not a made-up fill.
- Five open/reserved positions and twenty filled/reserved entries per IST day
  are global across segments. Pending approvals reserve cash and capacity.
  Reject/cancel/expiry releases reservations without consuming filled entries.
- Approval defaults ON. `PAPER_APPROVAL_EXPIRY_SECONDS=60` controls its bounded
  lifetime. Expired signals are never replayed merely because capacity returns.
- Daily P&L is equity minus day-opening equity, not lifetime realized P&L plus
  current unrealized P&L. Closing equity becomes the next opening equity; cash
  never resets. Paid premium is **premium committed**, not margin.
- Aggregate daily P&L at +INR 30,000 or -INR 10,000 latches an entry block,
  cancels pending entries, and requests all-segment square-off. Increasing limits
  does not clear today's latch. These thresholds trigger exits; they cannot
  guarantee a realized exit price.

### Quote and protection model

`PAPER_QUOTE_FRESHNESS_SECONDS=10` is the default maximum evidence age.
Executable quotes use option-contract bid/ask where available; otherwise they
explicitly use an **LTP-only simulation**, not actual broker fills. Request-start
time conservatively bounds snapshot age; LTP-only/partial-book quotes require
a usable fresh source last-trade timestamp. Cached quotes keep their original timestamps.
Missing, stale, nonpositive, nonfinite, or invalid evidence never fills.
Partial books also require fresh LTP evidence, because the missing execution
side would otherwise fall back to LTP.

BUY LIMIT is fillable only at a fresh ask/LTP at or below its limit (SELL uses
bid/LTP at or above). After five seconds an unfilled limit requires new quote
evidence and falls back with adverse 1% slippage: BUY ×1.01 rounded up to a tick,
SELL ×0.99 rounded down. MARKET uses the same adverse fresh-quote model. Marks
are valuation evidence, not executable prices. Requests share batching, a bounded
cache, pacing and HTTP 429 backoff; pacing is a local policy, not a claimed API
quota. There is no execution-speed guarantee.

Protection runs independently of signal arrival and approval state, including
startup recovery: equity options square off at 15:25 IST, commodities at 23:00
IST (earlier exchange closing takes precedence). Entry is blocked after cutoff.
An unresolved exit remains exposed and pending, blocks new exposure, alerts, and
retries; quote outages never become fictitious closed/no-overnight reports.

Long-option initial targets are entry +1R and +2R from valid entry-stop risk.
Target1 records one milestone, keeps the entire lot, and tightens SL to at least
entry. Target2 closes the entire lot. Trailing uses only the previous completed
option candle in the strategy timeframe; stops only tighten. A candle low
already breached triggers protection instead of installing an invalid stop.
Tick/candle processing checks account risk and time exits before strategy exits;
existing stop breaches take precedence over targets, then completed-candle
tightening is evaluated. Gaps execute at fresh executable quote evidence with
the documented model, not at an assumed stop/target price.

### Authorized Telegram webhook deployment

Configure **all** of:

- `BOT_TRADE_CONTROL_TOKEN` and `CHANNEL_TRADE_CONTROL_ID`;
- `PAPER_TELEGRAM_ALLOWED_USER_IDS`: comma-separated numeric Telegram user IDs;
- `PAPER_TELEGRAM_WEBHOOK_SECRET`: a random secret of at least 32 characters
  stored only in deployment secrets.

Controls deny by default, require both the allowlisted sender and the correct
control chat, and persist update/callback deduplication and actor audit. Never
expose the database or webhook secret in logs. Put the Flask app behind HTTPS
and a reverse proxy request-size limit (64 KiB is sufficient).

Register the **trade_control bot's** Telegram `setWebhook` with your HTTPS
`/telegram/paper` URL, `secret_token` equal to the configured webhook secret,
and `allowed_updates=["message","callback_query"]`. Telegram sends the
`X-Telegram-Bot-Api-Secret-Token` header; the endpoint validates it. Use your
deployment's secret-aware tooling for registration, not a committed token URL.
Do not run `getUpdates`/long polling or another webhook for this same bot.
The separate `service_alerts` bot remains outbound-only.

Run one application/quote-worker process per Dhan credential (`gunicorn --workers
1` without preloading; `deployment/startup.sh` defaults to one worker).
SQLite admission/deduplication remains process-safe, but
in-process quote coordination does not throttle independent replicas. All
controllers must point to the same database; a database file on separate replica
filesystems is not a shared account.

Pending-entry controls remain **Approve Limit**, **Approve Market**, **Modify**,
**Reject**. Open positions have **Exit Market**, **Exit Limit**, **Modify SL**,
**Modify Target 1/2**, **Trailing ON/OFF**, and **Refresh**. `/positions`,
`/portfolio`, or `/position <id>` recovers current controls after a restart.
Commands in the configured control chat:

```text
/mode approval on|off
/pending
/approve <request-id> [limit|market]
/modify <request-id> <limit-price> [stop-loss]
/reject <request-id>
/portfolio
/positions
/orders
/position <position-id>
/close <position-id> [market|limit <limit-price>]
/sl <position-id> [price]
/target1 <position-id> [price]
/target2 <position-id> [price]
/trailing <position-id> on|off
/input <edit-token> <price>
/confirm <edit-token>
/cancel <edit-token>
/help
/limits <profit-INR> <loss-INR>
/trades [YYYY-MM-DD]
```

Position edits and manual exits require an explicit confirmation preview;
`/close` starts that preview rather than immediately submitting an exit.
Price buttons start a keypad (digits, decimal point, backspace, Preview, Cancel)
and an optional ForceReply prompt. Replies and `/input` must come from the same
authorized actor in the same configured chat. Edit tokens persist in SQLite,
expire after five minutes, and are bound to the position's protection version.
A protection change, another confirmed edit, closure, or risk/cutoff exit
invalidates a stale preview. No quantity edits are supported.

In a **group/supergroup**, send commands/replies as your own allowlisted numeric
Telegram user, not anonymously as `sender_chat`. In a **channel**, posts do not
provide an authenticated user for commands or replies and are rejected; use the
inline keypad and confirmation buttons instead. Channel callbacks are checked
against the actual clicking user's numeric ID and the configured channel ID,
not the channel post's sender. Forwarded messages/callbacks are rejected.
No private-chat or linked-discussion-chat bypass is permitted. The bot needs
permission to post and edit its own messages in the control chat.

Manual SL may only tighten the effective stop, must be tick-aligned and below
fresh option evidence. Trailing OFF freezes existing protection; it does not
remove the stop. ON resumes monotonic completed-option-candle protection.
Custom T1/T2 values are durable and explicitly labeled; T1 remains a milestone
that raises protection to at least entry, and T2 exits the entire one lot.
An exit request is `#EXIT_PENDING`, not a fill; LIMIT exits retain the existing
five-second fresh-evidence/adverse-slippage fallback. Old keyboards are removed
on closure where Telegram allows; server-side validation always remains active.

Lifecycle messages use Telegram HTML bold and square emojis, not CSS:
🟨 **ENTRY FILLED** `#PAPER #ENTRY`, 🟨 **PENDING APPROVAL**
`#PAPER #APPROVAL`, 🟩 **EXIT PROFIT** `#PAPER #EXIT #PROFIT`,
🟥 **EXIT LOSS** `#PAPER #EXIT #LOSS`, or neutral **EXIT BREAKEVEN**
`#PAPER #EXIT #BREAKEVEN`. Exit classification uses authoritative realized
ledger INR rounded half-up to INR 0.01, not signal prices or the exit reason.
Points are exit minus entry; ledger INR applies units/multiplier exactly once.
Fees are not modeled and are explicitly excluded. Messages show IST timestamps,
contract identity, one-lot units, actual simulated prices and effective
protection. Changes use `#SL_UPDATE`, `#TARGET_UPDATE`, `#TRAIL_UPDATE`;
T1 uses `#TARGET1`, with stop/risk/cutoff reason tags on relevant exits.
`service_alerts` routing and formatting are unchanged.

Risk, cutoff, stop and emergency exits never wait for approval. Pending exits
are informational, not approval requests. Protection escalates a
pending manual LIMIT exit to MARKET when necessary. Reports include
account valuation, positions, capacity, milestones, exit reasons and the trade
log. Interim equity-segment and final commodity summaries are persisted once per
IST day, explicitly including unresolved exits. Orders/executions/audit retain
separate approval, order/fill, protection/fill, and Telegram delivery timings.
Delivery retries are at-least-once: a transport failure after Telegram accepts a
message can produce a duplicate notification, but cannot duplicate execution.
The application drains its persistent notification outbox on a separate thread,
so slow Telegram delivery does not hold up quote evaluation or cutoff recovery.

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
- ✅ Trade signal reception via Telegram (trade_control channel)
- ✅ Combined options screener alerts via the service_alerts channel
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
│  ├─ TRADE_CONTROL (Trades, orders, positions, system alerts)│
│  └─ SERVICE_ALERTS (Combined options screener alerts:       │
│      stock options, index options, commodity options)       │
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
ACCESS_TOKEN=your_dhanhq_jwt_token
ACCESS_TOKEN_EXPIRES_AT=2026-09-19T08:00:00+05:30
DHAN_CLIENT_ID=your_dhan_client_id
API_KEY=your_dhan_api_key
DHAN_PIN=your_dhan_pin
DHAN_TOTP_SECRET=your_totp_secret
DHAN_WEBHOOK_SECRET=your_random_webhook_secret

# ============================================================================
# TELEGRAM - EXACTLY TWO BOTS / CHANNELS
# ============================================================================
# 1) trade_control  - automated/paper trade approvals, order and position
#    updates, and every system/service/risk/token-renewal failure alert.
# 2) service_alerts - combined strategy screener channel for the only three
#    supported segments: NIFTY50 stock options, index options
#    (NIFTY/BANKNIFTY/SENSEX) and commodity options (GOLD/SILVER/CRUDE/
#    NATURALGAS/MCX).
#
# The "service_alerts" key and its env names are intentionally retained for
# deployment compatibility even though that channel now carries screener alerts.
# Crypto, NIFTY50 intraday 5X and NIFTY50 pay-later alerts are retired: do not
# configure bots or channels for them.
TELEGRAM_BOT_TOKEN=your_default_telegram_bot_token
TELEGRAM_CHAT_ID=your_default_telegram_chat_id

BOT_TRADE_CONTROL_TOKEN=your_trade_control_bot_token
CHANNEL_TRADE_CONTROL_ID=your_trade_control_channel_id

BOT_SERVICE_ALERTS_TOKEN=your_options_screener_bot_token
CHANNEL_SERVICE_ALERTS_ID=your_options_screener_channel_id

TELEGRAM_TEST_SECRET=your_private_telegram_test_secret

# ============================================================================
# TRADING PARAMETERS
# ============================================================================
PRACTICE_MODE=true                # Keep true for safe practice trading
AUTO_TRADING_ENABLED=false        # Must also be true before any live order API call
ENABLE_MARKET_SCANNER=false       # Enable only after market-data config is verified
ENABLE_DHAN_TOKEN_RENEWAL=true    # Runtime-only renewal; secrets still live in Render
MAX_LOSS_PER_TRADE=500            # Maximum loss per trade (₹)
MAX_POSITION_SIZE=5               # Maximum position size (lots)
MIN_RR_RATIO=1.0                  # Minimum risk-reward ratio

# ============================================================================
# SERVER CONFIGURATION
# ============================================================================
FLASK_ENV=production
PORT=5000
SERVER_HOST=0.0.0.0
WEBHOOK_SECRET=your_random_webhook_secret

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
TRADING_DB_PATH=/var/data/trading.db
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

### **Two Bots: Trade Control + Options Screener**

✅ **`trade_control` bot (`BOT_TRADE_CONTROL_TOKEN` / `CHANNEL_TRADE_CONTROL_ID`):**
- Trade signal reception and confirmations
- Order and position lifecycle updates
- SL/Target hit alerts and P&L notifications
- System, service, risk and token-renewal failure alerts

✅ **`service_alerts` bot (`BOT_SERVICE_ALERTS_TOKEN` / `CHANNEL_SERVICE_ALERTS_ID`):**
- Combined strategy screener alerts for exactly three segments:
  NIFTY50 stock options, index options and commodity options
  All three use a 10-minute breakout; NIFTY50 stock spot scans feeding stock-option alerts use 10-minute candles too.

❌ **Retired:** crypto, NIFTY50 intraday 5X and NIFTY50 pay-later alerts are no
longer generated or routed, so no bots/channels are needed for them.

Both routes can be verified with `POST /telegram/test`
(`{"channel": "trade_control"}` or `{"channel": "service_alerts"}`); any other
channel is rejected with HTTP 400. If `channel` is omitted, the probe defaults to
`trade_control`.

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

### **Live Market Scanner: Runtime Dependencies & 10-Minute Candles**

The live screener (`ENABLE_MARKET_SCANNER=true`, requires `ACCESS_TOKEN` and `API_KEY`)
needs these packages, all pinned in `requirements.txt` (Render installs only that file):

| Package | Used for | If missing |
|---|---|---|
| `dhanhq` | Security master lookup (`DhanContext`, `fetch_security_list`) + DhanHQ candles | Scanner **does not start** (API keeps running); logs `Market scanner cannot start: missing dhanhq` |
| `pandas` | Security master DataFrame returned by `dhanhq` | Scanner **does not start** |
| `websockets` | TradingView websocket candles (primary source) | Warning logged once; scanner continues on DhanHQ candles |

At startup the scanner logs one `Market data dependency ...` line per package, and
`/health` → `market_scanner.dependencies` shows the detected status.

The active commodity, index and NIFTY50 stock-option categories evaluate the
**10-minute option-premium breakout strategy**. Underlying spot data is used only
to select real listed strikes; strategy indicators are never calculated from spot:

- **TradingView** serves native 10-minute candles.
- **DhanHQ** intraday only offers 1/5/15/25/60-minute candles, so the bot fetches
  **5-minute candles and aggregates them into 10-minute OHLCV candles**
  (open = first open, high = max high, low = min low, close = last close,
  volume = sum). Buckets are aligned in IST to the session start (NSE/BSE 09:15,
  MCX 09:00), and the still-forming latest bucket is excluded so partial candles
  cannot trigger breakouts. This applies to listed ITM+1 option premium candles and to
  the DhanHQ fallback for underlying/spot candles.

### Option-premium strategy entry rules

- Only real listed **ITM+1 CE and PE** contracts are fetched for strategy entries.
  CE selects the next lower listed strike from nearest-to-spot ATM; PE selects
  the next higher listed strike. ATM/OTM contracts are not scanned, alerted, or
  sent for approval. If ITM+1 is unavailable, that side is skipped without a
  synthetic or ATM/OTM fallback. Existing paper positions and pending orders in
  any strike band continue to receive independent quote, risk, stop, and target
  protection.
- The base strategy remains the most recent strictly red option-premium candle
  (`close < open`) among the last 20 completed 10-minute candles. A later candle
  high or fresh live premium must be **strictly above** its high. Entry is the red
  high, stop is 5% below the red low, and the existing 2R target/trailing and
  paper approval flow remain unchanged.
- A CE/PE entry is eligible only when **all five** same-contract premium checks
  pass: premium > same-session VWAP; Wilder RSI(14) > 30 and > its preceding
  completed observation; premium > EMA(9); MACD(12,26) line > EMA(9) signal;
  PSAR(0.02, max 0.2) < premium. Equality fails; none of these conditions is
  optional. Failing filters do not consume the red reference; a later live
  crossing can qualify if the price condition and all indicators still pass.
- EMA/MACD use SMA seeding; Wilder RSI seeds from 14 changes (flat=50,
  gain-only=100, loss-only=0). At least 34 valid, ordered 10-minute candles are
  required; prior-session candles warm the close-based indicators. VWAP resets by
  IST exchange session and uses completed current-session candles only, weighted
  by real volume at HLC3 (`(high + low + close) / 3`). Missing/invalid volume,
  zero session volume, stale evidence, or inadequate warmup means no signal.
- The live quote feed provides timestamped LTP, not forming OHLCV/volume. Live
  close-based checks therefore use a provisional candle built from observed
  fresh LTP ticks; it is a snapshot, not a repeated appended bar. Live VWAP uses
  only completed real-volume evidence, and the alert shows the indicator-bar,
  fresh-LTP, and VWAP as-of times. This is a disclosed approximation, not
  inferred live cumulative volume. The service alert includes each value and
  pass mark:

  ```text
  🧭 PREMIUM CONFIRMATIONS
  ✓ VWAP: 120.50 / 110.25
  ✓ RSI14: 61.30 / 58.10
  ✓ EMA9: 120.50 / 118.40
  ✓ MACD: 2.50 / 1.80
  ✓ PSAR: 115.20 / 120.50
  Evidence: Live provisional close; VWAP uses completed volume
  Fresh option LTP as of 05-Oct-2026 10:35:03 IST
  VWAP volume as of 05-Oct-2026 10:25:00 IST
  ```
- Filters narrow signal eligibility; they are **not a guarantee of accuracy or
  profit**. Keep practice mode enabled and evaluate with paper trading/backtests.
  See [`docs/SCANNER_RUNBOOK.md`](docs/SCANNER_RUNBOOK.md) for detailed session,
  data-quality, deployment-verification, and sample-message guidance.

**Render operational notes:** after deploying, the logs should contain no
`dhanhq SDK is unavailable` / `TradingView websocket dependency is unavailable`
lines. If they appear, clear the build cache and redeploy (Manual Deploy →
"Clear build cache & deploy"). With the scanner enabled during market hours,
look for `✅ [GOLD] Got N CE candles`. Keep `PRACTICE_MODE=true` /
`AUTO_TRADING_ENABLED=false` while verifying.

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
**A:** ✅ **NO!** All trading signals, execution updates and system alerts go through the **trade_control** bot. The only other bot is **service_alerts**, which carries the combined options screener alerts (stock, index and commodity options).

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
