"""
DhanHQ Position Tracker - Real-time position monitoring, P&L calculation, and SL/Target detection.

OFFICIAL FORMAT from DhanHQ docs:
https://github.com/Kalaiviswa/dhan-api-v2-docs/blob/main/04-positions.md

Features:
- Real-time position fetching
- Live P&L calculation
- SL/Target hit detection
- Auto-close on trigger
- Position history tracking
- Daily position summary
"""

import logging
import json
from typing import Dict, Tuple, Optional, List
from datetime import datetime
from zoneinfo import ZoneInfo
from enum import Enum
import threading
import time

from dhan_api_client import DhanAPIClient

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


class PositionStatus(Enum):
    """Position statuses"""
    OPEN = "OPEN"
    SL_HIT = "SL_HIT"
    TARGET_HIT = "TARGET_HIT"
    MANUAL_CLOSE = "MANUAL_CLOSE"
    MARKET_CLOSE = "MARKET_CLOSE"
    CLOSED = "CLOSED"


class DhanPositionTracker:
    """
    Tracks live positions from DhanHQ and monitors for SL/Target hits.
    
    Responsibilities:
    - Fetch positions from DhanHQ API
    - Calculate real-time P&L
    - Monitor SL/Target prices
    - Trigger auto-closes
    - Maintain position history
    - Generate position reports
    """
    
    def __init__(self, dhan_client: DhanAPIClient):
        """
        Initialize position tracker
        
        Args:
            dhan_client: DhanAPIClient instance
        """
        self.logger = logging.getLogger(__name__)
        self.client = dhan_client
        
        # Live positions (from DhanHQ)
        self.live_positions: Dict[str, Dict] = {}
        
        # Tracked positions (with SL/Target)
        self.tracked_positions: Dict[str, Dict] = {}
        
        # Closed positions (history)
        self.closed_positions: Dict[str, Dict] = {}
        
        # SL/Target hit callbacks
        self.sl_hit_callbacks: List[callable] = []
        self.target_hit_callbacks: List[callable] = []
        
        # Tracking thread
        self.tracking_active = False
        self.tracking_thread = None
        self.tracking_interval = 5  # seconds
        
        self.logger.info("✅ DhanHQ Position Tracker initialized")
    
    # =========================================================================
    # POSITION FETCHING (OFFICIAL DhanHQ FORMAT)
    # =========================================================================
    
    def fetch_live_positions(self) -> Tuple[bool, str, List[Dict]]:
        """
        Fetch live positions from DhanHQ (Official API format)
        
        Response Format (Official):
        {
            "data": [
                {
                    "positionId": "string",
                    "symbol": "string",
                    "exchangeSegment": "NSE_FO",
                    "productType": "INTRADAY",
                    "quantity": number,
                    "buyQuantity": number,
                    "sellQuantity": number,
                    "entryPrice": number,
                    "currentPrice": number,
                    "pnl": number,
                    "pnlPercentage": number,
                    "intraday_pnl": number,
                    "mtm": number
                }
            ]
        }
        
        Returns:
            (success, message, positions_list)
        """
        try:
            self.logger.info("📊 Fetching live positions from DhanHQ...")
            
            success, msg, positions = self.client.get_positions()
            
            if success:
                # Update live positions
                for pos in positions:
                    pos_id = pos.get("positionId")
                    
                    # Track only if we have SL/Target for it
                    if pos_id in self.tracked_positions:
                        tracked = self.tracked_positions[pos_id]
                        
                        # Update current price and P&L
                        tracked["currentPrice"] = pos.get("currentPrice")
                        tracked["currentPnL"] = pos.get("pnl")
                        tracked["currentPnLPercent"] = pos.get("pnlPercentage")
                        tracked["lastUpdateTime"] = datetime.now(IST).isoformat()
                        
                        self.live_positions[pos_id] = pos
                        
                        self.logger.info(
                            f"   {tracked['symbol']}: "
                            f"Q:{pos.get('quantity')} "
                            f"@ ₹{pos.get('currentPrice')} "
                            f"P&L: ₹{pos.get('pnl')} "
                            f"({pos.get('pnlPercentage')}%)"
                        )
                
                return True, f"Fetched {len(positions)} position(s)", positions
            else:
                return False, msg, []
            
        except Exception as e:
            self.logger.error(f"❌ Position fetch error: {e}")
            return False, f"Error: {e}", []
    
    # =========================================================================
    # POSITION TRACKING
    # =========================================================================
    
    def add_tracked_position(
        self,
        order_id: str,
        symbol: str,
        transaction_type: str,
        quantity: int,
        entry_price: float,
        current_price: float,
        sl_price: float,
        target_price: float,
        strategy: str = ""
    ) -> Tuple[bool, str]:
        """
        Add a position to track for SL/Target hits
        
        Args:
            order_id: Order ID / Position ID
            symbol: Trading symbol
            transaction_type: BUY or SELL
            quantity: Position quantity
            entry_price: Entry price
            current_price: Current market price
            sl_price: Stop loss price
            target_price: Target price
            strategy: Strategy name
            
        Returns:
            (success, message)
        """
        try:
            self.logger.info(f"📍 Adding tracked position: {order_id}")
            
            position = {
                "positionId": order_id,
                "symbol": symbol,
                "transactionType": transaction_type,
                "quantity": quantity,
                "entryPrice": entry_price,
                "currentPrice": current_price,
                "slPrice": sl_price,
                "targetPrice": target_price,
                "strategy": strategy,
                "status": PositionStatus.OPEN.value,
                "createdAt": datetime.now(IST).isoformat(),
                "lastUpdateTime": datetime.now(IST).isoformat(),
                "currentPnL": (current_price - entry_price) * quantity,
                "currentPnLPercent": ((current_price - entry_price) / entry_price * 100),
                "slHitTime": None,
                "targetHitTime": None,
                "closeTime": None,
                "closePrice": None,
                "finalPnL": 0.0,
                "closeReason": None
            }
            
            self.tracked_positions[order_id] = position
            
            self.logger.info(f"✅ Position tracked")
            self.logger.info(f"   Symbol: {symbol}")
            self.logger.info(f"   Type: {transaction_type}")
            self.logger.info(f"   Entry: ₹{entry_price}")
            self.logger.info(f"   SL: ₹{sl_price}")
            self.logger.info(f"   Target: ₹{target_price}")
            self.logger.info(f"   Current P&L: ₹{position['currentPnL']:.2f}")
            
            return True, f"Position {order_id} added to tracker"
            
        except Exception as e:
            self.logger.error(f"❌ Add position error: {e}")
            return False, f"Error: {e}"
    
    # =========================================================================
    # SL/TARGET DETECTION
    # =========================================================================
    
    def check_sl_hit(self, position: Dict) -> bool:
        """
        Check if stop loss is hit
        
        Args:
            position: Position dict
            
        Returns:
            True if SL hit, False otherwise
        """
        entry = position["entryPrice"]
        current = position["currentPrice"]
        sl = position["slPrice"]
        trans_type = position["transactionType"]
        
        # For BUY: check if current price fell below SL
        if trans_type == "BUY":
            return current <= sl
        
        # For SELL: check if current price rose above SL
        else:  # SELL
            return current >= sl
    
    def check_target_hit(self, position: Dict) -> bool:
        """
        Check if target is hit
        
        Args:
            position: Position dict
            
        Returns:
            True if target hit, False otherwise
        """
        current = position["currentPrice"]
        target = position["targetPrice"]
        trans_type = position["transactionType"]
        
        # For BUY: check if current price reached target
        if trans_type == "BUY":
            return current >= target
        
        # For SELL: check if current price reached target
        else:  # SELL
            return current <= target
    
    def monitor_positions(self) -> Tuple[List[str], List[str]]:
        """
        Monitor all tracked positions for SL/Target hits
        
        Returns:
            (sl_hit_positions, target_hit_positions)
        """
        sl_hits = []
        target_hits = []
        
        try:
            # Fetch live positions
            success, msg, positions = self.fetch_live_positions()
            
            if not success:
                self.logger.warning(f"⚠️ Cannot fetch positions: {msg}")
                return sl_hits, target_hits
            
            # Check each tracked position
            for pos_id, tracked_pos in self.tracked_positions.items():
                if tracked_pos["status"] != PositionStatus.OPEN.value:
                    continue  # Skip closed positions
                
                # Check target first (better to close at profit)
                if self.check_target_hit(tracked_pos):
                    self.logger.info(f"🎯 TARGET HIT: {pos_id}")
                    self._close_position(
                        pos_id,
                        tracked_pos["targetPrice"],
                        PositionStatus.TARGET_HIT
                    )
                    target_hits.append(pos_id)
                    
                    # Call callbacks
                    for callback in self.target_hit_callbacks:
                        try:
                            callback(tracked_pos)
                        except Exception as e:
                            self.logger.error(f"Callback error: {e}")
                
                # Check SL
                elif self.check_sl_hit(tracked_pos):
                    self.logger.info(f"🚨 STOP LOSS HIT: {pos_id}")
                    self._close_position(
                        pos_id,
                        tracked_pos["slPrice"],
                        PositionStatus.SL_HIT
                    )
                    sl_hits.append(pos_id)
                    
                    # Call callbacks
                    for callback in self.sl_hit_callbacks:
                        try:
                            callback(tracked_pos)
                        except Exception as e:
                            self.logger.error(f"Callback error: {e}")
            
            return sl_hits, target_hits
            
        except Exception as e:
            self.logger.error(f"❌ Monitor error: {e}")
            return sl_hits, target_hits
    
    # =========================================================================
    # POSITION CLOSING
    # =========================================================================
    
    def _close_position(
        self,
        pos_id: str,
        close_price: float,
        close_reason: PositionStatus
    ) -> Tuple[bool, str, float]:
        """
        Close a position and calculate final P&L
        
        Args:
            pos_id: Position ID
            close_price: Price at which position closes
            close_reason: Reason for closing
            
        Returns:
            (success, message, final_pnl)
        """
        try:
            if pos_id not in self.tracked_positions:
                return False, f"Position {pos_id} not found", 0.0
            
            position = self.tracked_positions[pos_id]
            
            # Calculate final P&L
            entry = position["entryPrice"]
            qty = position["quantity"]
            trans_type = position["transactionType"]
            
            if trans_type == "BUY":
                final_pnl = (close_price - entry) * qty
            else:  # SELL
                final_pnl = (entry - close_price) * qty
            
            # Update position
            position["status"] = PositionStatus.CLOSED.value
            position["closeTime"] = datetime.now(IST).isoformat()
            position["closePrice"] = close_price
            position["finalPnL"] = final_pnl
            position["closeReason"] = close_reason.value
            
            # Move to closed positions
            self.closed_positions[pos_id] = position
            del self.tracked_positions[pos_id]
            
            self.logger.info(f"✅ Position closed: {pos_id}")
            self.logger.info(f"   Entry: ₹{entry}")
            self.logger.info(f"   Close: ₹{close_price}")
            self.logger.info(f"   Final P&L: ₹{final_pnl:.2f}")
            self.logger.info(f"   Reason: {close_reason.value}")
            
            return True, f"Position closed with P&L ₹{final_pnl:.2f}", final_pnl
            
        except Exception as e:
            self.logger.error(f"❌ Close position error: {e}")
            return False, f"Error: {e}", 0.0
    
    def close_position_manual(
        self,
        pos_id: str,
        close_price: float
    ) -> Tuple[bool, str, float]:
        """
        Manually close a position
        
        Args:
            pos_id: Position ID
            close_price: Close price
            
        Returns:
            (success, message, pnl)
        """
        return self._close_position(
            pos_id,
            close_price,
            PositionStatus.MANUAL_CLOSE
        )
    
    def close_all_positions(self, close_price_map: Dict[str, float]) -> Tuple[int, float]:
        """
        Close all open positions (market close routine)
        
        Args:
            close_price_map: Dict mapping position_id to close_price
            
        Returns:
            (closed_count, total_pnl)
        """
        closed_count = 0
        total_pnl = 0.0
        
        pos_ids = list(self.tracked_positions.keys())
        
        for pos_id in pos_ids:
            close_price = close_price_map.get(pos_id)
            if close_price:
                success, msg, pnl = self._close_position(
                    pos_id,
                    close_price,
                    PositionStatus.MARKET_CLOSE
                )
                
                if success:
                    closed_count += 1
                    total_pnl += pnl
        
        self.logger.info(f"🏁 Market close: {closed_count} positions closed")
        self.logger.info(f"   Total P&L: ₹{total_pnl:.2f}")
        
        return closed_count, total_pnl
    
    # =========================================================================
    # BACKGROUND MONITORING
    # =========================================================================
    
    def start_monitoring(self, interval: int = 5) -> None:
        """
        Start background position monitoring thread
        
        Args:
            interval: Monitoring interval in seconds
        """
        if self.tracking_active:
            self.logger.warning("Monitoring already active")
            return
        
        self.tracking_interval = interval
        self.tracking_active = True
        
        self.tracking_thread = threading.Thread(
            target=self._monitoring_loop,
            daemon=True
        )
        self.tracking_thread.start()
        
        self.logger.info(f"✅ Position monitoring started (interval: {interval}s)")
    
    def stop_monitoring(self) -> None:
        """Stop background monitoring"""
        self.tracking_active = False
        
        if self.tracking_thread:
            self.tracking_thread.join(timeout=5)
        
        self.logger.info("🛑 Position monitoring stopped")
    
    def _monitoring_loop(self) -> None:
        """Background monitoring loop"""
        while self.tracking_active:
            try:
                sl_hits, target_hits = self.monitor_positions()
                
                if sl_hits or target_hits:
                    self.logger.info(
                        f"📊 Monitor cycle: "
                        f"SL hits: {len(sl_hits)}, "
                        f"Target hits: {len(target_hits)}"
                    )
                
                time.sleep(self.tracking_interval)
                
            except Exception as e:
                self.logger.error(f"❌ Monitoring loop error: {e}")
                time.sleep(self.tracking_interval)
    
    # =========================================================================
    # CALLBACKS
    # =========================================================================
    
    def on_sl_hit(self, callback: callable) -> None:
        """Register callback for SL hit events"""
        self.sl_hit_callbacks.append(callback)
    
    def on_target_hit(self, callback: callable) -> None:
        """Register callback for target hit events"""
        self.target_hit_callbacks.append(callback)
    
    # =========================================================================
    # REPORTING
    # =========================================================================
    
    def get_open_positions(self) -> List[Dict]:
        """Get all open positions"""
        return list(self.tracked_positions.values())
    
    def get_closed_positions(self) -> List[Dict]:
        """Get all closed positions"""
        return list(self.closed_positions.values())
    
    def get_daily_pnl(self) -> float:
        """Calculate total daily P&L"""
        total = 0.0
        
        for pos in self.closed_positions.values():
            total += pos.get("finalPnL", 0.0)
        
        return total
    
    def get_position_summary(self) -> Dict:
        """Get position summary"""
        open_count = len(self.tracked_positions)
        closed_count = len(self.closed_positions)
        daily_pnl = self.get_daily_pnl()
        
        # Calculate win rate
        wins = sum(1 for p in self.closed_positions.values() if p.get("finalPnL", 0) > 0)
        total_closed = len(self.closed_positions)
        win_rate = (wins / total_closed * 100) if total_closed > 0 else 0
        
        return {
            "openPositions": open_count,
            "closedPositions": closed_count,
            "totalPositions": open_count + closed_count,
            "dailyPnL": daily_pnl,
            "winRate": f"{win_rate:.1f}%",
            "wins": wins,
            "losses": total_closed - wins
        }
    
    def print_summary(self) -> None:
        """Print position tracker summary"""
        self.logger.info("\n" + "="*60)
        self.logger.info("POSITION TRACKER SUMMARY")
        self.logger.info("="*60)
        
        summary = self.get_position_summary()
        
        self.logger.info(f"Open Positions: {summary['openPositions']}")
        for pos_id, pos in self.tracked_positions.items():
            self.logger.info(
                f"  {pos['symbol']}: "
                f"Entry ₹{pos['entryPrice']} @ ₹{pos['currentPrice']} "
                f"P&L ₹{pos['currentPnL']:.2f}"
            )
        
        self.logger.info(f"\nClosed Positions: {summary['closedPositions']}")
        self.logger.info(f"Wins: {summary['wins']} | Losses: {summary['losses']}")
        self.logger.info(f"Win Rate: {summary['winRate']}")
        
        self.logger.info(f"\nDaily P&L: ₹{summary['dailyPnL']:.2f}")


# ============================================================================
# SINGLETON INSTANCE
# ============================================================================

_position_tracker: Optional[DhanPositionTracker] = None


def get_position_tracker(
    dhan_client: DhanAPIClient = None
) -> DhanPositionTracker:
    """
    Get or create DhanPositionTracker instance
    
    Args:
        dhan_client: DhanAPIClient instance
        
    Returns:
        DhanPositionTracker instance
    """
    global _position_tracker
    
    if _position_tracker is None:
        from dhan_api_client import get_dhan_client
        
        if dhan_client is None:
            dhan_client = get_dhan_client()
        
        _position_tracker = DhanPositionTracker(dhan_client=dhan_client)
    
    return _position_tracker
