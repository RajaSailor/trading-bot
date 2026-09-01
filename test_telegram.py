"""
TELEGRAM BOT TEST SCRIPT - FINAL FIX
Forces .env to override system environment variables
"""

import os
from dotenv import load_dotenv
from telegram import Bot
import asyncio
import sys

# Force load from exact .env path and override system variables
load_dotenv(dotenv_path="C:/Users/A Bharathiraja/Trading Bot/.env", override=True)

# Load credentials
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

print("[INFO] Telegram Bot Configuration Test")
print("="*60)

# Step 1: Check if credentials are loaded
print("\n[STEP 1] Checking environment variables...")
print(f"  TELEGRAM_TOKEN: {TELEGRAM_TOKEN[:15]}..." if TELEGRAM_TOKEN else "  TELEGRAM_TOKEN: NOT FOUND ❌")
print(f"  CHAT_ID: {CHAT_ID if CHAT_ID else 'NOT FOUND ❌'}")

if not TELEGRAM_TOKEN or not CHAT_ID:
    print("\n[ERROR] Missing credentials!")
    print("Make sure your .env file has:")
    print("  TELEGRAM_TOKEN=your_token_here")
    print("  CHAT_ID=your_chat_id_here")
    exit(1)

print("\n✓ Credentials loaded successfully")

# Step 2: Initialize bot
print("\n[STEP 2] Initializing Telegram bot...")
try:
    bot = Bot(token=TELEGRAM_TOKEN)
    print("✓ Bot initialized successfully")
except Exception as e:
    print(f"✗ Failed to initialize bot: {e}")
    exit(1)

# Step 3: Test bot connection and send message
print("\n[STEP 3] Testing bot connection and sending message...")
print(f"  Bot: @wintrade_signals_bot")
print(f"  Target Chat ID: {CHAT_ID}")

async def test_bot():
    try:
        # Get bot info
        me = await bot.get_me()
        print(f"\n✓ Bot connected successfully!")
        print(f"  Bot Name: {me.first_name}")
        print(f"  Bot Username: @{me.username if me.username else 'N/A'}")
        
        # Send test message
        print(f"\n  Sending test message...")
        
        try:
            chat_id_int = int(CHAT_ID)
        except ValueError:
            print(f"✗ Invalid CHAT_ID format: {CHAT_ID}")
            print("  CHAT_ID must be a number (e.g., -123456789 or 123456789)")
            return False
        
        message = """🤖 Trading Scanner - Telegram Bot Test ✅

This is a test message from your Trading Scanner.

✓ Bot is properly configured!
✓ Messages will be sent to this chat!

If you received this message, your Telegram integration is working correctly. 

Your scanner is ready to send live data! 📊"""
        
        await bot.send_message(chat_id=chat_id_int, text=message)
        print("✓ Test message sent successfully!")
        print("  Check your Telegram for the message!")
        return True
        
    except Exception as e:
        print(f"✗ Error: {e}")
        return False

# Step 4: Run async test
print("\n[STEP 4] Running async test...\n")
try:
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result = loop.run_until_complete(test_bot())
    loop.close()
    
except Exception as e:
    print(f"✗ Async error: {e}")
    result = False

# Step 5: Summary
print("\n" + "="*60)
if result:
    print("[SUCCESS] ✅ Telegram bot is properly configured!")
    print("\n🎉 Your scanner WILL send messages to your chat!")
    print(f"\nChat ID: {CHAT_ID}")
    print("Bot: @wintrade_signals_bot")
else:
    print("[FAILURE] ❌ Telegram bot configuration has issues!")
    print("\nTo fix:")
    print("1. Make sure bot is added to your group/chat")
    print("2. Verify CHAT_ID is correct")
    print("3. Check bot permissions in the group")
    print("\nTo get correct CHAT_ID:")
    print("  - Go to: https://api.telegram.org/bot{TOKEN}/getUpdates")
    print("  - Look for 'chat': {'id': YOUR_CHAT_ID}")
print("="*60 + "\n")
