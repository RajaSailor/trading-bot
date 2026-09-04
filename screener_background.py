"""
PRODUCTION SCREENER - BACKGROUND THREAD (MULTI-BOT VERSION)
Handles real-time market scanning with smart multi-channel routing
10-minute breakout strategy for all asset classes
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
import pytz

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv(dotenv_path="./.env", override=True)

# ============================================================================
# TIMEZONE CONFIGURATION - INDIA STANDARD TIME (IST)
# ============================================================================
IST = pytz.timezone('Asia/Kolkata')

# ============================================================================
# API CREDENTIALS
# ============================================================================
CLIENT_ID = os.getenv("API_KEY")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")

# ============================================================================
# TELEGRAM MULTI-BOT CONFIGURATION
# ============================================================================
# System alerts bot (daily checks, errors, mobile control)
SYSTEM_BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")  # 8654404135:AAGHqdH81h1t1_RzjfqBSsbRk8O5l-ozRdc
SYSTEM_CHAT_ID = int(os.getenv("CHAT_ID"))      # -1004321977761

# Trading bots - one for each asset class
TRADING_BOTS = {
    "INDEX": {
        "token": os.getenv("BOT_INDEX_TOKEN"),           # 8601160697:AAFFxscCMfqcrXaf1lw69xK7Ue-RW_8aIzI
        "channel_id": int(os.getenv("CHANNEL_INDEX_ID")) # -1003966854994
    },
    "NIFTY50_STOCKS": {
        "token": os.getenv("BOT_NIFTY50_OPTIONS_TOKEN"),           # 8746059399:AAGfpg6rQfluICaezqiamCujN8_NcXbt1NQ
        "channel_id": int(os.getenv("CHANNEL_NIFTY50_OPTIONS_ID")) # -1003804613787
    },
    "COMMODITY": {
        "token": os.getenv("BOT_COMMODITY_TOKEN"),           # 8762956800:AAEkQZfYhawfxQEua8OSYcnp3FPRU2xywsc
        "channel_id": int(os.getenv("CHANNEL_COMMODITY_ID")) # -1004403277287
    },
    "NIFTY50_INTRADAY": {
        "token": os.getenv("BOT_NIFTY50_5X_TOKEN"),           # 8265739611:AAFbraUdEY01eJOel76S8mMgBiZT4otxkd4
        "channel_id": int(os.getenv("CHANNEL_NIFTY50_5X_ID")) # -1004466883026
    },
    "NIFTY50_PAYLATER": {
        "token": os.getenv("BOT_NIFTY50_PAY_LATER_TOKEN"),           # 8934391945:AAEdycuHV7sZP6eASCU2j7kQ9SBG7e9D4Q0
        "channel_id": int(os.getenv("CHANNEL_NIFTY50_PAY_LATER_ID")) # -1003814243881
    },
    "CRYPTO": {
        "token": os.getenv("BOT_CRYPTO_TOKEN"),           # 8921592389:AAF7IKqXz2a7yp0a--m0vP21itKHVKqF-7k
        "channel_id": int(os.getenv("CHANNEL_CRYPTO_ID")) # -1004482078964
    }
}

# Import DhanHQ
try:
    from dhanhq import DhanContext, dhanhq
    DHANHQ_AVAILABLE = True
except ImportError:
    DHANHQ_AVAILABLE = False
    logger.warning("[WARNING] dhanhq library not installed")

# ============================================================================
# MARKET SYMBOLS CONFIGURATION
# ============================================================================

# INDEX F&O (NSE) → Goes to INDEX channel
INDEX_SYMBOLS = {
    "NIFTY": {"security_id": 13, "exchange": "NSE_FNO", "type": "INDEX"},
    "BANKNIFTY": {"security_id": 25, "exchange": "NSE_FNO", "type": "INDEX"},
}

# INDEX SPOT (BSE) → Goes to INDEX channel
INDEX_SYMBOLS_SPOT = {
    "SENSEX": {"security_id": 1, "exchange": "BSE_FNO", "type": "INDEX"},
}

# NIFTY 50 STOCKS (NSE) - ALL 50 STOCKS → Goes to NIFTY50_STOCKS channel
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
    "COALINDIA": 1233,
    "BPCL": 1210,
    "GRASIM": 1247,
    "ULTRACEMCO": 1435,
    "BRITANNIA": 1233,
    "NESTLEIND": 1395,
    "DRREDDY": 1255,
    "BAJAJFINSV": 1031,
    "ADANIENT": 1254,
    "ADANIPORTS": 1245,
    "ADANIGREEN": 1242,
    "ADANIPOWER": 1240,
    "APOLLOHOSP": 1295,
    "SHREECEM": 1450,
    "HINDALCO": 1213,
    "JSWSTEEL": 1286,
    "TATASTEEL": 1442,
    "SAILIND": 1289,
    "BAJAJ-AUTO": 1021,
    "HEROMOTOCO": 1213,
    "EICHERMOT": 1255,
    "M&M": 1359,
    "MAHINDRA": 1359,
    "BOSCHIND": 1241,
    "MRF": 1366,
    "MOTHERSON": 1375,
    "TIINDIA": 1439,
}

# COMMODITY F&O (MCX) → Goes to COMMODITY channel
COMMODITY_SYMBOLS = {
    "CRUDEOIL": {"security_id": 565899, "exchange": "MCX_FUT", "type": "COMMODITY"},
    "NATURALGAS": {"security_id": 565900, "exchange": "MCX_FUT", "type": "COMMODITY"},
    "GOLD": {"security_id": 565901, "exchange": "MCX_FUT", "type": "COMMODITY"},
    "SILVER": {"security_id": 565902, "exchange": "MCX_FUT", "type": "COMMODITY"},
}

# CRYPTO (TradingView) → Goes to CRYPTO channel
CRYPTO_SYMBOLS = {
    "BTCUSD": {"type": "CRYPTO"},
    "ETHUSD": {"type": "CRYPTO"},
}

# Combine all for monitoring
ALL_SYMBOLS = {}
ALL_SYMBOLS.update(INDEX_SYMBOLS)
ALL_SYMBOLS.update(INDEX_SYMBOLS_SPOT)
for stock_name, sec_id in NIFTY_50_STOCKS.items():
    ALL_SYMBOLS[stock_name] = {"security_id": sec_id, "exchange": "NSE", "type": "STOCK"}
ALL_SYMBOLS.update(COMMODITY_SYMBOLS)

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
    "public_ip": None,
    "errors": [],
    "bot_status": {},  # Track each bot connection
}

strategies = {}
previous_ltp = {}
system_bot = None
trading_bots = {}
dhan_api = None
telegram_event_loop = None

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def detect_public_ip():
    """Detect Render's public IP address"""
    try:
        response = requests.get('https://api.ipify.org?format=json', timeout=5)
        return response.json()['ip']
    except Exception as e:
        logger.error(f"IP detection failed: {e}")
        return "UNKNOWN"

def get_ist_time():
    """Get current time in India Standard Time (IST)"""
    utc_now = datetime.now(pytz.UTC)
    ist_now = utc_now.astimezone(IST)
    return ist_now

def get_channel_for_symbol(symbol):
    """
    Determine which channel a signal should go to based on symbol
    
    Returns: (bot_type, bot_config) tuple or (None, None)
    """
    # INDEX F&O
    if symbol in INDEX_SYMBOLS or symbol in INDEX_SYMBOLS_SPOT:
        return "INDEX", TRADING_BOTS["INDEX"]
    
    # NIFTY 50 STOCKS (50 stocks → NIFTY50_STOCKS channel)
    elif symbol in NIFTY_50_STOCKS:
        return "NIFTY50_STOCKS", TRADING_BOTS["NIFTY50_STOCKS"]
    
    # COMMODITY F&O (MCX)
    elif symbol in COMMODITY_SYMBOLS:
        return "COMMODITY", TRADING_BOTS["COMMODITY"]
    
    # CRYPTO (from TradingView)
    elif symbol in CRYPTO_SYMBOLS:
        return "CRYPTO", TRADING_BOTS["CRYPTO"]
    
    else:
        return None, None

# ============================================================================
# INITIALIZATION
# ============================================================================

def initialize():
    """Initialize screener components with multi-bot setup"""
    global system_bot, trading_bots, dhan_api, telegram_event_loop
    
    PUBLIC_IP = detect_public_ip()
    screener_state["public_ip"] = PUBLIC_IP
    
    ist_time = get_ist_time()
    logger.info("=" * 80)
    logger.info("🚀 INITIALIZING PRODUCTION SCREENER (MULTI-BOT VERSION)")
    logger.info("=" * 80)
    logger.info(f"🌍 Server Time (IST): {ist_time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    logger.info(f"Public IP: {PUBLIC_IP}")
    logger.info(f"Monitoring Symbols: {len(ALL_SYMBOLS)} total")
    logger.info(f"  ├─ Index F&O: {len(INDEX_SYMBOLS) + len(INDEX_SYMBOLS_SPOT)}")
    logger.info(f"  ├─ NIFTY 50 Stocks: {len(NIFTY_50_STOCKS)}")
    logger.info(f"  ├─ Commodity F&O: {len(COMMODITY_SYMBOLS)}")
    logger.info(f"  └─ Crypto: {len(CRYPTO_SYMBOLS)}")
    logger.info(f"Trading Channels: 6 bots")
    logger.info(f"System Channel: 1 bot")
    logger.info("=" * 80)
    
    # Initialize strategies for all symbols
    for symbol in ALL_SYMBOLS.keys():
        strategies[symbol] = FiveMinBreakoutStrategy()
        previous_ltp[symbol] = None
    
    # Initialize System Bot (for daily checks, errors, mobile control)
    try:
        system_bot = Bot(token=SYSTEM_BOT_TOKEN)
        screener_state["bot_status"]["SYSTEM"] = "✅ Connected"
        logger.info("[SUCCESS] ✓ System Bot Connected (-1004321977761)")
    except Exception as e:
        logger.error(f"[ERROR] System Bot failed: {e}")
        screener_state["bot_status"]["SYSTEM"] = f"❌ {str(e)[:50]}"
        screener_state["errors"].append(f"System Bot: {str(e)}")
    
    # Initialize Trading Bots (one for each asset class)
    for bot_type, config in TRADING_BOTS.items():
        try:
            if config["token"]:
                bot = Bot(token=config["token"])
                trading_bots[bot_type] = bot
                screener_state["bot_status"][bot_type] = "✅ Connected"
                channel_desc = {
                    "INDEX": "📊 INDEX OPTIONS",
                    "NIFTY50_STOCKS": "📈 NIFTY 50 STOCKS OPTIONS",
                    "COMMODITY": "⚫ COMMODITY OPTIONS",
                    "NIFTY50_INTRADAY": "⚡️ NIFTY 50 INTRADAY 5X",
                    "NIFTY50_PAYLATER": "🏦 NIFTY 50 PAY LATER",
                    "CRYPTO": "💰 CRYPTO MARKET"
                }
                logger.info(f"[SUCCESS] ✓ {channel_desc[bot_type]} Bot Connected ({config['channel_id']})")
            else:
                logger.warning(f"[WARNING] {bot_type} token not configured in .env")
                screener_state["bot_status"][bot_type] = "⚠️ Token missing"
        except Exception as e:
            logger.error(f"[ERROR] {bot_type} Bot failed: {e}")
            screener_state["bot_status"][bot_type] = f"❌ {str(e)[:50]}"
            screener_state["errors"].append(f"{bot_type} Bot: {str(e)}")
    
    screener_state["telegram_connected"] = True if system_bot and trading_bots else False
    
    # Initialize DhanHQ
    try:
        if DHANHQ_AVAILABLE:
            dhan_context = DhanContext(client_id=CLIENT_ID, access_token=ACCESS_TOKEN)
            dhan_api = dhanhq(dhan_context)
            screener_state["dhan_connected"] = True
            logger.info("[SUCCESS] ✓ DhanHQ API Connected")
        else:
            logger.warning("[WARNING] DhanHQ library not available")
    except Exception as e:
        logger.error(f"[ERROR] DhanHQ failed: {e}")
        screener_state["dhan_connected"] = False
        screener_state["errors"].append(f"DhanHQ: {str(e)}")
    
    # Initialize global event loop for async telegram operations
    telegram_event_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(telegram_event_loop)
    
    logger.info("✅ Screener initialized successfully")
    logger.info("=" * 80)

def is_market_hours():
    """Check if market is open (NSE: 9:15-15:30, MCX: 9:00-23:30) - IST TIMEZONE"""
    # Get current time in IST (India Standard Time)
    ist_now = get_ist_time()
    current_time = ist_now.time()
    day = ist_now.weekday()
    
    # Weekend check (Saturday=5, Sunday=6)
    if day >= 5:
        logger.debug(f"❌ Weekend (Day {day}) - Market closed")
        return False
    
    # NSE trading hours (9:15 AM - 3:30 PM IST)
    nse_open = dtime(9, 15)
    nse_close = dtime(15, 30)
    
    if nse_open <= current_time <= nse_close:
        logger.debug(f"✅ NSE market open (IST: {current_time})")
        return True
    
    # MCX trading hours (9:00 AM - 11:30 PM IST)
    mcx_open = dtime(9, 0)
    mcx_close = dtime(23, 30)
    
    if mcx_open <= current_time <= mcx_close:
        logger.debug(f"✅ MCX market open (IST: {current_time})")
        return True
    
    logger.debug(f"❌ Market closed (IST: {current_time})")
    return False

def fetch_market_data():
    """Fetch real-time OHLC data from DhanHQ"""
    quotes = {}
    
    if not dhan_api:
        return quotes
    
    try:
        for symbol, config in ALL_SYMBOLS.items():
            if symbol in CRYPTO_SYMBOLS:
                # Crypto data comes from TradingView webhook, skip here
                continue
                
            try:
                resp = dhan_api.get_intraday_paracande(
                    exchange_tokens=[],
                    security_id=[config["security_id"]],
                    exchange=config["exchange"],
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
        logger.error(f"[ERROR] API fetch failed: {e}")
        screener_state["errors"].append(f"API fetch: {str(e)}")
    
    return quotes

# ============================================================================
# TELEGRAM ALERT FUNCTIONS
# ============================================================================

async def send_telegram_alert(bot_type, channel_id, message):
    """
    Send alert to specific Telegram channel
    
    Args:
        bot_type: Which bot to use (INDEX, NIFTY50_STOCKS, COMMODITY, etc)
        channel_id: Target channel ID
        message: Message text with HTML formatting
    """
    try:
        bot = trading_bots.get(bot_type)
        if not bot or not channel_id:
            logger.error(f"[ERROR] No bot for {bot_type}")
            return False
        
        await bot.send_message(
            chat_id=int(channel_id),
            text=message,
            parse_mode="HTML"
        )
        logger.info(f"✅ Alert sent to {bot_type} channel {channel_id}")
        return True
    except Exception as e:
        logger.error(f"[ERROR] Telegram send failed ({bot_type}): {e}")
        screener_state["errors"].append(f"Telegram ({bot_type}): {str(e)[:100]}")
        return False

async def send_system_alert(message):
    """Send system alerts (daily checks, errors, etc) to system channel"""
    try:
        if not system_bot:
            return False
        
        await system_bot.send_message(
            chat_id=int(SYSTEM_CHAT_ID),
            text=message,
            parse_mode="HTML"
        )
        logger.info(f"✅ System alert sent to {SYSTEM_CHAT_ID}")
        return True
    except Exception as e:
        logger.error(f"[ERROR] System alert failed: {e}")
        return False

def format_signal_message(symbol, signal_data, bot_type):
    """Format 10-minute breakout signal for Telegram"""
    entry_premium = signal_data.get('premium', signal_data['entry'])
    target_premium = entry_premium * 1.30  # 30% target
    stoploss_premium = entry_premium * 0.90  # 10% stop loss
    
    signal_type = signal_data['buy_side']
    strike_price = signal_data['strike_price']
    option_type = "CE" if signal_type == "CALL" else "PE"
    
    # Get IST time for alert
    ist_time = get_ist_time()
    alert_time = ist_time.strftime('%H:%M:%S %Z')
    
    channel_name = {
        "INDEX": "📊 INDEX F&O",
        "NIFTY50_STOCKS": "📈 NIFTY 50 STOCKS",
        "COMMODITY": "⚫ COMMODITY F&O",
        "NIFTY50_INTRADAY": "⚡️ INTRADAY 5X",
        "NIFTY50_PAYLATER": "🏦 PAY LATER",
        "CRYPTO": "💰 CRYPTO"
    }
    
    msg = f"""
<b>{'🚀 CALL ENTRY' if signal_type == 'CALL' else '📉 PUT ENTRY'}</b>

<b>Channel:</b> {channel_name.get(bot_type, bot_type)}
<b>Symbol:</b> {symbol} | {ALL_SYMBOLS[symbol]['type']}
<b>Strike Price:</b> {strike_price:.0f} {option_type}
<b>Premium (LTP):</b> {entry_premium:.2f}

<b>📊 POSITION DETAILS:</b>
  <b>Entry:</b> {entry_premium:.2f}
  <b>Target:</b> {target_premium:.2f} (30% gain)
  <b>Stop Loss:</b> {stoploss_premium:.2f} (10% loss)

<b>⏰ Time (IST):</b> {alert_time}
<b>🕐 Timeframe:</b> 10-MINUTE BREAKOUT
<b>📈 Current Price:</b> {entry_premium:.2f}

<b>📢 DISCLAIMER</b>
This is NOT SEBI-registered advice. For educational purposes only.
"""
    return msg

def process_signal(symbol, signal_data, signal_type):
    """
    Process signal and route to correct channel based on symbol
    """
    # Get the correct channel for this symbol
    bot_type, bot_config = get_channel_for_symbol(symbol)
    
    if not bot_type:
        logger.error(f"[ERROR] No channel routing for {symbol}")
        return
    
    # Update stats
    if signal_type == "CALL":
        screener_state["call_signals"] += 1
    else:
        screener_state["put_signals"] += 1
    
    screener_state["total_signals"] += 1
    
    entry_premium = signal_data.get('premium', signal_data['entry'])
    ist_time = get_ist_time()
    logger.info(f"\n{'='*80}")
    logger.info(f"🚀 SIGNAL #{screener_state['total_signals']} TRIGGERED - {signal_type}!")
    logger.info(f"Time (IST): {ist_time.strftime('%H:%M:%S')} | Market: {symbol} | Channel: {bot_type} | Entry: {entry_premium:.2f}")
    logger.info(f"{'='*80}\n")
    
    # Format message
    message = format_signal_message(symbol, signal_data, bot_type)
    
    # Send via thread-safe coroutine
    future = asyncio.run_coroutine_threadsafe(
        send_telegram_alert(bot_type, bot_config["channel_id"], message),
        telegram_event_loop
    )
    
    try:
        result = future.result(timeout=5)
        if result:
            logger.info(f"✅ {bot_type} signal delivered successfully")
        else:
            logger.error(f"❌ {bot_type} signal failed to send")
    except Exception as e:
        logger.error(f"[ERROR] Signal delivery failed: {e}")

# ============================================================================
# MAIN SCREENER LOOP
# ============================================================================

def screener_loop():
    """Main screener loop - runs in background thread"""
    
    ist_time = get_ist_time()
    logger.info("🚀 SCREENER BACKGROUND THREAD STARTED")
    logger.info(f"🌍 Current Time (IST): {ist_time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    logger.info(f"Monitoring {len(ALL_SYMBOLS)} symbols across 6 channels")
    logger.info(f"10-minute candle breakout strategy")
    
    screener_state["running"] = True
    scan_count = 0
    
    try:
        while screener_state["running"]:
            screener_state["total_scans"] += 1
            market_open = is_market_hours()
            screener_state["market_open"] = market_open
            
            ist_time = get_ist_time()
            screener_state["last_scan_time"] = ist_time.isoformat()
            
            # Skip if market closed
            if not market_open:
                time.sleep(5)
                continue
            
            current_time = ist_time.strftime("%H:%M:%S")
            scan_count += 1
            
            # Fetch real-time data
            quotes = fetch_market_data()
            
            if quotes:
                screener_state["successful_scans"] += 1
                
                for symbol in ALL_SYMBOLS.keys():
                    if symbol in CRYPTO_SYMBOLS:
                        # Crypto data comes from TradingView webhook
                        continue
                    
                    if symbol not in quotes:
                        continue
                    
                    quote = quotes[symbol]
                    ltp = quote.get("ltp", 0)
                    
                    # Check if price changed
                    if previous_ltp[symbol] is None or (ltp != previous_ltp[symbol] and ltp > 0):
                        previous_ltp[symbol] = ltp
                        
                        # Add candle to strategy
                        strategies[symbol].add_candle(
                            symbol,
                            open_price=quote.get("open", ltp),
                            high=quote.get("high", ltp),
                            low=quote.get("low", ltp),
                            close=quote.get("close", ltp),
                            oi=0,
                            timestamp=current_time
                        )
                        
                        # Check for CALL breakout (previous RED candle → higher close)
                        call_triggered, call_data = strategies[symbol].check_call_breakout(symbol)
                        if call_triggered:
                            strike = (ltp // 100) * 100
                            premium = ltp * 0.015
                            call_data['strike_price'] = strike
                            call_data['premium'] = premium
                            process_signal(symbol, call_data, "CALL")
                        
                        # Check for PUT breakout (previous GREEN candle → lower close)
                        put_triggered, put_data = strategies[symbol].check_put_breakout(symbol)
                        if put_triggered:
                            strike = (ltp // 100) * 100
                            premium = ltp * 0.015
                            put_data['strike_price'] = strike
                            put_data['premium'] = premium
                            process_signal(symbol, put_data, "PUT")
            
            # Log every 100 scans
            if scan_count % 100 == 0:
                logger.info(f"📊 Scan #{scan_count} | Signals: {screener_state['total_signals']} | Scans: {screener_state['successful_scans']}/{screener_state['total_scans']}")
            
            time.sleep(10)  # 10 second scan interval
    
    except Exception as e:
        logger.error(f"[ERROR] Screener crashed: {e}")
        screener_state["errors"].append(str(e))
        screener_state["running"] = False
        
        # Send crash alert to system channel
        if system_bot:
            msg = f"<b>🚨 SCREENER CRASH DETECTED</b>\n<b>Error:</b> {str(e)[:200]}"
            asyncio.run_coroutine_threadsafe(
                send_system_alert(msg),
                telegram_event_loop
            )
    finally:
        logger.info("🛑 Screener background thread stopped")

def start_screener():
    """Start screener in background thread"""
    initialize()
    
    # Start screener thread (daemon so it doesn't block shutdown)
    screener_thread = threading.Thread(target=screener_loop, daemon=True)
    screener_thread.start()
    
    logger.info("✅ Screener background thread started successfully")
    return screener_thread

def stop_screener():
    """Stop screener gracefully"""
    screener_state["running"] = False
    logger.info("🛑 Screener stop signal sent")

