from dotenv import load_dotenv
import os
from dhanhq import DhanContext, dhanhq

load_dotenv()
API_KEY = os.getenv("API_KEY")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")

context = DhanContext(API_KEY, ACCESS_TOKEN)
client = dhanhq(context)

resp = client.intraday_minute_data(
    security_id=543388,        # NIFTY 50
    exchange_segment="NSE_EQ",
    instrument_type="INDEX",
    interval="5",
    from_date="2021-01-01",
    to_date="2021-01-10"
)

print(resp)
