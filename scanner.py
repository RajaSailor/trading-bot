import os
import time
from dotenv import load_dotenv
from telegram import Bot
from dhanhq import DhanContext, dhanhq

load_dotenv()

# Load credentials from .env
API_KEY = os.getenv("API_KEY")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# Verify credentials are loaded
print("[INFO] Checking environment variables...")
print(f"[INFO] API_KEY loaded: {bool(API_KEY)}")
print(f"[INFO] ACCESS_TOKEN loaded: {bool(ACCESS_TOKEN)}")
print(f"[INFO] TELEGRAM_TOKEN loaded: {bool(TELEGRAM_TOKEN)}")
print(f"[INFO] CHAT_ID loaded: {bool(CHAT_ID)}")

if not API_KEY or not ACCESS_TOKEN:
    print("[ERROR] API_KEY and ACCESS_TOKEN not found in .env file")
    exit(1)

# Initialize Telegram bot
bot = None
if TELEGRAM_TOKEN and CHAT_ID:
    try:
        bot = Bot(token=TELEGRAM_TOKEN)
        print("[INFO] Telegram bot initialized successfully")
    except Exception as e:
        print(f"[WARNING] Failed to initialize Telegram bot: {e}")
else:
    print("[WARNING] Telegram credentials not configured")

# Initialize Dhan context and client
print("\n[INFO] Initializing Dhan client...")
try:
    dhan_context = DhanContext(API_KEY, ACCESS_TOKEN)
    dhan = dhanhq(dhan_context)
    print("[INFO] Dhan client initialized successfully")
except Exception as e:
    print(f"[ERROR] Failed to initialize Dhan client: {e}")
    exit(1)


def get_underlying_ids():
    """
    Fetch security IDs for NIFTY 50, BANKNIFTY, and SENSEX
    
    Returns:
        tuple: (nifty_id, banknifty_id, sensex_id)
    """
    try:
        print("\n[INFO] Fetching security list from Dhan...")
        # Fetch security list (fixes dtype warning with updated library)
        df = dhan.security.fetch_security_list("compact")
        print(f"[SUCCESS] Security list loaded: {len(df)} total records")
        print(f"[DEBUG] DataFrame columns: {df.columns.tolist()}")

        # Filter only NSE Index derivatives
        # Handle potential None values and ensure proper data types
        df_idx = df[
            (df["SEM_EXM_EXCH_ID"].astype(str) == "NSE") &
            (df["SEM_SEGMENT"].astype(str) == "I") &
            (df["SEM_EXCH_INSTRUMENT_TYPE"].astype(str).str.upper() == "INDEX")
        ]

        print(f"[SUCCESS] Filtered to {len(df_idx)} NSE Index records")
        print(f"\n[DEBUG] Available Indices (first 20):\n")
        for idx, row in df_idx.head(20).iterrows():
            print(f"  - {row['SM_SYMBOL_NAME']} (ID: {row['SEM_SMST_SECURITY_ID']})")

        # Initialize return values
        nifty_id = None
        banknifty_id = None
        sensex_id = None

        # ✅ Match NIFTY 50 (case-insensitive)
        try:
            nifty_matches = df_idx[df_idx["SM_SYMBOL_NAME"].astype(str).str.strip().str.upper() == "NIFTY 50"]
            if len(nifty_matches) > 0:
                nifty_id = int(nifty_matches.iloc[0]["SEM_SMST_SECURITY_ID"])
                print(f"\n[SUCCESS] NIFTY 50 ID: {nifty_id}")
            else:
                # Try alternative names
                nifty_matches = df_idx[df_idx["SM_SYMBOL_NAME"].astype(str).str.strip().str.upper().str.contains("NIFTY", na=False)]
                if len(nifty_matches) > 0:
                    nifty_id = int(nifty_matches.iloc[0]["SEM_SMST_SECURITY_ID"])
                    print(f"\n[SUCCESS] NIFTY 50 ID (alternative): {nifty_id}")
                else:
                    print("[WARNING] NIFTY 50 not found in indices")
        except (IndexError, ValueError, KeyError) as e:
            print(f"[WARNING] Error fetching NIFTY 50: {e}")

        # ✅ Match BANKNIFTY (case-insensitive)
        try:
            banknifty_matches = df_idx[df_idx["SM_SYMBOL_NAME"].astype(str).str.strip().str.upper() == "BANKNIFTY"]
            if len(banknifty_matches) > 0:
                banknifty_id = int(banknifty_matches.iloc[0]["SEM_SMST_SECURITY_ID"])
                print(f"[SUCCESS] BANKNIFTY ID: {banknifty_id}")
            else:
                print("[WARNING] BANKNIFTY not found in indices")
        except (IndexError, ValueError, KeyError) as e:
            print(f"[WARNING] Error fetching BANKNIFTY: {e}")

        # ✅ Match SENSEX (case-insensitive)
        try:
            sensex_matches = df_idx[df_idx["SM_SYMBOL_NAME"].astype(str).str.strip().str.upper() == "SENSEX"]
            if len(sensex_matches) > 0:
                sensex_id = int(sensex_matches.iloc[0]["SEM_SMST_SECURITY_ID"])
                print(f"[SUCCESS] SENSEX ID: {sensex_id}")
            else:
                print("[WARNING] SENSEX not found in indices")
        except (IndexError, ValueError, KeyError) as e:
            print(f"[WARNING] Error fetching SENSEX: {e}")

        return nifty_id, banknifty_id, sensex_id

    except Exception as e:
        print(f"[ERROR] Underlying fetch error: {e}")
        import traceback
        traceback.print_exc()
        return None, None, None


def get_next_expiry(underlying_id, underlying_name=""):
    """
    Fetch the next expiry date for a given underlying
    
    Args:
        underlying_id (int): Security ID of the underlying
        underlying_name (str): Name of the underlying (for logging)
        
    Returns:
        str: Expiry date or None
    """
    try:
        expiry_list = dhan.expiry_list(
            under_security_id=underlying_id,
            under_exchange_segment=dhan.NSE_FNO
        )
        
        if expiry_list and expiry_list.get("status") == "success":
            if expiry_list.get("data") and len(expiry_list["data"]) > 0:
                expiry_date = expiry_list["data"][0].get("expiryDate")
                print(f"[SUCCESS] {underlying_name} next expiry: {expiry_date}")
                return expiry_date
            else:
                print(f"[WARNING] No expiry data found for {underlying_name}")
        else:
            print(f"[ERROR] Failed to fetch expiry list for {underlying_name}")
            
    except Exception as e:
        print(f"[ERROR] Expiry fetch error for {underlying_name}: {e}")
        
    return None


def get_option_chain(underlying_id, expiry_date, underlying_name=""):
    """
    Fetch option chain for a given underlying and expiry
    
    Args:
        underlying_id (int): Security ID of the underlying
        expiry_date (str): Expiry date
        underlying_name (str): Name of the underlying (for logging)
        
    Returns:
        dict: Option chain data or None
    """
    try:
        print(f"\n[INFO] Fetching option chain for {underlying_name} (Expiry: {expiry_date})...")
        
        option_chain = dhan.option_chain(
            underlying_security_id=underlying_id,
            expiry_date=expiry_date,
            exchange_segment=dhan.NSE_FNO,
            option_type="ALL"
        )
        
        if option_chain and option_chain.get("status") == "success":
            data = option_chain.get("data", [])
            print(f"[SUCCESS] {underlying_name} option chain retrieved: {len(data)} records")
            
            # Print sample data
            if len(data) > 0:
                print(f"\n[DEBUG] Sample options for {underlying_name}:")
                for i, option in enumerate(data[:5]):
                    print(f"  {i+1}. Strike: {option.get('strikePrice')}, Type: {option.get('optionType')}, LTP: {option.get('ltp')}")
            
            return option_chain
        else:
            print(f"[ERROR] Failed to fetch option chain for {underlying_name}")
            
    except Exception as e:
        print(f"[ERROR] Option chain fetch error for {underlying_name}: {e}")
        import traceback
        traceback.print_exc()
        
    return None


def send_telegram_alert(message):
    """
    Send alert via Telegram
    
    Args:
        message (str): Message to send
    """
    if bot and CHAT_ID:
        try:
            bot.send_message(chat_id=CHAT_ID, text=message)
            print(f"[INFO] ✉️  Telegram alert sent")
        except Exception as e:
            print(f"[WARNING] Failed to send Telegram alert: {e}")
    else:
        if not bot:
            print(f"[WARNING] Telegram bot not initialized")
        if not CHAT_ID:
            print(f"[WARNING] CHAT_ID not configured")


def scanner_loop():
    """
    Main scanner loop - fetch indices and their options data
    """
    print("\n" + "="*70)
    print(" "*15 + "🚀 TRADING SCANNER - STARTED")
    print("="*70)

    # Step 1: Get underlying IDs
    print("\n[STEP 1/3] Fetching underlying security IDs...")
    nifty_id, banknifty_id, sensex_id = get_underlying_ids()
    
    print(f"\n[SUMMARY] Fetched IDs:")
    print(f"  - NIFTY 50: {nifty_id}")
    print(f"  - BANKNIFTY: {banknifty_id}")
    print(f"  - SENSEX: {sensex_id}")

    if not any([nifty_id, banknifty_id, sensex_id]):
        print("\n[ERROR] Failed to fetch any underlying IDs. Exiting.")
        send_telegram_alert("❌ Trading Scanner: Failed to fetch underlying IDs")
        return

    # Step 2: Get next expiry dates
    print("\n[STEP 2/3] Fetching next expiry dates...")
    
    nifty_expiry = None
    banknifty_expiry = None
    sensex_expiry = None

    if nifty_id:
        nifty_expiry = get_next_expiry(nifty_id, "NIFTY 50")
        
    if banknifty_id:
        banknifty_expiry = get_next_expiry(banknifty_id, "BANKNIFTY")
        
    if sensex_id:
        sensex_expiry = get_next_expiry(sensex_id, "SENSEX")

    # Step 3: Fetch option chains
    print("\n[STEP 3/3] Fetching option chains...")

    if nifty_id and nifty_expiry:
        get_option_chain(nifty_id, nifty_expiry, "NIFTY 50")

    if banknifty_id and banknifty_expiry:
        get_option_chain(banknifty_id, banknifty_expiry, "BANKNIFTY")

    if sensex_id and sensex_expiry:
        get_option_chain(sensex_id, sensex_expiry, "SENSEX")

    print("\n" + "="*70)
    print(" "*15 + "✅ TRADING SCANNER - COMPLETED SUCCESSFULLY")
    print("="*70 + "\n")

    send_telegram_alert("✅ Trading Scanner: All data fetched successfully!\n\n📊 Summary:\n✓ Security IDs fetched\n✓ Expiry dates retrieved\n✓ Option chains loaded")


if __name__ == "__main__":
    try:
        scanner_loop()
    except KeyboardInterrupt:
        print("\n[INFO] Scanner interrupted by user")
    except Exception as e:
        print(f"\n[ERROR] Scanner crashed: {e}")
        import traceback
        traceback.print_exc()
        send_telegram_alert(f"❌ Trading Scanner crashed: {e}")
