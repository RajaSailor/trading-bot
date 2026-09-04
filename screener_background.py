"""
PRODUCTION SCREENER - BACKGROUND THREAD (FIXED VERSION)
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
from telegram.error import TelegramError
import asyncio
from strategy import FiveMinBreakoutStrategy

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv(dotenv_path="./.env", override=True)

CLIENT_ID = os.getenv("API_KEY")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")

# TELEGRAM MULTI-CHANNEL CONFIGURATION
TELEGRAM_CHANNELS = {
    "INDEX": {
        "token": "8601160697:AAFFxscCMfqcrXaf1lw69xK7Ue-RW_8aIzI",
        "chat_id": "-1003814243881",
        "symbols": ["NIFTY", "BANKNIFTY", "SENSEX"]
    },
    "COMMODITY": {
        "token": "8762956800:AAEkQZfYhawfxQEua8OSYcnp3FPRU2xywsc",
        "chat_id": "-1004466883026",
        "symbols": ["CRUDEOIL", "GOLD", "SILVER", "NATURALGAS"]
    },
    "NIFTY_50_OPTIONS": {
        "token": "8746059399:AAGfpg6rQfluICaezqiamCujN8_NcXbt1NQ",
        "chat_id": "-1003966854933",
        "symbols": ["RELIANCE", "TCS", "INFY"]
    },
    "NIFTY_50_5X": {
        "token": "8746059399:AAGfpg6rQfluICaezqiamCujN8_NcXbt1NQ",
        "chat_id": "-1004403277287",
        "symbols": ["RELIANCE", "TCS", "INFY"]
    },
    "NIFTY_50_PAY_LATER": {
        "token": "8746059399:AAGfpg6rQfluICaezqiamCujN8_NcXbt1NQ",
        "chat_id": "-1003966854994",
        "symbols": ["RELIANCE", "TCS", "INFY"]
    },
    "ERRORS": {
        "token": "8601160697:AAFFxscCMfqcrXaf1lw69xK7Ue-RW_8aIzI",
        "chat_id": "-1003814243881",
    }
}

# Try to import DhanHQ library
try:
    from dhanhq import DhanContext, dhanhq
    DHANHQ_AVAILABLE = True
    logger.info("✅ DhanHQ library available")
except ImportError:
    DHANHQ_AVAILABLE = False
    logger.warning("⚠️ DhanHQ library not available - using mock data for testing")

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
telegram_bots = {}
dhan_api = None
async_loop = None

# Create global async event loop
def create_async_loop():
    """Create and return async event loop"""
    global async_loop
    if async_loop is None or async_loop.is_closed():
        try:
            async_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(async_loop)
            logger.info("✅ Async event loop created")
        except Exception as e:
            logger.error(f"Failed to create async loop: {e}")
    return async_loop

# Initialize components
def initialize():
    """Initialize screener components"""
    global dhan_api, telegram_bots
    
    logger.info("="*80)
    logger.info("🚀 INITIALIZING SCREENER BACKGROUND")
    logger.info("="*80)
    
    # Initialize strategies
    for symbol in SYMBOLS.keys():
        strategies[symbol] = FiveMinBreakoutStrategy()
    logger.info(f"✅ Strategies initialized for {len(SYMBOLS)} symbols")
    
    # Initialize Telegram bots
    for channel_name, config in TELEGRAM_CHANNELS.items():
        try:
            bot = Bot(token=config["token"])
            telegram_bots[channel_name] = bot
            logger.info(f"✅ Telegram bot '{channel_name}' initialized")
        except Exception as e:
            logger.error(f"❌ Telegram bot '{channel_name}' failed: {e}")
            screener_state["last_error"] = str(e)
    
    if len(telegram_bots) > 0:
        screener_state["telegram_connected"] = True
        logger.info(f"[SUCCESS] ✓ All {len(telegram_bots)} Telegram bots connected")
    
    # Initialize DhanHQ
    if DHANHQ_AVAILABLE:
        try:
            dhan_context = DhanContext(client_id=CLIENT_ID, access_token=ACCESS_TOKEN)
            dhan_api = dhanhq(dhan_context)
            screener_state["dhan_connected"] = True
            logger.info("[SUCCESS] ✓ DhanHQ API Connected")
        except Exception as e:
            logger.error(f"[ERROR] DhanHQ initialization failed: {e}")
            screener_state["dhan_connected"] = False
            screener_state["last_error"] = str(e)
    else:
        logger.warning("⚠️ DhanHQ not available - using mock mode")
    
    # Create async loop
    create_async_loop()
    
    logger.info("✅ Screener background initialized")
    logger.info("="*80)

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

async def send_telegram_alert_async(channel_name, message):
    """Send Telegram alert to specific channel (async)"""
    try:
        if channel_name not in telegram_bots:
            logger.error(f"Bot '{channel_name}' not available")
            return False
        
        bot = telegram_bots[channel_name]
        config = TELEGRAM_CHANNELS[channel_name]
        chat_id = int(config["chat_id"])
        
        logger.info(f"📤 Sending alert to {channel_name} (Chat ID: {chat_id})...")
        
        await bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode="HTML"
        )
        
        logger.info(f"✅ Alert successfully sent to {channel_name}")
        return True
        
    except TelegramError as e:
        logger.error(f"[ERROR] Telegram {channel_name} API error: {e}")
        screener_state["last_error"] = f"Telegram error: {str(e)}"
        return False
    except Exception as e:
        logger.error(f"[ERROR] Telegram {channel_name} failed: {e}")
        screener_state["last_error"] = str(e)
        return False

def send_telegram_alert(channel_name, message):
    """Send Telegram alert (blocking wrapper)"""
    try:
        loop = create_async_loop()
        if loop.is_running():
            # If loop is running, schedule as task
            import concurrent.futures
            executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            future = executor.submit(asyncio.run, send_telegram_alert_async(channel_name, message))
            result = future.result(timeout=10)
            executor.shutdown(wait=False)
            return result
        else:
            # Run in current loop
            return loop.run_until_complete(send_telegram_alert_async(channel_name, message))
    except Exception as e:
        logger.error(f"Error sending alert: {e}")
        return False

def format_signal_message(symbol, signal_data):
    """Format signal for Telegram"""
    signal_type = signal_data["buy_side"]
    entry = signal_data["entry"]
    strike = signal_data["strike_price"]
    target = signal_data["target"]
    stop_loss = signal_data["stop_loss"]
    
    emoji = "🚀" if signal_type == "CALL/BUY" else "📉"
    
    msg = f"""
<b>{emoji} {signal_type} BREAKOUT SIGNAL!</b>

<b>Symbol:</b> {symbol}
<b>Current Price:</b> ₹{signal_data['current_price']:.2f}

<b>📊 POSITION DETAILS:</b>
  <b>Entry:</b> ₹{entry:.2f}
  <b>Strike:</b> ₹{strike:.2f}
  <b>Target:</b> ₹{target:.2f} (30% profit)
  <b>Stop Loss:</b> ₹{stop_loss:.2f} (10% loss)

<b>⏰ Time:</b> {signal_data['timestamp']}

<b>⚠️ DISCLAIMER</b>
Educational & informational purposes only.
Consult SEBI-registered advisor before trading.
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
        logger.warning(f"No channel configured for {symbol}, using INDEX by default")
        channel = "INDEX"
    
    message = format_signal_message(symbol, signal_data)
    
    # Send alert to appropriate channel
    if send_telegram_alert(channel, message):
        logger.info(f"✅ Alert sent to {channel} for {symbol}")
    else:
        logger.error(f"❌ Failed to send alert to {channel} for {symbol}")

def is_market_hours():
    """Check if market is open - CORRECTED"""
    now = datetime.now()
    current_time = now.time()
    day = now.weekday()
    
    # Weekend check (Saturday=5, Sunday=6)
    if day >= 5:
        return False
    
    # NSE/BSE Market hours: 9:15 AM to 3:30 PM (15:30)
    if dtime(9, 15) <= current_time <= dtime(15, 30):
        logger.debug(f"✅ NSE/BSE market open (Current time: {current_time})")
        return True
    
    # Commodity market: 9:00 AM to 11:30 PM (23:30)
    if dtime(9, 0) <= current_time <= dtime(23, 30):
        logger.debug(f"✅ MCX commodity market open (Current time: {current_time})")
        return True
    
    logger.debug(f"❌ Market closed (Current time: {current_time})")
    return False

def fetch_market_data():
    """Fetch real-time data from DhanHQ"""
    quotes = {}
    
    if not dhan_api:
        logger.debug("DhanHQ API not initialized - using mock data")
        # Generate mock data for testing
        import random
        for symbol in SYMBOLS.keys():
            base_price = 100 + random.randint(0, 10000) / 100
            quotes[symbol] = {
                "open": base_price,
                "high": base_price + random.randint(1, 100) / 100,
                "low": base_price - random.randint(1, 100) / 100,
                "close": base_price + random.randint(-100, 100) / 100,
                "oi": 0,
                "timestamp": datetime.now().strftime("%H:%M:%S")
            }
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
    
    logger.info("\n" + "="*80)
    logger.info("🚀 SCREENER BACKGROUND THREAD STARTED")
    logger.info("="*80)
    logger.info(f"Public IP: {PUBLIC_IP}")
    logger.info(f"Monitoring: {len(SYMBOLS)} Symbols")
    logger.info(f"Telegram Channels: {len(telegram_bots)}")
    logger.info(f"Scan frequency: Every 5 seconds")
    logger.info("="*80 + "\n")
    
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
                logger.debug("Market closed, waiting for market open...")
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

def send_test_alerts():
    """Send test alerts to all channels"""
    logger.info("\n" + "="*80)
    logger.info("🧪 SENDING TEST ALERTS TO ALL CHANNELS")
    logger.info("="*80 + "\n")
    
    for channel_name in ["INDEX", "COMMODITY", "NIFTY_50_OPTIONS", "NIFTY_50_5X", "NIFTY_50_PAY_LATER"]:
        test_message = f"""
<b>🧪 TEST ALERT - {channel_name}</b>

✅ This Telegram channel is working correctly!

<b>Status:</b> Connected and Ready
<b>Time:</b> {datetime.now().strftime('%H:%M:%S')}
<b>Public IP:</b> {PUBLIC_IP}

Market opens at 9:15 AM IST.
Live alerts will flow when breakouts are detected! 🚀
"""
        logger.info(f"Sending test alert to {channel_name}...")
        send_telegram_alert(channel_name, test_message)
        time.sleep(1)
    
    logger.info("✅ Test alerts completed!\n")
