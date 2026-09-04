"""
PRODUCTION SCREENER - BACKGROUND THREAD (FIXED VERSION)
Handles real-time market scanning with proper alert system
File: screener_background.py
"""

import os
import time
import threading
import logging
from datetime import datetime, time as dtime
from dotenv import load_dotenv
import requests
from telegram import Bot
import asyncio
from collections import defaultdict

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv(dotenv_path="./.env", override=True)

CLIENT_ID = os.getenv("API_KEY")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# DhanHQ API Configuration
DHAN_API_URL = "https://api.dhan.co"
DHAN_HEADERS = {
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Content-Type": "application/json",
    "Accept": "application/json"
}

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

# Store candle history for strategy
candle_history = defaultdict(list)
bot = None
dhan_api_working = False

# Strategy: 5-Minute Breakout
class FiveMinBreakoutStrategy:
    def __init__(self):
        self.history_limit = 5
        
    def add_candle(self, symbol, candle):
        """Add new candle to history"""
        if len(candle_history[symbol]) >= self.history_limit:
            candle_history[symbol].pop(0)
        candle_history[symbol].append(candle)
    
    def check_call_breakout(self, symbol):
        """Check for CALL (bullish) breakout"""
        if len(candle_history[symbol]) < 3:
            return False, None
        
        candles = candle_history[symbol]
        
        # Check if current candle breaks above previous high
        current_high = candles[-1]['high']
        previous_max_high = max([c['high'] for c in candles[-3:-1]])
        
        if current_high > previous_max_high:
            entry = candles[-1]['close']
            return True, {
                'entry': entry,
                'premium': entry * 0.015,
                'target': entry * 1.30,
                'stoploss': entry * 0.90,
                'timestamp': datetime.now().strftime("%H:%M:%S"),
                'buy_side': 'CALL',
                'strike_price': (entry // 100) * 100,
            }
        
        return False, None
    
    def check_put_breakout(self, symbol):
        """Check for PUT (bearish) breakout"""
        if len(candle_history[symbol]) < 3:
            return False, None
        
        candles = candle_history[symbol]
        
        # Check if current candle breaks below previous low
        current_low = candles[-1]['low']
        previous_min_low = min([c['low'] for c in candles[-3:-1]])
        
        if current_low < previous_min_low:
            entry = candles[-1]['close']
            return True, {
                'entry': entry,
                'premium': entry * 0.015,
                'target': entry * 0.70,
                'stoploss': entry * 1.10,
                'timestamp': datetime.now().strftime("%H:%M:%S"),
                'buy_side': 'PUT',
                'strike_price': (entry // 100) * 100,
            }
        
        return False, None

strategies = {}
for symbol in SYMBOLS.keys():
    strategies[symbol] = FiveMinBreakoutStrategy()

# Telegram
async def send_telegram_alert(message):
    """Send Telegram alert asynchronously"""
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
    signal_type = signal_data['buy_side']
    entry = signal_data['entry']
    strike = signal_data['strike_price']
    premium = signal_data['premium']
    target = signal_data['target']
    stoploss = signal_data['stoploss']
    
    emoji = "🚀" if signal_type == "CALL" else "🔻"
    
    msg = f"""
<b>{emoji} {signal_type} SIGNAL TRIGGERED!</b>

<b>Market:</b> {symbol}
<b>Signal Type:</b> {signal_type}

<b>📊 POSITION DETAILS:</b>
  <b>Entry:</b> {entry:.2f}
  <b>Strike:</b> {strike:.0f}
  <b>Premium:</b> {premium:.2f}
  <b>Target:</b> {target:.2f} (30% gain)
  <b>Stop Loss:</b> {stoploss:.2f} (10% loss)

<b>⏰ Time:</b> {signal_data['timestamp']}
<b>📈 Timeframe:</b> 5-MIN

<b>⚠️ IMPORTANT DISCLAIMER & NOTICE</b>
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
    
    logger.info(f"\n{'='*80}")
    logger.info(f"🚀 SIGNAL #{screener_state['total_signals']} TRIGGERED - {signal_type}!")
    logger.info(f"Market: {symbol} | Entry: {signal_data['entry']:.2f}")
    logger.info(f"{'='*80}\n")
    
    message = format_signal_message(symbol, signal_data)
    
    # Send async telegram alert
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        result = loop.run_until_complete(send_telegram_alert(message))
        if result:
            logger.info(f"✅ Telegram alert sent for {symbol} {signal_type}")
        else:
            logger.error(f"❌ Telegram alert failed for {symbol}")
    finally:
        loop.close()

def is_market_hours():
    """Check if market is open"""
    now = datetime.now()
    current_time = now.time()
    day = now.weekday()
    
    # Weekend check
    if day >= 5:
        return False
    
    # Market hours: 9:15 AM to 3:30 PM
    if dtime(9, 15) <= current_time <= dtime(15, 30):
        return True
    
    # Commodity market: 9:00 AM to 11:30 PM
    if dtime(9, 0) <= current_time <= dtime(23, 30):
        return True
    
    return False

def fetch_intraday_data():
    """Fetch OHLC data from DhanHQ"""
    quotes = {}
    
    try:
        for symbol, config in SYMBOLS.items():
            try:
                # DhanHQ API endpoint for intraday candles
                url = f"{DHAN_API_URL}/charts/intraday"
                
                payload = {
                    "securityId": config["security_id"],
                    "exchange": config["exchange"],
                    "interval": "5min"
                }
                
                response = requests.get(
                    url,
                    params=payload,
                    headers=DHAN_HEADERS,
                    timeout=10
                )
                
                if response.status_code == 200:
                    data = response.json()
                    
                    if data.get('status') == 'success' and data.get('data'):
                        candles = data['data']
                        if isinstance(candles, list) and len(candles) > 0:
                            candle = candles[-1]  # Latest candle
                            quotes[symbol] = {
                                'open': float(candle.get('open', 0)),
                                'high': float(candle.get('high', 0)),
                                'low': float(candle.get('low', 0)),
                                'close': float(candle.get('close', 0)),
                                'volume': int(candle.get('volume', 0)),
                            }
                else:
                    logger.debug(f"API response for {symbol}: {response.status_code}")
                    
            except Exception as e:
                logger.debug(f"Error fetching {symbol}: {e}")
                continue
    
    except Exception as e:
        logger.error(f"[ERROR] API fetch failed: {e}")
    
    return quotes

def initialize():
    """Initialize screener components"""
    global bot, dhan_api_working
    
    logger.info("🚀 Initializing screener background...")
    
    # Initialize strategies
    for symbol in SYMBOLS.keys():
        strategies[symbol] = FiveMinBreakoutStrategy()
        candle_history[symbol] = []
    
    # Initialize DhanHQ
    try:
        # Test API connection
        response = requests.get(
            f"{DHAN_API_URL}/user/profile",
            headers=DHAN_HEADERS,
            timeout=5
        )
        if response.status_code in [200, 401, 403]:
            screener_state["dhan_connected"] = True
            dhan_api_working = True
            logger.info("[SUCCESS] ✓ DhanHQ API Connected")
        else:
            screener_state["dhan_connected"] = False
            logger.warning(f"DhanHQ API check returned: {response.status_code}")
    except Exception as e:
        logger.error(f"[ERROR] DhanHQ connection failed: {e}")
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

def screener_loop():
    """Main screener loop - runs in background thread"""
    
    logger.info("🚀 SCREENER BACKGROUND THREAD STARTED")
    logger.info(f"Public IP: {PUBLIC_IP}")
    logger.info(f"Monitoring: {len(SYMBOLS)} Symbols")
    
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
            
            # Fetch market data
            quotes = fetch_intraday_data()
            
            if quotes:
                screener_state["successful_scans"] += 1
                
                # Process each symbol
                for symbol in SYMBOLS.keys():
                    if symbol not in quotes:
                        continue
                    
                    candle = quotes[symbol]
                    
                    # Add to history
                    strategies[symbol].add_candle(symbol, candle)
                    
                    # Check for signals
                    call_triggered, call_data = strategies[symbol].check_call_breakout(symbol)
                    if call_triggered:
                        process_signal(symbol, call_data, "CALL")
                    
                    put_triggered, put_data = strategies[symbol].check_put_breakout(symbol)
                    if put_triggered:
                        process_signal(symbol, put_data, "PUT")
            
            # 10-second scan frequency
            time.sleep(10)
    
    except Exception as e:
        logger.error(f"[ERROR] Screener crashed: {e}")
        screener_state["errors"].append(str(e))
        screener_state["running"] = False
    finally:
        logger.info("🔴 Screener background thread stopped")

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
    logger.info("🔴 Screener stop signal sent")
