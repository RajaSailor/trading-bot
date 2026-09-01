"""
SCREENER TEST - MCX CRUDE OIL (24/5 MARKET)
Tests strategy with live MCX data (always open, even when NSE closed)
File: screener_mcx_test.py
"""

import os
import requests
import pandas as pd
from dotenv import load_dotenv
from telegram import Bot
import asyncio
from datetime import datetime
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
🛢️  SCREENER TEST - MCX CRUDE OIL
{'='*70}
[INFO] Configuration:
  API_KEY: {API_KEY[:10]}...
  ACCESS_TOKEN: {ACCESS_TOKEN[:20]}...
  TELEGRAM_TOKEN: {TELEGRAM_TOKEN[:15]}...
  CHAT_ID: {CHAT_ID}
  
Purpose:
  ✓ Test 5-min candle breakout strategy
  ✓ Verify Telegram alerts work
  ✓ MCX Crude runs 24/5 (no market hours restriction)
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

# Initialize strategy
strategy = ScreenerStrategy(lookback_periods=20)

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
    """Fetch LTP (Last Traded Price) snapshot from DhanHQ"""
    url = f"{BASE_URL}/marketfeed/ltp"
    headers = {
        "X-Client-Id": API_KEY,
        "X-Access-Token": ACCESS_TOKEN,
        "Content-Type": "application/json"
    }
    payload = instruments
    
    print(f"\n[INFO] Fetching LTP from {url}")
    print(f"[DEBUG] Payload: {payload}")
    
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        print(f"[DEBUG] Status Code: {resp.status_code}")
        print(f"[DEBUG] Response: {resp.text[:300]}")
        
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") == "success":
                print(f"[SUCCESS] LTP data received")
                return data.get("data", {})
            else:
                print(f"[ERROR] API returned status: {data.get('status')}")
                return None
        else:
            print(f"[ERROR] HTTP {resp.status_code}: API authentication may have failed")
            print(f"[HINT] Check your .env file - API_KEY and ACCESS_TOKEN must be exact")
            return None
    except Exception as e:
        print(f"[ERROR] Request failed: {e}")
        return None

async def send_telegram_alert(message):
    """Send alert to Telegram"""
    try:
        await bot.send_message(chat_id=int(CHAT_ID), text=message)
        print(f"[SUCCESS] Telegram alert sent ✓")
    except Exception as e:
        print(f"[ERROR] Telegram send failed: {e}")

def test_screener():
    """
    Test screener with MCX Crude Oil
    MCX Crude Security ID: 12989 (or similar)
    """
    
    print(f"\n[STEP 1] Getting MCX Crude Oil security ID...")
    
    # Try to find MCX Crude Oil in CSV
    # If not available, use a placeholder ID
    try:
        mcx_crude_id = get_security_id("CRUDEOIL")
        if not mcx_crude_id:
            # Try alternative names
            if "tradingSymbol" in df.columns:
                mcx_rows = df[df['tradingSymbol'].str.contains("CRUDE", case=False, na=False)]
                if not mcx_rows.empty:
                    mcx_crude_id = str(mcx_rows.iloc[0]['tradingSymbol'])
        
        if mcx_crude_id:
            print(f"[SUCCESS] MCX Crude Oil ID found: {mcx_crude_id}")
        else:
            print(f"[WARNING] MCX Crude Oil not found in security list")
            print(f"[INFO] Using demo data for testing...")
            simulate_strategy()
            return
    except Exception as e:
        print(f"[ERROR] Error getting MCX ID: {e}")
        print(f"[INFO] Using demo data for testing...")
        simulate_strategy()
        return
    
    # Fetch real data
    print(f"\n[STEP 2] Fetching live LTP data...")
    instruments = {"MCX_FUT": [mcx_crude_id]} if mcx_crude_id else {}
    
    ltp_data = fetch_ltp(instruments)
    
    if ltp_data:
        print(f"\n[STEP 3] Processing strategy...")
        print(f"LTP Data: {ltp_data}")
    else:
        print(f"\n[STEP 2] Falling back to SIMULATED candle data for testing...")
        simulate_strategy()

def simulate_strategy():
    """Simulate 5-min candles for testing when market is closed or API fails"""
    print(f"\n[STEP 3] Running SIMULATED candle data test...")
    print(f"[INFO] Simulating 5-min breakout scenario...")
    
    # Simulate candle data
    symbol = "CRUDEOIL"
    
    # Add historical candles (setup) - INCREASING OI
    print(f"\n[DEBUG] Adding 19 historical candles...")
    for i in range(19):
        strategy.add_candle(
            symbol,
            open_price=7800 + i*10,
            high=7820 + i*10,
            low=7790 + i*10,
            close=7810 + i*10,
            oi=500000 + i*10000,  # ✓ INCREASING OI each candle
            timestamp=f"2026-08-24 10:00:00 - Candle {i+1}"
        )
    
    print(f"[SUCCESS] Added 19 historical candles")
    print(f"  Last candle: High=7820, Close=7810, OI=690000")
    
    # Add breakout candle (triggers alert)
    print(f"\n[STEP 4] Simulating BREAKOUT candle...")
    print(f"  ✓ Close > Previous High (breakout)")
    print(f"  ✓ OI increases by 15% (liquidity surge)")
    print(f"  ✓ Price above 20-period average")
    
    strategy.add_candle(
        symbol,
        open_price=7990,
        high=8050,      # ✓ Higher than previous high (7820)
        low=7980,
        close=8040,     # ✓ Close > Previous High (breakout!)
        oi=793500,      # ✓ OI surge from 690000 to 793500 (+15%)
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    
    # Check strategy
    print(f"\n[STEP 5] Checking strategy conditions...")
    triggered, message = strategy.check_breakout(symbol)
    
    print(f"\nStrategy Check Results:")
    print(f"Triggered: {triggered}")
    print(f"Message:\n{message}\n")
    
    if triggered:
        print(f"[SUCCESS] ✓ BREAKOUT DETECTED!")
        print(f"[INFO] Sending Telegram alert...")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(send_telegram_alert(message))
        loop.close()
    else:
        print(f"[INFO] No breakout detected (conditions not met)")
    
    # Show signal summary
    signal = strategy.get_signal_summary(symbol)
    if signal:
        print(f"\n[STEP 6] Signal Summary:")
        print(f"  Symbol: {signal['symbol']}")
        print(f"  Close: {signal['close']:.2f}")
        print(f"  OI: {signal['oi']:.0f}")
        print(f"  Candles Tracked: {signal['candle_count']}")
    
    print(f"\n{'='*70}")
    print(f"✅ TEST COMPLETE")
    print(f"If you received a Telegram alert above, your screener is working!")
    print(f"{'='*70}\n")

if __name__ == "__main__":
    try:
        test_screener()
    except KeyboardInterrupt:
        print(f"\n[INFO] Test interrupted by user")
    except Exception as e:
        print(f"\n[ERROR] Test crashed: {e}")
        import traceback
        traceback.print_exc()
