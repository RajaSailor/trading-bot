import os
from dotenv import load_dotenv

# Load .env file
load_dotenv()

# Fetch values
API_KEY = os.getenv("API_KEY")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

print("API_KEY:", API_KEY)
print("ACCESS_TOKEN:", ACCESS_TOKEN[:20], "...")  # just preview
print("TELEGRAM_TOKEN:", TELEGRAM_TOKEN[:15], "...")
print("CHAT_ID:", CHAT_ID)
