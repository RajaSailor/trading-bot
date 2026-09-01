"""
DIAGNOSTIC - Find correct DhanHTTP API method
File: test_dhanhttp_methods.py
"""

import os
from dotenv import load_dotenv
import inspect

load_dotenv(dotenv_path="./.env", override=True)

CLIENT_ID = os.getenv("API_KEY")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")

print(f"""
Finding DhanHTTP Available Methods
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

# Get DhanHTTP object
try:
    dhan_http = dhan_context.get_dhan_http()
    print(f"[SUCCESS] ✓ DhanHTTP object obtained")
    print(f"[DEBUG] DhanHTTP type: {type(dhan_http)}\n")
except Exception as e:
    print(f"[ERROR] Failed to get DhanHTTP: {e}")
    exit(1)

# List ALL methods
print(f"{'='*80}")
print("ALL Methods in DhanHTTP object:")
print(f"{'='*80}\n")

all_methods = [m for m in dir(dhan_http) if not m.startswith('_')]
for method in all_methods:
    try:
        attr = getattr(dhan_http, method)
        if callable(attr):
            # Get signature
            try:
                sig = inspect.signature(attr)
                print(f"✓ {method}{sig}")
            except:
                print(f"✓ {method}(...)")
        else:
            print(f"  {method}: {type(attr).__name__}")
    except:
        print(f"  {method}")

print(f"\n{'='*80}")
print("DIAGNOSTIC COMPLETE")
print(f"{'='*80}\n")
