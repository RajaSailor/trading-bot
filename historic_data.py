from dhanhq import DhanContext, dhanhq
import os
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("API_KEY")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")

context = DhanContext(API_KEY, ACCESS_TOKEN)
dhan = dhanhq(context)

print(dir(dhan))
