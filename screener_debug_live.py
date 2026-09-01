"""
DEBUG SCREENER - Show Every Tick and Strategy Status
Real-time debugging to see why signals aren't triggering
File: screener_debug_live.py
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

print(f"""
{'='*90}
🐛 DEBUG SCREENER - 5 MIN BREAKOUT STRATEGY
{'='*90}
[INFO] Configuration:
  CLIENT_ID: {CLIENT_ID[:10]}...
  TELEGRAM: Connected to {CHAT_ID}
  
This screener will:
  ✓ Show EVERY tick received
  ✓ Display current candle status (RED/GREEN)
  ✓ Show entry/stop-loss levels
  ✓ Print WHY signals trigger or don't trigger
  
Market Hours:
  - MCX Crude Oil: 9:00 AM - 11:30 PM IST
{'='*90}
""")

if not DHANHQ_AVAILABLE:
    print("\n[ERROR] DhanHQ library not installed!")
    exit(1)

# Initialize DhanHQ context
try:
    dhan_context = DhanContext(client_id=CLIENT_ID, access_token=ACCESS_TOKEN)
    print("[SUCCESS] ✓ DhanHQ context initialized\n")
except Exception as e:
    print(f"[ERROR] Failed to initialize DhanHQ context: {e}")
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

# Track candle data for debugging
candle_data = {
    "NIFTY": {"ticks": 0, "last_high": None, "last_low": None},
    "BANKNIFTY": {"ticks": 0, "last_high": None, "last_low": None},
    "CRUDEOIL": {"ticks": 0, "last_high": None, "last_low": None},
}

tick_count = 0

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

def on_message(ticks):
    """Callback when MarketFeed receives tick data"""
    global tick_count
    
    if not ticks:
        return
    
    # ticks is a list of tick data
    if isinstance(ticks, list):
        for tick in ticks:
            tick_count += 1
            process_tick(tick)
    else:
        tick_count += 1
        process_tick(ticks)

def process_tick(tick):
    """Process a single tick with debugging"""
    global tick_count
    
    try:
        # Parse tick data
        security_id = str(tick.get("security_id", ""))
        ltp = tick.get("ltp", 0)
        high = tick.get("high_price", ltp)
        low = tick.get("low_price", ltp)
        
        # Map security ID to symbol
        symbol = None
        if security_id == "13":
            symbol = "NIFTY"
        elif security_id == "25":
            symbol = "BANKNIFTY"
        elif security_id == "565899":
            symbol = "CRUDEOIL"
        
        if not symbol:
            return
        
        # Update candle data tracking
        candle_data[symbol]["ticks"] += 1
        candle_data[symbol]["last_high"] = high
        candle_data[symbol]["last_low"] = low
        
        current_time = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        
        # Print tick info every 10 ticks per symbol
        if candle_data[symbol]["ticks"] % 10 == 0:
            print(f"\n[TICK #{tick_count}] {symbol} - {current_time}")
            print(f"  LTP: {ltp} | High: {high} | Low: {low}")
            print(f"  Ticks received for {symbol}: {candle_data[symbol]['ticks']}")
        
        # Add to strategy
        strategies[symbol].add_candle(
            symbol,
            open_price=ltp,
            high=high,
            low=low,
            close=ltp,
            oi=tick.get("open_interest", 0),
            timestamp=current_time
        )
        
        # Get strategy state
        strategy = strategies[symbol]
        
        # Print strategy debug info every 20 ticks
        if candle_data[symbol]["ticks"] % 20 == 0:
            print(f"\n[STRATEGY DEBUG] {symbol}")
            
            # Get candle history
            if symbol in strategy.candles:
                candles = strategy.candles[symbol]
                print(f"  Total candles: {len(candles)}")
                
                if len(candles) >= 2:
                    # Last 2 candles
                    prev_candle = candles[-2]
                    curr_candle = candles[-1]
                    
                    # Determine color
                    prev_color = "RED" if prev_candle['close'] < prev_candle['open'] else "GREEN"
                    curr_color = "RED" if curr_candle['close'] < curr_candle['open'] else "GREEN"
                    
                    print(f"\n  Prev Candle ({prev_color}):")
                    print(f"    Open: {prev_candle['open']}, High: {prev_candle['high']}, Low: {prev_candle['low']}, Close: {prev_candle['close']}")
                    
                    print(f"\n  Curr Candle ({curr_color}):")
                    print(f"    Open: {curr_candle['open']}, High: {curr_candle['high']}, Low: {curr_candle['low']}, Close: {curr_candle['close']}")
                    
                    # Check breakout conditions
                    print(f"\n  Breakout Check:")
                    
                    if prev_color == "RED" and curr_color == "GREEN":
                        breakout_level = prev_candle['high']
                        print(f"    RED → GREEN detected!")
                        print(f"    Prev RED High: {breakout_level}")
                        print(f"    Curr GREEN Close: {curr_candle['close']}")
                        
                        if curr_candle['close'] > breakout_level:
                            print(f"    ✅ CALL BREAKOUT CONDITION MET!")
                        else:
                            print(f"    ❌ Not enough breakout (need close > {breakout_level})")
                    
                    elif prev_color == "GREEN" and curr_color == "RED":
                        breakout_level = prev_candle['low']
                        print(f"    GREEN → RED detected!")
                        print(f"    Prev GREEN Low: {breakout_level}")
                        print(f"    Curr RED Close: {curr_candle['close']}")
                        
                        if curr_candle['close'] < breakout_level:
                            print(f"    ✅ PUT BREAKOUT CONDITION MET!")
                        else:
                            print(f"    ❌ Not enough breakout (need close < {breakout_level})")
                    else:
                        print(f"    {prev_color} → {curr_color} (waiting for RED↔GREEN flip)")
        
        # Check for signals
        call_triggered, call_data = strategies[symbol].check_call_breakout(symbol)
        if call_triggered:
            print_signal(symbol, call_data, "CALL")
        
        put_triggered, put_data = strategies[symbol].check_put_breakout(symbol)
        if put_triggered:
            print_signal(symbol, put_data, "PUT")
            
    except Exception as e:
        print(f"[ERROR] Error processing tick: {e}")
        import traceback
        traceback.print_exc()

def print_signal(symbol, signal_data, signal_type):
    """Print signal with all details"""
    if signal_data:
        print(f"\n{'='*90}")
        print(f"🚀 SIGNAL DETECTED - {signal_type}")
        print(f"{'='*90}")
        print(f"Market: {symbol}")
        print(f"Signal Type: {signal_data['buy_side']}")
        print(f"Strike Price: {signal_data['strike_price']}")
        print(f"Entry: {signal_data['entry']}")
        print(f"Stop Loss: {signal_data['stop_loss']}")
        print(f"Target: {signal_data['target']}")
        print(f"Current Price: {signal_data['current_price']}")
        print(f"Breakout Level: {signal_data['breakout_level']}")
        print(f"Signal Time: {signal_data['timestamp']}")
        print(f"{'='*90}\n")

def on_error(error):
    """Callback for errors"""
    print(f"[ERROR] MarketFeed error: {error}")

def on_close():
    """Callback when connection closes"""
    print(f"[INFO] MarketFeed connection closed")

def screener_loop():
    """Main screener loop using MarketFeed"""
    
    market_open, market_status = is_market_hours()
    
    print(f"\n[MARKET STATUS]")
    print(f"  📅 Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  📊 Status: {market_status}\n")
    
    if not market_open:
        print(f"[INFO] Market is closed. Screener not running.")
        return
    
    print(f"[SUCCESS] Market is OPEN ✓")
    print(f"[INFO] Starting 5-min screener (DEBUG MODE - Real-time ticks)\n")
    
    # Get security IDs
    nifty_id = get_security_id("NIFTY")
    banknifty_id = get_security_id("BANKNIFTY")
    crudeoil_id = get_security_id("CRUDEOIL")
    
    print(f"[INFO] Security IDs loaded")
    print(f"  NIFTY: {nifty_id}")
    print(f"  BANKNIFTY: {banknifty_id}")
    print(f"  CRUDEOIL: {crudeoil_id}\n")
    
    # Build instruments list for MarketFeed
    instruments = {
        "NSE_FNO": [nifty_id, banknifty_id] if nifty_id and banknifty_id else [],
        "MCX_FUT": [crudeoil_id] if crudeoil_id else []
    }
    
    # Remove empty segments
    instruments = {k: v for k, v in instruments.items() if v}
    
    print(f"[INFO] Instruments to subscribe:")
    print(f"  {instruments}\n")
    
    if not instruments:
        print(f"[ERROR] No valid instruments found!")
        return
    
    try:
        print(f"[STEP] Initializing MarketFeed...")
        print(f"  Instruments: {instruments}\n")
        
        # Create MarketFeed with correct parameters
        marketfeed = MarketFeed(
            dhan_context,
            instruments,
            version='v2',
            on_message=on_message,
            on_error=on_error,
            on_close=on_close
        )
        
        print(f"[SUCCESS] ✓ MarketFeed initialized")
        print(f"[INFO] Listening for real-time ticks...")
        print(f"[INFO] Debug info will print every 10-20 ticks per symbol\n")
        
        # Keep the screener running
        while True:
            market_open, _ = is_market_hours()
            
            if not market_open:
                print(f"\n[INFO] Market hours ended. Screener stopped.")
                print(f"\n[SUMMARY]")
                for symbol in ["NIFTY", "BANKNIFTY", "CRUDEOIL"]:
                    print(f"  {symbol}: {candle_data[symbol]['ticks']} ticks received")
                break
            
            time.sleep(1)
            
    except Exception as e:
        print(f"[ERROR] MarketFeed failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    try:
        screener_loop()
        
        print(f"\n{'='*90}")
        print(f"✅ DEBUG SCREENER SESSION COMPLETE")
        print(f"{'='*90}\n")
        
    except KeyboardInterrupt:
        print(f"\n\n[INFO] Screener stopped by user")
        print(f"\n[SUMMARY]")
        for symbol in ["NIFTY", "BANKNIFTY", "CRUDEOIL"]:
            print(f"  {symbol}: {candle_data[symbol]['ticks']} ticks received")
    except Exception as e:
        print(f"\n[ERROR] Screener crashed: {e}")
        import traceback
        traceback.print_exc()
