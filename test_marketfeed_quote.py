"""
DIAGNOSTIC - Test MarketFeed Quote API
Debug why MarketFeed.quote() is returning None
File: test_marketfeed_quote.py
"""

import os
from dotenv import load_dotenv

load_dotenv(dotenv_path="./.env", override=True)

CLIENT_ID = os.getenv("API_KEY")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")

print(f"""
Testing MarketFeed Quote API
CLIENT_ID: {CLIENT_ID}
ACCESS_TOKEN: {ACCESS_TOKEN[:30]}...
""")

try:
    from dhanhq import DhanContext, MarketFeed
    print("[SUCCESS] ✓ DhanHQ imported\n")
except Exception as e:
    print(f"[ERROR] Failed to import: {e}")
    exit(1)

# Initialize context
try:
    dhan_context = DhanContext(client_id=CLIENT_ID, access_token=ACCESS_TOKEN)
    print("[SUCCESS] ✓ DhanContext initialized\n")
except Exception as e:
    print(f"[ERROR] DhanContext init failed: {e}")
    exit(1)

# Test symbols
test_symbols = [
    {"name": "NIFTY", "id": "13", "segment": "NSE_FNO"},
    {"name": "BANKNIFTY", "id": "25", "segment": "NSE_FNO"},
    {"name": "CRUDEOIL", "id": "565899", "segment": "MCX_FUT"},
]

for symbol in test_symbols:
    print(f"{'='*80}")
    print(f"Testing: {symbol['name']} (ID: {symbol['id']}, Segment: {symbol['segment']})")
    print(f"{'='*80}")
    
    try:
        # Create MarketFeed object
        print(f"[STEP 1] Creating MarketFeed object...")
        marketfeed = MarketFeed(
            security_id=symbol['id'],
            exchange_segment=symbol['segment'],
            context=dhan_context
        )
        print(f"[SUCCESS] ✓ MarketFeed object created")
        
        # Try to get quote
        print(f"\n[STEP 2] Calling marketfeed.quote()...")
        quote = marketfeed.quote()
        
        print(f"[DEBUG] Quote returned: {quote}")
        print(f"[DEBUG] Quote type: {type(quote)}")
        
        if quote:
            print(f"\n[SUCCESS] ✓ Quote data received!")
            print(f"[DATA]")
            for key, value in quote.items():
                print(f"  {key}: {value}")
        else:
            print(f"\n[FAILED] ✗ Quote is None or empty")
            
            # Try alternative methods
            print(f"\n[STEP 3] Trying alternative methods...")
            
            # Try get_quotes
            try:
                print(f"  Trying: dhan_context.get_quotes()...")
                quotes = dhan_context.get_quotes(
                    security_id=symbol['id'],
                    exchange_segment=symbol['segment']
                )
                print(f"  Result: {quotes}")
            except Exception as e2:
                print(f"  Error: {e2}")
            
            # Try get_market_feed
            try:
                print(f"  Trying: dhan_context.get_market_feed()...")
                feed = dhan_context.get_market_feed(
                    security_id=symbol['id'],
                    exchange_segment=symbol['segment']
                )
                print(f"  Result: {feed}")
            except Exception as e3:
                print(f"  Error: {e3}")
    
    except Exception as e:
        print(f"[ERROR] Failed: {e}")
        import traceback
        traceback.print_exc()
    
    print()

print(f"{'='*80}")
print("DIAGNOSTIC COMPLETE")
print(f"{'='*80}\n")
