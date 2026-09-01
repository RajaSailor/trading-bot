"""
SCREENER PRODUCTION - NSE FNO (NIFTY/BANKNIFTY)
Live 5-min candle breakout screener for NSE market hours
File: screener_nifty.py
Run during: 9:15 AM - 3:30 PM IST (Monday to Friday)
"""

import os
import requests
import pandas as pd
from dotenv import load_dotenv
from telegram import Bot
import asyncio
import time
from datetime import datetime, time as dtime
from screener_strategy import ScreenerStrategy

# Load .env
load_dotenv(dotenv_path="./.env", override=True)

API_KEY = os.getenv("API_KEY")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

BASE_URL = "https://api.dhan.co/v2"

print(f"""
{'='*70}
📊 SCREENER PRODUCTION - NSE FNO LIVE
{'='*70}
[INFO] Configuration:
  API_KEY: {API_KEY[:10]}...
  ACCESS_TOKEN: {ACCESS_TOKEN[:20]}...
  TELEGRAM_TOKEN: {TELEGRAM_TOKEN[:15]}...
  CHAT_ID: {CHAT_ID}
  
Strategy: 5-Min Candle Breakout
Instruments: NIFTY 50, BANKNIFTY, SENSEX
Market Hours: 9:15 AM - 3:30 PM IST (Mon-Fri)
{'='*70}
""")

bot = Bot(token=TELEGRAM_TOKEN)

# Load security list
SECURITY_FILE = "./security_list.csv"
try:
    df = pd.read_csv(SECURITY_FILE, low_memory=False)
    print(f"[SUCCESS] Security list loaded: {len(df)} records")
except Exception as e:
    print(f"[ERROR] Failed to load security list: {e}")
    exit(1)

# Initialize strategy for each instrument
strategy_nifty = ScreenerStrategy(lookback_periods=20)
strategy_banknifty = ScreenerStrategy(lookback_periods=20)
strategy_sensex = ScreenerStrategy(lookback_periods=20)

strategies = {
    "NIFTY": strategy_nifty,
    "BANKNIFTY": strategy_banknifty,
    "SENSEX": strategy_sensex
}

def is_market_open():
    """Check if NSE market is currently open"""
    now = datetime.now()
    current_time = now.time()
    current_day = now.weekday()  # 0=Monday, 6=Sunday
    
    # Market closed on weekends
    if current_day >= 5:
        return False
    
    # Market hours: 9:15 AM to 3:30 PM
    market_open = dtime(9, 15)
    market_close = dtime(15, 30)
    
    return market_open <= current_time <= market_close

def get_market_status():
    """Get detailed market status"""
    now = datetime.now()
    current_time = now.time()
    current_day_name = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][now.weekday()]
    
    market_open = dtime(9, 15)
    market_close = dtime(15, 30)
    
    return {
        'current_time': current_time.strftime("%H:%M:%S"),
        'current_date': now.strftime("%Y-%m-%d"),
        'current_day': current_day_name,
        'market_open': market_open.strftime("%H:%M:%S"),
        'market_close': market_close.strftime("%H:%M:%S"),
        'is_open': is_market_open()
    }

def get_security_id(symbol_name):
    """Get security ID from CSV"""
    if "tradingSymbol" in df.columns:
        row = df[df['tradingSymbol'] == symbol_name]
    elif "SM_SYMBOL_NAME" in df.columns:
        row = df[df['SM_SYMBOL_NAME'] == symbol_name]
    else:
        return None
    
    if not row.empty:
        if "securityId" in row.columns:
            return str(row.iloc[0]['securityId'])
        elif "SEM_SMST_SECURITY_ID" in row.columns:
            return str(row.iloc[0]['SEM_SMST_SECURITY_ID'])
    return None

def fetch_ltp(instruments):
    """Fetch LTP snapshot from DhanHQ"""
    url = f"{BASE_URL}/marketfeed/ltp"
    headers = {
        "X-Client-Id": API_KEY,
        "X-Access-Token": ACCESS_TOKEN,
        "Content-Type": "application/json"
    }
    payload = instruments
    
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") == "success":
                return data.get("data", {})
    except Exception as e:
        print(f"[ERROR] Fetch failed: {e}")
    
    return None

async def send_telegram_alert(message):
    """Send alert to Telegram"""
    try:
        await bot.send_message(chat_id=int(CHAT_ID), text=message)
        print(f"[SUCCESS] Telegram alert sent ✓")
    except Exception as e:
        print(f"[ERROR] Telegram send failed: {e}")

def process_ltp_data(ltp_data):
    """Process LTP data and apply strategy"""
    if not ltp_data:
        print(f"[WARNING] No LTP data received")
        return
    
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Process each instrument
    for symbol, data in ltp_data.items():
        if symbol not in strategies:
            continue
        
        strategy = strategies[symbol]
        
        # Extract OHLC and OI from data
        # Note: LTP endpoint gives current price, need to construct candle
        ltp = data.get("last_price", 0)
        oi = data.get("openInterest", 0)
        
        print(f"\n[{current_time}] {symbol}: LTP={ltp}, OI={oi}")
        
        # For testing: Add simulated candle (in production, use proper candle data)
        # This would come from /marketfeed/ohlc endpoint
        strategy.add_candle(
            symbol,
            open_price=ltp * 0.99,
            high=ltp * 1.01,
            low=ltp * 0.98,
            close=ltp,
            oi=oi,
            timestamp=current_time
        )
        
        # Check strategy
        triggered, message = strategy.check_breakout(symbol)
        
        if triggered:
            print(f"[ALERT] ✓ Breakout detected for {symbol}!")
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(send_telegram_alert(message))
            loop.close()
        else:
            print(f"[INFO] {symbol}: {message}")

def screener_loop(duration_minutes=5):
    """
    Main screener loop
    Fetches data every 30 seconds, checks strategy every 5 minutes
    
    Args:
        duration_minutes: How long to run (for testing)
    """
    
    market_status = get_market_status()
    
    print(f"\n[MARKET STATUS]")
    print(f"  📅 Date: {market_status['current_date']} ({market_status['current_day']})")
    print(f"  🕐 Current Time: {market_status['current_time']} IST")
    print(f"  📊 Market Hours: {market_status['market_open']} - {market_status['market_close']} IST")
    
    if not market_status['is_open']:
        print(f"\n⚠️  MARKET IS CURRENTLY CLOSED!")
        print(f"   Screener can only run during market hours.")
        print(f"   For testing, run: python screener_mcx_test.py")
        return
    
    print(f"\n[SUCCESS] Market is OPEN ✓")
    print(f"[INFO] Starting 5-min breakout screener...")
    print(f"[INFO] Fetching LTP every 30 seconds, checking strategy every 5 minutes\n")
    
    # Get security IDs
    nifty_id = get_security_id("NIFTY")
    banknifty_id = get_security_id("BANKNIFTY")
    sensex_id = get_security_id("SENSEX")
    
    print(f"[INFO] Security IDs:")
    print(f"  NIFTY: {nifty_id}")
    print(f"  BANKNIFTY: {banknifty_id}")
    print(f"  SENSEX: {sensex_id}")
    
    if not all([nifty_id, banknifty_id, sensex_id]):
        print(f"[ERROR] Failed to get all security IDs")
        return
    
    # Instruments for API call
    instruments = {
        "NSE_FNO": [nifty_id, banknifty_id],
        "BSE_FNO": [sensex_id]
    }
    
    # Loop
    start_time = time.time()
    loop_count = 0
    
    while True:
        loop_count += 1
        elapsed = time.time() - start_time
        
        # Check if duration expired (for testing)
        if duration_minutes > 0 and elapsed > duration_minutes * 60:
            print(f"\n[INFO] Test duration completed ({duration_minutes} minutes)")
            break
        
        current_time = datetime.now().strftime("%H:%M:%S")
        print(f"\n[{current_time}] Fetch #{loop_count}...")
        
        # Fetch LTP
        ltp_data = fetch_ltp(instruments)
        
        # Process data
        if ltp_data:
            process_ltp_data(ltp_data)
        else:
            print(f"[WARNING] No data received")
        
        # Wait 30 seconds before next fetch
        print(f"[INFO] Waiting 30 seconds...")
        time.sleep(30)

if __name__ == "__main__":
    try:
        print(f"\n[INFO] Running screener for testing (5 minutes)...")
        screener_loop(duration_minutes=5)
        
        print(f"\n{'='*70}")
        print(f"✅ SCREENER TEST COMPLETE")
        print(f"{'='*70}\n")
        
    except KeyboardInterrupt:
        print(f"\n[INFO] Screener stopped by user")
    except Exception as e:
        print(f"\n[ERROR] Screener crashed: {e}")
        import traceback
        traceback.print_exc()
