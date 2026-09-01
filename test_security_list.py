from dotenv import load_dotenv
import os
from dhanhq import DhanContext, dhanhq

load_dotenv()
API_KEY = os.getenv("API_KEY")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")

context = DhanContext(API_KEY, ACCESS_TOKEN)
client = dhanhq(context)

# Fetch compact list (returns DataFrame)
df = client.fetch_security_list("compact")
print(df.head())   # show first few rows

# Filter for NIFTY
nifty = df[df["SM_SYMBOL_NAME"].str.contains("NIFTY", case=False)]
print(nifty[["SEM_SMST_SECURITY_ID", "SM_SYMBOL_NAME"]])
