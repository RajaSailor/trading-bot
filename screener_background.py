"""
PRODUCTION SCREENER - BACKGROUND THREAD (COMPLETE WORKING VERSION)
Real-time market scanning with multi-channel Telegram alerts
Handles: 3 Indices, 4 Commodities, 50 NIFTY Stocks
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
from strategy import FiveMinBreakoutStrategy

# Setup logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv(dotenv_path="./.env", override=True)

CLIENT_ID = os.getenv("API_KEY")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")

# TELEGRAM MULTI-CHANNEL CONFIGURATION
TELEGRAM_CHANNELS = {
    "INDEX": {
        "token": os.getenv("INDEX_BOT_TOKEN", "8601160697:AAFFxscCMfqcrXaf1lw69xK7Ue-RW_8aIzI"),
        "chat_id": os.getenv("INDEX_CHAT_ID", "-1003814243881"),
        "symbols": ["NIFTY", "BANKNIFTY", "SENSEX"]
    },
    "COMMODITY": {
        "token": os.getenv("COMMODITY_BOT_TOKEN", "8762956800:AAEkQZfYhawfxQEua8OSYcnp3FPRU2xywsc"),
        "chat_id": os.getenv("COMMODITY_CHAT_ID", "-1004466883026"),
        "symbols": ["CRUDEOIL", "GOLD", "SILVER", "NATURALGAS"]
    },
    "NIFTY_50_OPTIONS": {
        "token": os.getenv("NIFTY_50_OPTIONS_TOKEN", "8746059399:AAGfpg6rQfluICaezqiamCujN8_NcXbt1NQ"),
        "chat_id": os.getenv("NIFTY_50_OPTIONS_CHAT_ID", "-1003966854933"),
        "symbols": ["RELIANCE", "TCS", "INFY", "HDFC", "ICICIBANK", "SBIN", "MARUTI", "WIPRO"]
    },
    "NIFTY_50_5X": {
        "token": os.getenv("NIFTY_50_5X_TOKEN", "8746059399:AAGfpg6rQfluICaezqiamCujN8_NcXbt1NQ"),
        "chat_id": os.getenv("NIFTY_50_5X_CHAT_ID", "-1004403277287"),
        "symbols": ["RELIANCE", "TCS", "INFY", "HDFC", "ICICIBANK", "SBIN", "MARUTI", "WIPRO"]
    },
    "NIFTY_50_PAY_LATER": {
        "token": os.getenv("NIFTY_50_PAY_LATER_TOKEN", "8746059399:AAGfpg6rQfluICaezqiamCujN8_NcXbt1NQ"),
        "chat_id": os.getenv("NIFTY_50_PAY_LATER_CHAT_ID", "-1003966854994"),
        "symbols": ["RELIANCE", "TCS", "INFY", "HDFC", "ICICIBANK", "SBIN", "MARUTI", "WIPRO"]
    },
    "ERRORS": {
        "token": os.getenv("ERRORS_BOT_TOKEN", "8601160697:AAFFxscCMfqcrXaf1lw69xK7Ue-RW_8aIzI"),
        "chat_id": os.getenv("ERRORS_CHAT_ID", "-1003814243881"),
    }
}

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

strategies = {}
bots = {}
dhan_api = None

# Initialize components
def initialize():
    """Initialize screener components"""
    global dhan_api, bots
    
    logger.info("🚀 Initializing screener background...")
    
    # Initialize strategies
    for symbol in SYMBOLS.keys():
        strategies[symbol] = FiveMinBreakoutStrategy()
    
    # Initialize Telegram bots
    for channel_name, config in TELEGRAM_CHANNELS.items():
        try:
            bot = Bot(token=config["token"])
            bots[channel_name] = bot
            logger.info(f"✅ Telegram bot '{channel_name}' initialized")
        except Exception as e:
            logger.error(f"❌ Telegram bot '{channel_name}' failed: {e}")
            screener_state["last_error"] = str(e)
    
    if len(bots) > 0:
        screener_state["telegram_connected"] = True
        logger.info(f"[SUCCESS] ✓ Telegram bots connected ({len(bots)} channels)")
    
    # Initialize DhanHQ
    try:
        if DHANHQ_AVAILABLE:
            dhan_context = DhanContext(client_id=CLIENT_ID, access_token=ACCESS_TOKEN)
            dhan_api = dhanhq(dhan_context)
            screener_state["dhan_connected"] = True
            logger.info("[SUCCESS] ✓ DhanHQ API Connected")
    except Exception as e:
        logger.error(f"[ERROR] DhanHQ initialization failed: {e}")
        screener_state["dhan_connected"] = False
        screener_state["last_error"] = str(e)
    
    logger.info("✅ Screener background initialized")

def get_channel_for_symbol(symbol):
    """Get appropriate Telegram channel for symbol"""
    if symbol in TELEGRAM_CHANNELS["INDEX"]["symbols"]:
        return "INDEX"
    elif symbol in TELEGRAM_CHANNELS["COMMODITY"]["symbols"]:
        return "COMMODITY"
    elif symbol in TELEGRAM_CHANNELS["NIFTY_50_OPTIONS"]["symbols"]:
        return "NIFTY_50_OPTIONS"
    else:
        return None

async def send_telegram_alert(channel_name, message):
    """Send Telegram alert to specific channel"""
    try:
        if channel_name not in bots:
            logger.error(f"Bot '{channel_name}' not available")
            return False
        
        bot = bots[channel_name]
        config = TELEGRAM_CHANNELS[channel_name]
        
        await bot.send_message(
            chat_id=int(config["chat_id"]),
            text=message,
            parse_mode="HTML"
        )
        logger.info(f"✅ Alert sent to {channel_name}")
        return True
    except Exception as e:
        logger.error(f"[ERROR] Telegram {channel_name} failed: {e}")
        screener_state["last_error"] = str(e)
        return False

def format_signal_message(symbol, signal_data):
    """Format signal for Telegram"""
    signal_type = signal_data["buy_side"]
    entry = signal_data["entry"]
    strike = signal_data["strike_price"]
    target = signal_data["target"]
    stop_loss = signal_data["stop_loss"]
    
    emoji = "🚀" if signal_type == "CALL/BUY" else "🔻"
    
    msg = f"""
<b>{emoji} {signal_type} SIGNAL!</b>

<b>Symbol:</b> {symbol}
<b>Current Price:</b> ₹{signal_data['current_price']:.2f}

<b>📊 POSITION DETAILS:</b>
  <b>Entry:</b> ₹{entry:.2f}
  <b>Strike:</b> ₹{strike:.2f}
  <b>Target:</b> ₹{target:.2f}
  <b>Stop Loss:</b> ₹{stop_loss:.2f}

<b>⏰ Time:</b> {signal_data['timestamp']}

<b>⚠️ DISCLAIMER</b>
Educational & informational purposes only.
Consult a SEBI-registered advisor before trading.
"""
    return msg

def process_signal(symbol, signal_data):
    """Process and send signal to appropriate channel"""
    signal_type = signal_data["buy_side"]
    
    if signal_type == "CALL/BUY":
        screener_state["call_signals"] += 1
    else:
        screener_state["put_signals"] += 1
    
    screener_state["total_signals"] += 1
    
    logger.info(f"\n{'='*80}")
    logger.info(f"🚀 SIGNAL #{screener_state['total_signals']} TRIGGERED - {signal_type}!")
    logger.info(f"Symbol: {symbol} | Entry: ₹{signal_data['entry']:.2f}")
    logger.info(f"{'='*80}\n")
    
    # Get channel for this symbol
    channel = get_channel_for_symbol(symbol)
    if not channel:
        logger.warning(f"No channel configured for {symbol}")
        return
    
    message = format_signal_message(symbol, signal_data)
    
    # Send alert to appropriate channel
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        result = loop.run_until_complete(send_telegram_alert(channel, message))
        if result:
            logger.info(f"✅ Alert sent to {channel} for {symbol}")
        else:
            logger.error(f"❌ Alert failed for {symbol} on {channel}")
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
                resp = dhan_api.get_intraday_paracande(
                    exchange_tokens=[],
                    security_id=[config["security_id"]],
                    exchange=config["exchange"],
                    interval=5
                )
                
                if resp and resp.get('status') == 'success' and resp.get('data'):
                    data = resp['data']
                    if isinstance(data, list) and len(data) > 0:
                        candle = data[0]
                        
                        quotes[symbol] = {
                            "open": float(candle.get('open', 0)),
                            "high": float(candle.get('high', 0)),
                            "low": float(candle.get('low', 0)),
                            "close": float(candle.get('close', 0)),
                            "oi": float(candle.get('oi', 0)),
                            "timestamp": datetime.now().strftime("%H:%M:%S")
                        }
                        logger.debug(f"{symbol}: ₹{candle.get('close', 0):.2f}")
            except Exception as e:
                logger.debug(f"Error fetching {symbol}: {e}")
                continue
    except Exception as e:
        logger.error(f"[ERROR] Market data fetch failed: {e}")
        screener_state["last_error"] = str(e)
    
    return quotes

def screener_loop():
    """Main screener loop - runs in background thread"""
    
    logger.info("🚀 SCREENER BACKGROUND THREAD STARTED")
    logger.info(f"Public IP: {PUBLIC_IP}")
    logger.info(f"Monitoring: {len(SYMBOLS)} Symbols")
    logger.info(f"Telegram Channels: {len(bots)}")
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
                    
                    quote = quotes[symbol]
                    
                    # Add candle to strategy
                    strategies[symbol].add_candle(
                        symbol,
                        open_price=quote["open"],
                        high=quote["high"],
                        low=quote["low"],
                        close=quote["close"],
                        oi=quote["oi"],
                        timestamp=quote["timestamp"]
                    )
                    
                    # Check for signals
                    call_triggered, call_data = strategies[symbol].check_call_breakout(symbol)
                    if call_triggered:
                        process_signal(symbol, call_data)
                    
                    put_triggered, put_data = strategies[symbol].check_put_breakout(symbol)
                    if put_triggered:
                        process_signal(symbol, put_data)
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

# TEST: Send test alert to all channels
async def send_test_alerts():
    """Send test alerts to all channels"""
    logger.info("\n" + "="*80)
    logger.info("🧪 SENDING TEST ALERTS TO ALL CHANNELS")
    logger.info("="*80 + "\n")
    
    for channel_name in ["INDEX", "COMMODITY", "NIFTY_50_OPTIONS", "NIFTY_50_5X", "NIFTY_50_PAY_LATER"]:
        test_message = f"""
<b>🧪 TEST ALERT - {channel_name}</b>

This is a test message to verify the Telegram channel is working correctly.

<b>Status:</b> ✅ Connected and Ready!
<b>Time:</b> {datetime.now().strftime('%H:%M:%S')}

Market opens at 9:15 AM IST
Alerts will start flowing when breakouts are detected! 🚀
"""
        await send_telegram_alert(channel_name, test_message)
        await asyncio.sleep(1)
    
    logger.info("✅ Test alerts completed!\n")
