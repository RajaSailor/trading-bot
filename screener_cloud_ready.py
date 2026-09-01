"""
CLOUD-READY SCREENER - FREE OPTIONS (No Payment Required)
5 MIN BREAKOUT STRATEGY - Works on Free Cloud Platforms
File: screener_cloud_ready.py
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
    from dhanhq import DhanContext, dhanhq
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
📊 CLOUD-READY LIVE SCREENER - 5 MIN BREAKOUT STRATEGY
{'='*90}
[INFO] This screener is optimized to run on FREE cloud platforms:
  ✓ Replit.com (Free)
  ✓ Render.com (Free tier)
  ✓ PythonAnywhere.com (Free tier)
  ✓ Railway.app (Free credits)
  ✓ GitHub Codespaces (Free hours)
  ✓ Any Linux VPS with free tier
  
[INFO] Configuration:
  CLIENT_ID: {CLIENT_ID[:10]}...
  ACCESS_TOKEN: {ACCESS_TOKEN[:20]}...
  TELEGRAM: Connected to {CHAT_ID}
  
Strategy: Most Recent RED/GREEN Candle Breakout
  - Real-time market data (OHLC) from DhanHQ
  - Scans every 5 seconds
  - Triggers on breakout of RED/GREEN highs/lows
  - Sends alerts to Telegram
  - SEBI Compliant (IP Whitelisted)
  - Cloud-optimized (Low CPU/Memory)
  
Market Hours:
  - MCX Crude Oil: 9:00 AM - 11:30 PM IST
  - NSE (NIFTY/BANKNIFTY): 9:15 AM - 3:40 PM IST
{'='*90}
""")

if not DHANHQ_AVAILABLE:
    print("\n[ERROR] DhanHQ library not installed!")
    exit(1)

# Initialize DhanHQ properly
try:
    dhan_context = DhanContext(client_id=CLIENT_ID, access_token=ACCESS_TOKEN)
    dhan_api = dhanhq(dhan_context)
    print("[SUCCESS] ✓ DhanHQ API initialized")
    print("[SUCCESS] ✓ Real-time data feed ready\n")
except Exception as e:
    print(f"[ERROR] Failed to initialize: {e}")
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

# Initialize strategies for each symbol
strategies = {
    "NIFTY": FiveMinBreakoutStrategyRevised(),
    "BANKNIFTY": FiveMinBreakoutStrategyRevised(),
    "CRUDEOIL": FiveMinBreakoutStrategyRevised(),
}

# Track previous LTP to detect price changes
previous_ltp = {
    "NIFTY": None,
    "BANKNIFTY": None,
    "CRUDEOIL": None,
}

# Track scan statistics
scan_stats = {
    "total_scans": 0,
    "successful_scans": 0,
    "signals_triggered": 0,
    "last_update": None
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

def fetch_market_data():
    """Fetch real-time OHLC data from DhanHQ - Cloud Optimized"""
    quotes = {}
    
    try:
        # Build request for all three symbols
        securities_dict = {
            "NSE_FNO": [13, 25],       # NIFTY, BANKNIFTY
            "MCX_FUT": [565899]        # CRUDEOIL
        }
        
        # Use ticker_data for fastest response (minimal bandwidth)
        response = dhan_api.ticker_data(securities_dict)
        
        if response and isinstance(response, dict):
            if response.get('status') == 'success' and 'data' in response:
                data = response.get('data', {})
                
                # Parse each segment's data
                for exchange_segment, quote_data in data.items():
                    if isinstance(quote_data, list):
                        for quote in quote_data:
                            if isinstance(quote, dict):
                                security_id = str(quote.get('security_id', ''))
                                ltp = float(quote.get('LTP', 0))
                                
                                # Map to symbol name
                                symbol = None
                                if security_id == "13":
                                    symbol = "NIFTY"
                                elif security_id == "25":
                                    symbol = "BANKNIFTY"
                                elif security_id == "565899":
                                    symbol = "CRUDEOIL"
                                
                                if symbol and ltp > 0:
                                    # Get OHLC data
                                    high = float(quote.get('high_price', ltp))
                                    low = float(quote.get('low_price', ltp))
                                    open_price = float(quote.get('open_price', ltp))
                                    close_price = float(quote.get('close_price', ltp))
                                    
                                    quotes[symbol] = {
                                        "ltp": ltp,
                                        "high": high if high > 0 else ltp,
                                        "low": low if low > 0 else ltp,
                                        "open": open_price if open_price > 0 else ltp,
                                        "close": close_price if close_price > 0 else ltp,
                                    }
    
    except Exception as e:
        pass
    
    return quotes

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
        scan_stats["signals_triggered"] += 1
        
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

def print_stats():
    """Print screener statistics"""
    if scan_stats["total_scans"] > 0:
        success_rate = (scan_stats["successful_scans"] / scan_stats["total_scans"]) * 100
        print(f"\n[STATS] Total Scans: {scan_stats['total_scans']}")
        print(f"[STATS] Successful: {scan_stats['successful_scans']} ({success_rate:.1f}%)")
        print(f"[STATS] Signals Triggered: {scan_stats['signals_triggered']}")

def screener_loop():
    """Main screener loop - Cloud Optimized"""
    
    market_open, market_status = is_market_hours()
    
    print(f"\n[MARKET STATUS]")
    print(f"  📅 Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  📊 Status: {market_status}\n")
    
    if not market_open:
        print(f"[INFO] Market is closed. Screener not running.")
        return
    
    print(f"[SUCCESS] Market is OPEN ✓")
    print(f"[INFO] Starting 5-min screener (Cloud-Optimized)\n")
    print(f"[INFO] Press Ctrl+C to stop screener\n")
    
    loop_count = 0
    
    try:
        while True:
            loop_count += 1
            scan_stats["total_scans"] = loop_count
            
            market_open, _ = is_market_hours()
            
            if not market_open:
                print(f"\n[INFO] Market hours ended. Screener stopped.")
                print_stats()
                break
            
            current_time = datetime.now().strftime("%H:%M:%S")
            print(f"[{current_time}] Scan #{loop_count}...", end=" ", flush=True)
            
            # Fetch real-time market data
            quotes = fetch_market_data()
            
            if quotes:
                scan_stats["successful_scans"] += 1
                scan_stats["last_update"] = current_time
                print(f"✓ {len(quotes)} quotes", end=" | ", flush=True)
                
                # Process each symbol
                for symbol in ["NIFTY", "BANKNIFTY", "CRUDEOIL"]:
                    if symbol not in quotes:
                        continue
                    
                    quote_data = quotes[symbol]
                    ltp = quote_data.get("ltp", 0)
                    
                    # Only add candle if price changed
                    if previous_ltp[symbol] is None or (ltp != previous_ltp[symbol] and ltp > 0):
                        previous_ltp[symbol] = ltp
                        
                        # Add candle to strategy
                        strategies[symbol].add_candle(
                            symbol,
                            open_price=quote_data.get("open", ltp),
                            high=quote_data.get("high", ltp),
                            low=quote_data.get("low", ltp),
                            close=quote_data.get("close", ltp),
                            oi=0,
                            timestamp=current_time
                        )
                        
                        # Check for signals
                        call_triggered, call_data = strategies[symbol].check_call_breakout(symbol)
                        if call_triggered:
                            process_signal(symbol, call_data)
                        
                        put_triggered, put_data = strategies[symbol].check_put_breakout(symbol)
                        if put_triggered:
                            process_signal(symbol, put_data)
                
                print("Waiting 5s...", flush=True)
            else:
                print(f"⚠️  No data", flush=True)
            
            # Wait 5 seconds before next scan
            time.sleep(5)
    
    except KeyboardInterrupt:
        print(f"\n\n[INFO] Screener stopped by user")
        print_stats()

if __name__ == "__main__":
    try:
        screener_loop()
        
        print(f"\n{'='*90}")
        print(f"✅ SCREENER SESSION COMPLETE")
        print(f"{'='*90}\n")
        
    except Exception as e:
        print(f"\n[ERROR] Screener crashed: {e}")
        import traceback
        traceback.print_exc()
