"""
PRODUCTION LIVE SCREENER - 5 MIN BREAKOUT STRATEGY
Real-time NIFTY, BANKNIFTY, CRUDEOIL trading signals
Sends Telegram alerts on breakout signals
File: screener_production.py
"""

import os
from dotenv import load_dotenv
from telegram import Bot
import asyncio
import time
from datetime import datetime, time as dtime
from strategy import FiveMinBreakoutStrategy
import pandas as pd

# Import DhanHQ
try:
    from dhanhq import DhanContext, dhanhq
    DHANHQ_AVAILABLE = True
except ImportError:
    DHANHQ_AVAILABLE = False
    print("[WARNING] dhanhq library not installed. Run: pip install dhanhq")

# Load environment variables
load_dotenv(dotenv_path="./.env", override=True)

CLIENT_ID = os.getenv("API_KEY")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# Configuration
SYMBOLS = {
    "NIFTY": {"security_id": 13, "exchange": "NSE_FNO"},
    "BANKNIFTY": {"security_id": 25, "exchange": "NSE_FNO"},
    "CRUDEOIL": {"security_id": 565899, "exchange": "MCX_FUT"}
}

DISCLAIMER = """⚠️ DISCLAIMER:
Educational purposes only. Not investment advice.
Consult a SEBI-registered advisor before trading."""

print(f"""
{'='*90}
🚀 PRODUCTION LIVE SCREENER - 5 MIN BREAKOUT STRATEGY
{'='*90}
[INFO] Configuration:
  CLIENT_ID: {CLIENT_ID[:10]}... ✓
  ACCESS_TOKEN: Fresh Token ✓
  TELEGRAM: {CHAT_ID} ✓
  IP WHITELISTED: 106.200.21.44 ✓
  
Strategy: RED/GREEN Candle Breakout (5-min)
  • Real-time OHLC data from DhanHQ
  • Scans every 5 seconds
  • CALL signals on RED candle breakout
  • PUT signals on GREEN candle breakout
  • Telegram alerts sent instantly
  
Markets:
  • NIFTY (NSE): 9:15 AM - 3:40 PM IST
  • BANKNIFTY (NSE): 9:15 AM - 3:40 PM IST
  • CRUDEOIL (MCX): 9:00 AM - 11:30 PM IST
{'='*90}
""")

if not DHANHQ_AVAILABLE:
    print("\n[ERROR] DhanHQ not installed!")
    print("Run: pip install dhanhq")
    exit(1)

# Initialize DhanHQ API
try:
    dhan_context = DhanContext(client_id=CLIENT_ID, access_token=ACCESS_TOKEN)
    dhan_api = dhanhq(dhan_context)
    print("[SUCCESS] ✓ DhanHQ API Connected")
except Exception as e:
    print(f"[ERROR] DhanHQ initialization failed: {e}")
    exit(1)

# Initialize Telegram Bot
try:
    bot = Bot(token=TELEGRAM_TOKEN)
    print("[SUCCESS] ✓ Telegram Bot Connected")
except Exception as e:
    print(f"[ERROR] Telegram connection failed: {e}")
    exit(1)

# Load security list
try:
    df = pd.read_csv("security_list.csv", low_memory=False)
    print(f"[SUCCESS] ✓ Security list loaded: {len(df)} symbols\n")
except Exception as e:
    print(f"[WARNING] Security list not found: {e}\n")

# Initialize strategies
strategies = {
    "NIFTY": FiveMinBreakoutStrategy(),
    "BANKNIFTY": FiveMinBreakoutStrategy(),
    "CRUDEOIL": FiveMinBreakoutStrategy(),
}

# Track price changes
previous_ltp = {
    "NIFTY": None,
    "BANKNIFTY": None,
    "CRUDEOIL": None,
}

# Statistics
stats = {
    "total_scans": 0,
    "successful_scans": 0,
    "call_signals": 0,
    "put_signals": 0,
    "total_signals": 0,
}

def is_market_hours():
    """Check if market is open"""
    now = datetime.now()
    current_time = now.time()
    day = now.weekday()
    
    if day >= 5:  # Weekend
        return False, "Market Closed (Weekend)"
    
    # MCX: 9:00 AM - 11:30 PM
    if dtime(9, 0) <= current_time <= dtime(23, 30):
        return True, "MCX Open (9:00 AM - 11:30 PM IST)"
    
    # NSE: 9:15 AM - 3:40 PM
    if dtime(9, 15) <= current_time <= dtime(15, 40):
        return True, "NSE Open (9:15 AM - 3:40 PM IST)"
    
    return False, "Market Closed"

def fetch_market_data():
    """Fetch real-time OHLC data from DhanHQ"""
    quotes = {}
    
    try:
        # Build securities dict
        securities_dict = {
            "NSE_FNO": [13, 25],       # NIFTY, BANKNIFTY
            "MCX_FUT": [565899]        # CRUDEOIL
        }
        
        # Fetch data
        response = dhan_api.ticker_data(securities_dict)
        
        if response and response.get('status') == 'success' and 'data' in response:
            data = response.get('data', {})
            
            for exchange, quote_list in data.items():
                if isinstance(quote_list, list):
                    for quote in quote_list:
                        if isinstance(quote, dict):
                            sec_id = str(quote.get('security_id', ''))
                            ltp = float(quote.get('LTP', 0))
                            
                            # Map to symbol
                            symbol = None
                            if sec_id == "13":
                                symbol = "NIFTY"
                            elif sec_id == "25":
                                symbol = "BANKNIFTY"
                            elif sec_id == "565899":
                                symbol = "CRUDEOIL"
                            
                            if symbol and ltp > 0:
                                quotes[symbol] = {
                                    "ltp": ltp,
                                    "high": float(quote.get('high_price', ltp)),
                                    "low": float(quote.get('low_price', ltp)),
                                    "open": float(quote.get('open_price', ltp)),
                                    "close": float(quote.get('close_price', ltp)),
                                }
    
    except Exception as e:
        pass
    
    return quotes

async def send_telegram_alert(message):
    """Send Telegram alert"""
    try:
        await bot.send_message(chat_id=int(CHAT_ID), text=message, parse_mode="HTML")
        return True
    except Exception as e:
        print(f"[ERROR] Telegram failed: {e}")
        return False

def format_signal_message(symbol, signal_data):
    """Format signal for Telegram"""
    msg = f"""
<b>🚀 BREAKOUT SIGNAL TRIGGERED!</b>

<b>Market:</b> {symbol}
<b>Signal Type:</b> {signal_data['buy_side']}
<b>Strike Price:</b> {signal_data['strike_price']}

<b>📊 Entry Levels:</b>
  Entry: {signal_data['entry']}
  Stop Loss: {signal_data['stop_loss']}
  Target: {signal_data['target']}

<b>⏰ Time:</b> {signal_data['timestamp']}
<b>💹 Current Price:</b> {signal_data['current_price']}

{DISCLAIMER}
"""
    return msg

def process_signal(symbol, signal_data, signal_type):
    """Process and send signal"""
    if signal_type == "CALL":
        stats["call_signals"] += 1
    else:
        stats["put_signals"] += 1
    
    stats["total_signals"] += 1
    
    print(f"\n{'='*90}")
    print(f"🚀 SIGNAL #{stats['total_signals']} TRIGGERED!")
    print(f"{'='*90}")
    print(f"Market: {symbol}")
    print(f"Type: {signal_data['buy_side']}")
    print(f"Strike: {signal_data['strike_price']}")
    print(f"Entry: {signal_data['entry']}")
    print(f"SL: {signal_data['stop_loss']}")
    print(f"Target: {signal_data['target']}")
    print(f"Time: {signal_data['timestamp']}")
    print(f"{'='*90}\n")
    
    # Send Telegram
    message = format_signal_message(symbol, signal_data)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(send_telegram_alert(message))
    loop.close()

def print_status():
    """Print status update"""
    if stats["total_scans"] > 0:
        success_rate = (stats["successful_scans"] / stats["total_scans"]) * 100
        print(f"\n[STATS] Scans: {stats['total_scans']} | Success: {success_rate:.1f}% | Signals: {stats['total_signals']}")

def screener_loop():
    """Main screener loop"""
    
    market_open, market_status = is_market_hours()
    
    print(f"\n[MARKET STATUS]")
    print(f"  📅 Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  📊 Status: {market_status}\n")
    
    if not market_open:
        print(f"[INFO] Market is closed. Please run during market hours.")
        return
    
    print(f"[SUCCESS] Market is OPEN ✓")
    print(f"[INFO] Press Ctrl+C to stop screener\n")
    
    try:
        while True:
            stats["total_scans"] += 1
            market_open, _ = is_market_hours()
            
            if not market_open:
                print(f"\n[INFO] Market closed. Screener stopped.")
                print_status()
                break
            
            current_time = datetime.now().strftime("%H:%M:%S")
            print(f"[{current_time}] Scan #{stats['total_scans']}...", end=" ", flush=True)
            
            # Fetch data
            quotes = fetch_market_data()
            
            if quotes:
                stats["successful_scans"] += 1
                print(f"✓ {len(quotes)} quotes | ", end="", flush=True)
                
                # Process each symbol
                for symbol in ["NIFTY", "BANKNIFTY", "CRUDEOIL"]:
                    if symbol not in quotes:
                        continue
                    
                    quote = quotes[symbol]
                    ltp = quote.get("ltp", 0)
                    
                    # Only process if price changed
                    if previous_ltp[symbol] is None or (ltp != previous_ltp[symbol] and ltp > 0):
                        previous_ltp[symbol] = ltp
                        
                        # Add candle
                        strategies[symbol].add_candle(
                            symbol,
                            open_price=quote.get("open", ltp),
                            high=quote.get("high", ltp),
                            low=quote.get("low", ltp),
                            close=quote.get("close", ltp),
                            oi=0,
                            timestamp=current_time
                        )
                        
                        # Check signals
                        call_triggered, call_data = strategies[symbol].check_call_breakout(symbol)
                        if call_triggered:
                            process_signal(symbol, call_data, "CALL")
                        
                        put_triggered, put_data = strategies[symbol].check_put_breakout(symbol)
                        if put_triggered:
                            process_signal(symbol, put_data, "PUT")
                
                print("Waiting 5s...")
            else:
                print("⚠️ No data")
            
            time.sleep(5)
    
    except KeyboardInterrupt:
        print(f"\n\n[INFO] Screener stopped by user")
        print_status()

if __name__ == "__main__":
    try:
        screener_loop()
    except Exception as e:
        print(f"\n[ERROR] Screener crashed: {e}")
        import traceback
        traceback.print_exc()
