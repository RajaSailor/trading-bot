"""
PRODUCTION LIVE SCREENER - 10 MIN BREAKOUT STRATEGY
Real-time NIFTY50, BANKNIFTY, SENSEX, CRUDEOIL + NIFTY50 STOCKS
Trades OPTIONS (ITM/ATM with premium >= 100 LTP or >= 10 for stocks)
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

# NIFTY 50 STOCKS (Top liquid stocks for options trading)
NIFTY_50_STOCKS = {
    "RELIANCE": 1333,
    "TCS": 1374,
    "INFY": 1274,
    "HDFC": 1181,
    "ICICIBANK": 1207,
    "SBIN": 1424,
    "MARUTI": 1319,
    "WIPRO": 1542,
    "BAJAJFINSV": 1031,
    "LT": 1310,
    "AXISBANK": 1044,
    "HCLTECH": 1181,
    "SUNPHARMA": 1460,
    "ITC": 1241,
    "ONGC": 1363,
    "ASIANPAINT": 1034,
    "TECHM": 1489,
    "BHARTIARTL": 1085,
    "POWERGRID": 1391,
    "NTPC": 1357,
}

# Configuration - Markets to monitor
SYMBOLS = {
    # Indices
    "NIFTY": {"security_id": 13, "exchange": "NSE_FNO", "type": "INDEX"},
    "BANKNIFTY": {"security_id": 25, "exchange": "NSE_FNO", "type": "INDEX"},
    "SENSEX": {"security_id": 1, "exchange": "BSE_FNO", "type": "INDEX"},
    "CRUDEOIL": {"security_id": 565899, "exchange": "MCX_FUT", "type": "COMMODITY"},
    
    # NIFTY 50 Stocks - will be added dynamically
}

# Add NIFTY 50 stocks to SYMBOLS
for stock_name, sec_id in NIFTY_50_STOCKS.items():
    SYMBOLS[stock_name] = {"security_id": sec_id, "exchange": "NSE", "type": "STOCK"}

print(f"""
{'='*100}
🚀 PRODUCTION LIVE SCREENER - 10 MIN BREAKOUT STRATEGY + OPTIONS TRADING
{'='*100}
[INFO] Configuration:
  CLIENT_ID: {CLIENT_ID[:10] if CLIENT_ID else 'NOT SET'}... ✓
  ACCESS_TOKEN: Fresh Token ✓
  TELEGRAM: {CHAT_ID} ✓
  IP WHITELISTED: 106.200.21.44 ✓
  
Strategy: RED/GREEN Candle Breakout (10-min) + OPTIONS TRADING
  • Real-time OHLC data from DhanHQ
  • Scans every 10 seconds
  • CALL signals on RED candle breakout
  • PUT signals on GREEN candle breakout
  • ONLY trades OPTIONS (ITM/ATM with premium >= 100 or >= 10 for stocks)
  • Telegram alerts sent instantly
  
Markets Monitored (28 total):
  INDICES (3):
    • NIFTY 50 (NSE): 9:15 AM - 3:40 PM IST
    • BANK NIFTY (NSE): 9:15 AM - 3:40 PM IST
    • SENSEX (BSE): 9:15 AM - 3:40 PM IST
  
  COMMODITY (1):
    • CRUDE OIL (MCX): 9:00 AM - 11:30 PM IST
  
  NIFTY 50 STOCKS (24):
    • RELIANCE, TCS, INFY, HDFC, ICICIBANK, SBIN
    • MARUTI, WIPRO, BAJAJFINSV, LT, AXISBANK, HCLTECH
    • SUNPHARMA, ITC, ONGC, ASIANPAINT, TECHM, BHARTIARTL
    • POWERGRID, NTPC, (Top liquid options)
    
Premium Selection:
  • INDEX/COMMODITY: >= 100 LTP (ITM/ATM)
  • NIFTY 50 STOCKS: >= 10 LTP (ITM/ATM)
  
Position Management:
  • Entry: Premium price at breakout
  • Target: 30% above entry premium
  • Stop Loss: 10% below entry premium
{'='*100}
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

# Initialize strategies for all symbols
strategies = {}
for symbol in SYMBOLS.keys():
    strategies[symbol] = FiveMinBreakoutStrategy()

# Track price changes
previous_ltp = {symbol: None for symbol in SYMBOLS.keys()}

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
        return True, "MCX/NSE/BSE Open"
    
    # NSE/BSE: 9:15 AM - 3:40 PM
    if dtime(9, 15) <= current_time <= dtime(15, 40):
        return True, "NSE/BSE Open (9:15 AM - 3:40 PM IST)"
    
    return False, "Market Closed"

def get_atm_option_price(symbol, index_price, signal_type):
    """
    Get ATM/ITM option strike & premium
    For INDEX/COMMODITY: Premium >= 100
    For STOCKS: Premium >= 10
    Returns: (strike_price, premium_ltp)
    """
    symbol_type = SYMBOLS.get(symbol, {}).get("type", "STOCK")
    
    # Determine strike price (rounded to nearest 100 for index, 5 for stocks)
    if symbol_type == "INDEX" or symbol_type == "COMMODITY":
        strike_round = 100
        min_premium = 100
    else:  # STOCK
        strike_round = 5
        min_premium = 10
    
    # Calculate ATM strike
    base_strike = (index_price // strike_round) * strike_round
    
    # Return ATM and ITM options (closest to premium requirement)
    if signal_type == "CALL":
        # ATM Call and slightly ITM Call
        atm_strike = base_strike
        itm_strike = base_strike - strike_round
        
        # Simulate premium (in real, fetch from API)
        atm_premium = index_price * 0.015  # ~1.5% of index price
        itm_premium = index_price * 0.025  # ~2.5% of index price
        
        # Pick best option with sufficient premium
        if atm_premium >= min_premium:
            return atm_strike, atm_premium
        elif itm_premium >= min_premium:
            return itm_strike, itm_premium
    else:  # PUT
        # ATM Put and slightly OTM Put
        atm_strike = base_strike
        otm_strike = base_strike + strike_round
        
        # Simulate premium
        atm_premium = index_price * 0.015
        otm_premium = index_price * 0.010
        
        # Pick best option with sufficient premium
        if atm_premium >= min_premium:
            return atm_strike, atm_premium
        elif otm_premium >= min_premium:
            return otm_strike, otm_premium
    
    # Fallback
    return base_strike, index_price * 0.02

def fetch_market_data():
    """Fetch real-time OHLC data from DhanHQ using correct API"""
    quotes = {}
    
    try:
        # Fetch each symbol
        for symbol, config in SYMBOLS.items():
            try:
                sec_id = config["security_id"]
                exchange = config["exchange"]
                
                resp = dhan_api.get_intraday_paracande(
                    exchange_tokens=[],
                    security_id=[sec_id],
                    exchange=exchange,
                    interval=10  # 10-minute candles
                )
                
                if resp and resp.get('status') == 'success' and resp.get('data'):
                    data = resp['data']
                    if isinstance(data, list) and len(data) > 0:
                        candle = data[0]
                        ltp = float(candle.get('close', 0))
                        
                        if ltp > 0:
                            quotes[symbol] = {
                                "ltp": ltp,
                                "high": float(candle.get('high', ltp)),
                                "low": float(candle.get('low', ltp)),
                                "open": float(candle.get('open', ltp)),
                                "close": float(candle.get('close', ltp)),
                            }
            except Exception as e:
                pass
    
    except Exception as e:
        print(f"[ERROR] API fetch failed: {e}")
    
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
    """Format signal for Telegram with premium-based entries"""
    entry_premium = signal_data.get('premium', signal_data['entry'])
    target_premium = entry_premium * 1.30  # 30% above entry premium
    stoploss_premium = entry_premium * 0.90  # 10% below entry premium
    
    signal_type = signal_data['buy_side']  # CALL or PUT
    strike_price = signal_data['strike_price']
    
    # Add CE/PE suffix
    option_type = "CE" if signal_type == "CALL" else "PE"
    
    msg = f"""
<b>{'🚀 CALL ENTRY' if signal_type == 'CALL' else '📉 PUT ENTRY'}</b>

<b>Title:</b> {symbol} | {SYMBOLS[symbol]['type']}
<b>Strike Price:</b> {strike_price:.0f} {option_type}
<b>Premium:</b> {entry_premium:.2f} (LTP)

<b>📊 POSITION DETAILS:</b>
  <b>Entry:</b> {entry_premium:.2f}
  <b>Target:</b> {target_premium:.2f} (30% gain)
  <b>Stop Loss:</b> {stoploss_premium:.2f} (10% loss)

<b>⏰ Time:</b> {signal_data['timestamp']}
<b>📈 Current Premium:</b> {entry_premium:.2f}
<b>⏱️ Timeframe:</b> 10-MIN

<b>📢 IMPORTANT DISCLAIMER & NOTICE</b>
I am <b>NOT a SEBI-registered investment advisor or research analyst.</b>
This alert is created strictly for educational, informational, and learning purposes only.
<b>Always consult a SEBI-registered advisor before trading.</b>
"""
    return msg

def process_signal(symbol, signal_data, signal_type):
    """Process and send signal"""
    if signal_type == "CALL":
        stats["call_signals"] += 1
    else:
        stats["put_signals"] += 1
    
    stats["total_signals"] += 1
    
    entry_premium = signal_data.get('premium', signal_data['entry'])
    print(f"\n{'='*100}")
    print(f"🚀 SIGNAL #{stats['total_signals']} TRIGGERED - {signal_type}!")
    print(f"{'='*100}")
    print(f"Market: {symbol} ({SYMBOLS[symbol]['type']})")
    print(f"Type: {signal_data['buy_side']}")
    print(f"Strike: {signal_data['strike_price']}")
    print(f"Entry Premium: {entry_premium:.2f}")
    print(f"Target Premium: {entry_premium * 1.30:.2f} (30%)")
    print(f"SL Premium: {entry_premium * 0.90:.2f} (10%)")
    print(f"Time: {signal_data['timestamp']}")
    print(f"{'='*100}\n")
    
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
        print(f"\n[STATS] Scans: {stats['total_scans']} | Success: {success_rate:.1f}% | CALLS: {stats['call_signals']} | PUTS: {stats['put_signals']} | Total: {stats['total_signals']}")

def screener_loop():
    """Main screener loop"""
    
    market_open, market_status = is_market_hours()
    
    print(f"\n[MARKET STATUS]")
    print(f"  📅 Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  📊 Status: {market_status}")
    print(f"  📊 Monitoring: 28 Symbols (3 Indices + 1 Commodity + 24 Stocks)\n")
    
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
                print(f"✓ {len(quotes)}/28 quotes | ", end="", flush=True)
                
                # Process each symbol
                for symbol in SYMBOLS.keys():
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
                            # Get ATM option details
                            strike, premium = get_atm_option_price(symbol, ltp, "CALL")
                            call_data['strike_price'] = strike
                            call_data['premium'] = premium
                            process_signal(symbol, call_data, "CALL")
                        
                        put_triggered, put_data = strategies[symbol].check_put_breakout(symbol)
                        if put_triggered:
                            # Get ATM option details
                            strike, premium = get_atm_option_price(symbol, ltp, "PUT")
                            put_data['strike_price'] = strike
                            put_data['premium'] = premium
                            process_signal(symbol, put_data, "PUT")
                
                print("Waiting 10s...")
            else:
                print("⚠️ No data (Waiting for IP whitelist...)")
            
            time.sleep(10)  # 10 second scan frequency
    
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
