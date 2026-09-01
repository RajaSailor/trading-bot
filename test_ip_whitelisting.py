"""
TEST IP WHITELISTING - Check if your IP is properly configured
File: test_ip_whitelisting.py
"""

import os
from dotenv import load_dotenv
import requests

load_dotenv(dotenv_path="./.env", override=True)

CLIENT_ID = os.getenv("API_KEY")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")

print(f"""
{'='*80}
TESTING IP WHITELISTING WITH DHANHQ
{'='*80}

[INFO] Checking your public IP and DhanHQ access
[INFO] CLIENT_ID: {CLIENT_ID[:10]}...
[INFO] ACCESS_TOKEN: {ACCESS_TOKEN[:20]}...
""")

# Test 1: Get your public IP
print(f"\n[STEP 1] Getting your public IP address...")
print(f"{'─'*80}")

try:
    response = requests.get('https://api.ipify.org?format=json', timeout=5)
    if response.status_code == 200:
        public_ip = response.json()['ip']
        print(f"[SUCCESS] ✓ Your public IP: {public_ip}")
    else:
        print(f"[ERROR] Could not get IP: {response.status_code}")
except Exception as e:
    print(f"[ERROR] {e}")
    public_ip = None

# Test 2: Test DhanHQ connection
print(f"\n[STEP 2] Testing DhanHQ API connection...")
print(f"{'─'*80}")

try:
    from dhanhq import DhanContext, MarketFeed
    
    dhan_context = DhanContext(client_id=CLIENT_ID, access_token=ACCESS_TOKEN)
    market_feed = MarketFeed(dhan_context)
    
    # Try to fetch OHLC data for NIFTY
    securities = {
        "NSE_FNO": [13]  # NIFTY
    }
    
    print(f"[CALL] Attempting to fetch OHLC data for NIFTY...")
    response = market_feed.ohlc_data(securities)
    
    print(f"[RESPONSE] {response}")
    
    if response and response.get('status') == 'success':
        print(f"\n[SUCCESS] ✓✓✓ YOUR IP IS WHITELISTED AND WORKING!")
        print(f"[SUCCESS] Data API is properly configured!")
        print(f"\n🎉 YOU CAN NOW RUN THE SCREENER!")
        
    elif response and response.get('status') == 'failure':
        error_msg = response.get('remarks', {})
        print(f"\n[FAILED] ✗ API returned failure")
        print(f"[ERROR] Response: {error_msg}")
        print(f"\n⚠️  This means:")
        print(f"   1. Your IP might NOT be whitelisted yet")
        print(f"   2. Your Data API subscription might have issues")
        print(f"   3. Contact Dhan Support with this info:")
        if public_ip:
            print(f"      'My IP {public_ip} is set but API returns failure'")
        
except Exception as e:
    print(f"[ERROR] {type(e).__name__}: {e}")
    print(f"\n⚠️  Could not initialize DhanHQ API")

print(f"\n{'='*80}")
print("TEST COMPLETE")
print(f"{'='*80}\n")
