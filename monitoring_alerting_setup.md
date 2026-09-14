# DhanHQ Trading Bot - Monitoring & Alerting Setup Guide

**Last Updated:** September 2026  
**Version:** 1.0.0  
**Status:** Production Ready

---

## 📋 Table of Contents

1. [Monitoring Overview](#monitoring-overview)
2. [Application Health Monitoring](#application-health-monitoring)
3. [Trading Performance Monitoring](#trading-performance-monitoring)
4. [System Resource Monitoring](#system-resource-monitoring)
5. [Alert Configuration](#alert-configuration)
6. [Telegram Alert Setup](#telegram-alert-setup)
7. [Render Monitoring](#render-monitoring)
8. [Log Analysis & Dashboards](#log-analysis--dashboards)
9. [Performance Metrics](#performance-metrics)
10. [Alerting Rules & Thresholds](#alerting-rules--thresholds)

---

## Monitoring Overview

### What to Monitor

| Category | Metric | Alert Threshold | Action |
|----------|--------|-----------------|--------|
| **Health** | API Response | > 5s | Investigate API |
| **Health** | Postback Handler | Any failure | Manual check |
| **Trading** | Open Positions | > 5 positions | Risk check |
| **Trading** | Daily P&L | Loss > MAX_LOSS | Stop trading |
| **Trading** | Win Rate | < 40% | Review strategy |
| **System** | Memory Usage | > 80% | Restart service |
| **System** | CPU Usage | > 90% | Optimize code |
| **System** | Error Rate | > 1% | Review logs |
| **Telegram** | Message Delivery | Failed sends | Check bot token |
| **DhanHQ** | API Latency | > 2s | Check connection |

---

## Application Health Monitoring

### 1. Health Check Endpoint

**Endpoint:** `GET /health`

```bash
# Test health check
curl https://trading-bot-0p7j.onrender.com/health

# Expected Response (200 OK):
{
  "status": "healthy",
  "timestamp": "2026-09-14T10:30:00.123456",
  "service": "dhan-trading-bot",
  "version": "1.0.0",
  "dhan_connected": true,
  "telegram_connected": true
}

# Alert if: status != "healthy"
# Alert if: Response code != 200
# Alert if: dhan_connected = false
# Alert if: telegram_connected = false
```

### 2. DhanHQ Health Check

**Endpoint:** `GET /dhan/health`

```bash
# Test DhanHQ connection
curl https://trading-bot-0p7j.onrender.com/dhan/health

# Expected Response:
{
  "status": "healthy",
  "dhan_connected": true,
  "account_info": {
    "clientID": "1111778442",
    "email": "user@email.com",
    "balance": 500000.50,
    "available": 450000.25,
    "margin_used": 50000.25
  },
  "timestamp": "2026-09-14T10:30:00.123456"
}

# Alert Conditions:
# - status = "unhealthy"
# - dhan_connected = false
# - balance < minimum_required (e.g., 100000)
```

### 3. Bot Status Check

**Endpoint:** `GET /api/status`

```bash
# Get current bot status
curl https://trading-bot-0p7j.onrender.com/api/status

# Response includes:
{
  "status": "running",
  "timestamp": "2026-09-14T10:30:00.123456",
  "bot": {
    "telegram_connected": true,
    "postback_handler_ready": true
  },
  "dhan": {
    "practice_mode": true,
    "auto_trading_enabled": false
  },
  "trading": {
    "open_positions": 2,
    "positions": [...]
  }
}

# Alert if:
# - telegram_connected = false
# - postback_handler_ready = false
# - open_positions > MAX_POSITION_SIZE
```

### 4. Statistics Endpoint

**Endpoint:** `GET /api/stats`

```bash
# Get trading statistics
curl https://trading-bot-0p7j.onrender.com/api/stats

# Response includes:
{
  "status": "success",
  "stats": {
    "total_trades": 45,
    "winning_trades": 35,
    "losing_trades": 10,
    "win_rate": "77.78%",
    "total_profit": 15250.50,
    "total_loss": -2450.25,
    "net_profit": 12800.25,
    "avg_win": 435.86,
    "avg_loss": -245.03,
    "profit_factor": 6.22,
    "daily_pnl": 2350.75,
    "weekly_pnl": 8900.50,
    "monthly_pnl": 28500.00
  },
  "timestamp": "2026-09-14T10:30:00.123456"
}

# Alert if:
# - win_rate < 40%
# - daily_pnl < -MAX_LOSS_PER_TRADE
# - profit_factor < 1.5
```

---

## Trading Performance Monitoring

### Real-time Position Monitoring

```python
# Monitor open positions in real-time
import requests
import time
from datetime import datetime

def monitor_positions():
    """Monitor open positions every 60 seconds"""
    
    while True:
        try:
            response = requests.get(
                'https://your-url/api/status',
                timeout=5
            )
            data = response.json()
            
            positions = data['trading']['positions']
            
            # Alert on position count
            if len(positions) > 5:
                alert(f"⚠️ High position count: {len(positions)}")
            
            # Monitor each position
            for pos in positions:
                symbol = pos['symbol']
                qty = pos['quantity']
                pnl = pos['pnl']
                pnl_percent = pos['pnl_percent']
                
                # Alert on large loss
                if pnl < -1000:
                    alert(f"🚨 Large loss on {symbol}: ₹{pnl}")
                
                # Alert on large gain
                if pnl > 5000:
                    alert(f"✅ Large gain on {symbol}: ₹{pnl}")
                
                print(f"{symbol}: {qty} lots, P&L: ₹{pnl} ({pnl_percent}%)")
            
            time.sleep(60)  # Check every minute
            
        except Exception as e:
            alert(f"❌ Monitoring error: {str(e)}")
            time.sleep(60)

# Run in background
# python -c "from monitoring import monitor_positions; monitor_positions()"
```

### Daily P&L Monitoring

```python
def monitor_daily_pnl():
    """Alert if daily loss exceeds maximum"""
    
    max_daily_loss = -500  # ₹500 max loss per day
    
    while True:
        try:
            response = requests.get('https://your-url/api/stats')
            stats = response.json()['stats']
            
            daily_pnl = stats['daily_pnl']
            
            if daily_pnl < max_daily_loss:
                alert(f"🚨 DAILY LOSS LIMIT EXCEEDED: ₹{daily_pnl}")
                disable_auto_trading()  # Stop trading
                break
            
            print(f"Daily P&L: ₹{daily_pnl} ({daily_pnl/max_daily_loss*100:.1f}% of limit)")
            
            time.sleep(300)  # Check every 5 minutes
            
        except Exception as e:
            alert(f"❌ P&L monitoring error: {str(e)}")
            time.sleep(300)
```

---

## System Resource Monitoring

### Render Metrics Monitoring

```bash
# Monitor via Render Dashboard
# Navigate to: Service → Metrics

# Key Metrics:
# 1. CPU Usage (%)
#    - Alert if > 80% for > 5 minutes
#    - Action: Optimize code or upgrade plan

# 2. Memory Usage (MB)
#    - Alert if > 80% of plan limit
#    - Action: Restart service or upgrade plan
#    - Free plan: 512MB (RISKY)
#    - Starter: 1GB (RECOMMENDED)
#    - Standard: 4GB (PRODUCTION)

# 3. Network In/Out (Mbps)
#    - Monitor for unusual patterns
#    - Indicator of DDoS or excessive API calls

# 4. Build/Deploy Duration
#    - Alert if > 30 minutes (timeout)
#    - Action: Check for stuck processes
```

### Automated Resource Monitoring Script

```python
# monitoring/resource_monitor.py

import os
import psutil
import requests
from datetime import datetime
import time

class ResourceMonitor:
    """Monitor system resources and alert on thresholds"""
    
    def __init__(self, alert_callback):
        self.alert = alert_callback
        self.cpu_threshold = 80
        self.memory_threshold = 80
        
    def get_system_stats(self):
        """Get current system stats"""
        
        return {
            "timestamp": datetime.now().isoformat(),
            "cpu_percent": psutil.cpu_percent(interval=1),
            "memory_percent": psutil.virtual_memory().percent,
            "memory_mb": psutil.virtual_memory().used / (1024**2),
            "memory_total_mb": psutil.virtual_memory().total / (1024**2),
            "disk_percent": psutil.disk_usage('/').percent,
            "process_count": len(psutil.pids()),
        }
    
    def monitor(self):
        """Continuous monitoring loop"""
        
        while True:
            try:
                stats = self.get_system_stats()
                
                # Check CPU
                if stats['cpu_percent'] > self.cpu_threshold:
                    self.alert(
                        f"⚠️ HIGH CPU: {stats['cpu_percent']:.1f}%"
                    )
                
                # Check Memory
                if stats['memory_percent'] > self.memory_threshold:
                    self.alert(
                        f"⚠️ HIGH MEMORY: {stats['memory_percent']:.1f}% "
                        f"({stats['memory_mb']:.0f}MB/{stats['memory_total_mb']:.0f}MB)"
                    )
                
                # Check Disk
                if stats['disk_percent'] > 90:
                    self.alert(
                        f"⚠️ LOW DISK: {100-stats['disk_percent']:.1f}% free"
                    )
                
                time.sleep(60)  # Check every minute
                
            except Exception as e:
                self.alert(f"❌ Resource monitor error: {str(e)}")
                time.sleep(60)

# Usage
# from monitoring.resource_monitor import ResourceMonitor
# monitor = ResourceMonitor(alert_callback=send_telegram_alert)
# monitor.monitor()
```

---

## Alert Configuration

### Alert Channels

```
1. Telegram (CHANNEL_SERVICE_ALERTS_ID)
   - System alerts
   - Error alerts
   - Critical warnings
   - Daily summary

2. Email (Optional)
   - Critical issues only
   - Daily report
   - Weekly summary

3. Logs (logs/trading-bot.log)
   - All events
   - Queryable history
   - Archive for compliance
```

### Alert Severity Levels

| Level | Color | Action | Channel |
|-------|-------|--------|---------|
| **CRITICAL** | 🔴 Red | Immediate action required | Telegram + Email |
| **ERROR** | 🟠 Orange | Investigate within 1 hour | Telegram + Log |
| **WARNING** | 🟡 Yellow | Monitor closely | Log + Telegram |
| **INFO** | 🔵 Blue | Informational | Log only |
| **DEBUG** | ⚪ Gray | Development | Log only |

---

## Telegram Alert Setup

### Alert Message Format

```
🔴 CRITICAL: Server Down

⏰ Time: 2026-09-14 14:30:00
📊 Status: Service unavailable
❌ Error: Connection timeout after 30s

🔧 Action Required:
1. Check Render dashboard
2. Verify DhanHQ API status
3. Restart service if needed

📞 Support: support@dhan.co
```

### Sending Alerts via Telegram

```python
# alerts/telegram_alerter.py

from telegram import Bot
import asyncio
from datetime import datetime
import logging

class TelegramAlerter:
    """Send alerts to Telegram channel"""
    
    def __init__(self, bot_token, channel_id):
        self.bot = Bot(token=bot_token)
        self.channel_id = channel_id
        self.logger = logging.getLogger(__name__)
    
    async def send_alert(self, title, message, severity='INFO'):
        """
        Send alert to Telegram
        
        Args:
            title: Alert title
            message: Alert message
            severity: CRITICAL, ERROR, WARNING, INFO, DEBUG
        """
        
        severity_emoji = {
            'CRITICAL': '🔴',
            'ERROR': '🟠',
            'WARNING': '🟡',
            'INFO': '🔵',
            'DEBUG': '⚪'
        }
        
        emoji = severity_emoji.get(severity, '❓')
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        alert_text = f"""
{emoji} {severity}: {title}

⏰ Time: {timestamp}
📊 Message: {message}

🔗 Dashboard: https://trading-bot-0p7j.onrender.com/api/status
"""
        
        try:
            await self.bot.send_message(
                chat_id=self.channel_id,
                text=alert_text,
                parse_mode='HTML'
            )
            self.logger.info(f"Alert sent: {title}")
        except Exception as e:
            self.logger.error(f"Failed to send alert: {str(e)}")
    
    def send_alert_sync(self, title, message, severity='INFO'):
        """Synchronous wrapper for async send"""
        asyncio.run(self.send_alert(title, message, severity))

# Usage
# alerter = TelegramAlerter(
#     bot_token=os.getenv('TELEGRAM_BOT_TOKEN'),
#     channel_id=os.getenv('CHANNEL_SERVICE_ALERTS_ID')
# )
# alerter.send_alert_sync(
#     title='High Memory Usage',
#     message='Memory usage is at 85%',
#     severity='WARNING'
# )
```

### Daily Summary Report

```python
def send_daily_summary():
    """Send daily trading summary at market close"""
    
    schedule.every().day.at("15:45").do(
        lambda: send_daily_report()
    )

def send_daily_report():
    """Generate and send daily report"""
    
    response = requests.get('https://your-url/api/stats')
    stats = response.json()['stats']
    
    report = f"""
📊 DAILY TRADING SUMMARY
{datetime.now().strftime('%Y-%m-%d')}

📈 Performance:
  • Total Trades: {stats['total_trades']}
  • Winning Trades: {stats['winning_trades']}
  • Losing Trades: {stats['losing_trades']}
  • Win Rate: {stats['win_rate']}

💰 P&L:
  • Daily P&L: ₹{stats['daily_pnl']:,.2f}
  • Total Profit: ₹{stats['total_profit']:,.2f}
  • Total Loss: ₹{stats['total_loss']:,.2f}
  • Net Profit: ₹{stats['net_profit']:,.2f}

📊 Metrics:
  • Average Win: ₹{stats['avg_win']:.2f}
  • Average Loss: ₹{stats['avg_loss']:.2f}
  • Profit Factor: {stats['profit_factor']:.2f}

✅ Status: All systems operational
"""
    
    alerter.send_alert_sync(
        title='Daily Summary',
        message=report,
        severity='INFO'
    )
```

---

## Render Monitoring

### Render Dashboard Monitoring

```
1. Service Page
   - Status: Running / Crashed / Suspended
   - Memory: Current usage vs limit
   - CPU: Current percentage
   - Network: Incoming/Outgoing traffic

2. Logs
   - Build Log: Installation and build process
   - Runtime Log: Application output
   - Filter by: Error, Warning, Info

3. Events
   - Deployments: When code was deployed
   - Restarts: Why service restarted
   - Scale events: Resource changes

4. Metrics
   - CPU Graph
   - Memory Graph
   - Network Graph
   - Error Rate Graph
```

### Monitor Logs Continuously

```bash
# Watch logs in real-time
# Render Dashboard → Logs → click "Follow"

# Or use tail command if accessible
tail -f /var/log/trading-bot.log

# Search for errors
grep -i "error" /var/log/trading-bot.log | tail -20

# Count error frequency
grep -i "error" /var/log/trading-bot.log | wc -l

# Most recent errors
grep -i "error" /var/log/trading-bot.log | tail -5

# Specific error type
grep "ModuleNotFoundError" /var/log/trading-bot.log
```

### Set Up Render Alerts

```
Render Dashboard → Settings → Notifications

Alert Types:
- Service Status
- Build Failure
- Scale Events
- Resource Warnings

Notification Channels:
- Email
- Slack (integrations)
- PagerDuty (on-call alerts)
```

---

## Log Analysis & Dashboards

### Log File Locations

```
Local Development:
  logs/trading-bot.log

Render Production:
  Render Dashboard → Logs
  
Log Rotation:
  logs/trading-bot.log
  logs/trading-bot.log.1
  logs/trading-bot.log.2
  (Old logs archived)
```

### Parsing Logs for Metrics

```python
# analysis/log_analyzer.py

import re
from collections import defaultdict
from datetime import datetime, timedelta

class LogAnalyzer:
    """Analyze trading bot logs"""
    
    def __init__(self, log_file):
        self.log_file = log_file
        self.metrics = defaultdict(int)
    
    def analyze_errors(self, hours=24):
        """Get error count in past N hours"""
        
        cutoff = datetime.now() - timedelta(hours=hours)
        error_count = 0
        
        with open(self.log_file, 'r') as f:
            for line in f:
                if 'ERROR' in line:
                    # Parse timestamp
                    match = re.search(r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
                    if match:
                        ts = datetime.strptime(match.group(1), '%Y-%m-%d %H:%M:%S')
                        if ts > cutoff:
                            error_count += 1
        
        return error_count
    
    def analyze_trades(self):
        """Extract trade execution events"""
        
        trades = []
        
        with open(self.log_file, 'r') as f:
            for line in f:
                if 'TRADE EXECUTED' in line or 'Order:' in line:
                    trades.append(line)
        
        return trades
    
    def get_statistics(self):
        """Get overall statistics"""
        
        with open(self.log_file, 'r') as f:
            content = f.read()
        
        stats = {
            'total_lines': len(content.split('\n')),
            'error_count': content.count('ERROR'),
            'warning_count': content.count('WARNING'),
            'info_count': content.count('INFO'),
            'debug_count': content.count('DEBUG'),
            'postback_count': content.count('Postback'),
            'trade_count': content.count('TRADE'),
            'telegram_count': content.count('Telegram'),
        }
        
        return stats

# Usage
# analyzer = LogAnalyzer('logs/trading-bot.log')
# print(analyzer.get_statistics())
# print(f"Errors (24h): {analyzer.analyze_errors()}")
```

### Create Simple Dashboard

```html
<!-- monitoring/dashboard.html -->

<!DOCTYPE html>
<html>
<head>
    <title>Trading Bot Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body { font-family: Arial; background: #f5f5f5; margin: 0; padding: 20px; }
        .container { max-width: 1200px; margin: 0 auto; }
        .status { padding: 20px; border-radius: 8px; margin: 10px 0; }
        .healthy { background: #90EE90; color: #333; }
        .warning { background: #FFD700; color: #333; }
        .error { background: #FF6B6B; color: white; }
        .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
        .card { background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
    </style>
</head>
<body>
    <div class="container">
        <h1>🤖 Trading Bot Dashboard</h1>
        
        <div id="status" class="status healthy">
            <h2>Status: <span id="statusText">Healthy</span></h2>
            <p>Last Updated: <span id="lastUpdate">--:--:--</span></p>
        </div>
        
        <div class="grid">
            <div class="card">
                <h3>📊 Trading Stats</h3>
                <p>Win Rate: <strong id="winRate">--</strong></p>
                <p>Daily P&L: <strong id="dailyPnL">₹--</strong></p>
                <p>Open Positions: <strong id="openPos">--</strong></p>
            </div>
            
            <div class="card">
                <h3>⚙️ System Health</h3>
                <p>CPU: <strong id="cpuUsage">--</strong>%</p>
                <p>Memory: <strong id="memUsage">--</strong>%</p>
                <p>DhanHQ: <strong id="dhanStatus">--</strong></p>
            </div>
        </div>
        
        <div class="card">
            <canvas id="pnlChart"></canvas>
        </div>
    </div>
    
    <script>
        async function updateDashboard() {
            try {
                // Fetch status
                const statusResp = await fetch('/api/status');
                const statusData = await statusResp.json();
                
                // Fetch stats
                const statsResp = await fetch('/api/stats');
                const statsData = await statsResp.json();
                
                // Update UI
                document.getElementById('statusText').textContent = statusData.status;
                document.getElementById('lastUpdate').textContent = new Date().toLocaleTimeString();
                document.getElementById('winRate').textContent = statsData.stats.win_rate;
                document.getElementById('dailyPnL').textContent = '₹' + statsData.stats.daily_pnl.toFixed(2);
                document.getElementById('openPos').textContent = statusData.trading.open_positions;
                
                // Update health indicators
                const statusEl = document.getElementById('status');
                if (statusData.bot.telegram_connected && statusData.bot.postback_handler_ready) {
                    statusEl.className = 'status healthy';
                } else {
                    statusEl.className = 'status warning';
                }
                
            } catch (e) {
                console.error('Dashboard error:', e);
                document.getElementById('status').className = 'status error';
                document.getElementById('statusText').textContent = 'Error';
            }
        }
        
        // Update every 30 seconds
        setInterval(updateDashboard, 30000);
        updateDashboard(); // Initial load
    </script>
</body>
</html>
```

---

## Performance Metrics

### Key Performance Indicators (KPIs)

```
TRADING METRICS:
├─ Win Rate: Percentage of profitable trades
│  Target: > 55%
│
├─ Profit Factor: Total Profit / Total Loss
│  Target: > 2.0
│
├─ Average Win: Average profit per winning trade
│  Target: > Average Loss * 2
│
├─ Drawdown: Maximum loss from peak
│  Target: < 20% of account
│
└─ Sharpe Ratio: Risk-adjusted returns
   Target: > 1.5

OPERATIONAL METRICS:
├─ API Response Time: Time to execute trade
│  Target: < 1 second
│
├─ Postback Processing Time: Time to receive and process webhook
│  Target: < 500ms
│
├─ Error Rate: Percentage of failed operations
│  Target: < 0.1%
│
└─ Uptime: Percentage of time service is running
   Target: > 99.9%

SYSTEM METRICS:
├─ CPU Usage: Processor utilization
│  Target: < 50%
│
├─ Memory Usage: RAM utilization
│  Target: < 60%
│
├─ Disk Space: Free disk space
│  Target: > 10%
│
└─ Network Latency: Delay to DhanHQ
   Target: < 200ms
```

### Tracking Metrics Over Time

```python
# monitoring/metrics_tracker.py

import json
from datetime import datetime
from pathlib import Path

class MetricsTracker:
    """Track metrics over time for analysis"""
    
    def __init__(self, metrics_file='metrics/daily.json'):
        self.metrics_file = Path(metrics_file)
        self.metrics_file.parent.mkdir(exist_ok=True)
    
    def record_daily_metrics(self, stats):
        """Record daily metrics snapshot"""
        
        today = datetime.now().strftime('%Y-%m-%d')
        
        metrics = {
            'date': today,
            'timestamp': datetime.now().isoformat(),
            'win_rate': stats['win_rate'],
            'daily_pnl': stats['daily_pnl'],
            'profit_factor': stats['profit_factor'],
            'avg_win': stats['avg_win'],
            'avg_loss': stats['avg_loss'],
            'total_trades': stats['total_trades'],
        }
        
        # Append to metrics file
        data = []
        if self.metrics_file.exists():
            with open(self.metrics_file, 'r') as f:
                data = json.load(f)
        
        data.append(metrics)
        
        with open(self.metrics_file, 'w') as f:
            json.dump(data, f, indent=2)
    
    def get_trend(self, metric, days=30):
        """Get trend of metric over N days"""
        
        if not self.metrics_file.exists():
            return []
        
        with open(self.metrics_file, 'r') as f:
            data = json.load(f)
        
        # Get recent entries
        recent = data[-days:]
        
        return [
            {
                'date': d['date'],
                'value': d[metric]
            }
            for d in recent
        ]
```

---

## Alerting Rules & Thresholds

### Default Alert Rules

```yaml
CRITICAL ALERTS:
  - Service Down
    Condition: /health returns non-200
    Action: Page on-call engineer
    
  - DhanHQ Disconnected
    Condition: dhan_connected = false
    Action: Send Telegram alert, disable trading
    
  - Daily Loss Exceeded
    Condition: daily_pnl < -MAX_LOSS_PER_TRADE
    Action: Close all positions, disable trading
    
  - Telegram Bot Down
    Condition: telegram_connected = false
    Action: Alert via log and email

ERROR ALERTS:
  - API Timeout
    Condition: Response time > 5 seconds
    Frequency: Alert once per 5 minutes
    
  - High Error Rate
    Condition: Error count > 10 per hour
    Frequency: Alert once per hour
    
  - Postback Processing Failed
    Condition: Postback handler error
    Frequency: Alert immediately
    
  - Memory Critical
    Condition: Memory usage > 90%
    Frequency: Alert immediately

WARNING ALERTS:
  - High Memory Usage
    Condition: Memory > 75%
    Frequency: Alert once per 30 minutes
    
  - Low Win Rate
    Condition: Win rate < 40%
    Frequency: Alert at market close
    
  - Position Limit Approaching
    Condition: Open positions > 4
    Frequency: Alert immediately
    
  - Low Account Balance
    Condition: Balance < 200000
    Frequency: Alert once per day

INFO ALERTS:
  - Trade Executed
    Condition: New trade created
    Frequency: Every trade
    
  - Position Closed
    Condition: Trade closed by SL/Target
    Frequency: Every close
    
  - Daily Summary
    Condition: Market close time
    Frequency: Once per day
```

### Customizing Alert Thresholds

```python
# config/alert_config.py

ALERT_THRESHOLDS = {
    'health': {
        'api_response_time_max': 5.0,  # seconds
        'postback_processing_time_max': 500,  # milliseconds
    },
    'trading': {
        'max_daily_loss': -500,  # ₹
        'max_open_positions': 5,
        'min_win_rate': 0.40,  # 40%
        'min_profit_factor': 1.5,
    },
    'system': {
        'cpu_usage_max': 80,  # %
        'memory_usage_max': 75,  # %
        'memory_critical': 90,  # %
        'disk_usage_max': 90,  # %
    },
    'account': {
        'min_balance': 200000,  # ₹
        'critical_balance': 100000,  # ₹
    }
}

# Override defaults in environment or config file
# Or pass custom thresholds to alert functions
```

---

## Monitoring Checklist

- [ ] Health check endpoint working (/health)
- [ ] DhanHQ connection verified (/dhan/health)
- [ ] Telegram alerts configured and tested
- [ ] Daily P&L monitoring active
- [ ] Open position monitoring active
- [ ] System resource monitoring active
- [ ] Log rotation configured
- [ ] Error rate tracking implemented
- [ ] Win rate tracking implemented
- [ ] Profit factor tracking implemented
- [ ] Daily summary report enabled
- [ ] Render metrics accessible
- [ ] Emergency alerts configured
- [ ] Backup alert methods (email) configured
- [ ] Dashboard accessible from phone
- [ ] Logs stored for at least 30 days
- [ ] Weekly review of metrics scheduled

---

## Quick Reference

### Check Service Status
```bash
curl https://your-url/health
```

### Check Trading Stats
```bash
curl https://your-url/api/stats
```

### View Render Logs
```
Render Dashboard → Logs → Follow
```

### Send Test Alert
```bash
python -c "
from alerts.telegram_alerter import TelegramAlerter
alerter = TelegramAlerter(token, channel_id)
alerter.send_alert_sync('Test', 'Testing alerts', 'INFO')
"
```

---

## Support & Resources

- **Monitoring Dashboard:** https://your-url/dashboard
- **Render Metrics:** https://dashboard.render.com
- **DhanHQ Status:** https://status.dhan.co/
- **Telegram Bot:** @your_bot_username
- **Email:** er.bharathirajaathikkannan@gmail.com

---

**Last Updated:** September 2026  
**Maintained by:** RajaSailor  
**Version:** 1.0.0
