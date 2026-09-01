"""
TEST SCRIPT - DHAN MARKETFEED
Only tests live marketfeed API response, no strategy logic
"""

import os
import requests
import pandas as pd
from dotenv import load_dotenv

# Load .env
load_dotenv(dotenv_path="./.env", override=True)

API_KEY = os.getenv("API_KEY")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")

BASE_URL = "https://api.dhan.co/v2"

# Load security list
SECURITY_FILE = "./security_list.csv"
df = pd.read_csv(SECURITY_FILE, low_memory=False)

def get_security_id(symbol_name):
    if "tradingSymbol" in df.columns:
        row = df[df['tradingSymbol'] == symbol_name]
    elif "SM_SYMBOL_NAME" in df.columns:
        row = df[df['SM_SYMBOL_NAME'] == symbol_name]
    else:
        return None
    if not row.empty:
        if "securityId" in row.columns:
            return str(row.iloc[0]['securityId'])
        elif "SEM_SMST_SECURITY_ID" in row.columns:
            return str(row.iloc[0]['SEM_SMST_SECURITY_ID'])
    return None

def test_marketfeed():
    instruments = [
        {"securityId": get_security_id("NIFTY"), "exchangeSegment": "NSE_FNO"},
        {"securityId": get_security_id("BANKNIFTY"), "exchangeSegment": "NSE_FNO"},
        {"securityId": get_security_id("SENSEX"), "exchangeSegment": "BSE_FNO"}
    ]
    url = f"{BASE_URL}/marketfeed"
    headers = {
        "access-token": ACCESS_TOKEN,
        "client-id": API_KEY,
        "Content-Type": "application/json"
    }
    payload = {"instruments": instruments}
    print(f"[INFO] Posting to {url} with {payload}")
    resp = requests.post(url, headers=headers, json=payload)
    print(f"[DEBUG] Raw Response: {resp.text[:500]}")  # show first 500 chars
    try:
        data = resp.json()
        print("[INFO] Parsed JSON:", data)
    except Exception as e:
        print(f"[ERROR] JSON decode failed: {e}")

if __name__ == "__main__":
    test_marketfeed()
