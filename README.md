# 🚀 Trading Bot Screener - Multi-Channel Alert System

**Production-Ready Algorithmic Trading Screener with Telegram Alerts**

A sophisticated, multi-channel trading bot that monitors 53 financial instruments across 6 dedicated Telegram channels using a 10-minute breakout strategy.

---

## 📋 Table of Contents

- [Features](#features)
- [Architecture](#architecture)
- [System Components](#system-components)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [Mobile Control](#mobile-control)
- [Trading Modes](#trading-modes)
- [Backup & Recovery](#backup--recovery)
- [Troubleshooting](#troubleshooting)
- [Support](#support)

---

## ✨ Features

### 🎯 Core Trading
- **10-Minute Breakout Strategy**: Detects RED→GREEN and GREEN→RED candle patterns
- **53 Monitored Symbols**: INDEX F&O, NIFTY 50 Stocks, Commodities, Crypto
- **Real-Time Scanning**: Every 10 seconds during market hours
- **IST Timezone Support**: India Standard Time (UTC+5:30) for accurate market detection

### 📱 Multi-Channel System
- **6 Trading Channels**: Each asset class gets dedicated alerts
  - 📊 INDEX OPTIONS (NIFTY, BANKNIFTY, SENSEX)
  - 📈 NIFTY 50 STOCKS OPTIONS (All 50 stocks)
  - ⚫ COMMODITY OPTIONS (GOLD, SILVER, CRUDE, GAS)
  - ⚡️ NIFTY 50 INTRADAY 5X (50 stocks margin trading)
  - 🏦 NIFTY 50 PAY LATER (50 stocks BNPL)
  - 💰 CRYPTO MARKET (BTC/USD, ETH/USD)
- **1 System Channel**: Daily health checks, errors, and mobile control

### 🧪 Trading Modes
- **Practice Mode**: Paper trading simulator (₹1,00,000 starting capital)
- **Auto-Trade Mode**: Real trading with DhanHQ integration (₹5,00,000 capital)
- **Risk Management**: 
  - 10% stop loss per trade
  - 30% profit target
  - Max 2% loss per trade
  - Max 5 open positions

### 📊 Analytics & Reporting
- **Live Statistics**: Scan count, signal count, success rate
- **Trade Reports**: Win rate, P&L, average win/loss
- **Performance Metrics**: Real-time dashboard via `/api/stats`
- **Historical Data**: CSV/JSON export capability

### 💾 Data Protection
- **Automated Backups**: Every 6 hours to cloud
- **Compression**: Gzip compression for space efficiency
- **Full Recovery**: One-click restore from any backup
- **Data Retention**: Last 10 backups retained automatically

### 📱 Mobile Control
- **Telegram Commands**: Full screener control from phone
- **Real-Time Status**: Live market and bot status
- **Start/Stop/Restart**: Control screener remotely
- **Emergency Controls**: Kill switch and panic mode

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    FLASK WEB SERVER                          │
│                    (screener_app.py)                         │
│  Health Check • API Endpoints • Status Dashboard            │
└──────────────────┬──────────────────────────────────────────┘
                   │
       ┌───────────┴───────────┐
       │                       │
       ▼                       ▼
┌─────────────────┐    ┌──────────────────┐
│ SCREENER LOOP   │    │ MOBILE CONTROL   │
│ (Background)    │    │ (Telegram Bot)   │
│ • 10s interval  │    │ • Commands       │
│ • IST timezone  │    │ • Status         │
│ • Real-time     │    │ • Control        │
└────────┬────────┘    └──────────────────┘
         │
    ┌────┴─────────────────────────────┐
    │                                  │
    ▼                                  ▼
┌──────────────────┐          ┌────────────────┐
│ DHAN HQ API      │          │ TELEGRAM BOTS  │
│ • Market Data    │          │ • 6 Channels   │
│ • 10-min candles │          │ • Alerts       │
│ • OHLC data      │          │ • System Ch    │
└──────────────────┘          └────────────────┘
    │
    ▼
┌──────────────────┐
│ STRATEGY ENGINE  │
│ • Breakout check │
│ • CALL/PUT detect│
│ • Signal routing │
└────────┬─────────┘
         │
    ┌────┴──────────────────────────────┐
    │                                   │
    ▼                                   ▼
┌──────────────────┐          ┌────────────────┐
│ PRACTICE MODE    │          │ AUTO-TRADE     │
│ • Paper trading  │          │ • Real orders  │
│ • No capital     │          │ • DhanHQ API   │
│ • Full reporting │          │ • Risk mgmt    │
└──────────────────┘          └────────────────┘
    │                              │
    └──────────┬────────────────────┘
               │
               ▼
        ┌─────────────────┐
        │ BACKUP MANAGER  │
        │ • Auto backups  │
        │ • Compression   │
        │ • Recovery      │
        └─────────────────┘
```

---

## 🛠️ System Components

### 1. **screener_app.py** - Flask Web Server
REST API endpoints for status, control, and health checks.

**Key Endpoints:**
- `GET /health` - Health check
- `GET /api/status` - Screener status
- `GET /api/stats` - Trading statistics

### 2. **screener_background.py** - Main Screener Engine
Runs in background thread, monitors 53 symbols, detects breakouts, routes signals.

**Key Features:**
- IST timezone support
- Real-time OHLC fetching from DhanHQ
- 10-minute breakout detection
- Multi-channel signal routing

### 3. **strategy.py** - Breakout Strategy
10-minute candle breakout analysis (RED→GREEN, GREEN→RED patterns).

### 4. **mobile_control.py** - Telegram Control Bot
Full control dashboard via Telegram commands.

### 5. **practice_trading.py** - Paper Trading
Simulates trades with ₹1,00,000 starting capital, full P&L tracking.

### 6. **automated_trading.py** - Real Trading
Executes real trades via DhanHQ API with strict risk management.

### 7. **backup_manager.py** - Data Protection
Automated backups with compression and recovery.

---

## 📦 Installation

### Prerequisites
- Python 3.8+
- Render account (for cloud hosting)
- DhanHQ trading account (for live trading)
- Telegram bot tokens (6 trading bots + 1 system bot)

### Step 1: Clone Repository
```bash
git clone https://github.com/RajaSailor/trading-bot.git
cd trading-bot
```

### Step 2: Install Dependencies
```bash
pip install -r requirements.txt
```

### Step 3: Configure Environment
Create `.env` file with your credentials.

### Step 4: Deploy to Render
```bash
git push  # Triggers auto-deployment
```

---

## ⚙️ Configuration

### Market Hours (IST)
```
NSE (Stocks & Index F&O): 9:15 AM - 3:30 PM
MCX (Commodities): 9:00 AM - 11:30 PM
```

### Strategy Parameters
```
Timeframe: 10-minute candles
Entry: Candle breakout (RED→GREEN or GREEN→RED)
Target: +30% gain
Stop Loss: -10% loss
Max Positions: 5
Max Loss Per Trade: 2%
```

---

## 🚀 Usage

### Start Screener
```bash
python screener_app.py
```

### Web Dashboard
```
http://localhost:10000/
http://localhost:10000/api/status
http://localhost:10000/api/stats
```

### Telegram Commands
```
/start - Show menu
/status - Current status
/stats - Trading statistics
/health - System health
/practice_on - Enable paper trading
/practice_off - Disable paper trading
/auto_on - Enable auto trading
/auto_off - Disable auto trading
/kill - Emergency stop
```

---

## 📱 Mobile Control

### Enable/Disable Modes
```
/practice_on - Enable paper trading (no real capital)
/practice_off - Disable paper trading
/auto_on - Enable auto trading (real capital!)
/auto_off - Disable auto trading
```

### Emergency Controls
```
/kill - Stop everything immediately
/panic - Full panic mode
/restart_screener - Soft restart
```

---

## 🧪 Trading Modes

### Practice Mode (🧪)
- Capital: ₹1,00,000 (simulated)
- Real Money: NO
- Orders Executed: NO
- Use Case: Test strategy, learn, verify performance

### Auto-Trade Mode (💵)
- Capital: ₹5,00,000 (real)
- Real Money: YES
- Orders Executed: YES via DhanHQ
- Use Case: Fully automated trading

### Manual Mode (📬)
- Alerts: Received via Telegram
- Execution: Manual via trading app
- Use Case: Alerts only, you execute trades

---

## 💾 Backup & Recovery

### Automatic Backups
- Every 6 hours automatically
- Includes: screener state, trades, logs, config, code
- Compressed with gzip
- Last 10 backups retained

### Manual Backup
```bash
/backup  # Via Telegram
```

### View Backups
```bash
python backup_manager.py list_backups()
```

### Restore from Backup
```bash
python backup_manager.py restore_from_backup('backup_name')
```

---

## 📊 API Reference

### Health Check
```bash
curl https://your-url/health
```

### Get Status
```bash
curl https://your-url/api/status
```

### Get Statistics
```bash
curl https://your-url/api/stats
```

---

## 🚨 Troubleshooting

### Issue: No Alerts Received
**Check:**
1. Market is open (9:15 AM - 3:30 PM IST)
2. All Telegram bots connected
3. Screener is running
4. DhanHQ API is connected

**Solution:**
```bash
/health  # Check system status
/status  # Check screener status
```

### Issue: Wrong Time (Not IST)
**Fixed!** Now uses `pytz` for IST timezone support.

### Issue: DhanHQ Connection Failed
**Check:**
```bash
/health
# Should show: "dhan_connected": true
```

---

## 📈 Performance Monitoring

### Daily Checks
Screener sends health check at 5:00 AM IST to system channel.

### Statistics
View real-time stats:
- `/stats` - Via Telegram
- `/api/stats` - Via HTTP

---

## 🔒 Security

### API Protection
- Chat ID verification on all endpoints
- Environment variables for secrets
- No sensitive data in logs

### Capital Protection
- 2% max loss per trade
- Max 5 open positions
- Strict position sizing
- Stop loss enforcement

### Data Security
- Automated backups every 6 hours
- Gzip compression (space efficient)
- Multiple backup copies retained

---

## 📞 Support

### Getting Help
1. Check logs: `/logs`
2. View status: `/status`
3. Run health check: `/health`
4. Check configuration: `/config`

### Emergency
- Kill switch: `/kill`
- Panic mode: `/panic`

---

## 📜 License

**Educational Purpose Only**

This software is provided as-is for educational purposes. Users assume all responsibility for capital loss, trading decisions, API charges, and system failures.

**Disclaimer:** This is NOT SEBI-registered investment advice. Paper trading only recommended for new users.

---

## 🎯 Quick Start

1. **Start Practice Mode**: Test strategy without risk
2. **Monitor Performance**: Check `/stats` daily
3. **Review Backups**: Ensure backups are working
4. **Optimize Strategy**: Adjust targets/stops based on results
5. **Go Live**: Enable auto-trade when confident

---

**Made with ❤️ by RajaSailor**

*Last Updated: 2026-09-04*
