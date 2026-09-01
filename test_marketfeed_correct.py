"""
DIAGNOSTIC - Find Correct MarketFeed Initialization
File: test_marketfeed_correct.py
"""

import os
from dotenv import load_dotenv

load_dotenv(dotenv_path="./.env", override=True)

CLIENT_ID = os.getenv("API_KEY")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")

print(f"""
Testing MarketFeed Initialization (Finding Correct Syntax)
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

# Check MarketFeed class signature
print(f"{'='*80}")
print("Checking MarketFeed class structure")
print(f"{'='*80}\n")

print(f"[DEBUG] MarketFeed.__init__ signature:")
import inspect
try:
    sig = inspect.signature(MarketFeed.__init__)
    print(f"  {sig}\n")
except:
    print("  Could not get signature\n")

# Test different initialization methods
test_cases = [
    {
        "name": "Method 1: Positional (id, segment, context)",
        "test": lambda: MarketFeed("13", "NSE_FNO", dhan_context)
    },
    {
        "name": "Method 2: Positional (segment, id, context)",
        "test": lambda: MarketFeed("NSE_FNO", "13", dhan_context)
    },
    {
        "name": "Method 3: Positional (id, segment) only",
        "test": lambda: MarketFeed("13", "NSE_FNO")
    },
    {
        "name": "Method 4: With context kwarg",
        "test": lambda: MarketFeed("13", "NSE_FNO", context=dhan_context)
    },
]

for test_case in test_cases:
    print(f"\n{'─'*80}")
    print(f"Testing: {test_case['name']}")
    print(f"{'─'*80}")
    
    try:
        mf = test_case['test']()
        print(f"[SUCCESS] ✓ MarketFeed created!")
        print(f"[DEBUG] Type: {type(mf)}")
        print(f"[DEBUG] Methods: {[m for m in dir(mf) if not m.startswith('_')]}")
        
        # Try to get quote
        print(f"\n[STEP] Calling quote()...")
        quote = mf.quote()
        
        if quote:
            print(f"[SUCCESS] ✓ Quote received!")
            print(f"[DATA]")
            for key, value in quote.items():
                print(f"  {key}: {value}")
        else:
            print(f"[DEBUG] Quote returned: {quote}")
            
    except Exception as e:
        print(f"[FAILED] {type(e).__name__}: {e}")

print(f"\n{'='*80}")
print("DIAGNOSTIC COMPLETE")
print(f"{'='*80}\n")
