"""
PRACTICE TRADING MODE - PAPER TRADING SIMULATOR
Simulates real trades without using real money
File: practice_trading.py
"""

import os
import logging
from datetime import datetime
from dotenv import load_dotenv
from screener_background import screener_state, get_ist_time
import json
import threading
from pathlib import Path

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv(dotenv_path="./.env", override=True)

# ============================================================================
# PRACTICE TRADING STATE & STORAGE
# ============================================================================

PRACTICE_DATA_DIR = Path("./practice_trades")
PRACTICE_DATA_DIR.mkdir(exist_ok=True)

practice_trades = {
    "active_trades": [],      # Currently open positions
    "closed_trades": [],      # Completed trades
    "total_pnl": 0,          # Total profit/loss
    "win_rate": 0,           # Win percentage
    "balance": 100000,       # Starting capital (₹100,000)
    "portfolio_value": 100000,
    "trades_count": 0,
    "winning_trades": 0,
    "losing_trades": 0,
    "max_loss": 0,
    "max_profit": 0,
    "avg_win": 0,
    "avg_loss": 0,
    "created_at": datetime.now().isoformat(),
}

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def save_practice_trades():
    """Save practice trades to JSON file"""
    try:
        file_path = PRACTICE_DATA_DIR / "trades.json"
        with open(file_path, 'w') as f:
            json.dump(practice_trades, f, indent=2, default=str)
        logger.info(f"✅ Practice trades saved to {file_path}")
    except Exception as e:
        logger.error(f"[ERROR] Failed to save trades: {e}")

def load_practice_trades():
    """Load practice trades from JSON file"""
    global practice_trades
    try:
        file_path = PRACTICE_DATA_DIR / "trades.json"
        if file_path.exists():
            with open(file_path, 'r') as f:
                practice_trades = json.load(f)
            logger.info(f"✅ Practice trades loaded from {file_path}")
        else:
            logger.info("📝 New practice trades session initialized")
    except Exception as e:
        logger.error(f"[ERROR] Failed to load trades: {e}")

def calculate_statistics():
    """Calculate trading statistics"""
    if practice_trades["trades_count"] == 0:
        return
    
    total_trades = practice_trades["trades_count"]
    winning_trades = practice_trades["winning_trades"]
    
    # Win rate
    practice_trades["win_rate"] = (winning_trades / total_trades) * 100
    
    # Calculate average win/loss
    closed_trades = practice_trades["closed_trades"]
    if closed_trades:
        wins = [t for t in closed_trades if t["pnl"] > 0]
        losses = [t for t in closed_trades if t["pnl"] < 0]
        
        if wins:
            practice_trades["avg_win"] = sum([t["pnl"] for t in wins]) / len(wins)
        if losses:
            practice_trades["avg_loss"] = sum([t["pnl"] for t in losses]) / len(losses)

# ============================================================================
# TRADE EXECUTION FUNCTIONS
# ============================================================================

def execute_practice_call(symbol, entry_price, target_price, stop_loss_price):
    """Execute a practice CALL trade"""
    ist_time = get_ist_time()
    trade_id = f"CALL_{symbol}_{ist_time.strftime('%Y%m%d_%H%M%S')}"
    
    trade = {
        "trade_id": trade_id,
        "symbol": symbol,
        "type": "CALL",
        "entry_price": entry_price,
        "target_price": target_price,
        "stop_loss_price": stop_loss_price,
        "entry_time": ist_time.isoformat(),
        "exit_time": None,
        "exit_price": None,
        "quantity": 1,
        "pnl": 0,
        "pnl_percentage": 0,
        "status": "OPEN",
        "notes": "Paper trade - No real capital used"
    }
    
    practice_trades["active_trades"].append(trade)
    practice_trades["trades_count"] += 1
    
    logger.info(f"\n{'='*80}")
    logger.info(f"🧪 PRACTICE TRADE OPENED (CALL)")
    logger.info(f"{'='*80}")
    logger.info(f"Trade ID: {trade_id}")
    logger.info(f"Symbol: {symbol}")
    logger.info(f"Entry: ₹{entry_price:.2f}")
    logger.info(f"Target: ₹{target_price:.2f} (30% gain)")
    logger.info(f"Stop Loss: ₹{stop_loss_price:.2f} (10% loss)")
    logger.info(f"Time: {ist_time.strftime('%H:%M:%S %Z')}")
    logger.info(f"{'='*80}\n")
    
    save_practice_trades()
    return trade_id

def execute_practice_put(symbol, entry_price, target_price, stop_loss_price):
    """Execute a practice PUT trade"""
    ist_time = get_ist_time()
    trade_id = f"PUT_{symbol}_{ist_time.strftime('%Y%m%d_%H%M%S')}"
    
    trade = {
        "trade_id": trade_id,
        "symbol": symbol,
        "type": "PUT",
        "entry_price": entry_price,
        "target_price": target_price,
        "stop_loss_price": stop_loss_price,
        "entry_time": ist_time.isoformat(),
        "exit_time": None,
        "exit_price": None,
        "quantity": 1,
        "pnl": 0,
        "pnl_percentage": 0,
        "status": "OPEN",
        "notes": "Paper trade - No real capital used"
    }
    
    practice_trades["active_trades"].append(trade)
    practice_trades["trades_count"] += 1
    
    logger.info(f"\n{'='*80}")
    logger.info(f"🧪 PRACTICE TRADE OPENED (PUT)")
    logger.info(f"{'='*80}")
    logger.info(f"Trade ID: {trade_id}")
    logger.info(f"Symbol: {symbol}")
    logger.info(f"Entry: ₹{entry_price:.2f}")
    logger.info(f"Target: ₹{target_price:.2f} (30% gain)")
    logger.info(f"Stop Loss: ₹{stop_loss_price:.2f} (10% loss)")
    logger.info(f"Time: {ist_time.strftime('%H:%M:%S %Z')}")
    logger.info(f"{'='*80}\n")
    
    save_practice_trades()
    return trade_id

def close_practice_trade(trade_id, exit_price, status="TARGET_HIT"):
    """Close a practice trade and calculate P&L"""
    ist_time = get_ist_time()
    
    # Find trade
    trade = None
    for t in practice_trades["active_trades"]:
        if t["trade_id"] == trade_id:
            trade = t
            break
    
    if not trade:
        logger.error(f"[ERROR] Trade {trade_id} not found")
        return
    
    # Calculate P&L
    if trade["type"] == "CALL":
        pnl = (exit_price - trade["entry_price"]) * trade["quantity"]
        pnl_percentage = ((exit_price - trade["entry_price"]) / trade["entry_price"]) * 100
    else:  # PUT
        pnl = (trade["entry_price"] - exit_price) * trade["quantity"]
        pnl_percentage = ((trade["entry_price"] - exit_price) / trade["entry_price"]) * 100
    
    # Update trade
    trade["exit_price"] = exit_price
    trade["exit_time"] = ist_time.isoformat()
    trade["pnl"] = pnl
    trade["pnl_percentage"] = pnl_percentage
    trade["status"] = status
    
    # Move to closed trades
    practice_trades["active_trades"].remove(trade)
    practice_trades["closed_trades"].append(trade)
    
    # Update statistics
    practice_trades["total_pnl"] += pnl
    practice_trades["portfolio_value"] = practice_trades["balance"] + practice_trades["total_pnl"]
    
    if pnl > 0:
        practice_trades["winning_trades"] += 1
        if pnl > practice_trades["max_profit"]:
            practice_trades["max_profit"] = pnl
    else:
        practice_trades["losing_trades"] += 1
        if pnl < practice_trades["max_loss"]:
            practice_trades["max_loss"] = pnl
    
    # Calculate win rate
    calculate_statistics()
    
    logger.info(f"\n{'='*80}")
    logger.info(f"🧪 PRACTICE TRADE CLOSED ({status})")
    logger.info(f"{'='*80}")
    logger.info(f"Trade ID: {trade_id}")
    logger.info(f"Entry: ₹{trade['entry_price']:.2f}")
    logger.info(f"Exit: ₹{exit_price:.2f}")
    logger.info(f"P&L: ₹{pnl:.2f} ({pnl_percentage:.2f}%)")
    logger.info(f"Status: {status}")
    logger.info(f"Time: {ist_time.strftime('%H:%M:%S %Z')}")
    logger.info(f"{'='*80}\n")
    
    save_practice_trades()

# ============================================================================
# REPORT GENERATION
# ============================================================================

def generate_practice_report():
    """Generate practice trading report"""
    ist_time = get_ist_time()
    
    report = f"""
╔════════════════════════════════════════════════════════════════════════════╗
║                    🧪 PRACTICE TRADING REPORT                              ║
║                    Paper Trading - No Real Capital Used                    ║
╚════════════════════════════════════════════════════════════════════════════╝

📅 Report Generated: {ist_time.strftime('%Y-%m-%d %H:%M:%S %Z')}

┌─ 💼 ACCOUNT SUMMARY ─────────────────────────────────────────────────────┐
│  Starting Capital: ₹{practice_trades['balance']:,.2f}
│  Total P&L: ₹{practice_trades['total_pnl']:,.2f}
│  Portfolio Value: ₹{practice_trades['portfolio_value']:,.2f}
│  Return %: {(practice_trades['total_pnl']/practice_trades['balance'])*100:.2f}%
└──────────────────────────────────────────────────────────────────────────┘

┌─ 📊 TRADING STATISTICS ──────────────────────────────────────────────────┐
│  Total Trades: {practice_trades['trades_count']}
│  Winning Trades: {practice_trades['winning_trades']}
│  Losing Trades: {practice_trades['losing_trades']}
│  Win Rate: {practice_trades['win_rate']:.2f}%
│  
│  Average Win: ₹{practice_trades['avg_win']:.2f}
│  Average Loss: ₹{practice_trades['avg_loss']:.2f}
│  Max Win: ₹{practice_trades['max_profit']:.2f}
│  Max Loss: ₹{practice_trades['max_loss']:.2f}
└──────────────────────────────────────────────────────────────────────────┘

┌─ 📈 OPEN POSITIONS ({len(practice_trades['active_trades'])}) ──────────────────────┐
"""
    
    for i, trade in enumerate(practice_trades["active_trades"], 1):
        report += f"\n│  {i}. {trade['symbol']} - {trade['type']}\n"
        report += f"│     Entry: ₹{trade['entry_price']:.2f} | Target: ₹{trade['target_price']:.2f}\n"
        report += f"│     Time: {trade['entry_time']}\n"
    
    report += "\n└──────────────────────────────────────────────────────────────────────────┘\n"
    
    report += f"""
┌─ 📋 RECENT CLOSED TRADES (Last 10) ──────────────────────────────────────┐
"""
    
    recent_trades = practice_trades["closed_trades"][-10:]
    for i, trade in enumerate(recent_trades, 1):
        pnl_emoji = "✅" if trade["pnl"] > 0 else "❌"
        report += f"\n│  {i}. {pnl_emoji} {trade['symbol']} - {trade['type']} | {trade['status']}\n"
        report += f"│     Entry: ₹{trade['entry_price']:.2f} | Exit: ₹{trade['exit_price']:.2f}\n"
        report += f"│     P&L: ₹{trade['pnl']:.2f} ({trade['pnl_percentage']:.2f}%)\n"
    
    report += "\n└──────────────────────────────────────────────────────────────────────────┘\n"
    
    return report

def save_practice_report():
    """Save practice report to file"""
    try:
        report = generate_practice_report()
        file_path = PRACTICE_DATA_DIR / f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(file_path, 'w') as f:
            f.write(report)
        logger.info(f"📊 Report saved to {file_path}")
        return report
    except Exception as e:
        logger.error(f"[ERROR] Failed to save report: {e}")
        return None

# ============================================================================
# INITIALIZATION
# ============================================================================

def initialize_practice_trading():
    """Initialize practice trading"""
    logger.info("=" * 80)
    logger.info("🧪 INITIALIZING PRACTICE TRADING MODE")
    logger.info("=" * 80)
    logger.info(f"Starting Capital: ₹{practice_trades['balance']:,.2f}")
    logger.info(f"Data Directory: {PRACTICE_DATA_DIR}")
    logger.info("=" * 80)
    
    # Load existing trades if available
    load_practice_trades()
    
    logger.info("✅ Practice trading initialized successfully!")
    logger.info("📝 All trades will be simulated - No real capital used")

if __name__ == "__main__":
    initialize_practice_trading()
    print(generate_practice_report())

