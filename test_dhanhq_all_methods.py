"""
DIAGNOSTIC - List ALL available methods in dhanhq() object
File: test_dhanhq_all_methods.py
"""

import os
from dotenv import load_dotenv
import inspect

load_dotenv(dotenv_path="./.env", override=True)

CLIENT_ID = os.getenv("API_KEY")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")

print(f"""
Finding ALL Methods in dhanhq() Object
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

# List ALL methods
print(f"{'='*80}")
print("ALL Available Methods in dhanhq() object:")
print(f"{'='*80}\n")

all_items = dir(dhan_api)
methods = []
properties = []

for item in all_items:
    if item.startswith('_'):
        continue
    
    try:
        attr = getattr(dhan_api, item)
        if callable(attr):
            methods.append(item)
        else:
            properties.append(item)
    except:
        pass

print("📌 METHODS (callable):")
print(f"{'─'*80}")
for method in sorted(methods):
    try:
        attr = getattr(dhan_api, method)
        sig = str(inspect.signature(attr))
        print(f"  ✓ {method}{sig}")
    except:
        print(f"  ✓ {method}(...)")

print(f"\n📌 PROPERTIES:")
print(f"{'─'*80}")
for prop in sorted(properties):
    print(f"  • {prop}")

print(f"\n{'='*80}")
print("DIAGNOSTIC COMPLETE")
print(f"{'='*80}\n")
