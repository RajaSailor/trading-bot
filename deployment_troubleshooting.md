# DhanHQ Trading Bot - Deployment Troubleshooting Guide

**Last Updated:** September 2026  
**Version:** 1.0.0  
**Status:** Production Ready

---

## 📋 Table of Contents

1. [Common Deployment Issues](#common-deployment-issues)
2. [Render Deployment Errors](#render-deployment-errors)
3. [DhanHQ API Connection Issues](#dhanhq-api-connection-issues)
4. [Telegram Bot Issues](#telegram-bot-issues)
5. [Webhook & Postback Issues](#webhook--postback-issues)
6. [Performance & Resource Issues](#performance--resource-issues)
7. [Security Issues](#security-issues)
8. [Logging & Debugging](#logging--debugging)
9. [Emergency Procedures](#emergency-procedures)

---

## Common Deployment Issues

### ❌ Issue: `ModuleNotFoundError: No module named 'dhanhq'`

**Cause:** Missing or incomplete `requirements.txt` installation

**Solution:**
```bash
# Local testing
pip install -r requirements.txt
pip list | grep -i dhan

# Check if dhanhq is installed
python -c "import dhanhq; print(dhanhq.__version__)"

# Render deployment
# In Render Dashboard:
# 1. Go to Settings → Build & Deploy
# 2. Check Build Log for installation errors
# 3. Ensure requirements.txt is in root directory
# 4. Rebuild: Manual Deploy → Deploy Latest Commit
```

**Prevention:**
```bash
# Always test locally first
python -m pip install -r requirements.txt --upgrade
python main.py
```

---

### ❌ Issue: `ImportError: cannot import name 'get_dhan_integration'`

**Cause:** Missing or incorrectly named module files

**Solution:**
```bash
# Check if all modules exist
ls -la *.py | grep dhan

# Expected files:
# - dhan_api_client.py
# - dhan_integration.py
# - dhan_telegram_bridge.py
# - dhan_postback_handler.py

# If missing, create from GitHub:
git clone https://github.com/RajaSailor/trading-bot.git
cd trading-bot
pip install -r requirements.txt
```

**Debug:**
```python
# Test imports manually
python -c "from dhan_integration import get_dhan_integration; print('✅ OK')"
python -c "from dhan_telegram_bridge import DhanTelegramBridge; print('✅ OK')"
python -c "from dhan_postback_handler import DhanPostbackHandler; print('✅ OK')"
```

---

### ❌ Issue: `FileNotFoundError: .env file not found`

**Cause:** Missing `.env` file (template only)

**Solution:**
```bash
# Create .env from template
cp .env.example .env

# Fill in your credentials
nano .env

# Verify .env is in .gitignore
cat .gitignore | grep "^\.env"
# Output should show: .env

# DO NOT commit .env to git!
git status | grep .env
# Should show: nothing or ignored
```

**For Render Deployment:**
```
1. Go to Render Dashboard
2. Select your service
3. Environment → Add Environment Variable
4. Add each variable from .env.example
   - ACCESS_TOKEN
   - DHAN_CLIENT_ID
   - API_KEY
   - TELEGRAM_BOT_TOKEN
   - All CHANNEL_* IDs
   - Trading parameters
5. Do NOT upload .env file
6. Redeploy service
```

---

## Render Deployment Errors

### ❌ Error: `Build failed: pip install requirements.txt`

**Log Location:** Render Dashboard → Service → Logs → Build Log

**Common Causes & Solutions:**

#### A) Python Version Mismatch
```
Error: python 3.8 but requirements.txt needs 3.9+
```

**Fix:**
```bash
# In Render Dashboard:
# Settings → Build & Deploy → Python Version
# Change to: 3.9, 3.10, or 3.11

# Redeploy
```

#### B) Package Not Found
```
Error: No module named 'dhanhq'
```

**Fix:**
```bash
# Update requirements.txt to use correct package name
# Option 1: Use pip-installable version
pip search dhanhq  # Get correct name

# Option 2: Install from GitHub
# dhanhq @ git+https://github.com/Kalaiviswa/dhan-api-v2.git

# Update requirements.txt and push to GitHub
git add requirements.txt
git commit -m "Fix: Update dhanhq package reference"
git push

# Render will auto-redeploy
```

#### C) Memory/Resource Limits
```
Error: MemoryError during pip install
```

**Fix (Render):**
```
Settings → Plan:
- Free tier: 512MB RAM (might fail)
- Starter: 1GB RAM (recommended minimum)
- Standard: 4GB RAM (production)

Upgrade plan → Redeploy
```

---

### ❌ Error: `Web service failed to start`

**Log Location:** Render Dashboard → Service → Logs → Runtime Log

**Diagnostic Steps:**

```bash
# 1. Check if main.py exists
ls -la main.py

# 2. Check if Flask can start locally
python main.py

# 3. Check Render logs
curl https://api.render.com/v1/services/<service-id>/logs

# 4. Check environment variables
echo $ACCESS_TOKEN
echo $DHAN_CLIENT_ID

# 5. Test health endpoint
curl https://trading-bot-0p7j.onrender.com/health
```

**Common Causes:**

#### A) Missing Environment Variables
```
Error: KeyError: 'ACCESS_TOKEN'
```

**Fix:**
```
Render Dashboard → Environment:
- Add all required variables from .env.example
- Verify no typos
- Redeploy
```

#### B) Initialization Fails
```
Error: DhanHQ Integration failed
```

**Solution:**
```bash
# Test locally with same env vars
export ACCESS_TOKEN="your_token"
export DHAN_CLIENT_ID="1111778442"
python main.py

# Check logs for specific error
tail -f logs/trading-bot.log

# If offline, run in practice mode
export PRACTICE_MODE=true
python main.py
```

#### C) Port Already in Use
```
Error: Address already in use :5000
```

**Fix:**
```bash
# Render automatically assigns PORT from environment
# PORT is set to 5000 by default, but overridable

# Check for port conflicts
lsof -i :5000

# Kill process if stuck
kill -9 <PID>

# For Render, change in Environment:
PORT=8000
```

---

### ❌ Error: `Failed to fetch latest code from GitHub`

**Cause:** GitHub authentication or permission issue

**Solution:**

```bash
# 1. In Render Dashboard:
# Settings → Repository
# - Click "Disconnect GitHub"
# - Click "Connect GitHub" again
# - Re-authorize

# 2. Or manually trigger redeploy:
# Manual Deploy → Deploy Latest Commit

# 3. Check GitHub token permissions
# Account Settings → Developer Settings → Personal Access Tokens
# Ensure token has 'repo' scope
```

---

## DhanHQ API Connection Issues

### ❌ Issue: `401 Unauthorized - Invalid Access Token`

**Cause:** Token expired or invalid

**Solution:**

```bash
# 1. Get new token from DhanHQ
# Dashboard → Settings → API Configuration → Generate JWT Token

# 2. Copy token
# Format: eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJ...

# 3. Update .env
nano .env
# Set: ACCESS_TOKEN=your_new_token

# 4. For Render:
# Render Dashboard → Environment → ACCESS_TOKEN
# Paste new token
# Redeploy

# 5. Test connection
python -c "
from dhan_api_client import DhanAPIClient
client = DhanAPIClient()
success, msg, account = client.get_account_info()
print(f'Connected: {success}')
print(f'Message: {msg}')
print(f'Account: {account}')
"
```

---

### ❌ Issue: `Connection timeout - DhanHQ server not responding`

**Cause:** Network issue, DhanHQ server down, or IP whitelisting

**Solution:**

```bash
# 1. Check internet connectivity
ping google.com
ping dhan.co

# 2. Check IP whitelist in DhanHQ
# Dashboard → Settings → API → IP Whitelist
# Add your Render server IPs:
# - 106.200.21.44
# - 74.220.52.33
# - 83.231.47.38

# 3. Check firewall rules
# Ensure outbound HTTPS (443) is allowed

# 4. Verify endpoint URL
# Check dhan_api_client.py for correct API endpoint
grep -n "api.dhan" dhan_api_client.py

# 5. Test connection
python -c "
import requests
response = requests.get('https://api.dhan.co/health', timeout=5)
print(response.status_code)
"

# 6. Check DhanHQ status page
# https://status.dhan.co/
```

---

### ❌ Issue: `403 Forbidden - IP not whitelisted`

**Solution:**

```bash
# For Render deployment:
# 1. Find your Render server IP
curl ifconfig.me

# 2. Add to DhanHQ whitelist
# DhanHQ Dashboard → Settings → API → IP Whitelist
# Add your current IP

# 3. For multiple Render servers, add all possible IPs:
# Render uses multiple server IPs for redundancy

# 4. Whitelist entire Render subnet (if allowed by DhanHQ)
# Talk to DhanHQ support: support@dhan.co
```

---

## Telegram Bot Issues

### ❌ Issue: `Bot not receiving messages from channel`

**Cause:** Wrong channel ID or bot not added to channel

**Solution:**

```bash
# 1. Get correct channel ID
# Send /start to your bot
curl "https://api.telegram.org/bot<TOKEN>/getUpdates"
# Look for chat.id in response

# 2. Make bot admin in channel
# Telegram → Channel → Edit → Add Administrators
# Select your bot, give all permissions

# 3. Verify channel ID in .env
nano .env
grep CHANNEL_ .env

# 4. Test message sending
python -c "
from telegram import Bot
import asyncio

async def test():
    bot = Bot(token='your_token')
    await bot.send_message(
        chat_id=-1003704821776,
        text='🧪 Test message'
    )

asyncio.run(test())
"

# 5. Check bot token
# @BotFather → /mybots → Select bot
# Should show: <ID>:<TOKEN>
```

---

### ❌ Issue: `Bot token is invalid`

**Solution:**

```bash
# 1. Verify token format
# Correct: 123456789:ABCdefGHIjklmnoPQRstuvWXYZ-1a2b3c4d5

# 2. Get new token from @BotFather
# Telegram → @BotFather → /mybots
# Select bot → API Token → Copy

# 3. Update .env
TELEGRAM_BOT_TOKEN=new_token_here

# 4. Test token validity
python -c "
from telegram import Bot

bot = Bot(token='your_token')
me = bot.get_me()
print(f'Bot name: {me.first_name}')
print(f'Bot username: {me.username}')
"
```

---

### ❌ Issue: `Bot sends message but to wrong channel`

**Cause:** Wrong channel ID

**Solution:**

```bash
# 1. Get all channel IDs
curl "https://api.telegram.org/bot<TOKEN>/getUpdates"

# Look for "chat" object:
# {
#   "message": {
#     "chat": {
#       "id": -1003704821776,  # NEGATIVE for channels
#       "title": "TRADE_CONTROL",
#       "type": "supergroup"
#     }
#   }
# }

# 2. Always use NEGATIVE chat ID for channels/groups
# ✅ Correct: -1003704821776
# ❌ Wrong: 1003704821776 (missing minus sign)

# 3. Update .env with correct IDs
nano .env
# CHANNEL_TRADE_CONTROL_ID=-1003704821776
# CHANNEL_COMMODITY_ID=-1004403277287

# 4. Test sending to each channel
python -c "
from telegram import Bot
import asyncio

async def test():
    bot = Bot(token='your_token')
    channels = {
        'TRADE_CONTROL': -1003704821776,
        'COMMODITY': -1004403277287,
        'INDEX': -1003966854994,
    }
    
    for name, chat_id in channels.items():
        try:
            await bot.send_message(chat_id, f'✅ Testing {name}')
            print(f'✅ {name} OK')
        except Exception as e:
            print(f'❌ {name} FAILED: {e}')

asyncio.run(test())
"
```

---

## Webhook & Postback Issues

### ❌ Issue: `Webhook URL not accessible`

**Cause:** Render service not running or wrong URL

**Solution:**

```bash
# 1. Check if service is running
curl https://trading-bot-0p7j.onrender.com/health

# Expected response:
# {
#   "status": "healthy",
#   "service": "dhan-trading-bot",
#   "version": "1.0.0"
# }

# 2. Verify webhook URL in DhanHQ
# DhanHQ Dashboard → Settings → Webhooks
# URL should be: https://your-render-url/dhan/postback
# Example: https://trading-bot-0p7j.onrender.com/dhan/postback

# 3. Check Render logs
# Render Dashboard → Logs
# Should show: "✅ Postback received: ORDER_EXECUTION"

# 4. Test webhook manually
curl -X POST https://trading-bot-0p7j.onrender.com/dhan/postback \
  -H "Content-Type: application/json" \
  -H "X-Dhan-Signature: test-signature" \
  -d '{"eventType":"TEST","data":{}}'
```

---

### ❌ Issue: `Invalid webhook signature - postback rejected`

**Cause:** Webhook secret mismatch

**Solution:**

```bash
# 1. Get webhook secret from DhanHQ
# DhanHQ Dashboard → Settings → Webhooks
# Copy the "Webhook Secret"

# 2. Update .env
nano .env
# Set: DHAN_WEBHOOK_SECRET=your_secret_here
# Set: WEBHOOK_SECRET=your_secret_here  # Same value

# 3. For Render:
# Render Dashboard → Environment
# Set DHAN_WEBHOOK_SECRET and WEBHOOK_SECRET
# Redeploy

# 4. Update in DhanHQ if needed
# Settings → Webhooks → Edit → Update Secret
# Must match value in .env

# 5. Test signature verification
python -c "
import hmac
import hashlib
import json

secret = 'your_webhook_secret'
payload = json.dumps({'test': 'data'})

signature = hmac.new(
    secret.encode(),
    payload.encode(),
    hashlib.sha256
).hexdigest()

print(f'Signature: {signature}')
"
```

---

### ❌ Issue: `Postback received but not processed`

**Cause:** Missing required fields or processing error

**Solution:**

```bash
# 1. Check postback handler logs
tail -f logs/trading-bot.log | grep -i postback

# 2. Verify postback structure
# DhanHQ sends format:
# {
#   "eventType": "ORDER_EXECUTION",
#   "data": {
#     "orderId": "12345",
#     "status": "EXECUTED",
#     ...
#   }
# }

# 3. Enable debug logging
# .env: LOG_LEVEL=DEBUG
# Then redeploy and check logs

# 4. Test with mock postback
python -c "
from dhan_postback_handler import DhanPostbackHandler
import json

test_postback = {
    'eventType': 'TEST',
    'data': {}
}

handler = DhanPostbackHandler(None, None)
success, msg = handler.handle_postback(test_postback, '')
print(f'Success: {success}, Message: {msg}')
"
```

---

## Performance & Resource Issues

### ❌ Issue: `Service keeps restarting / Crashed with status 137`

**Cause:** Out of memory or resource limits exceeded

**Solution:**

```bash
# 1. Check Render plan (memory limits)
# Free tier: 512MB (INSUFFICIENT)
# Starter: 1GB (RECOMMENDED MINIMUM)
# Standard: 4GB (PRODUCTION)

# 2. Upgrade plan
# Render Dashboard → Billing → Change Plan
# Upgrade to Starter or Standard

# 3. Optimize code
# Check dhan_integration.py for memory leaks
# - Limit logging output
# - Don't cache entire position history in memory
# - Clean up old connections

# 4. Monitor resource usage
# Render Dashboard → Metrics
# Check CPU and Memory usage
```

---

### ❌ Issue: `Slow response times / High CPU usage`

**Cause:** Inefficient API calls or blocking operations

**Solution:**

```bash
# 1. Profile CPU usage
python -m cProfile -o output.prof main.py

# 2. Check for synchronous API calls
grep -n "requests.get\|requests.post" *.py
# Should use async: asyncio, aiohttp

# 3. Optimize DhanHQ API calls
# - Cache positions instead of fetching every time
# - Use batch operations when available
# - Add request timeouts

# 4. Check log size
ls -lh logs/
# If > 500MB, archive and clean

# 5. Reduce logging frequency
# .env: LOG_LEVEL=WARNING  (from INFO)
```

---

## Security Issues

### ❌ Issue: `Sensitive data in logs`

**Danger:** Token/credentials leaked in logs

**Solution:**

```bash
# 1. Sanitize logs
# grep -v "ACCESS_TOKEN\|API_KEY\|TELEGRAM_BOT_TOKEN" logs/*.log

# 2. Configure logging to exclude secrets
# In logging_setup.py, add:
class SensitiveDataFilter(logging.Filter):
    SENSITIVE_KEYS = ['token', 'secret', 'password', 'key']
    
    def filter(self, record):
        for key in self.SENSITIVE_KEYS:
            if key in record.getMessage().lower():
                record.msg = '[REDACTED]'
        return True

# 3. Add filter to logger
logger.addFilter(SensitiveDataFilter())

# 4. Rotate logs
# Max size: 10MB per file
# Keep only 3 files
```

---

### ❌ Issue: `.env file accidentally committed to git`

**CRITICAL SECURITY ISSUE!** ⚠️

**Immediate Actions:**

```bash
# 1. REVOKE ALL CREDENTIALS IMMEDIATELY
# - Generate new Access Token in DhanHQ
# - Regenerate Telegram Bot Token (@BotFather)
# - Update all credentials in Render

# 2. Remove .env from Git history
git filter-branch --tree-filter 'rm -f .env' HEAD

# 3. Force push (WARNING: Rewrites history)
git push --force origin main

# 4. Notify DhanHQ support: support@dhan.co

# 5. Audit logs for unauthorized access
# Check DhanHQ transaction history
# Check Telegram bot activity
```

---

### ❌ Issue: `Webhook signature verification fails`

**Cause:** Secret mismatch or signature calculation error

**Solution:**

```bash
# 1. Verify HMAC calculation
import hmac
import hashlib

secret = "your_webhook_secret"
payload = '{"eventType":"TEST"}'

correct_signature = hmac.new(
    secret.encode(),
    payload.encode(),
    hashlib.sha256
).hexdigest()

print(f"Expected: {correct_signature}")

# 2. Check signature in postback
# DhanHQ sends in header: X-Dhan-Signature
# Must match calculated signature

# 3. Ensure payload is not modified before verification
# Sign raw request body, not parsed JSON

# 4. Use timing-safe comparison
# ✅ CORRECT:
# hmac.compare_digest(expected, received)
# ❌ WRONG:
# expected == received  # Vulnerable to timing attacks
```

---

## Logging & Debugging

### Enable Debug Logging

```bash
# In .env
LOG_LEVEL=DEBUG

# In code
import logging
logging.basicConfig(level=logging.DEBUG)

# Check logs
tail -f logs/trading-bot.log | grep -i error
```

### Common Debug Patterns

```python
# 1. Test imports
python -c "from dhan_api_client import DhanAPIClient; print('✅')"

# 2. Test environment loading
python -c "from dotenv import load_dotenv; import os; load_dotenv(); print(os.getenv('ACCESS_TOKEN')[:20])"

# 3. Test DhanHQ connection
python -c "
from dhan_integration import get_dhan_integration
dhan = get_dhan_integration()
print(dhan.test_connection())
"

# 4. Test Telegram bot
python -c "
from telegram import Bot
bot = Bot(token='your_token')
print(bot.get_me())
"

# 5. Test Flask app
python main.py
# Should show: Running on http://0.0.0.0:5000
```

---

## Emergency Procedures

### 🚨 Emergency Shutdown

```bash
# 1. Stop trading immediately
# In Render Dashboard: Suspend Service

# 2. Check if any open positions
# DhanHQ Dashboard → Positions

# 3. Close all positions manually if needed
# DO NOT let bot auto-close until root cause found

# 4. Review logs for errors
# Render → Logs

# 5. Fix issue
# Debug locally, test thoroughly

# 6. Restart service
# Render → Resume Service
```

---

### 🚨 Recover from Failed Deployment

```bash
# 1. Revert to previous commit
git log --oneline | head -5
git revert <commit-hash>
git push

# 2. Render auto-deploys from latest commit

# 3. Or rollback in Render Dashboard
# Deployments → Select previous version → Redeploy
```

---

### 🚨 Token Expired

```bash
# 1. Generate new token in DhanHQ
# Dashboard → Settings → API Configuration

# 2. Update .env or Render environment
# Paste new token

# 3. Redeploy
# git push or Manual Deploy in Render

# 4. Verify connection
# curl https://your-url/dhan/health
```

---

## Getting Help

### Support Contacts

| Issue | Contact | Link |
|-------|---------|------|
| DhanHQ API | DhanHQ Support | support@dhan.co |
| Telegram Bot | @BotFather | https://t.me/botfather |
| Render Hosting | Render Support | https://render.com/support |
| GitHub Issues | Repository Issues | https://github.com/RajaSailor/trading-bot/issues |
| Email | Author | er.bharathirajaathikkannan@gmail.com |

### Useful Links

- **DhanHQ API Docs:** https://github.com/Kalaiviswa/dhan-api-v2-docs
- **Render Docs:** https://render.com/docs
- **Telegram Bot API:** https://core.telegram.org/bots/api
- **Python Logging:** https://docs.python.org/3/library/logging.html
- **Flask Docs:** https://flask.palletsprojects.com/

---

## Checklist Before Going Live

- [ ] Test with PRACTICE_MODE=true
- [ ] All environment variables set correctly
- [ ] .env file NOT committed to git
- [ ] Whitelist Render IP in DhanHQ
- [ ] Webhook URL set in DhanHQ
- [ ] Telegram bot added to all channels
- [ ] Health check working: /health
- [ ] Logs are being written: logs/trading-bot.log
- [ ] Test trade signal received and processed
- [ ] All 8 alert channels receive notifications
- [ ] Emergency shutdown procedure tested
- [ ] Monitoring setup complete
- [ ] Backup credentials stored securely

---

**Last Updated:** September 2026  
**Maintained by:** RajaSailor  
**Version:** 1.0.0
