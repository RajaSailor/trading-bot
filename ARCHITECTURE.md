"""
COMPLETE ARCHITECTURE & SETUP GUIDE
Live Market Alerts Trading Bot with DhanHQ

File: ARCHITECTURE.md
"""

# ============================================================================
# SYSTEM ARCHITECTURE
# ============================================================================

"""
┌─────────────────────────────────────────────────────────────────────────────┐
│                          TRADING BOT ARCHITECTURE                           │
└─────────────────────────────────────────────────────────────────────────────┘

                              ┌──────────────────┐
                              │   DhanHQ API     │
                              │  (Live Market)   │
                              └────────┬─────────┘
                                       │
                    ┌──────────────────┼──────────────────┐
                    │                  │                  │
            ┌───────▼────────┐  ┌──────▼─────────┐  ┌────▼─────────┐
            │ Quote Data     │  │  Chart Data    │  │ Option Chain │
            │ (LTP, Bid/Ask) │  │  (OHLC, Volume)│  │ (Strikes)    │
            └────────────────┘  └────────────────┘  └──────────────┘
                                       │
                    ┌──────────────────┴──────────────────┐
                    │                                     │
            ┌───────▼──────────────┐           ┌─────────▼──────┐
            │  Data Manager        │           │  Signal        │
            │  (API Wrapper)       │           │  Processor     │
            │  - Cache handling    │           │  - Breakout    │
            │  - Rate limiting     │           │  - Technical   │
            │  - Error recovery    │           │  - Options     │
            └─────────┬────────────┘           └────────────────┘
                      │
            ┌─────────▼──────────────────────┐
            │   Live Screener Main           │
            │   (Orchestrator)               │
            │   - Market hours check         │
            │   - Multi-symbol scan          │
            │   - Signal coordination        │
            └──────────┬─────────────────────┘
                       │
        ┌──────────────┼──────────────┬──────────────┐
        │              │              │              │
   ┌────▼──────┐ ┌─────▼─────┐ ┌─────▼─────┐ ┌─────▼──────┐
   │  Signal   │ │ Telegram  │ │  Alert    │ │ Position  │
   │Formatter  │ │  Alerts   │ │   Queue   │ │ Manager   │
   └───────────┘ └─────┬─────┘ └───────────┘ └───────────┘
                       │
        ┌──────────────┼──────────────┬──────────────┐
        │              │              │              │
   ┌────▼──────┐ ┌─────▼─────┐ ┌─────▼─────┐ ┌─────▼──────┐
   │ Index Bot │ │Commodity  │ │ Crypto    │ │ Nifty 50   │
   │(NIFTY,   │ │ Bot       │ │ Bot       │ │ Options    │
   │BANKNIFTY)│ │(GOLD,OIL) │ │(BTC,ETH)  │ │ Bot        │
   └───────────┘ └───────────┘ └───────────┘ └────────────┘
        │              │              │              │
        └──────────────┼──────────────┴──────────────┘
                       │
              ┌────────▼─────────┐
              │   Telegram       │
              │   Channels       │
              └──────────────────┘
                       │
              ┌────────▼─────────┐
              │   Your Mobile    │
              │   (Get Alerts)   │
              └──────────────────┘
"""

# ============================================================================
# DATA FLOW
# ============================================================================

"""
Every 5 seconds:

1. FETCH DATA
   DhanHQ API → Get latest candles (5-min)
   
2. PROCESS
   - Calculate breakout signal
   - Fetch option chain data
   - Format for Telegram
   
3. QUEUE
   - Add to alert queue
   - Assign to correct bot/channel
   
4. SEND
   - Background thread sends to Telegram
   - Queue is processed automatically
   
5. LOG
   - Save in logs/screener_*.log
   - Track all signals

Key Metrics:
   - Data Fetch: ~500ms
   - Signal Processing: ~200ms
   - Telegram Send: ~1000ms (queued)
   - Total Cycle: ~2-3 seconds
"""

# ============================================================================
# KEY FILES & COMPONENTS
# ============================================================================

"""
📁 PROJECT STRUCTURE
├── .env                              ← Configuration (API keys, Telegram tokens)
├── security_list.csv                 ← DhanHQ security master list
│
├── 🔴 PRODUCTION FILES (Ready to use)
├── live_screener_main.py             ← Main orchestrator (START HERE!)
├── telegram_alerts_fixed.py           ← Multi-bot alert handler (FIXED)
├── signal_processor.py                ← Breakout detection + options
├── data_manager.py                    ← DhanHQ API wrapper
│
├── 🟡 TESTING & VALIDATION
├── test_all_components.py             ← Complete test suite
├── QUICKSTART.md                      ← 5-minute setup guide
├── ARCHITECTURE.md                    ← This file
│
├── 📝 SUPPORT FILES
├── requirements.txt                   ← Python dependencies
├── logs/                              ← Screener logs (auto-created)
│   ├── screener_nifty.log
│   ├── screener_banknifty.log
│   ├── screener_commodity.log
│   └── screener_indices.log
│
└── 🔧 UTILITIES
    ├── test_signal_detection.py       ← Test signal detection
    ├── test_telegram_bots.py          ← Test bot connectivity
    └── test_security_ids.py           ← Verify security IDs


KEY COMPONENTS EXPLAINED:

1. live_screener_main.py (ORCHESTRATOR)
   ├── MarketHours class
   │   ├─ is_nse_open()              → Check if NSE is trading
   │   ├─ is_mcx_open()              → Check if MCX is trading
   │   └─ next_market_open()         → Get next market open time
   │
   ├── LiveScreener class
   │   ├─ _scan_nifty()              → Scan NIFTY 50 for breakouts
   │   ├─ _scan_banknifty()          → Scan BANKNIFTY for breakouts
   │   ├─ _scan_commodities()        → Scan GOLD/CRUDE OIL
   │   ├─ _process_signal()          → Format and send alert
   │   └─ run()                      → Main loop (every 5 seconds)
   │
   └─ main()
       └─ Starts screener with error handling

2. telegram_alerts_fixed.py (ALERT HANDLER)
   ├── TelegramAlertsFixed class
   │   ├─ send_signal_alert()        → Send formatted signal
   │   ├─ send_raw_alert()           → Send raw message
   │   ├─ get_bot_status()           → Check bot health
   │   ├─ get_alert_history()        → View sent alerts
   │   └─ shutdown()                 → Clean shutdown
   │
   └─ Background Thread
       ├─ Processes message queue
       ├─ Sends to Telegram
       └─ Handles rate limiting

3. signal_processor.py (SIGNAL DETECTION)
   ├── SignalProcessor class
   │   ├─ detect_breakout()          → Find breakout candles
   │   ├─ get_option_chain_data()    → Fetch option strikes
   │   ├─ format_signal_for_telegram()→ Format for display
   │   ├─ get_nifty_securities()     → NIFTY securities
   │   ├─ get_banknifty_securities() → BANKNIFTY securities
   │   └─ get_commodity_securities() → MCX commodities
   │
   └─ Signal Enum
       ├─ CALL                       → Breakout upside (buy signal)
       └─ PUT                        → Breakout downside (sell signal)

4. data_manager.py (API WRAPPER)
   ├── DhanAPIClient class
   │   ├─ quote_data()               → Get LTP, bid/ask
   │   ├─ chart_data()               → Get OHLC candles
   │   ├─ option_chain()             → Get option strikes
   │   ├─ place_order()              → Place order
   │   ├─ place_multi_order()        → Place multiple orders
   │   ├─ cancel_order()             → Cancel order
   │   └─ get_order_book()           → View orders
   │
   └─ Error Handling
       ├─ Retry logic
       ├─ Rate limiting
       └─ Connection pooling
"""

# ============================================================================
# SECURITY IDS REFERENCE
# ============================================================================

"""
🔵 NIFTY 50 (NSE_FNO)
   ├─ Index                : 543388 (for quotes)
   ├─ Futures              : 13     (NIFTYFUT)
   ├─ Options ATM          : 18250  (current spot ~18250)
   ├─ Option Strikes       : 18000, 18100, 18150, 18200, 18250, 18300, 18350, 18400, 18500
   │
   └─ NIFTY 50 STOCKS (50 constituent stocks)
       ├─ RELIANCE         : 1234 (example)
       ├─ TCS              : 1234 (example)
       ├─ INFY             : 1234 (example)
       └─ ... (50 stocks total)

🟣 BANKNIFTY (NSE_FNO)
   ├─ Index               : 25258 (for quotes)
   ├─ Futures             : 25    (BANKNIFTYFUT)
   ├─ Options ATM         : 47500 (current spot ~47500)
   └─ Option Strikes      : 47000, 47200, 47300, 47400, 47500, 47600, 47700, 47800, 48000

🟡 MCX COMMODITIES (MCX_COMM)
   ├─ CRUDEOIL            : 565899 (WTI Crude Oil, 100 BBL)
   │   └─ Lot Size: 100 barrels
   │
   ├─ GOLD                : 567647 (Gold Futures, 100g)
   │   └─ Lot Size: 100 grams
   │
   ├─ SILVER              : 567649 (Silver Futures, 30kg)
   │   └─ Lot Size: 30 kilograms
   │
   └─ NATURALGAS          : 565853 (Natural Gas, MMBTU)
       └─ Lot Size: 1 MMBTU

💎 INDEX STOCKS
   ├─ NIFTY 50 constituents (50 stocks)
   │   ├─ RELIANCE        : NSE_EQ segment
   │   ├─ TCS             : NSE_EQ segment
   │   ├─ INFY            : NSE_EQ segment
   │   └─ ... (50 total)
   │
   └─ SENSEX constituents (30 stocks)
       ├─ RIL             : BSE_EQ segment
       ├─ TCS             : BSE_EQ segment
       └─ ... (30 total)

🟢 INDICES (for reference only)
   ├─ NIFTY 50            : 543388 (NSE_INDEX)
   ├─ BANKNIFTY           : 25258  (NSE_INDEX)
   ├─ SENSEX              : 538683 (BSE_INDEX)
   └─ FINNIFTY            : (check security list)
"""

# ============================================================================
# EXCHANGE SEGMENTS & INSTRUMENTS
# ============================================================================

"""
EXCHANGE SEGMENTS (exchangeSegment parameter):
   ├─ NSE_EQ              → NSE Equity Cash (stocks)
   ├─ NSE_FNO             → NSE Futures & Options (NIFTY, BANKNIFTY, stocks)
   ├─ BSE_EQ              → BSE Equity Cash
   ├─ BSE_FNO             → BSE Futures & Options
   ├─ MCX_COMM            → MCX Commodities (GOLD, CRUDE, etc.)
   ├─ MCX_FUT             → MCX Futures
   ├─ IDX_I               → Indices (NIFTY, SENSEX, etc.)
   └─ NCDEX               → NCDEX Commodities

INSTRUMENTS (instrument parameter):
   ├─ INDEX               → Index (NIFTY 50, BANKNIFTY, SENSEX)
   ├─ EQUITY              → Stock (reliance, tcs, etc.)
   ├─ FUTIDX              → Index Futures (NIFTYFUT, BANKNIFTYFUT)
   ├─ FUTSTK              → Stock Futures
   ├─ FUTCOM              → Commodity Futures (CRUDEOIL, GOLD, SILVER)
   ├─ OPTIDX              → Index Options (NIFTY 50 options, BANKNIFTY options)
   ├─ OPTSTK              → Stock Options
   └─ OPTCOM              → Commodity Options

PRODUCT TYPES (productType parameter):
   ├─ CNC                 → Delivery (cash & carry)
   ├─ INTRADAY            → Intraday trading (square off same day)
   ├─ MARGIN              → Carry forward (F&O margin)
   ├─ MTF                 → Multi-leg trading fund
   ├─ CO                  → Cover order (with stop loss)
   └─ BO                  → Bracket order (with SL & target)
"""

# ============================================================================
# TRADING WORKFLOW
# ============================================================================

"""
PHASE 1: LIVE ALERTS (CURRENT ✅)
   ├─ Every 5 seconds:
   │  ├─ Fetch latest OHLC data
   │  ├─ Check for breakout
   │  ├─ Get option chain data
   │  └─ Send Telegram alert
   │
   └─ You receive alert with:
      ├─ Entry price
      ├─ Stop loss
      ├─ 3 target levels
      ├─ Strike & premium (options)
      └─ Timestamp (IST)

PHASE 2: MANUAL TRADING (Next)
   ├─ You place order manually via:
   │  ├─ DhanHQ mobile app
   │  ├─ Web portal
   │  └─ Desktop platform
   │
   └─ You manage:
      ├─ Position entry
      ├─ Stop loss level
      └─ Target exit

PHASE 3: SEMI-AUTOMATED (Future)
   ├─ Bot sends alert ✅
   ├─ You accept via mobile button
   ├─ Bot auto-places limit order
   │
   └─ You still manage:
      ├─ SL modifications
      └─ Profit booking

PHASE 4: FULLY AUTOMATED (Advanced)
   ├─ Bot detects signal
   ├─ Bot places limit order
   ├─ Bot manages stop loss
   ├─ Bot books profit at targets
   │
   └─ You only:
      └─ Monitor P&L in dashboard

PHASE 5: ADVANCED STRATEGIES (Enterprise)
   ├─ Multi-timeframe confluence
   ├─ Institutional order detection
   ├─ Volume & open interest analysis
   ├─ AI-based signal filtering
   └─ Risk management
"""

# ============================================================================
# SETUP CHECKLIST
# ============================================================================

"""
✅ BEFORE YOU START

1. DhanHQ Account
   ✓ Created account on dhan.co
   ✓ Completed KYC
   ✓ Have API_KEY and ACCESS_TOKEN
   ✓ Enabled live data subscription

2. Telegram Setup
   ✓ Created 6 Telegram bots (via @BotFather)
   ✓ Created 6 Telegram channels
   ✓ Added bots as admins to channels
   ✓ Have bot tokens and channel IDs

3. Environment
   ✓ Python 3.8+ installed
   ✓ pip installed
   ✓ Virtual environment (recommended)
   ✓ .env file with all credentials

4. Code Setup
   ✓ Cloned repository
   ✓ Installed dependencies (pip install -r requirements.txt)
   ✓ Updated security IDs in signal_processor.py
   ✓ Configured .env with tokens

5. Testing
   ✓ Run: python test_all_components.py
   ✓ All 6 tests passed
   ✓ Telegram alerts working
   ✓ DhanHQ API responding

✅ YOU'RE READY!
   python live_screener_main.py
"""

# ============================================================================
# TELEGRAM BOT SETUP GUIDE
# ============================================================================

"""
HOW TO CREATE TELEGRAM BOTS

1. Open Telegram and search for @BotFather
2. Click Start and type: /newbot
3. Follow prompts:
   - Name: "Index Options Bot"
   - Username: "nifty_alerts_bot" (must be unique)
4. You'll get a token like: 8601160697:AAFFxscCMfqcrXaf1lw69xK7Ue-RW_8aIzI
5. Save this token in .env as: BOT_INDEX_TOKEN

REPEAT FOR EACH BOT:
   1. BOT_INDEX_TOKEN         (NIFTY, BANKNIFTY alerts)
   2. BOT_COMMODITY_TOKEN     (GOLD, CRUDE OIL alerts)
   3. BOT_CRYPTO_TOKEN        (BTC, ETH alerts - future)
   4. BOT_NIFTY50_5X_TOKEN    (Intraday trades)
   5. BOT_NIFTY50_OPTIONS_TOKEN (Options chain alerts)
   6. BOT_NIFTY50_PAY_LATER_TOKEN (Advanced strategies)

HOW TO CREATE TELEGRAM CHANNELS

1. Open Telegram
2. Tap menu → New Channel
3. Enter name: "Index Options Alerts"
4. Choose "Private" (only invited members)
5. Add bot as admin
6. Right-click channel → Copy link
7. Extract channel ID from link (e.g., -1003966854994)
8. Save in .env as: CHANNEL_INDEX_ID

REPEAT FOR EACH CHANNEL:
   1. CHANNEL_INDEX_ID        (for index_options bot)
   2. CHANNEL_COMMODITY_ID    (for commodity bot)
   3. CHANNEL_CRYPTO_ID       (for crypto bot)
   4. CHANNEL_NIFTY50_5X_ID   (for intraday bot)
   5. CHANNEL_NIFTY50_OPTIONS_ID (for options bot)
   6. CHANNEL_NIFTY50_PAY_LATER_ID (for advanced bot)

TESTING BOTS

# Send test message to bot
curl -X POST \
  https://api.telegram.org/bot<YOUR_TOKEN>/sendMessage \
  -d chat_id=<YOUR_CHAT_ID> \
  -d text="Test message"

# Get bot info
curl https://api.telegram.org/bot<YOUR_TOKEN>/getMe

# Get updates (messages sent to bot)
curl https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
"""

# ============================================================================
# TROUBLESHOOTING
# ============================================================================

"""
ISSUE: No alerts received

SOLUTION:
1. Check bot status:
   python -c "from telegram_alerts_fixed import get_telegram_alerts; alerts = get_telegram_alerts(); print(alerts.get_bot_status()); alerts.shutdown()"
   
2. Verify .env has ALL bot tokens and channel IDs
   - grep "BOT_" .env
   - grep "CHANNEL_" .env
   
3. Check channel permissions:
   - Bot must be admin of channel
   - Bot must have "Post Messages" permission
   
4. Verify Telegram credentials:
   - Check token is correct (copy from @BotFather)
   - Check channel ID is correct (should start with -)
   
5. Check internet connection:
   - ping api.telegram.org
   - Check firewall rules


ISSUE: Market data not fetching

SOLUTION:
1. Verify DhanHQ credentials:
   - Check .env has API_KEY and ACCESS_TOKEN
   - Test: python -c "from data_manager import DhanAPIClient; client = DhanAPIClient(ACCESS_TOKEN); print(client.quote_data({'NSE_INDEX': ['543388']}))"
   
2. Check if market is open:
   - NSE: 9:15 AM - 3:30 PM IST (Mon-Fri)
   - MCX: 9:00 AM - 11:30 PM IST (Mon-Fri)
   
3. Verify security IDs:
   - Check signal_processor.py has correct IDs
   - Test: python test_security_ids.py
   
4. Check API rate limits:
   - Don't make >100 requests/minute
   - Add delays between calls


ISSUE: Too many/too few alerts

SOLUTION:
1. Too many alerts:
   - Increase threshold_percent in signal_processor.py
   - Reduce update frequency (change 5 to 10 in main loop)
   - Add volume filter
   
2. Too few alerts:
   - Decrease threshold_percent
   - Increase update frequency
   - Add more symbols to scan


ISSUE: Screener crashes

SOLUTION:
1. Check logs:
   - tail -f logs/screener_*.log
   
2. Verify dependencies:
   - pip install -r requirements.txt
   
3. Check Python version:
   - python --version (must be 3.8+)
   
4. Run test suite:
   - python test_all_components.py


ISSUE: Alerts delayed (>5 seconds)

SOLUTION:
1. Check Telegram API:
   - May be rate limited (happens during high volume)
   - Solution: Use separate bots for each channel
   
2. Check internet speed:
   - Run: speedtest-cli
   - Should be >10 Mbps
   
3. Check CPU usage:
   - run: top
   - Should be <50% CPU per thread
   
4. Check for blocking calls:
   - Profile with: python -m cProfile live_screener_main.py
"""

# ============================================================================
# PERFORMANCE OPTIMIZATION
# ============================================================================

"""
CURRENT PERFORMANCE:
   - Data fetch: ~500ms
   - Signal detection: ~200ms
   - Telegram queue: ~1000ms
   - Total cycle: ~5 seconds

WAYS TO IMPROVE:

1. Use multiple threads (for simultaneous scans)
   from concurrent.futures import ThreadPoolExecutor
   executor = ThreadPoolExecutor(max_workers=4)
   
2. Cache data locally
   - Store last 100 candles per symbol
   - Reduce API calls by 80%
   
3. Batch API requests
   - Get multiple symbols in single request
   - Reduce latency
   
4. Use WebSocket instead of REST
   - Real-time updates instead of polling
   - Reduce latency by 90%
   
5. Pre-compute indicators
   - Calculate SMA, RSI, MACD once
   - Reuse for multiple signals
   
6. Use database (SQLite)
   - Store signals locally
   - Fast historical queries
   - Better for backtesting
"""

# ============================================================================
# NEXT STEPS
# ============================================================================

"""
1. RUN QUICKSTART
   - Follow QUICKSTART.md
   - Takes 5 minutes
   
2. RUN TESTS
   - python test_all_components.py
   - All tests must pass
   
3. START SCREENER
   - python live_screener_main.py
   - Watch logs for alerts
   
4. MONITOR ALERTS
   - Check Telegram channels
   - Verify correct format
   - Note entry/SL/targets
   
5. PLACE FIRST TRADE
   - Use alert info
   - Place order in DhanHQ app
   - Track P&L
   
6. ITERATE & IMPROVE
   - Adjust thresholds
   - Add more symbols
   - Optimize strategy
   
7. SCALE UP
   - Add automated trading
   - Implement position management
   - Build mobile control
   
8. ADVANCED
   - Multi-timeframe analysis
   - Machine learning filters
   - News sentiment integration
"""

print("✅ Architecture documentation complete!")
