# 📋 Telegram Bot Testing Scenarios - Phase 1.5

**Status**: Market Holiday (Perfect for Testing!)  
**Environment**: Render (https://trading-bot-0p7j.onrender.com)  
**Mode**: PRACTICE_MODE=true (No real money)  
**Webhook**: `/webhook/alert`

---

## 🚀 QUICK START

### 1. Verify Webhook is Running
```bash
curl -X GET https://trading-bot-0p7j.onrender.com/health

# Expected Response:
# {
#   "status": "healthy",
#   "timestamp": "2026-09-13T15:30:00+05:30",
#   "market_phase": "📊 Trading Active"
# }
```

### 2. Get System Status
```bash
curl -X GET https://trading-bot-0p7j.onrender.com/status

# Expected Response shows all subsystems ✅
```

---

## 📝 TESTING SCENARIOS

### **SCENARIO 1: Send BUY Alert (NIFTY50)**

**Objective**: Alert → Parse → Route → Create pending trade

```bash
curl -X POST https://trading-bot-0p7j.onrender.com/webhook/alert \
  -H "X-Webhook-Secret: 7kJ9mL2pQ5xR8tV1wY3nB6cD9hF2jG5kL8mN" \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "NIFTY50",
    "signal_type": "BUY",
    "entry_price": 23450.50,
    "sl": 23400.00,
    "target": 23550.00,
    "sl_type": "static",
    "qty_lots": 1,
    "strategy": "5-MIN Breakout",
    "timeframe": "5 MIN",
    "confidence": 0.85,
    "timestamp": "2026-09-13T15:30:00+05:30"
  }'
```

**Expected Response**:
```json
{
  "success": true,
  "message": "✅ Alert received and routed\nChannel: INDEX OPTIONS\nTrade: NIFTY50_BUY_1694617800\nAwaiting approval...",
  "trade_id": "NIFTY50_BUY_1694617800",
  "timestamp": "2026-09-13T15:30:00+05:30"
}
```

**What Happens**:
1. ✅ Alert parsed by `alert_formatter.py`
2. ✅ Routed to INDEX OPTIONS channel by `strategy_alerts_updated.py`
3. ✅ Pending trade created in state
4. ✅ Telegram message sent with APPROVE/REJECT buttons
5. ✅ State persisted to `state.json`

**Check Telegram**:
- Message appears in **INDEX OPTIONS** channel
- Shows: NIFTY50, Entry: 23450.50, SL: 23400.00, Target: 23550.00
- Buttons: [APPROVE] [REJECT] [EDIT SL] [SKIP]

---

### **SCENARIO 2: User Clicks APPROVE Button**

**Objective**: Trade approval → Execution → Position tracking

**Telegram Action**: Click [APPROVE] button on the alert message

**Behind the scenes**:
```
1. Telegram sends callback_query with callback_data: "approve_trade_NIFTY50_BUY_1694617800"
2. telegram_bidirectional_handler.py receives callback
3. trade_approval_system.py executes trade
4. Position created in trade_state_manager.py
5. Confirmation message sent to Telegram
6. State persisted
```

**Expected Telegram Response**:
```
✅ Trade Approved!

Position: NIFTY50_BUY_1694617800
Status: OPEN
Entry: 23450.50
SL: 23400.00
Target: 23550.00
Quantity: 1 lot

P&L: 0.00 (0%)
```

**Verify State**:
```bash
curl -X GET https://trading-bot-0p7j.onrender.com/status | jq '.stats.open_positions'

# Should show 1 open position
```

---

### **SCENARIO 3: Send GOLD BUY Alert**

**Objective**: Test commodity routing

```bash
curl -X POST https://trading-bot-0p7j.onrender.com/webhook/alert \
  -H "X-Webhook-Secret: 7kJ9mL2pQ5xR8tV1wY3nB6cD9hF2jG5kL8mN" \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "GOLD",
    "signal_type": "BUY",
    "entry_price": 7850.50,
    "sl": 7820.00,
    "target": 7920.00,
    "sl_type": "static",
    "qty_lots": 1,
    "strategy": "Premium Breakout",
    "timeframe": "15 MIN",
    "confidence": 0.80,
    "timestamp": "2026-09-13T15:35:00+05:30"
  }'
```

**Expected**: Message routed to **COMMODITY** channel

**Approve**: Click [APPROVE] button

**Result**: 2 open positions (NIFTY50 + GOLD)

---

### **SCENARIO 4: User Clicks REJECT Button**

**Objective**: Trade rejection workflow

**Telegram Action**: Click [REJECT] button on GOLD alert

**Expected Telegram Response**:
```
❌ Trade Rejected

Trade: GOLD_BUY_1694617900
Reason: User rejected
Status: CANCELLED

No position opened.
```

**Verify**: Only 1 open position now (NIFTY50)

```bash
curl -X GET https://trading-bot-0p7j.onrender.com/status | jq '.stats.open_positions'
# Should show 1
```

---

### **SCENARIO 5: Test Command - /status**

**Telegram Action**: Send `/status` command in bot chat

**Expected Response**:
```
🤖 SYSTEM STATUS

📊 Market Status: Trading Active
   Time to close: 134 minutes

📈 Open Positions: 1
   NIFTY50: +25.50 P&L

📊 Daily Stats:
   Alerts Received: 2
   Trades Executed: 1
   Trades Rejected: 1
   Total P&L: +25.50

⚙️ System: ✅ All Subsystems Ready
```

---

### **SCENARIO 6: Test Command - /help**

**Telegram Action**: Send `/help` command

**Expected Response**:
```
📋 AVAILABLE COMMANDS

/status - Get system status
/help - Show this help message
/stats - Show daily statistics
/lots <number> - Set lot size (1-5)
/mode <practice|real> - Switch trading mode
/open - Show open positions
/history - Show trade history
/balance - Show account balance
/cancel <trade_id> - Cancel pending trade
```

---

### **SCENARIO 7: Test Command - /stats**

**Telegram Action**: Send `/stats` command

**Expected Response**:
```
📊 DAILY STATISTICS

Alerts Received: 2
Trades Approved: 1
Trades Rejected: 1
Trades Executed: 1

Open Positions: 1
Closed Positions: 0

Win Rate: N/A
Total P&L: +25.50
Max Profit: +25.50
Max Loss: 0.00

Last Trade: NIFTY50 BUY @ 23450.50
```

---

### **SCENARIO 8: Simulate Position Price Update**

**Objective**: Test position tracking and P&L calculation

**Manual Simulation** (since market is closed):
```bash
# In Python shell on Render:
from trade_state_manager import get_state_manager

state = get_state_manager()

# Get open position
pos = state.open_positions[0]

# Simulate price update
pos['current_price'] = 23480.50  # +30 points
pos['pnl'] = 30.00

# Persist
state.persist_state()

# Get status to verify
curl -X GET https://trading-bot-0p7j.onrender.com/status | jq '.stats.open_positions'
```

**Expected Telegram Update** (if webhook monitoring enabled):
```
📈 Position Update

Symbol: NIFTY50
Entry: 23450.50
Current: 23480.50
P&L: +30.00 (0.13%)
SL: 23400.00 (-50.50 away)
Target: 23550.00 (+69.50 away)
```

---

### **SCENARIO 9: Simulate TARGET HIT**

**Objective**: Test target hit detection and auto-close

**Using mock_dhan_webhook.py**:
```python
from mock_dhan_webhook import MockDhanWebhook

mock = MockDhanWebhook()

# Generate target hit response
target_hit = mock.get_target_hit_response(
    position_id="NIFTY50_BUY_1694617800",
    symbol="NIFTY50",
    target_price=23550.00,
    current_price=23550.00,
    entry_price=23450.50,
    quantity=1
)

# Expected profit: 100 points
print(target_hit)
```

**Expected Telegram Response**:
```
🎯 TARGET HIT!

Position: NIFTY50_BUY_1694617800
Symbol: NIFTY50
Current: 23550.00
Entry: 23450.50
Profit: +100.00
Profit %: +0.43%

✅ Position AUTO-CLOSED at target!
```

**State**: Position moves from open_positions → closed_positions

---

### **SCENARIO 10: Simulate STOP LOSS HIT**

**Objective**: Test SL hit detection and auto-close

**Using mock_dhan_webhook.py**:
```python
from mock_dhan_webhook import MockDhanWebhook

mock = MockDhanWebhook()

# Generate SL hit response
sl_hit = mock.get_sl_hit_response(
    position_id="NIFTY50_BUY_1694617800",
    symbol="NIFTY50",
    sl_price=23400.00,
    current_price=23400.00,
    entry_price=23450.50,
    quantity=1
)

# Expected loss: 50.5 points
print(sl_hit)
```

**Expected Telegram Response**:
```
🚨 STOP LOSS HIT!

Position: NIFTY50_BUY_1694617800
Symbol: NIFTY50
Current: 23400.00
Entry: 23450.50
Loss: -50.50
Loss %: -0.22%

❌ Position AUTO-CLOSED at SL!
```

---

### **SCENARIO 11: Test 2:50 PM IST Auto-Exit**

**Objective**: Verify market close routine closes all positions

**Current time**: Manually set to 2:49 PM IST

```python
from telegram_bidirectional_handler import get_telegram_handler
from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

handler = get_telegram_handler()

# Manually trigger market close routine
closed_count, closed_ids = handler.handle_market_close_routine()

print(f"Closed {closed_count} positions: {closed_ids}")
```

**Expected Result**:
```
✅ Market Close Routine Triggered (2:50 PM IST)

Positions Closed: 1
   NIFTY50_BUY_1694617800 → CLOSED @ current price

All positions closed before market close!
```

**Telegram Notification**:
```
⏰ Market Close Routine Executed

Positions Auto-Closed: 1
   NIFTY50: Exit @ 23450.50

All positions closed for the day!
```

---

### **SCENARIO 12: Test Daily Reset (9:00 AM IST)**

**Objective**: Verify daily reset resets counters

```python
from trade_state_manager import get_state_manager

state = get_state_manager()

# Trigger daily reset
state.reset_daily_state()

# Verify counters reset
print(state.daily_stats)
```

**Expected**:
```
{
  "alerts_received": 0,
  "trades_approved": 0,
  "trades_rejected": 0,
  "trades_executed": 0,
  "total_pnl": 0.00,
  "open_positions": 0,
  "closed_positions": 0
}
```

---

## 🧪 AUTOMATED TEST RUNNER

**Run all tests at once**:

```bash
# SSH into Render
# OR run locally

python test_telegram_bot.py
```

**Expected Output**:
```
╔════════════════════════════════════════════════════╗
║         TELEGRAM BOT TEST SUITE                    ║
╚════════════════════════════════════════════════════╝

TEST 1: Alert Reception & Parsing
✅ PASSED

TEST 2: Alert Routing
✅ PASSED

TEST 3: Approval Workflow
✅ PASSED

TEST 4: Rejection Workflow
✅ PASSED

TEST 5: Command Processing
✅ PASSED

TEST 6: State Persistence
✅ PASSED

TEST 7: Position Tracking & P&L
✅ PASSED

TEST 8: Market Close Auto-Exit
✅ PASSED

╔════════════════════════════════════════════════════╗
║           TEST SUMMARY                             ║
║  Total Tests: 8                                    ║
║  ✅ Passed: 8                                      ║
║  ❌ Failed: 0                                      ║
║  🎉 ALL TESTS PASSED! 🎉                          ║
╚════════════════════════════════════════════════════╝
```

---

## ✅ CHECKLIST - All Scenarios Complete

- [ ] Scenario 1: Send NIFTY50 BUY alert
- [ ] Scenario 2: Approve trade (APPROVE button)
- [ ] Scenario 3: Send GOLD BUY alert
- [ ] Scenario 4: Reject trade (REJECT button)
- [ ] Scenario 5: Test /status command
- [ ] Scenario 6: Test /help command
- [ ] Scenario 7: Test /stats command
- [ ] Scenario 8: Simulate price update
- [ ] Scenario 9: Simulate TARGET HIT
- [ ] Scenario 10: Simulate STOP LOSS HIT
- [ ] Scenario 11: Test 2:50 PM auto-exit
- [ ] Scenario 12: Test 9:00 AM daily reset
- [ ] Run automated test suite

---

## 🐛 TROUBLESHOOTING

### Issue: Webhook returns 401 (Invalid signature)
**Fix**: Verify X-Webhook-Secret header matches env var `WEBHOOK_SECRET`
```
Expected: 7kJ9mL2pQ5xR8tV1wY3nB6cD9hF2jG5kL8mN
```

### Issue: Alert doesn't route to channel
**Fix**: Check symbol is in ALLOWED_SYMBOLS list
```python
from webhook_config import get_webhook_config
config = get_webhook_config()
print(config.ALLOWED_SYMBOLS)
```

### Issue: Telegram message not received
**Fix**: Verify bot tokens in Render env vars
```
BOT_INDEX_TOKEN=8601160697:AAFFxscCMfqcrXaf1lw69xK7Ue-RW_8aIzI
CHANNEL_INDEX_ID=-1003966854994
```

### Issue: State not persisting
**Fix**: Check file permissions and disk space
```bash
# On Render
ls -la state.json
du -sh /opt/render/
```

---

## 📊 EXPECTED RESULTS SUMMARY

| Scenario | Input | Expected Output | Status |
|----------|-------|-----------------|--------|
| 1 | NIFTY50 BUY alert | Alert routed, pending trade created | ✅ |
| 2 | APPROVE button | Trade executed, position opened | ✅ |
| 3 | GOLD BUY alert | Routed to COMMODITY channel | ✅ |
| 4 | REJECT button | Trade rejected, no position | ✅ |
| 5 | /status command | System status message | ✅ |
| 6 | /help command | Commands list | ✅ |
| 7 | /stats command | Daily statistics | ✅ |
| 8 | Price update | P&L calculated | ✅ |
| 9 | TARGET HIT | Position auto-closed, profit shown | ✅ |
| 10 | STOP LOSS HIT | Position auto-closed, loss shown | ✅ |
| 11 | 2:50 PM | All positions closed | ✅ |
| 12 | 9:00 AM | Daily counters reset | ✅ |

---

## 🎯 SUCCESS CRITERIA

✅ All 12 scenarios pass  
✅ All 8 automated tests pass  
✅ No errors in Render logs  
✅ State persists correctly  
✅ Telegram messages appear in correct channels  
✅ Commands respond correctly  
✅ Position P&L calculates correctly  

**WHEN ALL PASS → READY FOR PHASE 2!** 🚀

---

**Last Updated**: 2026-09-13  
**Testing Environment**: Render (Production)  
**Market Status**: Holiday (Perfect for Testing)  
**Next Phase**: Phase 2 - DhanHQ Integration
