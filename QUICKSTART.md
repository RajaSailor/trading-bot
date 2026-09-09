"""
QUICK START GUIDE - Get Live Alerts Running in 5 Minutes!

This guide walks you through setting up the trading bot to receive
live Telegram alerts for NIFTY 50, BANKNIFTY, GOLD, and CRUDE OIL.

Step 1: Verify Your .env File
Step 2: Install Dependencies
Step 3: Test Telegram Connection
Step 4: Start the Screener
Step 5: Monitor Alerts

File: QUICKSTART.md
"""

# ============================================================================
# STEP 1: VERIFY YOUR .env FILE
# ============================================================================

# Make sure your .env has ALL these configured:

# DhanHQ Credentials (you already have these ✅)
API_KEY=f6c12cb2
ACCESS_TOKEN=eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9...

# Main Telegram Bot (for general alerts)
TELEGRAM_TOKEN=8654404135:AAGHqdH81h1t1_RzjfqBSsbRk8O5l-ozRdc
CHAT_ID=-1004321977761

# Index Options Bot (NIFTY, BANKNIFTY)
BOT_INDEX_TOKEN=8601160697:AAFFxscCMfqcrXaf1lw69xK7Ue-RW_8aIzI
CHANNEL_INDEX_ID=-1003966854994

# Commodity Bot (GOLD, CRUDEOIL, SILVER, NATURALGAS)
BOT_COMMODITY_TOKEN=8762956800:AAEkQZfYhawfxQEua8OSYcnp3FPRU2xywsc
CHANNEL_COMMODITY_ID=-1004403277287

# Crypto Bot
BOT_CRYPTO_TOKEN=8921592389:AAF7IKqXz2a7yp0a--m0vP21itKHVKqF-7k
CHANNEL_CRYPTO_ID=-1004482078964

# NIFTY 50 Intraday 5X
BOT_NIFTY50_5X_TOKEN=8265739611:AAFbraUdEY01eJOel76S8mMgBiZT4otxkd4
CHANNEL_NIFTY50_5X_ID=-1004466883026

# NIFTY 50 Options
BOT_NIFTY50_OPTIONS_TOKEN=8746059399:AAGfpg6rQfluICaezqiamCujN8_NcXbt1NQ
CHANNEL_NIFTY50_OPTIONS_ID=-1003804613787

# NIFTY 50 Pay Later
BOT_NIFTY50_PAY_LATER_TOKEN=8934391945:AAEdycuHV7sZP6eASCU2j7kQ9SBG7e9D4Q0
CHANNEL_NIFTY50_PAY_LATER_ID=-1003814243881


# ============================================================================
# STEP 2: INSTALL DEPENDENCIES
# ============================================================================

# Install required packages:
pip install python-telegram-bot==20.0
pip install dhanhq
pip install python-dotenv
pip install requests


# ============================================================================
# STEP 3: TEST TELEGRAM CONNECTION
# ============================================================================

# Test if Telegram bots are working:
python -c "
import os
from dotenv import load_dotenv
from telegram_alerts_fixed import get_telegram_alerts

load_dotenv()
alerts = get_telegram_alerts()

# Check bot status
status = alerts.get_bot_status()
print('\\n' + '='*80)
print('TELEGRAM BOT STATUS')
print('='*80)
for bot_name, bot_status in status.items():
    print(f'{bot_name}: {bot_status}')

# Send test alert
print('\\nSending test alert to index_options channel...')
test_signal = {
    'symbol': 'NIFTY 50',
    'signal': 'CALL',
    'entry': 18250.50,
    'stop_loss': 18200.00,
    'targets': [18300, 18350, 18400],
    'reference_timestamp': '14:25:00',
    'breakout_timestamp': '14:30:00',
}
test_option = {
    'call_strike': 18250,
    'put_strike': 18250,
    'call_premium': 42.50,
    'put_premium': 38.20
}
alerts.send_signal_alert('index_options', test_signal, test_option)
print('✅ Test alert queued (should arrive in 2-3 seconds)')

import time
time.sleep(3)
alerts.shutdown()
"


# ============================================================================
# STEP 4: START THE SCREENER
# ============================================================================

# Option A: Run from command line
python live_screener_main.py

# Option B: Run in background (Linux/Mac)
nohup python live_screener_main.py > screener.log 2>&1 &

# Option C: Run in background with Python subprocess
python -c "
import subprocess
import time

# Start screener
proc = subprocess.Popen(['python', 'live_screener_main.py'])
print('✅ Screener started (PID: {})'.format(proc.pid))
print('   Press Ctrl+C to stop')

try:
    proc.wait()
except KeyboardInterrupt:
    print('Stopping screener...')
    proc.terminate()
"


# ============================================================================
# STEP 5: MONITOR ALERTS IN REAL-TIME
# ============================================================================

# The screener will send alerts to your Telegram channels:

# 🔵 INDEX OPTIONS CHANNEL
#    - NIFTY 50 Breakouts (5-min)
#    - BANKNIFTY Breakouts (5-min)
#    Format: 🚀 CALL ENTRY / 📉 PUT ENTRY
#    Data: Entry, SL, 3 Targets, Strike, Premium

# 🟡 COMMODITY CHANNEL
#    - GOLD Breakouts
#    - CRUDE OIL Breakouts
#    Format: 🚀 CALL ENTRY / 📉 PUT ENTRY
#    Data: Entry, SL, 3 Targets

# Each alert includes:
#   ✅ Symbol and Signal Type (CALL/PUT)
#   ✅ Entry Price
#   ✅ Stop Loss
#   ✅ 3 Target Levels
#   ✅ Strike & Premium (for options)
#   ✅ Time (IST)


# ============================================================================
# TROUBLESHOOTING
# ============================================================================

# Problem: No alerts received
# Solution:
#   1. Check bot status: python test_bots.py
#   2. Verify .env has all tokens
#   3. Check channel IDs are correct
#   4. Ensure you're admin of the Telegram channels
#   5. Check logs: tail -f logs/screener_*.log

# Problem: Alerts delayed
# Solution:
#   1. Check internet connection
#   2. Verify Telegram bot API is accessible
#   3. Reduce update_interval in live_screener_main.py

# Problem: Too many alerts
# Solution:
#   1. Increase threshold_percent in _scan_* methods
#   2. Increase min_signal_interval in signal_processor.py
#   3. Add position limit check

# Problem: Data not fetching
# Solution:
#   1. Verify DhanHQ credentials in .env
#   2. Check if market is open (9:15 AM - 3:30 PM IST)
#   3. Verify security IDs are correct in signal_processor.py


# ============================================================================
# SECURITY IDS (FOR REFERENCE)
# ============================================================================

# NIFTY 50
NIFTY_INDEX = "543388"        # Index
NIFTY_FUTURES = "13"          # Futures
NIFTY_OPTIONS_ATM = 18250     # Call/Put strikes around this

# BANKNIFTY
BANKNIFTY_INDEX = "25258"     # Index
BANKNIFTY_FUTURES = "25"      # Futures
BANKNIFTY_OPTIONS_ATM = 47500 # Call/Put strikes around this

# MCX COMMODITIES
CRUDEOIL = "565899"           # WTI Crude Oil Futures
GOLD = "567647"               # Gold Futures (100g)
SILVER = "567649"             # Silver Futures (30kg)
NATURALGAS = "565853"         # Natural Gas Futures


# ============================================================================
# ADVANCED CONFIGURATION
# ============================================================================

# Edit live_screener_main.py to customize:

self.update_interval = 5          # Check every 5 seconds
self.position_limit = 5           # Max 5 open positions

# Edit signal_processor.py to customize:

threshold_percent = 0.1           # Breakout threshold (0.1% for indices)
min_signal_interval = 60          # Min 60s between same signals


# ============================================================================
# WHAT HAPPENS NEXT?
# ============================================================================

# Once you start the screener:

# 1. 🔍 Every 5 seconds, it checks:
#    - NIFTY 50 (5-min candles)
#    - BANKNIFTY (5-min candles)
#    - GOLD (5-min candles)
#    - CRUDE OIL (5-min candles)

# 2. 📊 It looks for breakouts:
#    - Close > Previous High (CALL signal)
#    - Close < Previous Low (PUT signal)

# 3. 📱 When breakout detected:
#    - Calculates targets (1:1, 1.5:1, 2:1 risk-reward)
#    - Fetches option chain data
#    - Sends Telegram alert to correct channel
#    - Logs in logs/screener_*.log

# 4. ✅ You receive alert with:
#    - Entry Price
#    - Stop Loss
#    - 3 Target Levels
#    - Strike & Premium (options)
#    - Entry Time (IST)

# 5. 🚀 You can then:
#    - Place order manually in DhanHQ app
#    - Or use automated order placement (coming soon)


# ============================================================================
# NEXT STEPS
# ============================================================================

# ✅ Phase 1 - LIVE ALERTS (Current - YOU ARE HERE)
#    - Receive Telegram alerts for breakouts
#    - Monitor from mobile

# 🔜 Phase 2 - AUTOMATED TRADING
#    - Auto-place limit orders when signal triggered
#    - Auto-manage stop loss & targets
#    - Position management

# 🔜 Phase 3 - MOBILE CONTROL
#    - Accept/Reject signals from mobile
#    - Modify SL/Targets via mobile
#    - Real-time P&L tracking

# 🔜 Phase 4 - ADVANCED STRATEGIES
#    - Multiple timeframe confluence
#    - Momentum filters
#    - Institutional order detection


# ============================================================================
# SUPPORT & DOCUMENTATION
# ============================================================================

# File Structure:
#   - telegram_alerts_fixed.py    : Multi-bot alert handler (FIXED)
#   - signal_processor.py         : Breakout detection & options data
#   - live_screener_main.py       : Main orchestrator (PRODUCTION READY)
#   - data_manager.py             : DhanHQ API wrapper
#   - .env                        : Configuration (NEVER COMMIT)
#   - logs/                       : Screener logs (auto-created)

# Key Classes:
#   - TelegramAlertsFixed         : Send alerts to 6 bots
#   - SignalProcessor             : Detect breakouts
#   - LiveScreenerMain            : Orchestrates everything
#   - MarketHours                 : Check market status (IST)

# Test Files:
#   - test_telegram_bots.py       : Test bot connectivity
#   - test_signal_detection.py    : Test breakout detection
#   - test_security_ids.py        : Verify security IDs


print("✅ QUICKSTART COMPLETE")
print("Next: python live_screener_main.py")
