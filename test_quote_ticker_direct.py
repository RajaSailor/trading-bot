"""
DIRECT TEST - Call quote_data() and ticker_data() directly
See what the API actually returns
File: test_quote_ticker_direct.py
"""

import os
from dotenv import load_dotenv
import json

load_dotenv(dotenv_path="./.env", override=True)

CLIENT_ID = os.getenv("API_KEY")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")

print(f"""
Testing quote_data() and ticker_data() Directly
CLIENT_ID: {CLIENT_ID}
ACCESS_TOKEN: {ACCESS_TOKEN[:30]}...
""")

try:
    from dhanhq import DhanContext, dhanhq
    print("[SUCCESS] ✓ DhanHQ imported\n")
except Exception as e:
    print(f"[ERROR] Failed to import: {e}")
    exit(1)

# Initialize
try:
    dhan_context = DhanContext(client_id=CLIENT_ID, access_token=ACCESS_TOKEN)
    dhan_api = dhanhq(dhan_context)
    print("[SUCCESS] ✓ dhanhq() initialized\n")
except Exception as e:
    print(f"[ERROR] Init failed: {e}")
    exit(1)

# Test data
test_cases = [
    {
        "name": "CRUDEOIL via quote_data",
        "method": "quote_data",
        "securities": [{"security_id": "565899", "exchange_segment": "MCX_FUT"}]
    },
    {
        "name": "CRUDEOIL via ticker_data",
        "method": "ticker_data",
        "securities": [{"security_id": "565899", "exchange_segment": "MCX_FUT"}]
    },
    {
        "name": "NIFTY via quote_data",
        "method": "quote_data",
        "securities": [{"security_id": "13", "exchange_segment": "NSE_FNO"}]
    },
    {
        "name": "NIFTY via ticker_data",
        "method": "ticker_data",
        "securities": [{"security_id": "13", "exchange_segment": "NSE_FNO"}]
    },
    {
        "name": "BANKNIFTY via quote_data",
        "method": "quote_data",
        "securities": [{"security_id": "25", "exchange_segment": "NSE_FNO"}]
    },
    {
        "name": "BANKNIFTY via ticker_data",
        "method": "ticker_data",
        "securities": [{"security_id": "25", "exchange_segment": "NSE_FNO"}]
    },
]

for test in test_cases:
    print(f"\n{'='*80}")
    print(f"[TEST] {test['name']}")
    print(f"{'='*80}")
    
    try:
        # Call the method
        method = getattr(dhan_api, test['method'])
        response = method(test['securities'])
        
        print(f"[SUCCESS] ✓ Got response!")
        print(f"[DEBUG] Response type: {type(response)}")
        print(f"[DEBUG] Response:\n{json.dumps(response, indent=2, default=str)[:1000]}")
        
        # Print full response if short
        full_response = json.dumps(response, indent=2, default=str)
        if len(full_response) > 1000:
            print(f"... (truncated, full length: {len(full_response)} chars)")
        
    except Exception as e:
        print(f"[ERROR] {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

print(f"\n{'='*80}")
print("DIRECT TEST COMPLETE")
print(f"{'='*80}\n")
