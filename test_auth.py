import os, requests
from dotenv import load_dotenv

load_dotenv(dotenv_path="./.env", override=True)

API_KEY = os.getenv("API_KEY")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")

url = "https://api.dhan.co/v2/marketfeed/ltp"
headers = {
    "client-id": API_KEY,
    "access-token": ACCESS_TOKEN,
    "Content-Type": "application/json"
}
payload = {"NSE_FNO": ["13"]}

resp = requests.post(url, headers=headers, json=payload)
print("[DEBUG] Status:", resp.status_code)
print("[DEBUG] Response:", resp.text)
