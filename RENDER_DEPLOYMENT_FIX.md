# 🔧 RENDER DEPLOYMENT FAILED - ROOT CAUSE & FIX

## ⚠️ LIKELY CAUSES (Based on Your Setup)

---

## **ISSUE #1: Missing `screener_app.py` Update** 🔴

Your latest commit updated `screener_background.py` but Render is still running OLD version of `screener_app.py`

### Problem:
```python
# OLD screener_app.py tries to import:
from screener_background import (
    screener_state,
    PUBLIC_IP  # ❌ This variable doesn't exist in NEW version!
)

# NEW screener_background.py has:
screener_state["public_ip"] = detect_public_ip()  # Different location!
```

### Fix:
✅ Already updated `screener_app.py` with correct imports

---

## **ISSUE #2: Event Loop Async Issues** 🔴

### Problem:
```python
# screener_background.py creates event loop in main thread
telegram_event_loop = asyncio.new_event_loop()

# Flask tries to use same loop in different thread
# ❌ NOT THREAD-SAFE!
```

### Symptoms in Render Logs:
```
RuntimeError: asyncio.run() cannot be called from a running event loop
RuntimeError: There is no current event loop in thread 'FlaskThread'
```

### Fix:
Need to create event loop in **separate daemon thread**

---

## **ISSUE #3: Missing Environment Variables** 🔴

Render's Render settings might not have all 6 bot tokens and channels

### Check in Render Dashboard:
1. Go to your web service
2. Click **Settings** → **Environment**
3. Verify ALL these variables exist:
   - ✅ `API_KEY`
   - ✅ `ACCESS_TOKEN`
   - ✅ `TELEGRAM_TOKEN` (system bot)
   - ✅ `CHAT_ID` (system channel)
   - ❌ `BOT_INDEX_TOKEN`
   - ❌ `BOT_COMMODITY_TOKEN`
   - ❌ `BOT_NIFTY50_OPTIONS_TOKEN`
   - ❌ `BOT_NIFTY50_5X_TOKEN`
   - ❌ `BOT_NIFTY50_PAY_LATER_TOKEN`
   - ❌ `BOT_CRYPTO_TOKEN`
   - ❌ `CHANNEL_INDEX_ID`
   - ❌ `CHANNEL_COMMODITY_ID`
   - ❌ `CHANNEL_NIFTY50_OPTIONS_ID`
   - ❌ `CHANNEL_NIFTY50_5X_ID`
   - ❌ `CHANNEL_NIFTY50_PAY_LATER_ID`
   - ❌ `CHANNEL_CRYPTO_ID`

**If missing:** Add them now! 👇

---

## **ISSUE #4: Import Errors** 🔴

### Problem:
```python
# If strategy.py has bugs, entire app crashes
from strategy import FiveMinBreakoutStrategy  # ❌ If this fails = deployment fails
```

---

## **STEP-BY-STEP FIX FOR RENDER**

### Step 1: Add Missing Environment Variables

Go to Render Dashboard → Your Service → Settings → Environment

**Add these variables:**

```
BOT_INDEX_TOKEN=8601160697:AAFFxscCMfqcrXaf1lw69xK7Ue-RW_8aIzI
BOT_COMMODITY_TOKEN=8762956800:AAEkQZfYhawfxQEua8OSYcnp3FPRU2xywsc
BOT_NIFTY50_OPTIONS_TOKEN=8746059399:AAGfpg6rQfluICaezqiamCujN8_NcXbt1NQ
BOT_NIFTY50_5X_TOKEN=8265739611:AAFbraUdEY01eJOel76S8mMgBiZT4otxkd4
BOT_NIFTY50_PAY_LATER_TOKEN=8934391945:AAEdycuHV7sZP6eASCU2j7kQ9SBG7e9D4Q0
BOT_CRYPTO_TOKEN=8921592389:AAF7IKqXz2a7yp0a--m0vP21itKHVKqF-7k

CHANNEL_INDEX_ID=-1003966854994
CHANNEL_COMMODITY_ID=-1004403277287
CHANNEL_NIFTY50_OPTIONS_ID=-1003804613787
CHANNEL_NIFTY50_5X_ID=-1004466883026
CHANNEL_NIFTY50_PAY_LATER_ID=-1003814243881
CHANNEL_CRYPTO_ID=-1004482078964
```

---

### Step 2: Update `screener_app.py`

✅ Already done - Check: `/screener_app.py`

---

### Step 3: Fix Event Loop Management

Create `screener_async_handler.py` to handle async operations safely:

```python
# screener_async_handler.py
import asyncio
import threading
import logging

logger = logging.getLogger(__name__)

class AsyncEventLoopManager:
    """Manages async event loop in separate thread"""
    
    def __init__(self):
        self.loop = None
        self.thread = None
    
    def start(self):
        """Start event loop in daemon thread"""
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        logger.info("✅ Async event loop started in separate thread")
    
    def _run_loop(self):
        """Run event loop in background thread"""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()
    
    def schedule_coroutine(self, coro):
        """Schedule coroutine on event loop (thread-safe)"""
        if self.loop and self.loop.is_running():
            return asyncio.run_coroutine_threadsafe(coro, self.loop)
        else:
            logger.error("Event loop not running")
            return None
    
    def stop(self):
        """Stop event loop"""
        if self.loop:
            self.loop.call_soon_threadsafe(self.loop.stop)

# Global instance
async_manager = AsyncEventLoopManager()
```

---

### Step 4: Check `Procfile`

Your Procfile should be:

```procfile
web: python screener_app.py
```

✅ Already correct

---

### Step 5: Check `runtime.txt`

```
python-3.11.7
```

Make sure Python version is compatible with all packages

---

## **RENDER LOGS - WHAT TO LOOK FOR**

### ✅ **Successful Deployment:**
```
Starting build...
Installing dependencies...
✓ pip install successful
✓ Python 3.11.7 ready
...
Build successful!
Starting screener_app.py...
🚀 Screener initialized successfully
```

### ❌ **Likely Errors:**

#### Error 1: Missing Environment Variable
```
KeyError: 'BOT_INDEX_TOKEN'
Traceback: screener_background.py line 45
```
**Fix:** Add all 6 bot tokens to Render environment

#### Error 2: Import Error
```
ModuleNotFoundError: No module named 'dhanhq'
```
**Fix:** Check requirements.txt has `dhanhq>=2.2.0`

#### Error 3: Event Loop Error
```
RuntimeError: asyncio.run() cannot be called from a running event loop
```
**Fix:** Use AsyncEventLoopManager above

#### Error 4: Port Binding
```
OSError: [Errno 48] Address already in use
```
**Fix:** App already running on port. Need to manually restart on Render.

---

## **DEPLOYMENT CHECKLIST** ✅

Before deploying to Render, verify:

- [ ] **1. `.env` updated** with all 6 bot tokens
- [ ] **2. `.env` updated** with all 6 channel IDs
- [ ] **3. `screener_background.py` committed** with multi-bot routing
- [ ] **4. `screener_app.py` committed** with test endpoints
- [ ] **5. `Procfile` correct** → `web: python screener_app.py`
- [ ] **6. `requirements.txt` complete** with all dependencies
- [ ] **7. Render environment variables** → Add all 6 bot tokens + 6 channel IDs
- [ ] **8. Render start command** → `python screener_app.py` (in Procfile)
- [ ] **9. Git push** → New commits trigger auto-deploy
- [ ] **10. Check Render Logs** → Verify deployment successful

---

## **HOW TO MANUALLY RESTART RENDER**

1. Go to https://dashboard.render.com/web/srv-dacrc1v40ujc739uue4g
2. Click **"Manual Deploy"** → **"Deploy latest commit"**
3. Wait 2-3 minutes for build
4. Check **Logs** tab for errors

---

## **QUICK RENDER RESTART (if stuck)**

```bash
# In Render Dashboard:
1. Go to Settings
2. Scroll to "Danger Zone"
3. Click "Delete Service"
4. Recreate web service from GitHub repo
```

---

## **VERIFICATION AFTER DEPLOYMENT**

Once deployed, test each channel:

```bash
# Test INDEX channel
curl -X POST https://your-render-url/test/index

# Test NIFTY50 channel
curl -X POST https://your-render-url/test/nifty50

# Test COMMODITY channel
curl -X POST https://your-render-url/test/commodity

# Test INTRADAY channel
curl -X POST https://your-render-url/test/intraday

# Test PAY LATER channel
curl -X POST https://your-render-url/test/paylater

# Test CRYPTO channel
curl -X POST https://your-render-url/test/crypto

# Test SYSTEM channel
curl -X POST https://your-render-url/test/system

# Test ALL channels at once
curl -X POST https://your-render-url/test/all
```

Expected response:
```json
{
  "status": "sent",
  "channel": "INDEX",
  "channel_id": -1003966854994
}
```

Check your Telegram channels - you should receive test alerts! ✅

---

## **IF STILL FAILING**

### Get exact error:
1. Render Dashboard → Logs
2. Search for `ERROR` or `Traceback`
3. Copy full error message
4. Share with me

### Common Command:
```
Check logs for: 
- KeyError (missing env vars)
- ModuleNotFoundError (missing package)
- ImportError (import path wrong)
- RuntimeError (event loop issue)
```

---

## **SUMMARY** 📋

| What | Status | Action |
|------|--------|--------|
| screener_background.py (multi-bot) | ✅ Fixed | Committed |
| screener_app.py (test endpoints) | ✅ Fixed | Committed |
| .env (6 bot tokens) | ✅ Fixed | Committed |
| .env (6 channel IDs) | ✅ Fixed | Committed |
| Render env vars | ⏳ TODO | Add 12 variables |
| Deploy to Render | ⏳ TODO | Manual Deploy |
| Test all channels | ⏳ TODO | POST /test/all |

---

**Next Step:** Add the 12 environment variables to Render and manually deploy! 🚀

