"""
PRODUCTION SCREENER - BACKGROUND THREAD (WORKING VERSION)
Real-time market scanning with proper DhanHQ API integration
File: screener_background.py
"""

import os
import time
import threading
import logging
from datetime import datetime, time as dtime
from dotenv import load_dotenv
from telegram import Bot
import asyncio
from collections import defaultdict

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv(dotenv_path="./.env", override=True)

CLIENT_ID = os.getenv("API_KEY")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# Try to import DhanHQ library
try:
    from dhanhq import DhanContext, dhanhq
    DHANHQ_AVAILABLE = True
    logger.info("✅ DhanHQ library available")
except ImportError:
    DHANHQ_AVAILABLE = False
    logger.warning("⚠️ DhanHQ library not available")

import requests

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
    "last_error": None,
}

# Store price history
price_history = defaultdict(list)
bot = None
dhan_api = None

# Strategy: Simple Breakout Detection
class SimpleBreakoutStrategy:
    def __init__(self):
        self.min_candles = 2  # Reduced from 5 for faster detection
        
    def add_price(self, symbol, price):
        """Add new price to history"""
        if len(price_history[symbol]) >= 10:
            price_history[symbol].pop(0)
        price_history[symbol].append(price)
        logger.debug(f"Price history for {symbol}: {price_history[symbol]}")
    
    def check_call_breakout(self, symbol):
        """Check for CALL (bullish) breakout - price breaking above resistance"""
        if len(price_history[symbol]) < self.min_candles:
            return False, None
        
        prices = price_history[symbol]
        current_price = prices[-1]
        
        # Check if current price is highest in recent history
        if current_price > max(prices[:-1]):
            logger.info(f"🚀 CALL BREAKOUT DETECTED for {symbol}: {current_price}")
            return True, {
                'entry': current_price,
                'premium': current_price * 0.015,
                'target': current_price * 1.30,
                'stoploss': current_price * 0.90,
                'timestamp': datetime.now().strftime("%H:%M:%S"),
                'buy_side': 'CALL',
                'strike_price': (current_price // 100) * 100,
            }
        
        return False, None
    
    def check_put_breakout(self, symbol):
        """Check for PUT (bearish) breakout - price breaking below support"""
        if len(price_history[symbol]) < self.min_candles:
            return False, None
        
        prices = price_history[symbol]
        current_price = prices[-1]
        
        # Check if current price is lowest in recent history
        if current_price < min(prices[:-1]):
            logger.info(f"🔻 PUT BREAKOUT DETECTED for {symbol}: {current_price}")
            return True, {
                'entry': current_price,
                'premium': current_price * 0.015,
                'target': current_price * 0.70,
                'stoploss': current_price * 1.10,
                'timestamp': datetime.now().strftime("%H:%M:%S"),
                'buy_side': 'PUT',
                'strike_price': (current_price // 100) * 100,
            }
        
        return False, None

strategies = {}
for symbol in SYMBOLS.keys():
    strategies[symbol] = SimpleBreakoutStrategy()

# Telegram
async def send_telegram_alert(message):
    """Send Telegram alert asynchronously"""
    try:
        if not bot:
            logger.error("Bot not initialized")
            return False
        await bot.send_message(chat_id=int(CHAT_ID), text=message, parse_mode="HTML")
        logger.info("✅ Telegram message sent successfully")
        return True
    except Exception as e:
        logger.error(f"[ERROR] Telegram failed: {e}")
        screener_state["last_error"] = str(e)
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
  <b>Entry:</b> ₹{entry:.2f}
  <b>Strike:</b> {strike:.0f}
  <b>Premium:</b> ₹{premium:.2f}
  <b>Target:</b> ₹{target:.2f} (30% gain)
  <b>Stop Loss:</b> ₹{stoploss:.2f} (10% loss)

<b>⏰ Time:</b> {signal_data['timestamp']}
<b>📈 Timeframe:</b> LIVE

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
    logger.info(f"Market: {symbol} | Entry: ₹{signal_data['entry']:.2f}")
    logger.info(f"{'='*80}\n")
    
    message = format_signal_message(symbol, signal_data)
    
    # Send async telegram alert
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        result = loop.run_until_complete(send_telegram_alert(message))
        if result:
            logger.info(f"✅ Alert sent for {symbol} {signal_type}")
        else:
            logger.error(f"❌ Alert failed for {symbol}")
    except Exception as e:
        logger.error(f"Event loop error: {e}")
        screener_state["last_error"] = str(e)
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
    
    # NSE/BSE Market hours: 9:15 AM to 3:30 PM
    if dtime(9, 15) <= current_time <= dtime(15, 30):
        return True
    
    # Commodity market: 9:00 AM to 11:30 PM
    if dtime(9, 0) <= current_time <= dtime(23, 30):
        return True
    
    return False

def fetch_market_data():
    """Fetch real-time data from DhanHQ"""
    quotes = {}
    
    if not dhan_api:
        logger.warning("DhanHQ API not initialized")
        return quotes
    
    try:
        for symbol, config in SYMBOLS.items():
            try:
                # Use DhanHQ library method
                resp = dhan_api.get_intraday_paracande(
                    exchange_tokens=[],
                    security_id=[config["security_id"]],
                    exchange=config["exchange"],
                    interval=5  # 5-minute candles
                )
                
                if resp and resp.get('status') == 'success' and resp.get('data'):
                    data = resp['data']
                    if isinstance(data, list) and len(data) > 0:
                        candle = data[0]
                        ltp = float(candle.get('close', 0))
                        
                        if ltp > 0:
                            quotes[symbol] = ltp
                            logger.debug(f"{symbol}: ₹{ltp:.2f}")
                else:
                    logger.debug(f"No data for {symbol}: {resp}")
            except Exception as e:
                logger.debug(f"Error fetching {symbol}: {e}")
                continue
    
    except Exception as e:
        logger.error(f"[ERROR] Market data fetch failed: {e}")
        screener_state["last_error"] = str(e)
    
    return quotes

def initialize():
    """Initialize screener components"""
    global bot, dhan_api
    
    logger.info("🚀 Initializing screener background...")
    
    # Initialize strategies and price history
    for symbol in SYMBOLS.keys():
        strategies[symbol] = SimpleBreakoutStrategy()
        price_history[symbol] = []
    
    # Initialize DhanHQ
    try:
        if DHANHQ_AVAILABLE:
            dhan_context = DhanContext(client_id=CLIENT_ID, access_token=ACCESS_TOKEN)
            dhan_api = dhanhq(dhan_context)
            screener_state["dhan_connected"] = True
            logger.info("[SUCCESS] ✓ DhanHQ API Connected")
        else:
            logger.error("DhanHQ library not available")
            screener_state["dhan_connected"] = False
    except Exception as e:
        logger.error(f"[ERROR] DhanHQ initialization failed: {e}")
        screener_state["dhan_connected"] = False
        screener_state["last_error"] = str(e)
    
    # Initialize Telegram
    try:
        bot = Bot(token=TELEGRAM_TOKEN)
        screener_state["telegram_connected"] = True
        logger.info("[SUCCESS] ✓ Telegram Bot Connected")
    except Exception as e:
        logger.error(f"[ERROR] Telegram failed: {e}")
        screener_state["telegram_connected"] = False
        screener_state["last_error"] = str(e)
    
    logger.info("✅ Screener background initialized")

def screener_loop():
    """Main screener loop - runs in background thread"""
    
    logger.info("🚀 SCREENER BACKGROUND THREAD STARTED")
    logger.info(f"Public IP: {PUBLIC_IP}")
    logger.info(f"Monitoring: {len(SYMBOLS)} Symbols")
    logger.info("Scan frequency: Every 5 seconds")
    
    screener_state["running"] = True
    scan_count = 0
    
    try:
        while screener_state["running"]:
            scan_count += 1
            screener_state["total_scans"] = scan_count
            
            market_open = is_market_hours()
            screener_state["market_open"] = market_open
            screener_state["last_scan_time"] = datetime.now().isoformat()
            
            if not market_open:
                logger.debug("Market closed, skipping scan")
                time.sleep(5)
                continue
            
            logger.info(f"\n📊 SCAN #{scan_count} - {datetime.now().strftime('%H:%M:%S')}")
            
            # Fetch market data
            quotes = fetch_market_data()
            
            if quotes:
                screener_state["successful_scans"] += 1
                logger.info(f"✅ Fetched data for {len(quotes)} symbols")
                
                # Process each symbol
                for symbol in SYMBOLS.keys():
                    if symbol not in quotes:
                        continue
                    
                    price = quotes[symbol]
                    strategies[symbol].add_price(symbol, price)
                    
                    # Check for signals
                    call_triggered, call_data = strategies[symbol].check_call_breakout(symbol)
                    if call_triggered:
                        process_signal(symbol, call_data, "CALL")
                    
                    put_triggered, put_data = strategies[symbol].check_put_breakout(symbol)
                    if put_triggered:
                        process_signal(symbol, put_data, "PUT")
            else:
                logger.warning("❌ No data fetched in this scan")
            
            # 5-second scan frequency
            time.sleep(5)
    
    except Exception as e:
        logger.error(f"[ERROR] Screener crashed: {e}")
        screener_state["errors"].append(str(e))
        screener_state["last_error"] = str(e)
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
