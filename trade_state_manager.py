"""
Trade State Manager - Track all pending trades, open positions, and system state.

Manages:
- Pending trade storage and lifecycle
- Open positions tracking
- Closed positions history
- Trade constraints validation
- State persistence to disk
- Daily statistics
"""

import logging
import json
import os
from typing import Dict, List, Optional
from datetime import datetime, time as dt_time
from dataclasses import dataclass, asdict
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# IST timezone
IST = ZoneInfo("Asia/Kolkata")

# State file paths
STATE_DIR = "trade_state"
PENDING_TRADES_FILE = os.path.join(STATE_DIR, "pending_trades.json")
OPEN_POSITIONS_FILE = os.path.join(STATE_DIR, "open_positions.json")
CLOSED_POSITIONS_FILE = os.path.join(STATE_DIR, "closed_positions.json")
SYSTEM_STATE_FILE = os.path.join(STATE_DIR, "system_state.json")


@dataclass
class PendingTrade:
    """Pending trade awaiting user approval"""
    trade_id: str
    symbol: str
    entry_price: float
    sl_type: str  # "static", "c2c", "trail"
    sl_value: float
    target: float
    qty_lots: int
    strategy: str
    timeframe: str
    channel_id: int
    message_id: int
    created_at: str
    approval_status: str  # "pending", "approved", "rejected"
    expires_at: str
    callback_data: str


@dataclass
class OpenPosition:
    """Open trading position"""
    position_id: str
    trade_id: str
    symbol: str
    entry_price: float
    entry_time: str
    qty_lots: int
    sl: float
    target: float
    trail_sl: float
    current_price: float
    pnl: float
    status: str  # "open", "partial_close", "closed"
    mode: str  # "practice", "real"
    created_at: str
    updated_at: str


@dataclass
class ClosedPosition:
    """Closed trading position (history)"""
    position_id: str
    symbol: str
    entry_price: float
    exit_price: float
    qty_lots: int
    pnl: float
    mode: str
    entry_time: str
    exit_time: str
    hold_duration: str
    exit_reason: str  # "target", "sl", "manual", "market_close"


class TradeStateManager:
    """Manage all trading state - pending, open, closed positions"""
    
    def __init__(self):
        """Initialize state manager"""
        self.logger = logging.getLogger(__name__)
        self._ensure_state_dir()
        
        # In-memory state
        self.pending_trades: Dict[str, PendingTrade] = {}
        self.open_positions: Dict[str, OpenPosition] = {}
        self.closed_positions: List[ClosedPosition] = []
        self.system_state = {
            "current_mode": "practice",  # or "real"
            "max_open_positions": 5,
            "max_trades_per_day": 20,
            "min_sl_points": 5,
            "trading_active": True,
            "lot_size": 1,
            "daily_trades_count": 0,
            "daily_pnl": 0.0,
            "last_reset": datetime.now(IST).isoformat()
        }
        
        # Load state from disk
        self.load_state()
        self.logger.info("✅ Trade State Manager initialized")
    
    def _ensure_state_dir(self) -> None:
        """Ensure state directory exists"""
        if not os.path.exists(STATE_DIR):
            os.makedirs(STATE_DIR)
            self.logger.info(f"✅ Created state directory: {STATE_DIR}")
    
    # =========================================================================
    # PENDING TRADE MANAGEMENT
    # =========================================================================
    
    def create_pending_trade(
        self,
        trade_id: str,
        symbol: str,
        entry_price: float,
        sl_type: str,
        sl_value: float,
        target: float,
        qty_lots: int,
        strategy: str,
        timeframe: str,
        channel_id: int,
        message_id: int,
        callback_data: str,
        expires_in_seconds: int = 300
    ) -> PendingTrade:
        """
        Create and store a pending trade
        
        Args:
            trade_id: Unique trade identifier
            symbol: Trading symbol
            entry_price: Entry price level
            sl_type: Stop loss type ("static", "c2c", "trail")
            sl_value: Stop loss value
            target: Target level
            qty_lots: Quantity in lots
            strategy: Strategy name
            timeframe: Timeframe
            channel_id: Telegram channel ID
            message_id: Telegram message ID
            callback_data: Callback identifier
            expires_in_seconds: Auto-reject after this many seconds (default 5 min)
            
        Returns:
            Created PendingTrade object
        """
        now = datetime.now(IST)
        expires_at = (now.timestamp() + expires_in_seconds)
        
        trade = PendingTrade(
            trade_id=trade_id,
            symbol=symbol,
            entry_price=entry_price,
            sl_type=sl_type,
            sl_value=sl_value,
            target=target,
            qty_lots=qty_lots,
            strategy=strategy,
            timeframe=timeframe,
            channel_id=channel_id,
            message_id=message_id,
            created_at=now.isoformat(),
            approval_status="pending",
            expires_at=datetime.fromtimestamp(expires_at, IST).isoformat(),
            callback_data=callback_data
        )
        
        self.pending_trades[trade_id] = trade
        self.logger.info(f"✅ Pending trade created: {trade_id}")
        return trade
    
    def get_pending_trade(self, trade_id: str) -> Optional[PendingTrade]:
        """Get pending trade by ID"""
        return self.pending_trades.get(trade_id)
    
    def approve_trade(self, trade_id: str) -> Optional[PendingTrade]:
        """
        Mark trade as approved
        
        Args:
            trade_id: Trade to approve
            
        Returns:
            Updated trade or None if not found
        """
        trade = self.pending_trades.get(trade_id)
        if trade:
            trade.approval_status = "approved"
            self.logger.info(f"✅ Trade approved: {trade_id}")
            return trade
        return None
    
    def reject_trade(self, trade_id: str) -> Optional[PendingTrade]:
        """
        Mark trade as rejected
        
        Args:
            trade_id: Trade to reject
            
        Returns:
            Updated trade or None if not found
        """
        trade = self.pending_trades.get(trade_id)
        if trade:
            trade.approval_status = "rejected"
            self.logger.info(f"❌ Trade rejected: {trade_id}")
            return trade
        return None
    
    def remove_pending_trade(self, trade_id: str) -> None:
        """Remove pending trade (after execution or timeout)"""
        if trade_id in self.pending_trades:
            del self.pending_trades[trade_id]
            self.logger.info(f"🗑️ Pending trade removed: {trade_id}")
    
    def check_expired_trades(self) -> List[str]:
        """
        Check for expired pending trades and auto-reject them
        
        Returns:
            List of expired trade IDs
        """
        now = datetime.now(IST).timestamp()
        expired = []
        
        for trade_id, trade in list(self.pending_trades.items()):
            expires_at = datetime.fromisoformat(trade.expires_at).timestamp()
            if now > expires_at and trade.approval_status == "pending":
                self.reject_trade(trade_id)
                expired.append(trade_id)
                self.logger.warning(f"⏰ Trade auto-rejected (expired): {trade_id}")
        
        return expired
    
    # =========================================================================
    # OPEN POSITION MANAGEMENT
    # =========================================================================
    
    def create_open_position(
        self,
        position_id: str,
        trade_id: str,
        symbol: str,
        entry_price: float,
        qty_lots: int,
        sl: float,
        target: float,
        trail_sl: float = 0.0,
        mode: str = "practice"
    ) -> OpenPosition:
        """
        Create and open a new position
        
        Args:
            position_id: Unique position identifier
            trade_id: Source trade ID
            symbol: Trading symbol
            entry_price: Entry price
            qty_lots: Lot quantity
            sl: Stop loss level
            target: Target level
            trail_sl: Trailing SL value (optional)
            mode: "practice" or "real"
            
        Returns:
            Created OpenPosition object
        """
        now = datetime.now(IST)
        
        position = OpenPosition(
            position_id=position_id,
            trade_id=trade_id,
            symbol=symbol,
            entry_price=entry_price,
            entry_time=now.isoformat(),
            qty_lots=qty_lots,
            sl=sl,
            target=target,
            trail_sl=trail_sl,
            current_price=entry_price,
            pnl=0.0,
            status="open",
            mode=mode,
            created_at=now.isoformat(),
            updated_at=now.isoformat()
        )
        
        self.open_positions[position_id] = position
        self.system_state["daily_trades_count"] += 1
        self.logger.info(f"✅ Position opened: {position_id} ({symbol} @ {entry_price})")
        return position
    
    def get_open_position(self, position_id: str) -> Optional[OpenPosition]:
        """Get open position by ID"""
        return self.open_positions.get(position_id)
    
    def update_position_price(self, position_id: str, current_price: float) -> Optional[OpenPosition]:
        """Update position current price and calculate P&L"""
        position = self.open_positions.get(position_id)
        if position:
            position.current_price = current_price
            position.pnl = (current_price - position.entry_price) * position.qty_lots * 100  # Approx
            position.updated_at = datetime.now(IST).isoformat()
            return position
        return None
    
    def close_position(
        self,
        position_id: str,
        exit_price: float,
        exit_reason: str = "manual"
    ) -> Optional[ClosedPosition]:
        """
        Close an open position and move to closed history
        
        Args:
            position_id: Position to close
            exit_price: Exit price
            exit_reason: Why closed ("target", "sl", "manual", "market_close")
            
        Returns:
            ClosedPosition object or None if not found
        """
        position = self.open_positions.get(position_id)
        if not position:
            return None
        
        exit_time = datetime.now(IST)
        entry_time = datetime.fromisoformat(position.entry_time)
        hold_duration = str(exit_time - entry_time)
        
        pnl = (exit_price - position.entry_price) * position.qty_lots * 100
        
        closed = ClosedPosition(
            position_id=position_id,
            symbol=position.symbol,
            entry_price=position.entry_price,
            exit_price=exit_price,
            qty_lots=position.qty_lots,
            pnl=pnl,
            mode=position.mode,
            entry_time=position.entry_time,
            exit_time=exit_time.isoformat(),
            hold_duration=hold_duration,
            exit_reason=exit_reason
        )
        
        self.closed_positions.append(closed)
        del self.open_positions[position_id]
        self.system_state["daily_pnl"] += pnl
        
        self.logger.info(f"✅ Position closed: {position_id} ({position.symbol}) P&L: {pnl}")
        return closed
    
    def get_all_open_positions(self) -> List[OpenPosition]:
        """Get all open positions"""
        return list(self.open_positions.values())
    
    def get_open_positions_by_symbol(self, symbol: str) -> List[OpenPosition]:
        """Get all open positions for a symbol"""
        return [p for p in self.open_positions.values() if p.symbol == symbol]
    
    # =========================================================================
    # CONSTRAINT VALIDATION
    # =========================================================================
    
    def validate_trade_constraints(self, symbol: str, sl_points: float) -> Tuple[bool, str]:
        """
        Validate trade against all constraints
        
        Args:
            symbol: Trading symbol
            sl_points: Stop loss points
            
        Returns:
            (is_valid, error_message)
        """
        # Check if trading is active
        if not self.system_state["trading_active"]:
            return False, "❌ Trading is currently disabled"
        
        # Check max open positions
        if len(self.open_positions) >= self.system_state["max_open_positions"]:
            return False, f"❌ Max open positions ({self.system_state['max_open_positions']}) reached"
        
        # Check max trades per day
        if self.system_state["daily_trades_count"] >= self.system_state["max_trades_per_day"]:
            return False, f"❌ Max trades per day ({self.system_state['max_trades_per_day']}) reached"
        
        # Check minimum SL points
        if sl_points < self.system_state["min_sl_points"]:
            return False, f"❌ SL must be at least {self.system_state['min_sl_points']} points"
        
        # Check market hours (no trades after 2:50 PM IST)
        now = datetime.now(IST).time()
        if now >= dt_time(14, 50):  # 2:50 PM
            return False, "❌ No new trades allowed after 2:50 PM IST"
        
        return True, "✅ All constraints passed"
    
    # =========================================================================
    # DAILY STATISTICS
    # =========================================================================
    
    def get_daily_stats(self) -> Dict:
        """Get daily trading statistics"""
        total_pnl = self.system_state["daily_pnl"]
        closed_today = len(self.closed_positions)
        open_now = len(self.open_positions)
        
        stats = {
            "mode": self.system_state["current_mode"],
            "trading_active": self.system_state["trading_active"],
            "trades_executed": self.system_state["daily_trades_count"],
            "max_trades": self.system_state["max_trades_per_day"],
            "trades_remaining": self.system_state["max_trades_per_day"] - self.system_state["daily_trades_count"],
            "open_positions": open_now,
            "max_positions": self.system_state["max_open_positions"],
            "closed_positions": closed_today,
            "daily_pnl": total_pnl,
            "lot_size": self.system_state["lot_size"],
            "last_updated": datetime.now(IST).isoformat()
        }
        return stats
    
    def get_current_state(self) -> Dict:
        """Get complete current system state"""
        return {
            "system_state": self.system_state,
            "pending_trades": {k: asdict(v) for k, v in self.pending_trades.items()},
            "open_positions": {k: asdict(v) for k, v in self.open_positions.items()},
            "closed_positions": [asdict(p) for p in self.closed_positions],
            "timestamp": datetime.now(IST).isoformat()
        }
    
    # =========================================================================
    # MODE & CONFIGURATION
    # =========================================================================
    
    def set_mode(self, mode: str) -> None:
        """Set trading mode (practice or real)"""
        if mode in ["practice", "real"]:
            self.system_state["current_mode"] = mode
            self.logger.info(f"✅ Mode set to: {mode.upper()}")
        else:
            self.logger.error(f"❌ Invalid mode: {mode}")
    
    def set_trading_active(self, active: bool) -> None:
        """Enable or disable trading"""
        self.system_state["trading_active"] = active
        status = "ENABLED" if active else "DISABLED"
        self.logger.info(f"✅ Trading {status}")
    
    def set_lot_size(self, lots: int) -> None:
        """Set lot size"""
        if lots > 0:
            self.system_state["lot_size"] = lots
            self.logger.info(f"✅ Lot size set to: {lots}")
        else:
            self.logger.error("❌ Lot size must be > 0")
    
    def set_max_positions(self, max_pos: int) -> None:
        """Set max open positions"""
        self.system_state["max_open_positions"] = max_pos
        self.logger.info(f"✅ Max open positions set to: {max_pos}")
    
    def set_max_trades(self, max_trades: int) -> None:
        """Set max trades per day"""
        self.system_state["max_trades_per_day"] = max_trades
        self.logger.info(f"✅ Max trades per day set to: {max_trades}")
    
    # =========================================================================
    # PERSISTENCE
    # =========================================================================
    
    def persist_state(self) -> None:
        """Save all state to disk"""
        try:
            self._ensure_state_dir()
            
            # Save pending trades
            with open(PENDING_TRADES_FILE, "w") as f:
                json.dump(
                    {k: asdict(v) for k, v in self.pending_trades.items()},
                    f,
                    indent=2,
                    default=str
                )
            
            # Save open positions
            with open(OPEN_POSITIONS_FILE, "w") as f:
                json.dump(
                    {k: asdict(v) for k, v in self.open_positions.items()},
                    f,
                    indent=2,
                    default=str
                )
            
            # Save closed positions
            with open(CLOSED_POSITIONS_FILE, "w") as f:
                json.dump(
                    [asdict(p) for p in self.closed_positions],
                    f,
                    indent=2,
                    default=str
                )
            
            # Save system state
            with open(SYSTEM_STATE_FILE, "w") as f:
                json.dump(
                    self.system_state,
                    f,
                    indent=2,
                    default=str
                )
            
            self.logger.info("💾 State persisted to disk")
            
        except Exception as e:
            self.logger.error(f"❌ Error persisting state: {e}")
    
    def load_state(self) -> None:
        """Load state from disk"""
        try:
            # Load pending trades
            if os.path.exists(PENDING_TRADES_FILE):
                with open(PENDING_TRADES_FILE, "r") as f:
                    trades_dict = json.load(f)
                    self.pending_trades = {k: PendingTrade(**v) for k, v in trades_dict.items()}
            
            # Load open positions
            if os.path.exists(OPEN_POSITIONS_FILE):
                with open(OPEN_POSITIONS_FILE, "r") as f:
                    positions_dict = json.load(f)
                    self.open_positions = {k: OpenPosition(**v) for k, v in positions_dict.items()}
            
            # Load closed positions
            if os.path.exists(CLOSED_POSITIONS_FILE):
                with open(CLOSED_POSITIONS_FILE, "r") as f:
                    positions_list = json.load(f)
                    self.closed_positions = [ClosedPosition(**p) for p in positions_list]
            
            # Load system state
            if os.path.exists(SYSTEM_STATE_FILE):
                with open(SYSTEM_STATE_FILE, "r") as f:
                    self.system_state = json.load(f)
            
            self.logger.info("✅ State loaded from disk")
            
        except Exception as e:
            self.logger.error(f"❌ Error loading state: {e}")
    
    def reset_daily_state(self) -> None:
        """Reset daily counters (call at 9:00 AM IST)"""
        self.system_state["daily_trades_count"] = 0
        self.system_state["daily_pnl"] = 0.0
        self.system_state["last_reset"] = datetime.now(IST).isoformat()
        self.logger.info("✅ Daily state reset")


# Singleton instance
_state_manager: Optional[TradeStateManager] = None


def get_state_manager() -> TradeStateManager:
    """Get or create state manager instance"""
    global _state_manager
    if _state_manager is None:
        _state_manager = TradeStateManager()
    return _state_manager
