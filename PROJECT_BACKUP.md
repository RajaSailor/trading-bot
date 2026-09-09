# Trading Bot - Complete Project Backup & Reference Guide

**Last Updated:** 2026-09-09 23:30 IST  
**Project Status:** ✅ LIVE - Commodity Alerts Working, NSE/BSE Pending  
**Author:** RajaSailor with GitHub Copilot

---

## 📋 TABLE OF CONTENTS

1. [Project Overview](#project-overview)
2. [Architecture](#architecture)
3. [Current Status](#current-status)
4. [Strategy Logic](#strategy-logic)
5. [57 Instruments Covered](#57-instruments-covered)
6. [Configuration](#configuration)
7. [Deployment Details](#deployment-details)
8. [Alert Format](#alert-format)
9. [Troubleshooting](#troubleshooting)
10. [Important Contacts & Resources](#important-contacts--resources)

---

## PROJECT OVERVIEW

### What This Bot Does

A **multi-channel algorithmic trading bot** that:
- Monitors **57 trading instruments** across 6 markets
- Analyzes **ATM option premiums** as price charts (not underlying price)
- Detects **RED/GREEN candle breakouts** in premiums
- Sends **real-time trading alerts** to 6 separate Telegram channels
- Supports **options & spot trading** across NSE, BSE, MCX, and Crypto

### Key Innovation

**Trades option premiums, not underlying prices:**
```
Traditional: Monitor GOLD price → Detect breakout → Trade
Our Bot: Monitor GOLD ATM CE/PE premium → Detect premium breakout → Trade
```

---

## ARCHITECTURE

### Core Components

```
┌─────────────────────────────────────────────────────────────┐
│                    TRADING BOT SUITE                         │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌──────────────────┐      ┌──────────────────┐             │
│  │  Data Manager    │      │  ATM Options     │             │
│  │  (DhanHQ API)    │      │  Fetcher         │             │
│  └──────────────────┘      └──────────────────┘             │
│           ▼                        ▼                          │
│  ┌──────────────────────────────────────────┐              │
│  │  Premium Strategy Engine                 │              │
│  │  - RED/GREEN candle detection            │              │
│  │  - Breakout signal generation            │              │
│  └──────────────────────────────────────────┘              │
│           ▼                                                  │
│  ┌──────────────────────────────────────────┐              │
│  │  Premium Screener                        │              │
│  │  - 10-min (Commodities, Indices)         │              │
│  │  - 15-min (Nifty50, Crypto)              │              │
│  └──────────────────────────────────────────┘              │
│           ▼                                                  │
│  ┌──────────────────────────────────────────┐              │
│  │  Telegram Handler (Multi-Bot)            │              │
│  │  - 6 separate bots (1 per channel)       │              │
│  │  - Route alerts to correct channels      │              │
│  └──────────────────────────────────────────┘              │
│           ▼                                                  │
│  ┌──────────────────────────────────────────┐              │
│  │  6 Telegram Channels                     │              │
│  │  - INDEX_OPTIONS, NIFTY50_OPTIONS        │              │
│  │  - NIFTY50_5X, NIFTY50_PAY_LATER         │              │
│  │  - COMMODITY_OPTIONS, CRYPTO             │              │
│  └──────────────────────────────────────────┘              │
└─────────────────────────────────────────────────────────────┘
```

### Key Files

| File | Purpose |
|------|---------|
| `main_screener.py` | Main orchestrator, runs all screeners |
| `screener_premium.py` | Scans all 57 instruments for premium breakouts |
| `premium_strategy_engine.py` | Detects RED/GREEN breakouts in premiums |
| `atm_options_fetcher.py` | Fetches ATM option premium candles from DhanHQ |
| `telegram_handler.py` | Routes alerts to 6 Telegram channels |
| `data_manager.py` | Handles DhanHQ API calls |
| `position_manager.py` | Tracks open positions |
| `atm_calculator.py` | Calculates ATM strikes |

---

## CURRENT STATUS

### ✅ WORKING

```
🔹 Commodities (MCX) - 10-min candles
  ✅ GOLD - Alerts flowing
  ✅ CRUDE OIL - Alerts flowing
  ✅ SILVER - Ready
  ✅ NATURALGAS - Alerts flowing
  
  Market Hours: 9:00 AM - 11:30 PM IST
  Scan Interval: Every 10 seconds
  Channel: COMMODITY OPTIONS (-1004403277287)
  
  Example Alert:
  📉 PUT ENTRY
  NATURALGAS | 10-MINUTE BREAKOUT (PE Premium)
  Entry: 2.35, SL: 2.25, T1: 12.35, T2: 22.35, T3: 32.35
```

### ⏳ PENDING (Starting 9:15 AM IST Tomorrow)

```
🔹 NSE Indices - 10-min candles
  ⏳ NIFTY
  ⏳ BANKNIFTY
  ⏳ SENSEX
  
  Market Hours: 9:15 AM - 3:39 PM IST
  Scan Interval: Every 10 seconds
  Channel: INDEX OPTIONS (-1003966854994)

🔹 NSE Nifty50 Stocks - 15-min candles
  ⏳ All 50 stocks (ADANI, AXIS, HDFC, etc.)
  
  Market Hours: 9:15 AM - 3:39 PM IST
  Scan Interval: Every 15 seconds
  Channels: 
    - NIFTY50 OPTIONS (-1003804613787)
    - NIFTY50 5X (-1004466883026)
    - NIFTY50 PAY LATER (-1003814243881)

🔹 Crypto Spot - 15-min candles
  ⏳ BTC/USD
  ⏳ ETH/USD
  
  Market Hours: 24/7 (5:10 AM - 11:45 PM IST priority)
  Scan Interval: Every 15 seconds
  Channel: CRYPTO (-1004482078964)
```

---

## STRATEGY LOGIC

### The Breakout Algorithm

**Fundamental Principle:** Track ATM option premiums as price charts

#### Step 1: Fetch ATM Premiums
```python
For each instrument (e.g., GOLD):
  1. Identify current ATM strike (e.g., 127)
  2. Fetch GOLD-24OCT-127-CE premium candles (10-min)
  3. Fetch GOLD-24OCT-127-PE premium candles (10-min)
```

#### Step 2: Detect RED Candle
```python
Premium candle analysis:
  RED candle = close < open (down candle)
  
Example for GOLD CE:
  15:15 candle: O=120, H=127, L=113, C=120 (RED)
  
Find last RED in last 7 candles
```

#### Step 3: Detect Breakout
```python
After RED candle found, check ANY upcoming candle:
  
For CALL (Premium breaks ABOVE RED high):
  15:15 RED: H=127
  15:25 candle: H=135 > 127? YES! ✅ BREAKOUT
  
For PUT (Premium breaks ABOVE RED high):
  [Same logic applies]
```

#### Step 4: Generate Signal
```python
CALL Signal:
  Entry = RED candle high (127)
  SL = RED candle low (113)
  T1 = 127 + 10 = 137
  T2 = 127 + 20 = 147
  T3 = 127 + 30 = 157
```

#### Step 5: Send Alert
```python
Route to correct Telegram channel based on category:
  - COMMODITY_OPTIONS for GOLD/CRUDE/SILVER/GAS
  - INDEX_OPTIONS for NIFTY/BANKNIFTY/SENSEX
  - NIFTY50_STOCK_OPTIONS for 50 stocks
  - CRYPTO for BTC/ETH
```

---

## 57 INSTRUMENTS COVERED

### Commodities (MCX) - 4 instruments
```
1. GOLD - 10-min → COMMODITY channel
2. SILVER - 10-min → COMMODITY channel
3. CRUDE OIL - 10-min → COMMODITY channel
4. NATURAL GAS - 10-min → COMMODITY channel
```

### Indices (NFO) - 3 instruments
```
5. NIFTY - 10-min → INDEX channel
6. BANKNIFTY - 10-min → INDEX channel
7. SENSEX - 10-min → INDEX channel
```

### Nifty50 Stocks (NFO) - 50 instruments
```
8. Adani Enterprises → NIFTY50_OPTIONS channel
9. Adani Ports & SEZ → NIFTY50_OPTIONS channel
10. Apollo Hospitals → NIFTY50_OPTIONS channel
11. Asian Paints → NIFTY50_OPTIONS channel
12. Axis Bank → NIFTY50_OPTIONS channel
13. Bajaj Auto → NIFTY50_OPTIONS channel
14. Bajaj Finance → NIFTY50_OPTIONS channel
15. Bajaj Finserv → NIFTY50_OPTIONS channel
16. Bharat Electronics → NIFTY50_OPTIONS channel
17. Bharti Airtel → NIFTY50_OPTIONS channel
18. Cipla → NIFTY50_OPTIONS channel
19. Coal India → NIFTY50_OPTIONS channel
20. Dr Reddys Laboratories → NIFTY50_OPTIONS channel
21. Eicher Motors → NIFTY50_OPTIONS channel
22. Eternal → NIFTY50_OPTIONS channel
23. Grasim Industries → NIFTY50_OPTIONS channel
24. HCL Technologies → NIFTY50_OPTIONS channel
25. HDFC Bank → NIFTY50_OPTIONS channel
26. HDFC Life Insurance → NIFTY50_OPTIONS channel
27. Hindalco Industries → NIFTY50_OPTIONS channel
28. Hindustan Unilever → NIFTY50_OPTIONS channel
29. ICICI Bank → NIFTY50_OPTIONS channel
30. ITC → NIFTY50_OPTIONS channel
31. Infosys → NIFTY50_OPTIONS channel
32. Interglobe Aviation → NIFTY50_OPTIONS channel
33. JSW Steel → NIFTY50_OPTIONS channel
34. Jio Financial Services → NIFTY50_OPTIONS channel
35. Kotak Bank → NIFTY50_OPTIONS channel
36. Larsen & Toubro → NIFTY50_OPTIONS channel
37. Mahindra & Mahindra → NIFTY50_OPTIONS channel
38. Maruti Suzuki → NIFTY50_OPTIONS channel
39. Max Healthcare Institute → NIFTY50_OPTIONS channel
40. NTPC → NIFTY50_OPTIONS channel
41. Nestle → NIFTY50_OPTIONS channel
42. Oil & Natural Gas Corporation → NIFTY50_OPTIONS channel
43. Power Grid Corporation of India → NIFTY50_OPTIONS channel
44. Reliance Industries → NIFTY50_OPTIONS channel
45. SBI Life Insurance → NIFTY50_OPTIONS channel
46. Shriram Finance → NIFTY50_OPTIONS channel
47. State Bank of India → NIFTY50_OPTIONS channel
48. Sun Pharmaceutical → NIFTY50_OPTIONS channel
49. Tata Consultancy Services → NIFTY50_OPTIONS channel
50. Tata Consumer Products → NIFTY50_OPTIONS channel
51. Tata Motors Passenger Vehicles → NIFTY50_OPTIONS channel
52. Tata Steel → NIFTY50_OPTIONS channel
53. Tech Mahindra → NIFTY50_OPTIONS channel
54. Titan → NIFTY50_OPTIONS channel
55. Trent → NIFTY50_OPTIONS channel
56. UltraTech Cement → NIFTY50_OPTIONS channel
57. Wipro → NIFTY50_OPTIONS channel
```

### Crypto (Spot) - 2 instruments
```
58. BTC/USD → CRYPTO channel
59. ETH/USD → CRYPTO channel
```

---

## CONFIGURATION

### Environment Variables (.env)

```env
# DhanHQ API Credentials
API_KEY=f6c12cb2
ACCESS_TOKEN=eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9...

# System Alerts Bot (Default)
TELEGRAM_TOKEN=8654404135:AAGHqdH81h1t1_RzjfqBSsbRk8O5l-ozRdc
CHAT_ID=-1004321977761

# Trading Bots (6 separate bots per channel)
BOT_INDEX_TOKEN=8601160697:AAFFxscCMfqcrXaf1lw69xK7Ue-RW_8aIzI
CHANNEL_INDEX_ID=-1003966854994

BOT_NIFTY50_OPTIONS_TOKEN=8746059399:AAGfpg6rQfluICaezqiamCujN8_NcXbt1NQ
CHANNEL_NIFTY50_OPTIONS_ID=-1003804613787

BOT_COMMODITY_TOKEN=8762956800:AAEkQZfYhawfxQEua8OSYcnp3FPRU2xywsc
CHANNEL_COMMODITY_ID=-1004403277287

BOT_NIFTY50_5X_TOKEN=8265739611:AAFbraUdEY01eJOel76S8mMgBiZT4otxkd4
CHANNEL_NIFTY50_5X_ID=-1004466883026

BOT_NIFTY50_PAY_LATER_TOKEN=8934391945:AAEdycuHV7sZP6eASCU2j7kQ9SBG7e9D4Q0
CHANNEL_NIFTY50_PAY_LATER_ID=-1003814243881

BOT_CRYPTO_TOKEN=8921592389:AAF7IKqXz2a7yp0a--m0vP21itKHVKqF-7k
CHANNEL_CRYPTO_ID=-1004482078964

# Market Config
MARKET_TIMEZONE=Asia/Kolkata
NSE_MARKET_OPEN=09:15
NSE_MARKET_CLOSE=15:30
MCX_MARKET_OPEN=09:00
MCX_MARKET_CLOSE=23:30
SCAN_INTERVAL=5

# Project Config
PROJECT_NAME=trading-bot-suite-multi-channel
PROJECT_VERSION=2.0.0
PROJECT_ENV=production
DEPLOYMENT_PLATFORM=render
STRATEGY=ATM-PREMIUM-BREAKOUT
```

---

## DEPLOYMENT DETAILS

### Where Bot Runs

- **Platform:** Render.com (Cloud)
- **Repository:** github.com/RajaSailor/trading-bot
- **Branch:** main
- **Status:** Continuous (Always running)

### How to Access Logs

1. Go to https://dashboard.render.com
2. Select "trading-bot" project
3. Go to "Logs" tab
4. Search for timestamps or keywords

### How to Restart Bot

1. Go to Render dashboard
2. Select "trading-bot"
3. Click "Reboot" button
4. Bot restarts automatically

### Monitor Health

Check logs for patterns:
```
✅ "premium alerts: X" = Bot scanning and finding signals
❌ "DhanHQ API error" = API connection issue
⚠️ "No instruments" = Instruments not loaded
```

---

## ALERT FORMAT

### Standard Alert Structure

```
🚀 CALL ENTRY (or 📉 PUT ENTRY)
INSTRUMENTNAME | 10-MINUTE BREAKOUT (CE/PE Premium)

⏰ Signal Time: HH:MM:SS | DD:MM:YYYY

📊 POSITION DETAILS:
Entry: X.XX
Target 1: Y.YY (+10 points)
Target 2: Z.ZZ (+20 points)
Target 3: W.WW (+30 points)
Stop Loss: SL.LL

Strike: SYMBOL-EXPIRY-STRIKE-TYPE
Premium (LTP): ₹XXX.XX

Channel: CHANNEL_NAME
📢 DISCLAIMER: Educational purposes only.
```

### Example: Actual Alert Received

```
📉 PUT ENTRY
NATURALGAS | 10-MINUTE BREAKOUT (PE Premium)

⏰ Signal Time: 23:25:04 | 09:09:2026

📊 POSITION DETAILS:
Entry: 2.35
Target 1: 12.35 (+10 points)
Target 2: 22.35 (+20 points)
Target 3: 32.35 (+30 points)
Stop Loss: 2.25

Strike: NATURALGAS-23Sep2026-250-PE
Premium (LTP): ₹2.45

Channel: COMMODITY OPTIONS
📢 DISCLAIMER: Educational purposes only.
```

---

## TROUBLESHOOTING

### Issue 1: No Alerts Coming

**Check List:**
1. ✅ Is market open? (MCX: 9:00-23:30, NSE: 9:15-15:39)
2. ✅ Check Render logs for errors
3. ✅ Look for "ERROR" or "API error" in logs
4. ✅ Verify DhanHQ access token valid

**If MCX hours but no alerts:**
```
1. Check logs: "premium alerts: 0"
2. Look for "No RED candle found" (means no breakout)
3. This is NORMAL if no breakouts happening
```

### Issue 2: DhanHQ API Error

**Error Messages:**
```
❌ "DhanHQ API error: 401" = Token expired
❌ "DhanHQ API error: 429" = Rate limit exceeded
❌ "DhanHQ API error: 500" = Server error (temporary)
```

**Solution:**
- 401: Regenerate access token on DhanHQ dashboard
- 429: Normal (handled automatically with delay)
- 500: Wait and retry (temporary)

### Issue 3: Wrong Channel Alerts

**Example:** Alert going to CRYPTO instead of COMMODITY

**Root Cause:** Bot routing wrong

**Check:**
```python
# In telegram_handler.py
BOT_CONFIG mapping should match:
  commodity_options → -1004403277287
  index_options → -1003966854994
  etc.
```

**Fix:** Contact me with logs, I'll verify routing

### Issue 4: Insufficient Candles

**Log:** "Insufficient candles for SYMBOL"

**Reason:** Need at least 3 candles for evaluation

**Solution:** Wait for market to generate more candles (automatic)

---

## IMPORTANT CONTACTS & RESOURCES

### DhanHQ

- **Website:** https://dhanhq.co
- **Dashboard:** https://dashboard.dhanhq.co
- **API Docs:** https://api-docs.dhan.co
- **Support:** support@dhan.co

### Render (Deployment)

- **Dashboard:** https://dashboard.render.com
- **Logs:** https://dashboard.render.com → Select project → Logs
- **Support:** support@render.com

### GitHub Repository

- **Repo URL:** https://github.com/RajaSailor/trading-bot
- **Main Branch:** main
- **Commits:** All changes tracked in git

### Telegram Channels

| Channel | ID | Purpose |
|---------|----|----|
| INDEX OPTIONS | -1003966854994 | NIFTY/BANKNIFTY/SENSEX alerts |
| NIFTY50 OPTIONS | -1003804613787 | Nifty50 stock alerts |
| COMMODITY OPTIONS | -1004403277287 | GOLD/CRUDE/SILVER/GAS alerts |
| NIFTY50 5X | -1004466883026 | Intraday 5X leverage alerts |
| NIFTY50 PAY LATER | -1003814243881 | Margin/BNPL alerts |
| CRYPTO | -1004482078964 | BTC/ETH spot alerts |
| SYSTEM ALERTS | -1004321977761 | Bot health & errors |

### GitHub Copilot

- **Chat:** Direct in this conversation
- **PR Reviews:** Check GitHub PRs for code changes
- **Issue Tracking:** github.com/RajaSailor/trading-bot/issues

---

## TIMELINE & MILESTONES

### ✅ Completed (2026-09-09)

- [x] Multi-bot Telegram routing (6 channels)
- [x] ATM options fetcher (DhanHQ integration)
- [x] Premium strategy engine (RED/GREEN detection)
- [x] Premium screener (all instruments)
- [x] Commodity alerts (MCX working)
- [x] Comprehensive debug logging
- [x] Telegram alert formatting

### ⏳ Next Milestones

- [ ] NSE indices alerts (Tomorrow 9:15 AM)
- [ ] Nifty50 stocks alerts (Tomorrow 9:15 AM)
- [ ] Crypto spot alerts (Tomorrow 24/7)
- [ ] Position tracking & validation
- [ ] SL miss alerts
- [ ] Performance dashboard

---

## QUICK REFERENCE COMMANDS

### Check Bot Status
```bash
# Go to Render dashboard
https://dashboard.render.com
```

### View Recent Logs
```
Search for keyword: "premium alerts"
Should see: "premium alerts: X" where X > 0 during market hours
```

### Monitor Telegram
```
- INDEX channel: Check for NIFTY/BANKNIFTY alerts
- COMMODITY channel: Check for GOLD/CRUDE alerts
- NIFTY50 channel: Check for stock alerts
- CRYPTO channel: Check for BTC/ETH alerts
```

### Common Log Searches

| Search Term | Meaning |
|-------------|---------|
| "✅ Alert sent" | Alert successfully delivered |
| "premium alerts: 0" | No breakouts in this scan (normal) |
| "No RED candle found" | No reversal pattern detected |
| "BREAKOUT DETECTED" | Signal found, alert being sent |
| "DhanHQ API error" | Connection problem |

---

## NEXT STEPS - TOMORROW (2026-09-10)

### 8:00 AM IST
- ✅ Switch on laptop
- ✅ Verify bot running on Render
- ✅ Check system alerts channel for any issues

### 9:15 AM IST (NSE Market Open)
- ⏳ Wait 2-3 minutes for first scan
- ✅ Check INDEX channel for NIFTY/BANKNIFTY alerts
- ✅ Check NIFTY50 channel for stock alerts
- 💬 If issues: Chat with Copilot immediately

### Throughout Market Hours (9:15 AM - 3:39 PM IST)
- ✅ Monitor Telegram channels for alerts
- ✅ Note any patterns or issues
- ⚠️ If API errors: Check token expiry

### After Market Close (3:39 PM IST)
- ✅ Review alert logs
- ✅ Note performance
- 📊 Prepare summary for optimization

---

## BACKUP & RECOVERY

### How to Use This Document

**If bot stops working:**
1. Read TROUBLESHOOTING section
2. Check relevant logs on Render
3. Share logs with this document to Copilot
4. We'll diagnose using STRATEGY LOGIC section

**If need to rebuild:**
1. All code on GitHub (github.com/RajaSailor/trading-bot)
2. All configuration in .env file
3. Redeploy from this backup

**If access token expires:**
1. Generate new token on DhanHQ dashboard
2. Update ACCESS_TOKEN in .env
3. Redeploy to Render

---

## PROJECT SUCCESS METRICS

### ✅ What Success Looks Like

```
Daily Checklist:
□ Commodity alerts flowing (MCX hours)
□ NSE alerts flowing (9:15-15:39 IST)
□ No API errors in logs
□ All 6 Telegram channels receiving alerts
□ Entry/SL/Target calculations correct
□ Zero missed breakouts (verified by log review)
```

### Current Metrics (2026-09-09)

- ✅ Alerts generated: 2 (NATURALGAS, CRUDE OIL)
- ✅ Success rate: 100% (all signals sent)
- ✅ Channels working: 1/6 (Commodities only)
- ✅ Instruments scanning: 4/57 (MCX commodities)
- ⏳ Instruments ready: 57/57 (all coded, waiting NSE open)

---

## GLOSSARY

| Term | Definition |
|------|-----------|
| **ATM** | At The Money - strike closest to current price |
| **CE** | Call option (bullish) |
| **PE** | Put option (bearish) |
| **Premium** | Option price (what we track) |
| **Breakout** | Price breaks previous high/low |
| **RED candle** | Down candle (close < open) |
| **GREEN candle** | Up candle (close > open) |
| **Entry** | Trade entry price |
| **SL** | Stop Loss price |
| **T1/T2/T3** | Target 1/2/3 prices |
| **NFO** | NSE Futures & Options |
| **MCX_OPT** | MCX Options segment |
| **MCX_COMM** | MCX Commodities segment |
| **IST** | Indian Standard Time (UTC+5:30) |

---

## FINAL NOTES

### This Document

- 📄 **Complete project reference** as of 2026-09-09
- 🔄 **Regularly updated** as features deployed
- 💾 **Backup of all key info** for continuity
- 🆘 **Troubleshooting guide** for common issues

### Next Update

Will occur when:
- ✅ NSE alerts start flowing (tomorrow)
- ✅ Any major changes deployed
- ✅ Any issues discovered

### Questions?

**Tomorrow morning (9:15 AM IST):**
- Chat directly in this conversation
- Share logs from Render
- I'll debug in real-time

---

**Safe sleep! Your bot is running fine. See you tomorrow! 🚀**

**📅 Date: 2026-09-09 23:30 IST**  
**✅ Status: LIVE & OPERATIONAL**  
**📊 Next Check: 2026-09-10 09:15 IST**
