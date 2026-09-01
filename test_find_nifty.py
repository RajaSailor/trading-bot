# test_find_nifty.py
from dotenv import load_dotenv
import os
from dhanhq import DhanContext, dhanhq

load_dotenv()
API_KEY = os.getenv("API_KEY")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")

context = DhanContext(API_KEY, ACCESS_TOKEN)
client = dhanhq(context)

df = client.fetch_security_list("compact")

# Filter specifically for NIFTY 50 index
nifty50 = df[
    (df["SM_SYMBOL_NAME"].str.upper() == "NIFTY 50") &
    (df["SEM_SEGMENT"] == "INDEX") &
    (df["SEM_EXM_EXCH_ID"] == "NSE")
]

print(nifty50[["SEM_SMST_SECURITY_ID", "SM_SYMBOL_NAME", "SEM_SEGMENT", "SEM_EXM_EXCH_ID"]])
