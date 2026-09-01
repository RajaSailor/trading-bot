import os
from dotenv import load_dotenv
from dhanhq import DhanContext, dhanhq

load_dotenv()
API_KEY = os.getenv("API_KEY")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")

context = DhanContext(API_KEY, ACCESS_TOKEN)
dhan = dhanhq(context)

resp = dhan.intraday_minute_data(
    security_id="NSE_INDEX|NIFTY 50",   # try symbol string instead of numeric ID
    exchange_segment="NSE",
    instrument_type="INTRA",
    interval="5",
    from_date="2026-01-01",
    to_date="2026-01-10"
)

print(resp)

print("Type of response:", type(resp))
print("Sample response:", str(resp)[:500])
