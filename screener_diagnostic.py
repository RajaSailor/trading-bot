"""
DIAGNOSTIC SCREENER - Find what's wrong with data fetch
File: screener_diagnostic.py
"""

import os
import requests
import pandas as pd
from dotenv import load_dotenv
from datetime import datetime, time as dtime

load_dotenv(dotenv_path="./.env", override=True)

API_KEY = os.getenv("API_KEY")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

BASE_URL = "https://api.dhan.co/v2"

print(f"""
{'='*85}
🔍 DIAGNOSTIC - Testing API Data Fetch
{'='*85}
[INFO] Configuration:
  API_KEY: {API_KEY[:10]}...
  ACCESS_TOKEN: {ACCESS_TOKEN[:20]}...
  BASE_URL: {BASE_URL}
{'='*85}
""")

# Load security list
SECURITY_FILE = "./security_list.csv"
try:
    df = pd.read_csv(SECURITY_FILE, low_memory=False)
    print(f"[SUCCESS] Security list loaded: {len(df)} records\n")
except Exception as e:
    print(f"[ERROR] Failed to load security list: {e}")
    exit(1)

def get_security_id(symbol_name):
    """Get security ID from CSV"""
    print(f"\n[STEP] Looking for symbol: {symbol_name}")
    
    # Try different column names
    if "tradingSymbol" in df.columns:
        row = df[df['tradingSymbol'] == symbol_name]
        if not row.empty:
            print(f"  ✓ Found in 'tradingSymbol' column")
            return str(row.iloc[0]['securityId'])
    
    if "SM_SYMBOL_NAME" in df.columns:
        row = df[df['SM_SYMBOL_NAME'] == symbol_name]
        if not row.empty:
            print(f"  ✓ Found in 'SM_SYMBOL_NAME' column")
            return str(row.iloc[0]['SEM_SMST_SECURITY_ID'])
    
    print(f"  ✗ Symbol NOT found")
    return None

def fetch_ltp_debug(instruments):
    """Fetch LTP with full debugging"""
    url = f"{BASE_URL}/marketfeed/ltp"
    headers = {
        "X-Client-Id": API_KEY,
        "X-Access-Token": ACCESS_TOKEN,
        "Content-Type": "application/json"
    }
    
    print(f"\n[STEP] Fetching LTP Data")
    print(f"  URL: {url}")
    print(f"  Headers: X-Client-Id={API_KEY[:10]}..., X-Access-Token={ACCESS_TOKEN[:20]}...")
    print(f"  Payload: {instruments}")
    
    try:
        resp = requests.post(url, headers=headers, json=instruments, timeout=10)
        
        print(f"\n[RESPONSE]")
        print(f"  Status Code: {resp.status_code}")
        print(f"  Response Text: {resp.text[:500]}")
        
        if resp.status_code == 200:
            data = resp.json()
            print(f"\n[PARSED JSON]")
            print(f"  Status: {data.get('status')}")
            print(f"  Data Keys: {list(data.get('data', {}).keys())}")
            
            if data.get("status") == "success":
                ltp_data = data.get("data", {})
                print(f"\n[LTP DATA STRUCTURE]")
                for key, value in ltp_data.items():
                    print(f"  {key}:")
                    if isinstance(value, dict):
                        for k, v in value.items():
                            print(f"    - {k}: {v}")
                    else:
                        print(f"    - {value}")
                
                return ltp_data
            else:
                print(f"  Error: {data}")
                return None
        else:
            print(f"  Error: HTTP {resp.status_code}")
            return None
            
    except Exception as e:
        print(f"\n[ERROR] Request failed: {e}")
        import traceback
        traceback.print_exc()
        return None

def test_all_symbols():
    """Test fetch for all symbols"""
    
    symbols_to_test = ["NIFTY", "BANKNIFTY", "CRUDEOIL"]
    
    for symbol in symbols_to_test:
        print(f"\n{'─'*85}")
        print(f"TESTING: {symbol}")
        print(f"{'─'*85}")
        
        security_id = get_security_id(symbol)
        
        if not security_id:
            print(f"  ✗ Cannot proceed - security ID not found")
            continue
        
        # Determine exchange segment
        if symbol == "CRUDEOIL":
            exchange = "MCX_FUT"
        else:
            exchange = "NSE_FNO"
        
        instruments = {exchange: [security_id]}
        
        ltp_data = fetch_ltp_debug(instruments)
        
        if ltp_data:
            print(f"\n[SUCCESS] ✓ Got data for {symbol}")
        else:
            print(f"\n[FAILED] ✗ No data for {symbol}")

def test_market_hours():
    """Check market hours"""
    print(f"\n{'='*85}")
    print("MARKET HOURS CHECK")
    print(f"{'='*85}")
    
    now = datetime.now()
    current_time = now.time()
    current_day = now.weekday()
    
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    
    print(f"\nCurrent Date/Time: {now.strftime('%Y-%m-%d %H:%M:%S')} ({day_names[current_day]})")
    
    # MCX hours
    mcx_open = dtime(9, 0)
    mcx_close = dtime(23, 30)
    
    # NSE hours
    nse_open = dtime(9, 15)
    nse_close = dtime(15, 40)
    
    print(f"\nMCX Hours: 9:00 AM - 11:30 PM")
    mcx_open_now = mcx_open <= current_time <= mcx_close
    print(f"  Status: {'✓ OPEN' if mcx_open_now else '✗ CLOSED'}")
    
    print(f"\nNSE Hours: 9:15 AM - 3:40 PM")
    nse_open_now = nse_open <= current_time <= nse_close
    print(f"  Status: {'✓ OPEN' if nse_open_now else '✗ CLOSED'}")
    
    if current_day >= 5:
        print(f"\n⚠️  WEEKEND - Markets Closed")

if __name__ == "__main__":
    test_market_hours()
    test_all_symbols()
    
    print(f"\n{'='*85}")
    print("DIAGNOSTIC COMPLETE")
    print(f"{'='*85}\n")
