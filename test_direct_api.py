"""
DIRECT API TEST - Fetch quotes using DhanContext methods
Bypass WebSocket to test if we can get ANY data
File: test_direct_api.py
"""

import os
from dotenv import load_dotenv
import time

load_dotenv(dotenv_path="./.env", override=True)

CLIENT_ID = os.getenv("API_KEY")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")

print(f"""
Testing DhanContext Direct API Methods
CLIENT_ID: {CLIENT_ID}
ACCESS_TOKEN: {ACCESS_TOKEN[:30]}...
""")

try:
    from dhanhq import DhanContext
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

# List all available methods
print(f"{'='*80}")
print("Available Methods in DhanContext:")
print(f"{'='*80}")
methods = [m for m in dir(dhan_context) if not m.startswith('_')]
for method in methods:
    print(f"  - {method}")
print()

# Test symbols
test_symbols = [
    {"name": "CRUDEOIL", "id": "565899", "segment": "MCX_FUT"},
]

for symbol in test_symbols:
    print(f"\n{'='*80}")
    print(f"Testing: {symbol['name']} (ID: {symbol['id']}, Segment: {symbol['segment']})")
    print(f"{'='*80}\n")
    
    # Try different methods
    methods_to_try = [
        {
            "name": "get_quotes()",
            "test": lambda: dhan_context.get_quotes(
                security_id=symbol['id'],
                exchange_segment=symbol['segment']
            )
        },
        {
            "name": "intraday_minute_charts()",
            "test": lambda: dhan_context.intraday_minute_charts(
                security_id=symbol['id'],
                exchange_segment=symbol['segment'],
                interval=5
            )
        },
        {
            "name": "historical_minute_charts()",
            "test": lambda: dhan_context.historical_minute_charts(
                security_id=symbol['id'],
                exchange_segment=symbol['segment'],
                interval=5,
                from_date="2026-08-24",
                to_date="2026-08-24"
            )
        },
    ]
    
    for method_test in methods_to_try:
        print(f"\n[TEST] {method_test['name']}")
        print(f"{'─'*80}")
        
        try:
            result = method_test['test']()
            
            print(f"[SUCCESS] ✓ Got response!")
            print(f"[DEBUG] Response type: {type(result)}")
            print(f"[DEBUG] Response keys: {result.keys() if isinstance(result, dict) else 'N/A'}")
            
            # Print response data
            if isinstance(result, dict):
                print(f"\n[DATA]")
                for key, value in result.items():
                    if isinstance(value, list) and len(value) > 0:
                        print(f"  {key}: [List with {len(value)} items]")
                        if len(value) > 0:
                            print(f"    First item: {value[0]}")
                    else:
                        print(f"  {key}: {value}")
            
        except Exception as e:
            print(f"[FAILED] {type(e).__name__}: {e}")

print(f"\n{'='*80}")
print("DIRECT API TEST COMPLETE")
print(f"{'='*80}\n")
