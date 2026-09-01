import os
from dotenv import load_dotenv
from dhanhq import dhanhq, DhanContext

load_dotenv()

API_KEY = os.getenv("API_KEY")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")

# Build context and client correctly
context = DhanContext(API_KEY, ACCESS_TOKEN)
dhan = dhanhq(context)

# Diagnostic: show available attributes and where the package lives
print("dhan object type:", type(dhan))
print("Top-level dhanhq attributes:", sorted([n for n in dir(dhan) if not n.startswith('_')]))

# Try to find profile-related helpers
print("Package exposes these top-level names:", sorted([n for n in dir(__import__('dhanhq')) if not n.startswith('_')]))

# If a profile method exists, call it safely
if hasattr(dhan, "get_profile"):
    print("Calling get_profile() ...")
    print(dhan.get_profile())
else:
    print("No get_profile on dhan. Look for DhanLogin or auth helpers.")
