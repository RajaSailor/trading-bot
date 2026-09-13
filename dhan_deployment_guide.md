# DhanHQ Trading Bot - Deployment & Production Guide

Complete guide for deploying the DhanHQ trading bot in production.

---

## 📋 Table of Contents

1. [Prerequisites](#prerequisites)
2. [Environment Setup](#environment-setup)
3. [Installation](#installation)
4. [Configuration](#configuration)
5. [DhanHQ Setup](#dhanhq-setup)
6. [Telegram Setup](#telegram-setup)
7. [Running the Bot](#running-the-bot)
8. [Monitoring](#monitoring)
9. [Troubleshooting](#troubleshooting)
10. [Production Checklist](#production-checklist)

---

## Prerequisites

### System Requirements
- Python 3.9+
- Ubuntu 20.04+ / CentOS 8+ / AWS EC2 t3.medium+
- 2GB RAM minimum
- 5GB disk space
- Static public IP (for DhanHQ webhooks)

### Required Accounts
- ✅ DhanHQ Trading Account (with API access)
- ✅ Telegram Bot (from BotFather)
- ✅ Server/VPS (AWS, DigitalOcean, Linode, etc.)

---

## Environment Setup

### 1. Create Virtual Environment

```bash
# Clone repository
git clone https://github.com/RajaSailor/trading-bot.git
cd trading-bot

# Create venv
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Requirements.txt

```txt
python-telegram-bot==20.3
flask==2.3.3
requests==2.31.0
python-dotenv==1.0.0
pytest==7.4.0
pytest-asyncio==0.21.1
```

### 3. Create .env File

```bash
cp .env.example .env
# Edit .env with your credentials
```

---

## Configuration

### 1. .env File (Complete)

```env
# ============================================================================
# DHANHQ CREDENTIALS
# ============================================================================
ACCESS_TOKEN=your_dhanhq_jwt_token_here
DHAN_CLIENT_ID=your_dhan_client_id_here
DHAN_WEBHOOK_SECRET=your_webhook_secret_here

# ============================================================================
# TELEGRAM CREDENTIALS
# ============================================================================
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
ALERT_CHAT_ID=your_telegram_chat_id_here

# ============================================================================
# TRADING PARAMETERS
# ============================================================================
PRACTICE_MODE=false              # Set to false for REAL trading
MAX_LOSS_PER_TRADE=500           # Maximum loss per trade (₹)
MAX_POSITION_SIZE=5              # Maximum position size (lots)
MIN_RR_RATIO=1.0                 # Minimum risk-reward ratio

# ============================================================================
# SERVER CONFIGURATION
# ============================================================================
SERVER_HOST=0.0.0.0              # Webhook server host
SERVER_PORT=5000                 # Webhook server port
LOG_LEVEL=INFO                   # DEBUG, INFO, WARNING, ERROR

# ============================================================================
# STATE PERSISTENCE
# ============================================================================
STATE_FILE=dhan_state.json       # State persistence file
```

### 2. Create .env.example

```bash
cp .env .env.example
# Remove sensitive values from .env.example
```

---

## DhanHQ Setup

### 1. Get API Credentials

1. Log in to DhanHQ: https://www.dhan.co/
2. Navigate to **Settings → API Configuration**
3. Generate **JWT Token** (Access Token)
4. Get your **Client ID**
5. Note the **Webhook Secret** (for signature verification)

### 2. Whitelist Your IP

**Critical for production!**

1. Go to **Settings → API → IP Whitelist**
2. Add your server's static public IP
   ```
   Example: 203.0.113.45
   ```
3. Save & verify

### 3. Configure Webhook

1. Go to **Settings → Webhooks**
2. Enter webhook URL:
   ```
   https://your-domain.com/dhan/postback
   ```
3. Set webhook secret (copy to .env as `DHAN_WEBHOOK_SECRET`)
4. Test webhook connection
5. Enable postbacks for:
   - ✅ Order Execution
   - ✅ Position Update
   - ✅ Account Update

### 4. Test DhanHQ Connection

```bash
python -c "
from dhan_api_client import DhanAPIClient
client = DhanAPIClient(access_token='YOUR_TOKEN', client_id='YOUR_CLIENT_ID')
success, msg, account = client.get_account_info()
print(f'Connection: {\"✅\" if success else \"❌\"} {msg}')
"
```

---

## Telegram Setup

### 1. Create Telegram Bot

1. Open Telegram & search for **@BotFather**
2. Send `/newbot`
3. Enter bot name: `DhanHQ Trading Bot`
4. Enter username: `your_dhanbot_username`
5. Copy the **API Token** → Add to .env as `TELEGRAM_BOT_TOKEN`

### 2. Get Chat ID

```bash
# Send any message to your bot, then run:
curl https://api.telegram.org/bot{YOUR_BOT_TOKEN}/getUpdates

# Look for "chat": {"id": XXXXXXXX}
# Copy this ID → Add to .env as ALERT_CHAT_ID
```

### 3. Test Telegram Connection

```bash
python -c "
from telegram import Bot
import asyncio

async def test():
    bot = Bot(token='YOUR_BOT_TOKEN')
    await bot.send_message(chat_id=YOUR_CHAT_ID, text='✅ Bot connected!')

asyncio.run(test())
"
```

---

## Running the Bot

### 1. Start Position Monitoring

```bash
# Terminal 1: Start DhanHQ integration with monitoring
python -c "
from dhan_integration import get_dhan_integration
dhan = get_dhan_integration()
dhan.start_monitoring(interval=5)
print('✅ Position monitoring started')
# Keep running...
"
```

### 2. Start Webhook Receiver

```bash
# Terminal 2: Start Flask webhook server
python -c "
from flask import Flask
from dhan_postback_handler import get_postback_handler, create_postback_app

handler = get_postback_handler()
app = create_postback_app(handler)
app.run(host='0.0.0.0', port=5000, debug=False)
"
```

### 3. Start Telegram Bot

```bash
# Terminal 3: Start Telegram bot
python -c "
from dhan_telegram_bridge import get_telegram_bridge
import asyncio

bridge = get_telegram_bridge()
app = asyncio.run(bridge.initialize_telegram_app())
asyncio.run(bridge.start_polling())
"
```

### Better: Use Supervisor for Process Management

Install supervisor:
```bash
sudo apt-get install supervisor
```

Create `/etc/supervisor/conf.d/dhan-bot.conf`:
```ini
[program:dhan-monitoring]
command=/home/user/trading-bot/venv/bin/python -c "from dhan_integration import get_dhan_integration; dhan = get_dhan_integration(); dhan.start_monitoring()"
directory=/home/user/trading-bot
user=user
autostart=true
autorestart=true
startsecs=10
stopasgroup=true
stdout_logfile=/var/log/dhan/monitoring.log
stderr_logfile=/var/log/dhan/monitoring_error.log

[program:dhan-webhook]
command=/home/user/trading-bot/venv/bin/python -c "from dhan_postback_handler import get_postback_handler, create_postback_app; handler = get_postback_handler(); app = create_postback_app(handler); app.run(host='0.0.0.0', port=5000, debug=False)"
directory=/home/user/trading-bot
user=user
autostart=true
autorestart=true
startsecs=10
stopasgroup=true
stdout_logfile=/var/log/dhan/webhook.log
stderr_logfile=/var/log/dhan/webhook_error.log

[program:dhan-telegram]
command=/home/user/trading-bot/venv/bin/python -c "from dhan_telegram_bridge import get_telegram_bridge; import asyncio; bridge = get_telegram_bridge(); app = asyncio.run(bridge.initialize_telegram_app()); asyncio.run(bridge.start_polling())"
directory=/home/user/trading-bot
user=user
autostart=true
autorestart=true
startsecs=10
stopasgroup=true
stdout_logfile=/var/log/dhan/telegram.log
stderr_logfile=/var/log/dhan/telegram_error.log

[group:dhan-bot]
programs=dhan-monitoring,dhan-webhook,dhan-telegram
priority=999
```

Start services:
```bash
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl start dhan-bot:*
```

---

## Monitoring

### 1. Check Status

```bash
# Check all processes
sudo supervisorctl status

# View logs
tail -f /var/log/dhan/monitoring.log
tail -f /var/log/dhan/webhook.log
tail -f /var/log/dhan/telegram.log
```

### 2. Health Check Endpoints

```bash
# Webhook health
curl http://localhost:5000/dhan/health
# Response: {"status": "healthy", "timestamp": "..."}

# Telegram health
curl http://your-domain.com/health
```

### 3. Monitor Position Tracking

```bash
python -c "
from dhan_integration import get_dhan_integration
dhan = get_dhan_integration()
dhan.print_summary()
"
```

---

## Troubleshooting

### Issue: "Invalid Access Token"

```bash
# Verify token is current (tokens expire)
# Go to DhanHQ → Settings → API → Regenerate Token
# Update .env with new token
# Restart all services
sudo supervisorctl restart dhan-bot:*
```

### Issue: "Webhook not receiving postbacks"

```bash
# 1. Check IP whitelisting
# Go to DhanHQ → Settings → API → IP Whitelist
# Verify your server IP is listed

# 2. Check webhook URL
# Should be: https://your-domain.com/dhan/postback
# Not: http (must be HTTPS)

# 3. Verify webhook secret
echo "DHAN_WEBHOOK_SECRET from .env should match DhanHQ settings"

# 4. Test manually
curl -X POST http://localhost:5000/dhan/postback \
  -H "Content-Type: application/json" \
  -d '{"eventType": "ORDER_EXECUTION", "orderId": "TEST_001"}'
```

### Issue: "Telegram bot not responding"

```bash
# 1. Verify bot token
curl https://api.telegram.org/bot{YOUR_BOT_TOKEN}/getMe

# 2. Check chat ID is correct
# Send message to bot, check logs

# 3. Restart Telegram service
sudo supervisorctl restart dhan-bot:dhan-telegram
```

### Issue: "Orders not being placed"

```bash
# 1. Check practice mode
# In production, set PRACTICE_MODE=false

# 2. Verify trading hours
# DhanHQ market hours: 9:15 AM - 3:30 PM IST (weekdays)

# 3. Check margin/funds
python -c "
from dhan_integration import get_dhan_integration
dhan = get_dhan_integration()
print(dhan.get_funds())
"

# 4. Check safety limits
# Verify MAX_LOSS_PER_TRADE and MAX_POSITION_SIZE in .env
```

---

## Production Checklist

Before going live:

### Security
- ✅ Use HTTPS only (SSL certificate from Let's Encrypt)
- ✅ Whitelist static IP in DhanHQ
- ✅ Use strong .env values
- ✅ Never commit .env to git
- ✅ Use environment variables for secrets
- ✅ Enable firewall (UFW on Ubuntu)

### Configuration
- ✅ Set PRACTICE_MODE=false
- ✅ Configure MAX_LOSS_PER_TRADE appropriately
- ✅ Set MAX_POSITION_SIZE based on capital
- ✅ Configure MIN_RR_RATIO (1.0 or higher)
- ✅ Use correct ALERT_CHAT_ID
- ✅ Configure LOG_LEVEL=INFO (not DEBUG in production)

### Testing
- ✅ Run integration tests: `pytest dhan_integration_tests.py -v`
- ✅ Test webhook postback manually
- ✅ Test Telegram signal parsing
- ✅ Verify SL/Target detection
- ✅ Test market close routine
- ✅ Verify state persistence (dhan_state.json)

### Deployment
- ✅ Use supervisor/systemd for process management
- ✅ Set up log rotation (logrotate)
- ✅ Enable monitoring/alerting
- ✅ Set up automated backups
- ✅ Configure cron for market close routine (2:50 PM)
- ✅ Test recovery from crashes

### Monitoring
- ✅ Monitor logs daily
- ✅ Track P&L regularly
- ✅ Check for API errors
- ✅ Verify webhook receipts
- ✅ Monitor server resources (CPU, RAM, disk)

### Risk Management
- ✅ Start with paper trading first
- ✅ Begin with small position sizes
- ✅ Monitor first trades carefully
- ✅ Have manual override ready
- ✅ Test stop-loss triggers
- ✅ Keep emergency contact list

---

## SSL Certificate Setup (Let's Encrypt)

```bash
# Install certbot
sudo apt-get install certbot python3-certbot-nginx

# Get certificate
sudo certbot certonly --standalone -d your-domain.com

# Auto-renew
sudo systemctl enable certbot.timer
sudo systemctl start certbot.timer

# Configure Nginx (if using reverse proxy)
sudo certbot install --nginx -d your-domain.com
```

---

## Log Rotation

Create `/etc/logrotate.d/dhan-bot`:

```
/var/log/dhan/*.log {
    daily
    rotate 30
    compress
    delaycompress
    notifempty
    create 0640 user user
    sharedscripts
    postrotate
        supervisorctl restart dhan-bot:* > /dev/null 2>&1 || true
    endscript
}
```

---

## Support

- 📧 Email: support@dhan.co
- 🔗 DhanHQ Docs: https://github.com/Kalaiviswa/dhan-api-v2-docs
- 🐛 Report Issues: Create GitHub issue
- 💬 Telegram Support: Join DhanHQ community group

---

## License

GNU General Public License v3.0 - See LICENSE file

---

**Last Updated:** September 2026
**Status:** ✅ Production Ready
