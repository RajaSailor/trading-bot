import os
from dotenv import load_dotenv
from dhanhq import DhanContext, dhanhq

load_dotenv()

API_KEY = os.getenv("API_KEY")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")

# Build context and client
context = DhanContext(API_KEY, ACCESS_TOKEN)
dhan = dhanhq(context)

# Step 1: Fetch scrip master list
df = dhan.fetch_security_list(mode="compact", filename="security_list.csv")

print("Columns in security_list.csv:", df.columns.tolist())

# Step 2: Search for NIFTY 50 in SM_SYMBOL_NAME
nifty_row = df[df['SM_SYMBOL_NAME'].astype(str).str.contains("NIFTY 50", case=False, na=False)]

if not nifty_row.empty:
    # Extract ID and segment directly from the row
    nifty_id = int(nifty_row.iloc[0]['SEM_SMST_SECURITY_ID'])
    segment = str(nifty_row.iloc[0]['SEM_SEGMENT']).strip()

    print(f"Found NIFTY 50 security_id: {nifty_id}, segment: {segment}")

    # Step 3: Use the correct segment dynamically
    securities = {segment: [nifty_id]}
    quote = dhan.quote_data(securities)
    print("Live NIFTY 50 Quote:", quote)
else:
    print("NIFTY 50 not found in security list.")
