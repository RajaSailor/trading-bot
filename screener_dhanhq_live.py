"""
LIVE SCREENER - Using DhanHQ Official Library (CORRECT)
5-Min Candle Breakout Strategy with proper DhanHQ initialization
File: screener_dhanhq_live.py
"""

import os
from dotenv import load_dotenv
from telegram import Bot
import asyncio
import time
from datetime import datetime, time as dtime
from strategy_5min_revised import FiveMinBreakoutStrategyRevised
import pandas as pd

# Import DhanHQ correctly
try:
    from dhanhq import DhanContext, MarketFeed
    DHANHQ_AVAILABLE = True
except ImportError:
    DHANHQ_AVAILABLE = False
    print("[WARNING] dhanhq library not installed. Run: pip install dhanhq")

# Load .env
load_dotenv(dotenv_path="./.env", override=True)

CLIENT_ID = os.getenv("API_KEY")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

DISCLAIMER = """⚠️ DISCLAIMER:
I am NOT a SEBI-registered Investment Adviser. Educational purposes only.
Not investment advice. Consult a SEBI-registered advisor before trading.
Trading carries significant risk."""

print(f"""
{'='*90}
📊 LIVE SCREENER - DhanHQ Official Library (5 MIN BREAKOUT STRATEGY)
{'='*90}
[INFO] Configuration:
  CLIENT_ID: {CLIENT_ID[:10]}...
  ACCESS_TOKEN: {ACCESS_TOKEN[:20]}...
  TELEGRAM: Connected to {CHAT_ID}
  DhanHQ Library: {'✓ Available' if DHANHQ_AVAILABLE else '✗ Not installed'}
  
Strategy: Most Recent RED/GREEN Candle Breakout
  - Scans every 5 seconds
  - Triggers on any breakout
  
Market Hours:
  - MCX Crude Oil: 9:00 AM - 11:30 PM IST
  - NSE (NIFTY/BANKNIFTY): 9:15 AM - 3:40 PM IST
{'='*90}
""")

if not DHANHQ_AVAILABLE:
    print("\n[ERROR] DhanHQ library not installed!")
    print("[INFO] Run this command first:")
    print("  pip install dhanhq\n")
    exit(1)

# Initialize DhanHQ context with CLIENT_ID and ACCESS_TOKEN
try:
    dhan_context = DhanContext(client_id=CLIENT_ID, access_token=ACCESS_TOKEN)
    print("[SUCCESS] ✓ DhanHQ context initialized\n")
except Exception as e:
    print(f"[ERROR] Failed to initialize DhanHQ context: {e}")
    print(f"[DEBUG] CLIENT_ID: {CLIENT_ID}, ACCESS_TOKEN: {ACCESS_TOKEN[:30]}...")
    exit(1)

bot = Bot(token=TELEGRAM_TOKEN)

# Load security list
SECURITY_FILE = "./security_list.csv"
try:
    df = pd.read_csv(SECURITY_FILE, low_memory=False)
    print(f"[SUCCESS] Security list loaded: {len(df)} records\n")
except Exception as e:
    print(f"[ERROR] Failed to load security list: {e}")
    exit(1)

# Initialize strategies
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

def fetch_candle_data(symbol_id, exchange_segment):
    """Fetch 5-min candle data using DhanHQ"""
    try:
        # Create MarketFeed object
        marketfeed = MarketFeed(
            security_id=symbol_id,
            exchange_segment=exchange_segment,
            context=dhan_context
        )
        
        # Get intraday data (5-min candles)
        data = marketfeed.intraday(interval=5)
        
        if data and len(data) > 0:
            # Get the most recent candle
            latest = data[-1]
            
            return {
                "open": latest.get("open", 0),
                "high": latest.get("high", 0),
                "low": latest.get("low", 0),
                "close": latest.get("close", 0),
                "volume": latest.get("volume", 0),
                "oi": latest.get("openInterest", 0)
            }
    except Exception as e:
        pass
    
    # Fallback: Try LTP quote
    try:
        marketfeed = MarketFeed(
            security_id=symbol_id,
            exchange_segment=exchange_segment,
            context=dhan_context
        )
        
        quote = marketfeed.quote()
        
        if quote:
            return {
                "open": quote.get("open", 0),
                "high": quote.get("high", 0),
                "low": quote.get("low", 0),
                "close": quote.get("close", 0),
                "volume": quote.get("volume", 0),
                "oi": quote.get("openInterest", 0)
            }
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
    """Format alert message"""
    
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

{DISCLAIMER}
"""
    return alert

def process_signal(symbol, signal_data):
    """Process and send signal"""
    if signal_data:
        print(f"\n{'='*90}")
        print(f"🚀 BREAKOUT SIGNAL TRIGGERED!")
        print(f"{'='*90}")
        print(f"Market: {symbol}")
        print(f"Signal: {signal_data['buy_side']}")
        print(f"Strike: {signal_data['strike_price']}")
        print(f"Entry: {signal_data['entry']}")
        print(f"Stop Loss: {signal_data['stop_loss']}")
        print(f"Target: {signal_data['target']}")
        print(f"Current Price: {signal_data['current_price']}")
        print(f"Time: {signal_data['timestamp']}")
        print(f"{'='*90}\n")
        
        alert_message = format_telegram_alert(symbol, signal_data)
        
        # Send to Telegram
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(send_telegram_alert(alert_message))
        loop.close()

def screener_loop():
    """Main screener loop using DhanHQ library"""
    
    market_open, market_status = is_market_hours()
    
    print(f"\n[MARKET STATUS]")
    print(f"  📅 Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  📊 Status: {market_status}\n")
    
    if not market_open:
        print(f"[INFO] Market is closed. Screener not running.")
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
    
    # Map symbols to exchange segments
    symbol_config = {
        "NIFTY": {"id": nifty_id, "segment": "NSE_FNO", "strategy": strategies["NIFTY"]},
        "BANKNIFTY": {"id": banknifty_id, "segment": "NSE_FNO", "strategy": strategies["BANKNIFTY"]},
        "CRUDEOIL": {"id": crudeoil_id, "segment": "MCX_FUT", "strategy": strategies["CRUDEOIL"]},
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
        
        data_received = False
        
        # Fetch data for each symbol
        for symbol, config in symbol_config.items():
            if not config["id"]:
                continue
            
            # Fetch candle data using DhanHQ library
            candle_data = fetch_candle_data(config["id"], config["segment"])
            
            if candle_data:
                data_received = True
                
                open_price = candle_data.get("open", 0)
                high = candle_data.get("high", 0)
                low = candle_data.get("low", 0)
                close_price = candle_data.get("close", 0)
                oi = candle_data.get("oi", 0)
                
                # Add candle to strategy
                config["strategy"].add_candle(
                    symbol,
                    open_price=open_price,
                    high=high,
                    low=low,
                    close=close_price,
                    oi=oi,
                    timestamp=current_time
                )
                
                # Check for signals
                call_triggered, call_data = config["strategy"].check_call_breakout(symbol)
                if call_triggered:
                    process_signal(symbol, call_data)
                
                put_triggered, put_data = config["strategy"].check_put_breakout(symbol)
                if put_triggered:
                    process_signal(symbol, put_data)
        
        if data_received:
            print(f"✓ Data received")
        else:
            print(f"⚠️  No data")
        
        # Wait 5 seconds before next scan
        time.sleep(5)

if __name__ == "__main__":
    try:
        screener_loop()
        
        print(f"\n{'='*90}")
        print(f"✅ SCREENER SESSION COMPLETE")
        print(f"{'='*90}\n")
        
    except KeyboardInterrupt:
        print(f"\n\n[INFO] Screener stopped by user")
    except Exception as e:
        print(f"\n[ERROR] Screener crashed: {e}")
        import traceback
        traceback.print_exc()
