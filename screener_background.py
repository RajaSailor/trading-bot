"""
PRODUCTION SCREENER - BACKGROUND THREAD
Handles real-time market scanning in background
File: screener_background.py
"""

import os
import time
import threading
import logging
from datetime import datetime, time as dtime
from dotenv import load_dotenv
from strategy import FiveMinBreakoutStrategy
import pandas as pd
import requests
from telegram import Bot
import asyncio

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv(dotenv_path="./.env", override=True)

CLIENT_ID = os.getenv("API_KEY")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# Import DhanHQ
try:
    from dhanhq import DhanContext, dhanhq
    DHANHQ_AVAILABLE = True
except ImportError:
    DHANHQ_AVAILABLE = False
    logger.warning("[WARNING] dhanhq library not installed")

# Detect public IP
def detect_public_ip():
    """Detect Render's public IP address"""
    try:
        response = requests.get('https://api.ipify.org?format=json', timeout=5)
        return response.json()['ip']
    except Exception as e:
        logger.error(f"IP detection failed: {e}")
        return "UNKNOWN"

PUBLIC_IP = detect_public_ip()

# NIFTY 50 STOCKS
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

# Configuration
SYMBOLS = {
    "NIFTY": {"security_id": 13, "exchange": "NSE_FNO", "type": "INDEX"},
    "BANKNIFTY": {"security_id": 25, "exchange": "NSE_FNO", "type": "INDEX"},
    "SENSEX": {"security_id": 1, "exchange": "BSE_FNO", "type": "INDEX"},
    "CRUDEOIL": {"security_id": 565899, "exchange": "MCX_FUT", "type": "COMMODITY"},
}

for stock_name, sec_id in NIFTY_50_STOCKS.items():
    SYMBOLS[stock_name] = {"security_id": sec_id, "exchange": "NSE", "type": "STOCK"}

# Global state
screener_state = {
    "running": False,
    "total_scans": 0,
    "successful_scans": 0,
    "call_signals": 0,
    "put_signals": 0,
    "total_signals": 0,
    "last_scan_time": None,
    "dhan_connected": False,
    "telegram_connected": False,
    "market_open": False,
    "public_ip": PUBLIC_IP,
    "errors": [],
}

strategies = {}
previous_ltp = {}
bot = None
dhan_api = None

# Initialize components
def initialize():
    """Initialize screener components"""
    global bot, dhan_api
    
    logger.info("🚀 Initializing screener background...")
    
    # Initialize strategies
    for symbol in SYMBOLS.keys():
        strategies[symbol] = FiveMinBreakoutStrategy()
        previous_ltp[symbol] = None
    
    # Initialize DhanHQ
    try:
        if DHANHQ_AVAILABLE:
            dhan_context = DhanContext(client_id=CLIENT_ID, access_token=ACCESS_TOKEN)
            dhan_api = dhanhq(dhan_context)
            screener_state["dhan_connected"] = True
            logger.info("[SUCCESS] ✓ DhanHQ API Connected")
    except Exception as e:
        logger.error(f"[ERROR] DhanHQ failed: {e}")
        screener_state["dhan_connected"] = False
    
    # Initialize Telegram
    try:
        bot = Bot(token=TELEGRAM_TOKEN)
        screener_state["telegram_connected"] = True
        logger.info("[SUCCESS] ✓ Telegram Bot Connected")
    except Exception as e:
        logger.error(f"[ERROR] Telegram failed: {e}")
        screener_state["telegram_connected"] = False
    
    logger.info("✅ Screener background initialized")

def is_market_hours():
    """Check if market is open"""
    now = datetime.now()
    current_time = now.time()
    day = now.weekday()
    
    if day >= 5:
        return False
    
    if dtime(9, 0) <= current_time <= dtime(23, 30):
        return True
    
    if dtime(9, 15) <= current_time <= dtime(15, 40):
        return True
    
    return False

def fetch_market_data():
    """Fetch real-time OHLC data"""
    quotes = {}
    
    if not dhan_api:
        return quotes
    
    try:
        for symbol, config in SYMBOLS.items():
            try:
                resp = dhan_api.get_intraday_paracande(
                    exchange_tokens=[],
                    security_id=[config["security_id"]],
                    exchange=config["exchange"],
                    interval=10
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
        logger.error(f"[ERROR] API fetch failed: {e}")
    
    return quotes

async def send_telegram_alert(message):
    """Send Telegram alert"""
    try:
        if not bot:
            return False
        await bot.send_message(chat_id=int(CHAT_ID), text=message, parse_mode="HTML")
        return True
    except Exception as e:
        logger.error(f"[ERROR] Telegram failed: {e}")
        return False

def format_signal_message(symbol, signal_data):
    """Format signal for Telegram"""
    entry_premium = signal_data.get('premium', signal_data['entry'])
    target_premium = entry_premium * 1.30
    stoploss_premium = entry_premium * 0.90
    
    signal_type = signal_data['buy_side']
    strike_price = signal_data['strike_price']
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
        screener_state["call_signals"] += 1
    else:
        screener_state["put_signals"] += 1
    
    screener_state["total_signals"] += 1
    
    entry_premium = signal_data.get('premium', signal_data['entry'])
    logger.info(f"\n{'='*80}")
    logger.info(f"🚀 SIGNAL #{screener_state['total_signals']} TRIGGERED - {signal_type}!")
    logger.info(f"Market: {symbol} | Entry: {entry_premium:.2f}")
    logger.info(f"{'='*80}\n")
    
    message = format_signal_message(symbol, signal_data)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(send_telegram_alert(message))
    loop.close()

def screener_loop():
    """Main screener loop - runs in background thread"""
    
    logger.info("🚀 SCREENER BACKGROUND THREAD STARTED")
    logger.info(f"Public IP: {PUBLIC_IP}")
    logger.info(f"Monitoring: 28 Symbols")
    
    screener_state["running"] = True
    
    try:
        while screener_state["running"]:
            screener_state["total_scans"] += 1
            market_open = is_market_hours()
            screener_state["market_open"] = market_open
            screener_state["last_scan_time"] = datetime.now().isoformat()
            
            if not market_open:
                time.sleep(5)
                continue
            
            current_time = datetime.now().strftime("%H:%M:%S")
            
            # Fetch data
            quotes = fetch_market_data()
            
            if quotes:
                screener_state["successful_scans"] += 1
                
                for symbol in SYMBOLS.keys():
                    if symbol not in quotes:
                        continue
                    
                    quote = quotes[symbol]
                    ltp = quote.get("ltp", 0)
                    
                    if previous_ltp[symbol] is None or (ltp != previous_ltp[symbol] and ltp > 0):
                        previous_ltp[symbol] = ltp
                        
                        strategies[symbol].add_candle(
                            symbol,
                            open_price=quote.get("open", ltp),
                            high=quote.get("high", ltp),
                            low=quote.get("low", ltp),
                            close=quote.get("close", ltp),
                            oi=0,
                            timestamp=current_time
                        )
                        
                        call_triggered, call_data = strategies[symbol].check_call_breakout(symbol)
                        if call_triggered:
                            strike = (ltp // 100) * 100
                            premium = ltp * 0.015
                            call_data['strike_price'] = strike
                            call_data['premium'] = premium
                            process_signal(symbol, call_data, "CALL")
                        
                        put_triggered, put_data = strategies[symbol].check_put_breakout(symbol)
                        if put_triggered:
                            strike = (ltp // 100) * 100
                            premium = ltp * 0.015
                            put_data['strike_price'] = strike
                            put_data['premium'] = premium
                            process_signal(symbol, put_data, "PUT")
            
            time.sleep(10)  # 10 second scan frequency
    
    except Exception as e:
        logger.error(f"[ERROR] Screener crashed: {e}")
        screener_state["errors"].append(str(e))
        screener_state["running"] = False
    finally:
        logger.info("🛑 Screener background thread stopped")

def start_screener():
    """Start screener in background thread"""
    initialize()
    
    # Start screener thread
    screener_thread = threading.Thread(target=screener_loop, daemon=True)
    screener_thread.start()
    
    logger.info("✅ Screener background thread started")
    return screener_thread

def stop_screener():
    """Stop screener gracefully"""
    screener_state["running"] = False
    logger.info("🛑 Screener stop signal sent")
