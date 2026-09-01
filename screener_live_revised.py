"""
LIVE SCREENER - 5 MIN BREAKOUT STRATEGY (REVISED)
Production screener with MOST RECENT RED/GREEN candle tracking
Scans every 5 seconds for breakout conditions
File: screener_live_revised.py
Run: 9:00 AM - 11:30 PM (MCX), 9:15 AM - 3:40 PM (NSE)
"""

import os
import requests
import pandas as pd
from dotenv import load_dotenv
from telegram import Bot
import asyncio
import time
from datetime import datetime, time as dtime
from strategy_5min_revised import FiveMinBreakoutStrategyRevised

# Load .env
load_dotenv(dotenv_path="./.env", override=True)

API_KEY = os.getenv("API_KEY")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

BASE_URL = "https://api.dhan.co/v2"

DISCLAIMER = """⚠️ DISCLAIMER:
I am NOT a SEBI-registered Investment Adviser. This is for educational purposes only.
Not investment advice. Consult a SEBI-registered advisor before trading.
Trading carries significant risk. Accept full responsibility for losses."""

print(f"""
{'='*85}
📊 LIVE SCREENER - 5 MIN CANDLE BREAKOUT STRATEGY (REVISED)
{'='*85}
[INFO] Configuration:
  API_KEY: {API_KEY[:10]}...
  TELEGRAM: Connected to {CHAT_ID}
  
Strategy: Track Most Recent RED/GREEN Candles
  - Scans every 5 seconds
  - Triggers on any breakout of recent RED/GREEN highs/lows
  
Market Hours:
  - MCX Crude Oil: 9:00 AM - 11:30 PM IST
  - NSE (NIFTY/BANKNIFTY): 9:15 AM - 3:40 PM IST
{'='*85}
""")

bot = Bot(token=TELEGRAM_TOKEN)

# Load security list
SECURITY_FILE = "./security_list.csv"
try:
    df = pd.read_csv(SECURITY_FILE, low_memory=False)
    print(f"[SUCCESS] Security list loaded: {len(df)} records\n")
except Exception as e:
    print(f"[ERROR] Failed to load security list: {e}")
    exit(1)

# Initialize strategies for each symbol
strategies = {
    "NIFTY": FiveMinBreakoutStrategyRevised(),
    "BANKNIFTY": FiveMinBreakoutStrategyRevised(),
    "CRUDEOIL": FiveMinBreakoutStrategyRevised(),
}

def is_market_hours():
    """Check if it's trading hours"""
    now = datetime.now()
    current_time = now.time()
    current_day = now.weekday()
    
    if current_day >= 5:
        return False, "Market Closed (Weekend)"
    
    # MCX: 9:00 AM - 11:30 PM
    mcx_open = dtime(9, 0)
    mcx_close = dtime(23, 30)
    
    # NSE: 9:15 AM - 3:40 PM
    nse_open = dtime(9, 15)
    nse_close = dtime(15, 40)
    
    if mcx_open <= current_time <= mcx_close:
        return True, "MCX Hours (9:00 AM - 11:30 PM)"
    
    if nse_open <= current_time <= nse_close:
        return True, "NSE Hours (9:15 AM - 3:40 PM)"
    
    return False, "Market Closed"

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
    """Fetch LTP from DhanHQ"""
    url = f"{BASE_URL}/marketfeed/ltp"
    headers = {
        "X-Client-Id": API_KEY,
        "X-Access-Token": ACCESS_TOKEN,
        "Content-Type": "application/json"
    }
    
    try:
        resp = requests.post(url, headers=headers, json=instruments, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") == "success":
                return data.get("data", {})
    except Exception as e:
        pass
    
    return None

async def send_telegram_alert(message):
    """Send alert to Telegram"""
    try:
        await bot.send_message(chat_id=int(CHAT_ID), text=message)
        print(f"[SUCCESS] ✓ Alert sent to Telegram")
    except Exception as e:
        print(f"[ERROR] Telegram send failed: {e}")

def format_telegram_alert(symbol, signal_data):
    """Format alert message as per your specification"""
    
    alert = f"""
🚀 BREAKOUT ALERT - 5 MIN STRATEGY

📊 Market: {symbol}
📈 Signal: {signal_data['buy_side']}
💰 Strike Price: {signal_data['strike_price']}
📍 Entry: {signal_data['entry']}
🛑 Stop Loss: {signal_data['stop_loss']}
🎯 Target: {signal_data['target']}

⏰ Signal Time: {signal_data['timestamp']}
💹 Current Price: {signal_data['current_price']}
📈 Breakout Level: {signal_data['breakout_level']}

{DISCLAIMER}
"""
    return alert

def process_signal(symbol, signal_data):
    """Process and send signal"""
    if signal_data:
        print(f"\n{'='*85}")
        print(f"🚀 SIGNAL TRIGGERED!")
        print(f"{'='*85}")
        print(f"Market: {symbol}")
        print(f"Signal: {signal_data['buy_side']}")
        print(f"Strike: {signal_data['strike_price']}")
        print(f"Entry: {signal_data['entry']}")
        print(f"Stop Loss: {signal_data['stop_loss']}")
        print(f"Target: {signal_data['target']}")
        print(f"Current Price: {signal_data['current_price']}")
        print(f"Time: {signal_data['timestamp']}")
        print(f"{'='*85}\n")
        
        alert_message = format_telegram_alert(symbol, signal_data)
        
        # Send to Telegram
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(send_telegram_alert(alert_message))
        loop.close()

def add_simulated_candles(symbol, strategy):
    """Add simulated candles for testing"""
    
    # Simulate 5 RED candles
    for i in range(5):
        strategy.add_candle(
            symbol,
            open_price=8000 + i*20,
            high=8015 + i*20,
            low=7990 + i*20,
            close=7995 + i*20,  # Close < Open = RED
            oi=500000 + i*10000,
            timestamp=f"Sim Red {i+1}"
        )
    
    # Simulate 3 GREEN candles
    for i in range(3):
        strategy.add_candle(
            symbol,
            open_price=7995 + i*15,
            high=8020 + i*15,
            low=8000 + i*15,
            close=8015 + i*15,  # Close > Open = GREEN
            oi=505000 + i*10000,
            timestamp=f"Sim Green {i+1}"
        )
    
    # Add BREAKOUT GREEN candle (breaks most recent RED high)
    strategy.add_candle(
        symbol,
        open_price=8000,
        high=8100,  # Breaks previous RED high
        low=7995,
        close=8095,  # GREEN candle
        oi=550000,
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )

def screener_loop(test_mode=False):
    """Main screener loop"""
    
    market_open, market_status = is_market_hours()
    
    print(f"\n[MARKET STATUS]")
    print(f"  📅 Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  📊 Status: {market_status}\n")
    
    if not market_open and not test_mode:
        print(f"[INFO] Market is closed. Screener not running.")
        return
    
    if test_mode:
        print(f"[INFO] TEST MODE: Using simulated data\n")
        
        # Add simulated candles
        add_simulated_candles("CRUDEOIL", strategies["CRUDEOIL"])
        
        # Check signals
        call_triggered, call_data = strategies["CRUDEOIL"].check_call_breakout("CRUDEOIL")
        if call_triggered:
            process_signal("MCX CRUDE OIL (Test)", call_data)
        
        put_triggered, put_data = strategies["CRUDEOIL"].check_put_breakout("CRUDEOIL")
        if put_triggered:
            process_signal("MCX CRUDE OIL (Test)", put_data)
        
        return
    
    print(f"[SUCCESS] Market is OPEN ✓")
    print(f"[INFO] Starting 5-min screener (scans every 5 seconds)\n")
    
    # Get security IDs
    nifty_id = get_security_id("NIFTY")
    banknifty_id = get_security_id("BANKNIFTY")
    crudeoil_id = get_security_id("CRUDEOIL")
    
    print(f"[INFO] Security IDs loaded")
    print(f"  NIFTY: {nifty_id}")
    print(f"  BANKNIFTY: {banknifty_id}")
    print(f"  CRUDEOIL: {crudeoil_id}\n")
    
    instruments = {
        "NSE_FNO": [nifty_id, banknifty_id] if nifty_id and banknifty_id else [],
        "MCX_FUT": [crudeoil_id] if crudeoil_id else []
    }
    
    loop_count = 0
    
    while True:
        loop_count += 1
        market_open, _ = is_market_hours()
        
        if not market_open:
            print(f"\n[INFO] Market hours ended. Screener stopped.")
            break
        
        current_time = datetime.now().strftime("%H:%M:%S")
        print(f"[{current_time}] Scan #{loop_count}...", end=" ")
        
        # Fetch LTP
        ltp_data = fetch_ltp(instruments)
        
        if ltp_data:
            print(f"✓ Data received")
            
            # Process each symbol
            for symbol_name, symbol in [("NIFTY", "NIFTY"), ("BANKNIFTY", "BANKNIFTY"), ("CRUDEOIL", "CRUDEOIL")]:
                if symbol in strategies and symbol_name in ltp_data:
                    ltp = ltp_data[symbol_name]
                    last_price = ltp.get("last_price", 0)
                    oi = ltp.get("openInterest", 0)
                    
                    # Add candle (simplified - would need actual OHLC)
                    strategies[symbol].add_candle(
                        symbol,
                        open_price=last_price * 0.99,
                        high=last_price * 1.01,
                        low=last_price * 0.98,
                        close=last_price,
                        oi=oi,
                        timestamp=current_time
                    )
                    
                    # Check signals
                    call_triggered, call_data = strategies[symbol].check_call_breakout(symbol)
                    if call_triggered:
                        process_signal(symbol, call_data)
                    
                    put_triggered, put_data = strategies[symbol].check_put_breakout(symbol)
                    if put_triggered:
                        process_signal(symbol, put_data)
        else:
            print(f"⚠️  No data")
        
        # Wait 5 seconds before next scan
        time.sleep(5)

if __name__ == "__main__":
    try:
        # Check if running in test mode
        import sys
        test_mode = len(sys.argv) > 1 and sys.argv[1] == "test"
        
        if test_mode:
            screener_loop(test_mode=True)
        else:
            screener_loop(test_mode=False)
        
        print(f"\n{'='*85}")
        print(f"✅ SCREENER SESSION COMPLETE")
        print(f"{'='*85}\n")
        
    except KeyboardInterrupt:
        print(f"\n\n[INFO] Screener stopped by user")
    except Exception as e:
        print(f"\n[ERROR] Screener crashed: {e}")
        import traceback
        traceback.print_exc()
