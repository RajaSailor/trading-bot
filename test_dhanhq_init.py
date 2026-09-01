"""
TEST DHANHQ INITIALIZATION
Find the correct way to initialize DhanHQ
File: test_dhanhq_init.py
"""

import os
from dotenv import load_dotenv

load_dotenv(dotenv_path="./.env", override=True)

API_KEY = os.getenv("API_KEY")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")

print(f"""
Testing DhanHQ Initialization Methods
API_KEY: {API_KEY}
ACCESS_TOKEN: {ACCESS_TOKEN[:30]}...
""")

# Method 1: Try with both API_KEY and ACCESS_TOKEN
print("\n[METHOD 1] dhanhq(API_KEY, ACCESS_TOKEN)")
try:
    from dhanhq import dhanhq
    dhan = dhanhq(API_KEY, ACCESS_TOKEN)
    print("[SUCCESS] ✓ Initialized with both API_KEY and ACCESS_TOKEN")
except Exception as e:
    print(f"[FAILED] {e}")

# Method 2: Try with only ACCESS_TOKEN
print("\n[METHOD 2] dhanhq(ACCESS_TOKEN)")
try:
    from dhanhq import dhanhq
    dhan = dhanhq(ACCESS_TOKEN)
    print("[SUCCESS] ✓ Initialized with only ACCESS_TOKEN")
except Exception as e:
    print(f"[FAILED] {e}")

# Method 3: Try instantiation
print("\n[METHOD 3] dhanhq() class instantiation")
try:
    from dhanhq import dhanhq
    dhan = dhanhq()
    dhan.set_token(ACCESS_TOKEN)
    print("[SUCCESS] ✓ Instantiated and set token")
except Exception as e:
    print(f"[FAILED] {e}")

# Method 4: Check dhanhq module structure
print("\n[METHOD 4] Checking dhanhq module structure")
try:
    import dhanhq
    print(f"[DEBUG] dhanhq module contents: {dir(dhanhq)}")
    
    # Try to get the correct class
    if hasattr(dhanhq, 'dhanhq'):
        print("[INFO] Found dhanhq.dhanhq class")
    if hasattr(dhanhq, 'DhanHQ'):
        print("[INFO] Found dhanhq.DhanHQ class")
except Exception as e:
    print(f"[FAILED] {e}")

print("\n" + "="*80)
