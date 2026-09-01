import os
import json
from dotenv import load_dotenv
from dhanhq import DhanContext, dhanhq
from datetime import datetime
import requests

load_dotenv()

API_KEY = os.getenv("API_KEY")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")

context = DhanContext(API_KEY, ACCESS_TOKEN)
dhan = dhanhq(context)

quotes_log = {}

# --- NSE Indices & MCX ---
instruments = {
    "NIFTY 50": {"INDEX": [543388]},
    "BANKNIFTY": {"INDEX": [25258]},
    "SENSEX": {"INDEX": [538683]},
    "CRUDEOIL": {"MCX": [565899]}
}

for name, sec in instruments.items():
    print(f"Fetching {name}...")
    quote = dhan.quote_data(sec)
    quotes_log[name] = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "data": quote
    }

# --- NSE Futures & Options (auto-loaded from fno_ids.json) ---
try:
    with open("fno_ids.json") as f:
        fno_instruments = json.load(f)

    for stock, ids in fno_instruments.items():
        for sec_id in ids:
            print(f"Fetching {stock} F&O ID {sec_id}...")
            quote = dhan.quote_data({"NSE_FNO": [sec_id]})
            quotes_log[f"{stock}_{sec_id}"] = {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "data": quote
            }
except FileNotFoundError:
    print("⚠️ fno_ids.json not found. Run fetch_fno_ids.py first to generate it.")

# --- BTC (via CoinGecko API) ---
try:
    btc_data = requests.get(
        "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=inr,usd"
    ).json()
    quotes_log["BTC"] = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "data": btc_data
    }
    print("BTC Quote:", btc_data)
except Exception as e:
    print("BTC fetch failed:", e)

# --- Save all quotes into quotes.json ---
with open("quotes.json", "a") as f:   # append mode for continuous logging
    f.write(json.dumps(quotes_log, indent=4))
    f.write("\n")

print("✅ Quotes saved to quotes.json")
