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
if not API_KEY or not ACCESS_TOKEN:
    print("[ERROR] API_KEY and ACCESS_TOKEN not found in .env file")
    exit(1)

# Initialize Telegram bot
bot = Bot(token=TELEGRAM_TOKEN) if TELEGRAM_TOKEN else None

# Initialize Dhan context and client
dhan_context = DhanContext(API_KEY, ACCESS_TOKEN)
dhan = dhanhq(dhan_context)


def get_underlying_ids():
    """
    Fetch security IDs for NIFTY 50, BANKNIFTY, and SENSEX
    
    Returns:
        tuple: (nifty_id, banknifty_id, sensex_id)
    """
    try:
        # Fetch security list (fixes dtype warning with updated library)
        df = dhan.security.fetch_security_list("compact")
        print("[DEBUG] DataFrame columns:", df.columns.tolist())
        print(f"[DEBUG] Total records in security list: {len(df)}")

        # Filter only NSE Index derivatives
        # Handle potential None values and ensure proper data types
        df_idx = df[
            (df["SEM_EXM_EXCH_ID"].astype(str) == "NSE") &
            (df["SEM_SEGMENT"].astype(str) == "I") &
            (df["SEM_EXCH_INSTRUMENT_TYPE"].astype(str).str.upper() == "INDEX")
        ]

        print(f"[DEBUG] Filtered to {len(df_idx)} indices")
        print("[DEBUG] Index subset head:\n", df_idx.head(100))

        # Initialize return values
        nifty_id = None
        banknifty_id = None
        sensex_id = None

        # ✅ Match NIFTY 50 (case-insensitive)
        try:
            nifty_matches = df_idx[df_idx["SM_SYMBOL_NAME"].astype(str).str.strip().str.upper() == "NIFTY 50"]
            if len(nifty_matches) > 0:
                nifty_id = int(nifty_matches.iloc[0]["SEM_SMST_SECURITY_ID"])
                print(f"[DEBUG] Found NIFTY 50 ID: {nifty_id}")
            else:
                print("[WARNING] NIFTY 50 not found in indices")
        except (IndexError, ValueError, KeyError) as e:
            print(f"[WARNING] Error fetching NIFTY 50: {e}")

        # ✅ Match BANKNIFTY (case-insensitive)
        try:
            banknifty_matches = df_idx[df_idx["SM_SYMBOL_NAME"].astype(str).str.strip().str.upper() == "BANKNIFTY"]
            if len(banknifty_matches) > 0:
                banknifty_id = int(banknifty_matches.iloc[0]["SEM_SMST_SECURITY_ID"])
                print(f"[DEBUG] Found BANKNIFTY ID: {banknifty_id}")
            else:
                print("[WARNING] BANKNIFTY not found in indices")
        except (IndexError, ValueError, KeyError) as e:
            print(f"[WARNING] Error fetching BANKNIFTY: {e}")

        # ✅ Match SENSEX (case-insensitive)
        try:
            sensex_matches = df_idx[df_idx["SM_SYMBOL_NAME"].astype(str).str.strip().str.upper() == "SENSEX"]
            if len(sensex_matches) > 0:
                sensex_id = int(sensex_matches.iloc[0]["SEM_SMST_SECURITY_ID"])
                print(f"[DEBUG] Found SENSEX ID: {sensex_id}")
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
        print(f"[DEBUG] expiry_list for {underlying_name} ({underlying_id}): {expiry_list}")
        
        if expiry_list and expiry_list.get("status") == "success":
            if expiry_list.get("data") and len(expiry_list["data"]) > 0:
                expiry_date = expiry_list["data"][0].get("expiryDate")
                print(f"[INFO] Next expiry for {underlying_name}: {expiry_date}")
                return expiry_date
            else:
                print(f"[WARNING] No expiry data found for {underlying_name}")
        else:
            print(f"[ERROR] Failed to fetch expiry list for {underlying_name}")
            print(f"[DEBUG] Response: {expiry_list}")
            
    except Exception as e:
        print(f"[ERROR] Expiry fetch error for {underlying_name}: {e}")
        import traceback
        traceback.print_exc()
        
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
        option_chain = dhan.option_chain(
            underlying_security_id=underlying_id,
            expiry_date=expiry_date,
            exchange_segment=dhan.NSE_FNO,
            option_type="ALL"
        )
        print(f"[DEBUG] Option chain for {underlying_name}: {option_chain}")
        return option_chain
        
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
            print(f"[INFO] Telegram alert sent: {message}")
        except Exception as e:
            print(f"[ERROR] Failed to send Telegram alert: {e}")
    else:
        print(f"[WARNING] Telegram not configured. Message: {message}")


def scanner_loop():
    """
    Main scanner loop - fetch indices and their options data
    """
    print("\n" + "="*60)
    print("TRADING SCANNER - STARTED")
    print("="*60 + "\n")

    # Step 1: Get underlying IDs
    print("[STEP 1] Fetching underlying security IDs...")
    nifty_id, banknifty_id, sensex_id = get_underlying_ids()
    print(f"\n[DEBUG] IDs - NIFTY: {nifty_id}, BANKNIFTY: {banknifty_id}, SENSEX: {sensex_id}\n")

    if not any([nifty_id, banknifty_id, sensex_id]):
        print("[ERROR] Failed to fetch any underlying IDs. Exiting.")
        send_telegram_alert("❌ Trading Scanner: Failed to fetch underlying IDs")
        return

    # Step 2: Get next expiry dates
    print("[STEP 2] Fetching next expiry dates...")
    
    nifty_expiry = None
    banknifty_expiry = None
    sensex_expiry = None

    if nifty_id:
        nifty_expiry = get_next_expiry(nifty_id, "NIFTY 50")
        
    if banknifty_id:
        banknifty_expiry = get_next_expiry(banknifty_id, "BANKNIFTY")
        
    if sensex_id:
        sensex_expiry = get_next_expiry(sensex_id, "SENSEX")

    print("\n[STEP 3] Fetching option chains...\n")

    # Step 3: Fetch option chains
    if nifty_id and nifty_expiry:
        print(f"Fetching NIFTY 50 option chain for expiry: {nifty_expiry}")
        nifty_chain = get_option_chain(nifty_id, nifty_expiry, "NIFTY 50")
        if nifty_chain:
            print(f"[INFO] NIFTY 50 option chain retrieved: {len(nifty_chain.get('data', []))} records")

    if banknifty_id and banknifty_expiry:
        print(f"Fetching BANKNIFTY option chain for expiry: {banknifty_expiry}")
        banknifty_chain = get_option_chain(banknifty_id, banknifty_expiry, "BANKNIFTY")
        if banknifty_chain:
            print(f"[INFO] BANKNIFTY option chain retrieved: {len(banknifty_chain.get('data', []))} records")

    if sensex_id and sensex_expiry:
        print(f"Fetching SENSEX option chain for expiry: {sensex_expiry}")
        sensex_chain = get_option_chain(sensex_id, sensex_expiry, "SENSEX")
        if sensex_chain:
            print(f"[INFO] SENSEX option chain retrieved: {len(sensex_chain.get('data', []))} records")

    print("\n" + "="*60)
    print("TRADING SCANNER - COMPLETED SUCCESSFULLY")
    print("="*60 + "\n")

    send_telegram_alert("✅ Trading Scanner: Completed successfully")


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
