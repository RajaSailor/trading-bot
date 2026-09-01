import pandas as pd

# Load your security_list.csv
df = pd.read_csv("security_list.csv", low_memory=False)

# Function to get all F&O IDs for a given stock name
def get_fno_ids(stock_name):
    # Filter rows where SM_SYMBOL_NAME contains the stock name
    rows = df[df['SM_SYMBOL_NAME'].astype(str).str.contains(stock_name, case=False, na=False)]
    # Keep only F&O segment rows (SEM_SEGMENT == 'D')
    fno_rows = rows[rows['SEM_SEGMENT'] == 'D']
    # Extract IDs
    ids = fno_rows['SEM_SMST_SECURITY_ID'].tolist()
    return ids, fno_rows[['SM_SYMBOL_NAME','SEM_SMST_SECURITY_ID']]

# Example usage
stocks = ["RELIANCE", "INFY", "TCS", "HDFCBANK"]

fno_dict = {}
for stock in stocks:
    ids, table = get_fno_ids(stock)
    fno_dict[stock] = ids
    print(f"{stock} F&O IDs:", ids)
    print(table)

# Save all F&O IDs into a JSON file
import json
with open("fno_ids.json", "w") as f:
    json.dump(fno_dict, f, indent=4)

print("✅ All F&O IDs saved to fno_ids.json")
