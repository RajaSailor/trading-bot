"""
AUTOMATED TRADING EXECUTION - REAL TRADE EXECUTION
Automatically executes trades based on signals from screener
Integrates with DhanHQ for order placement
File: automated_trading.py
"""

import os
import logging
from datetime import datetime
from dotenv import load_dotenv
from screener_background import screener_state, get_ist_time
from practice_trading import execute_practice_call, execute_practice_put, close_practice_trade
import json
from pathlib import Path
import threading

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv(dotenv_path="./.env", override=True)

# Import DhanHQ
try:
    from dhanhq import DhanContext, dhanhq
    DHANHQ_AVAILABLE = True
except ImportError:
    DHANHQ_AVAILABLE = False
    logger.warning("[WARNING] dhanhq library not installed")

# ============================================================================
# API CREDENTIALS
# ============================================================================
CLIENT_ID = os.getenv("API_KEY")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")

# ============================================================================
# AUTO-TRADING STATE & STORAGE
# ============================================================================

AUTO_TRADE_DATA_DIR = Path("./auto_trades")
AUTO_TRADE_DATA_DIR.mkdir(exist_ok=True)

auto_trading_state = {
    "enabled": False,
    "practice_mode": True,
    "active_positions": [],
    "closed_positions": [],
    "total_capital": 500000,  # ₹5 lakhs
    "available_capital": 500000,
    "used_capital": 0,
    "total_pnl": 0,
    "trades_executed": 0,
    "winning_trades": 0,
    "losing_trades": 0,
    "win_rate": 0,
    "max_loss_percentage": 2,  # Max 2% loss per trade
    "max_positions": 5,  # Max 5 open positions
    "created_at": datetime.now().isoformat(),
}

dhan_api = None

# ============================================================================
# INITIALIZATION
# ============================================================================

def initialize_auto_trading():
    """Initialize automated trading"""
    global dhan_api
    
    logger.info("=" * 80)
    logger.info("💰 INITIALIZING AUTOMATED TRADING")
    logger.info("=" * 80)
    logger.info(f"Mode: {'🧪 PRACTICE (Paper Trading)' if auto_trading_state['practice_mode'] else '💵 LIVE (Real Money)'}")
    logger.info(f"Total Capital: ₹{auto_trading_state['total_capital']:,.2f}")
    logger.info(f"Max Positions: {auto_trading_state['max_positions']}")
    logger.info(f"Max Loss Per Trade: {auto_trading_state['max_loss_percentage']}%")
    logger.info("=" * 80)
    
    # Initialize DhanHQ if not in practice mode
    if DHANHQ_AVAILABLE and not auto_trading_state['practice_mode']:
        try:
            dhan_context = DhanContext(client_id=CLIENT_ID, access_token=ACCESS_TOKEN)
            dhan_api = dhanhq(dhan_context)
            logger.info("[SUCCESS] ✓ DhanHQ API initialized for live trading")
        except Exception as e:
            logger.error(f"[ERROR] DhanHQ initialization failed: {e}")
            auto_trading_state['practice_mode'] = True
            logger.warning("[WARNING] Falling back to practice mode")
    
    logger.info("✅ Auto-trading initialized successfully!")

# ============================================================================
# POSITION MANAGEMENT
# ============================================================================

def check_position_limits():
    """Check if we can open new positions"""
    active_count = len(auto_trading_state["active_positions"])
    
    if active_count >= auto_trading_state["max_positions"]:
        logger.warning(f"[WARNING] Max positions ({auto_trading_state['max_positions']}) reached!")
        return False
    
    return True

def calculate_position_size(entry_price):
    """Calculate position size based on capital and risk"""
    max_loss = auto_trading_state['total_capital'] * (auto_trading_state['max_loss_percentage'] / 100)
    
    # Assume 10% stop loss from entry
    risk_per_share = entry_price * 0.10
    position_size = int(max_loss / risk_per_share)
    
    return max(1, position_size)

def can_afford_position(quantity, entry_price):
    """Check if we have enough capital for position"""
    required_capital = quantity * entry_price * 0.25  # 25% margin requirement
    
    if required_capital > auto_trading_state["available_capital"]:
        logger.warning(f"[WARNING] Insufficient capital. Required: ₹{required_capital:,.2f}, Available: ₹{auto_trading_state['available_capital']:,.2f}")
        return False
    
    return True

# ============================================================================
# REAL TRADING FUNCTIONS
# ============================================================================

def place_real_call_order(symbol, strike_price, entry_price, target_price, stop_loss_price, quantity):
    """Place real CALL order via DhanHQ"""
    if not dhan_api:
        logger.error("[ERROR] DhanHQ not initialized")
        return None
    
    try:
        ist_time = get_ist_time()
        
        # Security ID mapping (would need complete list in production)
        security_ids = {
            "NIFTY": 13,
            "BANKNIFTY": 25,
            "SENSEX": 1,
        }
        
        security_id = security_ids.get(symbol, None)
        if not security_id:
            logger.error(f"[ERROR] Security ID not found for {symbol}")
            return None
        
        # Place BUY order for CALL
        order_response = dhan_api.place_order(
            security_id=security_id,
            exchange_token=0,
            transaction_type="BUY",
            quantity=quantity,
            order_type="LIMIT",
            price=entry_price,
            product_type="MIS",  # Intraday
            order_tag=f"CALL_{symbol}_{ist_time.strftime('%H%M%S')}"
        )
        
        if order_response and order_response.get('status') == 'success':
            order_id = order_response.get('data', {}).get('order_id')
            
            logger.info(f"\n{'='*80}")
            logger.info(f"💵 REAL TRADE EXECUTED (CALL)")
            logger.info(f"{'='*80}")
            logger.info(f"Symbol: {symbol}")
            logger.info(f"Order ID: {order_id}")
            logger.info(f"Entry: ₹{entry_price:.2f}")
            logger.info(f"Target: ₹{target_price:.2f}")
            logger.info(f"Stop Loss: ₹{stop_loss_price:.2f}")
            logger.info(f"Quantity: {quantity}")
            logger.info(f"Time: {ist_time.strftime('%H:%M:%S %Z')}")
            logger.info(f"{'='*80}\n")
            
            # Record position
            position = {
                "order_id": order_id,
                "symbol": symbol,
                "type": "CALL",
                "entry_price": entry_price,
                "target_price": target_price,
                "stop_loss_price": stop_loss_price,
                "quantity": quantity,
                "entry_time": ist_time.isoformat(),
                "status": "OPEN",
                "pnl": 0,
            }
            
            auto_trading_state["active_positions"].append(position)
            auto_trading_state["used_capital"] += entry_price * quantity * 0.25  # 25% margin
            auto_trading_state["available_capital"] -= entry_price * quantity * 0.25
            
            return order_id
        else:
            logger.error(f"[ERROR] Order placement failed: {order_response}")
            return None
    
    except Exception as e:
        logger.error(f"[ERROR] Exception during order placement: {e}")
        return None

def place_real_put_order(symbol, strike_price, entry_price, target_price, stop_loss_price, quantity):
    """Place real PUT order via DhanHQ"""
    if not dhan_api:
        logger.error("[ERROR] DhanHQ not initialized")
        return None
    
    try:
        ist_time = get_ist_time()
        
        # Security ID mapping
        security_ids = {
            "NIFTY": 13,
            "BANKNIFTY": 25,
            "SENSEX": 1,
        }
        
        security_id = security_ids.get(symbol, None)
        if not security_id:
            logger.error(f"[ERROR] Security ID not found for {symbol}")
            return None
        
        # Place BUY order for PUT
        order_response = dhan_api.place_order(
            security_id=security_id,
            exchange_token=0,
            transaction_type="BUY",
            quantity=quantity,
            order_type="LIMIT",
            price=entry_price,
            product_type="MIS",  # Intraday
            order_tag=f"PUT_{symbol}_{ist_time.strftime('%H%M%S')}"
        )
        
        if order_response and order_response.get('status') == 'success':
            order_id = order_response.get('data', {}).get('order_id')
            
            logger.info(f"\n{'='*80}")
            logger.info(f"💵 REAL TRADE EXECUTED (PUT)")
            logger.info(f"{'='*80}")
            logger.info(f"Symbol: {symbol}")
            logger.info(f"Order ID: {order_id}")
            logger.info(f"Entry: ₹{entry_price:.2f}")
            logger.info(f"Target: ₹{target_price:.2f}")
            logger.info(f"Stop Loss: ₹{stop_loss_price:.2f}")
            logger.info(f"Quantity: {quantity}")
            logger.info(f"Time: {ist_time.strftime('%H:%M:%S %Z')}")
            logger.info(f"{'='*80}\n")
            
            # Record position
            position = {
                "order_id": order_id,
                "symbol": symbol,
                "type": "PUT",
                "entry_price": entry_price,
                "target_price": target_price,
                "stop_loss_price": stop_loss_price,
                "quantity": quantity,
                "entry_time": ist_time.isoformat(),
                "status": "OPEN",
                "pnl": 0,
            }
            
            auto_trading_state["active_positions"].append(position)
            auto_trading_state["used_capital"] += entry_price * quantity * 0.25
            auto_trading_state["available_capital"] -= entry_price * quantity * 0.25
            
            return order_id
        else:
            logger.error(f"[ERROR] Order placement failed: {order_response}")
            return None
    
    except Exception as e:
        logger.error(f"[ERROR] Exception during order placement: {e}")
        return None

# ============================================================================
# SIGNAL PROCESSING - MAIN EXECUTION FUNCTION
# ============================================================================

def execute_signal(symbol, signal_type, entry_price, target_price, stop_loss_price, bot_type):
    """
    Execute signal based on current mode
    Routes to practice or real trading based on settings
    """
    ist_time = get_ist_time()
    
    # Check if auto-trading is enabled
    if not auto_trading_state["enabled"]:
        logger.warning(f"[WARNING] Auto-trading disabled - Signal ignored for {symbol}")
        return
    
    # Check position limits
    if not check_position_limits():
        logger.warning(f"[WARNING] Position limit reached - Signal ignored for {symbol}")
        return
    
    # Calculate position size
    quantity = calculate_position_size(entry_price)
    
    # Check capital availability
    if not can_afford_position(quantity, entry_price):
        logger.warning(f"[WARNING] Insufficient capital - Signal ignored for {symbol}")
        return
    
    logger.info(f"\n{'='*80}")
    logger.info(f"🚀 EXECUTING AUTO-TRADE SIGNAL")
    logger.info(f"{'='*80}")
    logger.info(f"Mode: {'🧪 PRACTICE' if auto_trading_state['practice_mode'] else '💵 LIVE'}")
    logger.info(f"Symbol: {symbol} | Type: {signal_type}")
    logger.info(f"Entry: ₹{entry_price:.2f} | Target: ₹{target_price:.2f} | SL: ₹{stop_loss_price:.2f}")
    logger.info(f"Quantity: {quantity} | Channel: {bot_type}")
    logger.info(f"{'='*80}\n")
    
    # Execute based on mode
    if auto_trading_state["practice_mode"]:
        # Practice mode - paper trading
        if signal_type == "CALL":
            trade_id = execute_practice_call(symbol, entry_price, target_price, stop_loss_price)
        else:  # PUT
            trade_id = execute_practice_put(symbol, entry_price, target_price, stop_loss_price)
        
        logger.info(f"📝 Practice trade created: {trade_id}")
    
    else:
        # Live mode - real trading
        strike_price = int((entry_price // 100) * 100)
        
        if signal_type == "CALL":
            order_id = place_real_call_order(symbol, strike_price, entry_price, target_price, stop_loss_price, quantity)
        else:  # PUT
            order_id = place_real_put_order(symbol, strike_price, entry_price, target_price, stop_loss_price, quantity)
        
        if order_id:
            logger.info(f"✅ Real trade executed: {order_id}")
        else:
            logger.error(f"❌ Real trade execution failed!")
    
    # Update stats
    auto_trading_state["trades_executed"] += 1
    
    # Save state
    save_auto_trading_state()

def save_auto_trading_state():
    """Save auto-trading state to file"""
    try:
        file_path = AUTO_TRADE_DATA_DIR / "state.json"
        with open(file_path, 'w') as f:
            json.dump(auto_trading_state, f, indent=2, default=str)
    except Exception as e:
        logger.error(f"[ERROR] Failed to save state: {e}")

def load_auto_trading_state():
    """Load auto-trading state from file"""
    global auto_trading_state
    try:
        file_path = AUTO_TRADE_DATA_DIR / "state.json"
        if file_path.exists():
            with open(file_path, 'r') as f:
                auto_trading_state = json.load(f)
            logger.info(f"✅ Auto-trading state loaded from {file_path}")
    except Exception as e:
        logger.error(f"[ERROR] Failed to load state: {e}")

# ============================================================================
# REPORT GENERATION
# ============================================================================

def generate_auto_trading_report():
    """Generate auto-trading performance report"""
    ist_time = get_ist_time()
    
    if auto_trading_state["trades_executed"] > 0:
        win_rate = (auto_trading_state["winning_trades"] / auto_trading_state["trades_executed"]) * 100
    else:
        win_rate = 0
    
    report = f"""
╔════════════════════════════════════════════════════════════════════════════╗
║                      💰 AUTO-TRADING PERFORMANCE REPORT                    ║
║                  {'🧪 PAPER TRADING (No Real Capital Used)' if auto_trading_state['practice_mode'] else '💵 LIVE TRADING (Real Capital)'}
╚════════════════════════════════════════════════════════════════════════════╝

📅 Report Generated: {ist_time.strftime('%Y-%m-%d %H:%M:%S %Z')}

┌─ 💼 ACCOUNT SUMMARY ─────────────────────────────────────────────────────┐
│  Total Capital: ₹{auto_trading_state['total_capital']:,.2f}
│  Used Capital: ₹{auto_trading_state['used_capital']:,.2f}
│  Available Capital: ₹{auto_trading_state['available_capital']:,.2f}
│  Total P&L: ₹{auto_trading_state['total_pnl']:,.2f}
└──────────────────────────────────────────────────────────────────────────┘

┌─ 📊 PERFORMANCE METRICS ─────────────────────────────────────────────────┐
│  Trades Executed: {auto_trading_state['trades_executed']}
│  Winning Trades: {auto_trading_state['winning_trades']}
│  Losing Trades: {auto_trading_state['losing_trades']}
│  Win Rate: {win_rate:.2f}%
│  Active Positions: {len(auto_trading_state['active_positions'])}
└──────────────────────────────────────────────────────────────────────────┘

┌─ ⚙️ SETTINGS ────────────────────────────────────────────────────────────┐
│  Mode: {'🧪 Practice (Paper)' if auto_trading_state['practice_mode'] else '💵 Live'}
│  Max Positions: {auto_trading_state['max_positions']}
│  Max Loss Per Trade: {auto_trading_state['max_loss_percentage']}%
│  Status: {'✅ ENABLED' if auto_trading_state['enabled'] else '❌ DISABLED'}
└──────────────────────────────────────────────────────────────────────────┘
"""
    return report

if __name__ == "__main__":
    initialize_auto_trading()
    print(generate_auto_trading_report())

