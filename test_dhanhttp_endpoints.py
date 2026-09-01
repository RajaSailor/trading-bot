"""
TEST DhanHTTP API Endpoints
Try different endpoints to find the correct one for getting quotes
File: test_dhanhttp_endpoints.py
"""

import os
from dotenv import load_dotenv
import json

load_dotenv(dotenv_path="./.env", override=True)

CLIENT_ID = os.getenv("API_KEY")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")

print(f"""
Testing DhanHTTP API Endpoints
CLIENT_ID: {CLIENT_ID}
ACCESS_TOKEN: {ACCESS_TOKEN[:30]}...
""")

try:
    from dhanhq import DhanContext
    print("[SUCCESS] ✓ DhanHQ imported\n")
except Exception as e:
    print(f"[ERROR] Failed to import: {e}")
    exit(1)

# Initialize
try:
    dhan_context = DhanContext(client_id=CLIENT_ID, access_token=ACCESS_TOKEN)
    dhan_http = dhan_context.get_dhan_http()
    print("[SUCCESS] ✓ DhanContext initialized\n")
except Exception as e:
    print(f"[ERROR] Init failed: {e}")
    exit(1)

# Test different endpoints
endpoints_to_try = [
    # Endpoint, Description
    ("/securitymaster", "Security Master"),
    ("/securitymaster/", "Security Master with slash"),
    ("/user/profile", "User Profile"),
    ("/marketfeed/ltp/565899/MCX_FUT", "LTP endpoint 1"),
    ("/marketfeed/ltp", "LTP endpoint 2"),
    ("/marketfeed", "Market Feed"),
    ("/quotes", "Quotes"),
    ("/quotes/565899/MCX_FUT", "Quotes with params"),
    ("/instrumentmetadata", "Instrument Metadata"),
]

print(f"{'='*80}")
print("Testing API Endpoints")
print(f"{'='*80}\n")

for endpoint, description in endpoints_to_try:
    print(f"\n[TEST] {description}")
    print(f"  Endpoint: {endpoint}")
    print(f"  {'─'*76}")
    
    try:
        response = dhan_http.get(endpoint)
        
        print(f"  [SUCCESS] ✓ Got response!")
        print(f"  Status Code: {response.status_code if hasattr(response, 'status_code') else 'N/A'}")
        print(f"  Response Type: {type(response)}")
        
        # Try to parse as JSON
        try:
            data = response.json()
            print(f"  [DATA] JSON Response:")
            
            # Print first 500 chars
            data_str = json.dumps(data, indent=2)[:500]
            for line in data_str.split('\n'):
                print(f"    {line}")
            
            if len(json.dumps(data, indent=2)) > 500:
                print(f"    ... (truncated)")
        except:
            print(f"  Response Text: {str(response)[:200]}")
        
    except Exception as e:
        print(f"  [FAILED] {type(e).__name__}: {str(e)[:100]}")

print(f"\n{'='*80}")
print("ENDPOINT TEST COMPLETE")
print(f"{'='*80}\n")
