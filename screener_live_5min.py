"""
LIVE SCREENER - 5 MIN BREAKOUT STRATEGY
Production screener with your strategy and Telegram alerts
File: screener_live_5min.py
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
from strategy_5min_breakout import FiveMinBreakoutStrategy

# Load .env
load_dotenv(dotenv_path="./.env", override=True)

API_KEY = os.getenv("API_KEY")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

BASE_URL = "https://api.dhan.co/v2"

DISCLAIMER = """
⚠️ DISCLAIMER:
I am NOT a SEBI-registered Investment Adviser (IA) or Research Analyst (RA). All content, charts, analysis, and discussions provided are strictly for general educational and informational purposes only.

This content does NOT constitute financial, legal, tax, or investment advice, nor does it contain specific buy, sell, or hold recommendations.

Trading and investing in securities or derivatives carry SIGNIFICANT FINANCIAL RISKS. Historical results are for illustration only.

Please conduct your own independent research or consult a qualified, SEBI-registered financial professional before making any investment or trading decisions.

We accept NO LIABILITY for any direct or indirect profit or loss from this information.
"""

print(f"""
{'='*80}
📊 LIVE SCREENER - 5 MIN CANDLE BREAKOUT STRATEGY
{'='*80}
[INFO] Configuration:
  API_KEY: {API_KEY[:10]}...
  TELEGRAM_TOKEN: {TELEGRAM_TOKEN[:15]}...
  CHAT_ID: {CHAT_ID}
  
Strategy: 5-Min Candle Breakout (Call & Put)
Market Hours:
  - MCX Crude Oil: 9:00 AM - 11:30 PM
  - NSE (NIFTY/BANKNIFTY): 9:15 AM - 3:40 PM
  
Alert Format: Market | Buy Call/Put | Strike | Entry | Stop Loss | Target
{'='*80}
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

# Initialize strategy for each instrument
strategies = {
    "NIFTY": FiveMinBreakoutStrategy(lookback_periods=20),
    "BANKNIFTY": FiveMinBreakoutStrategy(lookback_periods=20),
    "CRUDEOIL": FiveMinBreakoutStrategy(lookback_periods=20),
}

def is_market_hours():
    """Check if it's trading hours for MCX or NSE"""
    now = datetime.now()
    current_time = now.time()
    current_day = now.weekday()  # 0=Monday, 6=Sunday
    
    # Closed on weekends
    if current_day >= 5:
        return False, "Market Closed (Weekend)"
    
    # MCX hours: 9:00 AM - 11:30 PM
    mcx_open = dtime(9, 0)
    mcx_close = dtime(23, 30)
    
    # NSE hours: 9:15 AM - 3:40 PM
    nse_open = dtime(9, 15)
    nse_close = dtime(15, 40)
    
    if mcx_open <= current_time <= mcx_close:
        return True, "MCX Hours (9:00 AM - 11:30 PM)"
    
    if nse_open <= current_time <= nse_close:
        return True, "NSE Hours (9:15 AM - 3:40 PM)"
    
    return False, "Market Closed (Outside Trading Hours)"

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
        print(f"[SUCCESS] ✓ Alert sent to Telegram")
    except Exception as e:
        print(f"[ERROR] Telegram send failed: {e}")

def format_telegram_alert(symbol, signal_data):
    """Format alert message in your specified format"""
    
    alert = f"""
📊 BREAKOUT ALERT - 5 MIN STRATEGY

🎯 Market: {symbol}
📈 Signal: {signal_data['buy_side']}
💰 Strike Price: {signal_data['strike_price']}
📍 Entry: {signal_data['entry']}
🛑 Stop Loss: {signal_data['stop_loss']}
🎪 Target: {signal_data['target']}

⏰ Time: {signal_data['timestamp']}
💹 Current Price: {signal_data['current_price']}

{'─'*50}
{DISCLAIMER}
"""
    return alert

def process_signal(symbol, signal_data):
    """Process and send signal to Telegram"""
    if signal_data:
        print(f"\n[ALERT] ✓ {signal_data['buy_side']} Signal detected for {symbol}")
        print(f"  Strike: {signal_data['strike_price']}")
        print(f"  Entry: {signal_data['entry']}")
        print(f"  Stop Loss: {signal_data['stop_loss']}")
        print(f"  Target: {signal_data['target']}")
        
        alert_message = format_telegram_alert(symbol, signal_data)
        
        # Send to Telegram
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(send_telegram_alert(alert_message))
        loop.close()

def add_simulated_candles(symbol, strategy):
    """Add simulated historical candles for testing"""
    
    # Generate 19 historical candles with alternating colors
    for i in range(19):
        if i % 2 == 0:  # RED candles
            strategy.add_candle(
                symbol,
                open_price=8000 + i*10,
                high=8020 + i*10,
                low=7990 + i*10,
                close=8005 + i*10,  # Close < Open = RED
                oi=500000 + i*10000,
                timestamp=f"Candle {i+1}"
            )
        else:  # GREEN candles
            strategy.add_candle(
                symbol,
                open_price=7995 + i*10,
                high=8025 + i*10,
                low=8000 + i*10,
                close=8020 + i*10,  # Close > Open = GREEN
                oi=505000 + i*10000,
                timestamp=f"Candle {i+1}"
            )

def screener_loop():
    """Main screener loop"""
    
    market_open, market_status = is_market_hours()
    
    print(f"\n[MARKET STATUS]")
    print(f"  📅 Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  📊 Status: {market_status}\n")
    
    if not market_open:
        print(f"[INFO] Market is closed. Using simulated data for testing...\n")
        
        # Use simulated data
        add_simulated_candles("CRUDEOIL", strategies["CRUDEOIL"])
        
        # Test CALL breakout
        call_triggered, call_data = strategies["CRUDEOIL"].check_call_breakout("CRUDEOIL")
        if call_triggered:
            print(f"[SIMULATION] CALL Breakout detected!")
            process_signal("MCX CRUDE OIL (Simulated)", call_data)
        
        # Test PUT breakout
        put_triggered, put_data = strategies["CRUDEOIL"].check_put_breakout("CRUDEOIL")
        if put_triggered:
            print(f"[SIMULATION] PUT Breakout detected!")
            process_signal("MCX CRUDE OIL (Simulated)", put_data)
        
        return
    
    print(f"[SUCCESS] Market is OPEN ✓")
    print(f"[INFO] Starting 5-min breakout screener...")
    print(f"[INFO] Checking conditions every 30 seconds...\n")
    
    # Get security IDs
    nifty_id = get_security_id("NIFTY")
    banknifty_id = get_security_id("BANKNIFTY")
    crudeoil_id = get_security_id("CRUDEOIL")
    
    print(f"[INFO] Security IDs:")
    print(f"  NIFTY: {nifty_id}")
    print(f"  BANKNIFTY: {banknifty_id}")
    print(f"  CRUDEOIL: {crudeoil_id}\n")
    
    if not all([nifty_id, banknifty_id, crudeoil_id]):
        print(f"[WARNING] Some security IDs not found, will use simulated data\n")
    
    # Instruments
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
        print(f"\n[{current_time}] Scan #{loop_count}...")
        
        # Fetch LTP
        ltp_data = fetch_ltp(instruments)
        
        if ltp_data:
            print(f"[DEBUG] Received LTP data")
            
            # Process each symbol
            for symbol, ltp in ltp_data.items():
                if symbol in strategies:
                    last_price = ltp.get("last_price", 0)
                    oi = ltp.get("openInterest", 0)
                    
                    # Add candle (simplified - actual would need OHLC)
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
            print(f"[WARNING] No LTP data received")
        
        # Wait 30 seconds before next scan
        print(f"[INFO] Waiting 30 seconds...")
        time.sleep(30)

if __name__ == "__main__":
    try:
        screener_loop()
        
        print(f"\n{'='*80}")
        print(f"✅ SCREENER SESSION COMPLETE")
        print(f"{'='*80}\n")
        
    except KeyboardInterrupt:
        print(f"\n[INFO] Screener stopped by user")
    except Exception as e:
        print(f"\n[ERROR] Screener crashed: {e}")
        import traceback
        traceback.print_exc()
