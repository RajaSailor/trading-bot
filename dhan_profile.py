import os
from dotenv import load_dotenv
from dhanhq import DhanLogin

load_dotenv()

API_KEY = os.getenv("API_KEY")        # client_id
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")

# Initialize DhanLogin with client_id
login = DhanLogin(API_KEY)

# Fetch profile with access_token
profile = login.user_profile(ACCESS_TOKEN)
print(profile)
