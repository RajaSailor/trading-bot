"""
DhanHQ Integration Master - Orchestrates all DhanHQ modules for trading.

Connects:
- dhan_api_client.py (API communication)
- dhan_order_manager.py (Order creation & management)
- dhan_position_tracker.py (Position monitoring)

Features:
- Unified trading interface
- Safety circuit breakers
- Auto-recovery mechanisms
- Comprehensive logging
- State persistence
"""

import logging
import json
import base64
from typing import Dict, Tuple, Optional, List
from datetime import datetime
from zoneinfo import ZoneInfo
import os

from dhan_api_client import (
    DhanAPIClient, ExchangeSegment, OrderType,
    TransactionType, ProductType, get_dhan_client
)
from dhan_order_manager import DhanOrderManager, get_order_manager
from dhan_position_tracker import DhanPositionTracker, get_position_tracker

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


class DhanIntegration:
    """
    Master integration for DhanHQ trading.
    
    Coordinates:
    1. API Client - Communication with DhanHQ
    2. Order Manager - Order lifecycle
    3. Position Tracker - Live position monitoring
    
    Safety Features:
    - Pre-trade validation
    - Circuit breakers
    - Error recovery
    - State persistence
    - Comprehensive logging
    """
    
    def __init__(
        self,
        access_token: str = None,
        client_id: str = None,
        practice_mode: bool = True,
        max_loss_per_trade: float = 500.0,
        max_position_size: int = 5,
        state_file: str = "dhan_state.json"
    ):
        """
        Initialize DhanHQ Integration
        
        Args:
            access_token: DhanHQ JWT token
            client_id: DhanHQ client ID (optional, will extract from JWT if not provided)
            practice_mode: Paper trading mode
            max_loss_per_trade: Max loss per trade (₹)
            max_position_size: Max position size (lots)
            state_file: State persistence file
        """
        self.logger = logging.getLogger(__name__)
        self.state_file = state_file
        
        # Get environment variables if not provided
        access_token = access_token or os.getenv("ACCESS_TOKEN", "")
        client_id = client_id or os.getenv("DHAN_CLIENT_ID", "")
        
        if not access_token:
            self.logger.error("❌ ACCESS_TOKEN required!")
            raise ValueError("Missing DhanHQ ACCESS_TOKEN")
        
        # Try to extract client_id from JWT token if not provided
        if not client_id:
            client_id = self._extract_client_id_from_jwt(access_token)
            if not client_id:
                self.logger.error("❌ DHAN_CLIENT_ID not found in JWT token or environment!")
                raise ValueError("Missing DhanHQ DHAN_CLIENT_ID")
        
        self.logger.info(f"✅ Using CLIENT_ID: {client_id}")
        
        # Initialize modules
        self.logger.info("🚀 Initializing DhanHQ Integration...")
        
        self.api_client = DhanAPIClient(
            access_token=access_token,
            client_id=client_id,
            practice_mode=practice_mode
        )
        
        self.order_manager = DhanOrderManager(
            dhan_client=self.api_client,
            max_loss_per_trade=max_loss_per_trade,
            max_position_size=max_position_size
        )
        
        self.position_tracker = DhanPositionTracker(
            dhan_client=self.api_client
        )
        
        # Setup callbacks
        self._setup_callbacks()
        
        # Load previous state
        self._load_state()
        
        self.logger.info("✅ DhanHQ Integration initialized")
        self.logger.info(f"   Mode: {'📄 PAPER' if practice_mode else '💰 REAL'}")
        self.logger.info(f"   Max Loss/Trade: ₹{max_loss_per_trade}")
        self.logger.info(f"   Max Position Size: {max_position_size}")
    
    # =========================================================================
    # JWT PARSING
    # =========================================================================
    
    def _extract_client_id_from_jwt(self, jwt_token: str) -> Optional[str]:
        """
        Extract DHAN_CLIENT_ID from JWT token
        
        JWT structure: header.payload.signature
        We need the payload which contains dhanClientId
        
        Args:
            jwt_token: JWT access token from DhanHQ
            
        Returns:
            dhanClientId from token or None
        """
        try:
            # JWT format: header.payload.signature
            parts = jwt_token.split('.')
            if len(parts) != 3:
                self.logger.warning("⚠️  Invalid JWT format")
                return None
            
            # Decode payload (add padding if needed)
            payload = parts[1]
            padding = 4 - len(payload) % 4
            if padding != 4:
                payload += '=' * padding
            
            decoded = base64.urlsafe_b64decode(payload)
            payload_json = json.loads(decoded)
            
            client_id = payload_json.get('dhanClientId')
            if client_id:
                self.logger.info(f"✅ Extracted dhanClientId from JWT: {client_id}")
                return client_id
            else:
                self.logger.warning("⚠️  dhanClientId not found in JWT payload")
                return None
                
        except Exception as e:
            self.logger.warning(f"⚠️  Failed to extract dhanClientId from JWT: {e}")
            return None
    
    # =========================================================================
    # TRADING INTERFACE
    # =========================================================================
    
    def place_trade(
        self,
        symbol: str,
        transaction_type: str,
        quantity: int,
        entry_price: float,
        sl_price: float,
        target_price: float,
        strategy: str = ""
    ) -> Tuple[bool, str, Optional[str]]:
        """
        Place a complete trade (order + position tracking)
        
        Steps:
        1. Validate order
        2. Place market order
        3. Track position
        4. Start monitoring
        
        Args:
            symbol: Trading symbol
            transaction_type: BUY or SELL
            quantity: Quantity (lots)
            entry_price: Entry price
            sl_price: Stop loss price
            target_price: Target price
            strategy: Strategy name
            
        Returns:
            (success, message, order_id)
        """
        try:
            self.logger.info("\n" + "="*70)
            self.logger.info("PLACING NEW TRADE")
            self.logger.info("="*70)
            
            # Determine exchange segment
            exchange = self._get_exchange_segment(symbol)
            trans_type = TransactionType.BUY if transaction_type == "BUY" else TransactionType.SELL
            
            # Place order
            success, msg, order_id = self.order_manager.create_market_order(
                symbol=symbol,
                exchange_segment=exchange,
                transaction_type=trans_type,
                quantity=quantity,
                entry_price=entry_price,
                sl_price=sl_price,
                target_price=target_price,
                strategy=strategy
            )
            
            if not success:
                self.logger.error(f"❌ Trade placement failed: {msg}")
                return False, msg, None
            
            # Add to position tracker
            success_track, msg_track = self.position_tracker.add_tracked_position(
                order_id=order_id,
                symbol=symbol,
                transaction_type=transaction_type,
                quantity=quantity,
                entry_price=entry_price,
                current_price=entry_price,
                sl_price=sl_price,
                target_price=target_price,
                strategy=strategy
            )
            
            if not success_track:
                self.logger.error(f"❌ Position tracking failed: {msg_track}")
            
            # Save state
            self._save_state()
            
            self.logger.info("\n✅ TRADE PLACED SUCCESSFULLY")
            self.logger.info(f"   Order ID: {order_id}")
            self.logger.info(f"   Symbol: {symbol}")
            self.logger.info(f"   Entry: ₹{entry_price}")
            self.logger.info(f"   SL: ₹{sl_price}")
            self.logger.info(f"   Target: ₹{target_price}")
            
            return True, f"Trade placed: {order_id}", order_id
            
        except Exception as e:
            self.logger.error(f"❌ Trade placement error: {e}")
            return False, f"Error: {e}", None
    
    def close_trade(self, order_id: str, close_price: float) -> Tuple[bool, str, float]:
        """
        Manually close a trade
        
        Args:
            order_id: Order ID to close
            close_price: Close price
            
        Returns:
            (success, message, pnl)
        """
        try:
            self.logger.info(f"📊 Closing trade {order_id}...")
            
            success, msg, pnl = self.position_tracker.close_position_manual(
                pos_id=order_id,
                close_price=close_price
            )
            
            if success:
                self._save_state()
            
            return success, msg, pnl
            
        except Exception as e:
            self.logger.error(f"❌ Close trade error: {e}")
            return False, f"Error: {e}", 0.0
    
    # =========================================================================
    # POSITION MONITORING
    # =========================================================================
    
    def get_positions(self) -> Dict:
        """Get current positions summary"""
        return {
            "open": self.position_tracker.get_open_positions(),
            "closed": self.position_tracker.get_closed_positions(),
            "summary": self.position_tracker.get_position_summary()
        }
    
    def start_monitoring(self, interval: int = 5) -> None:
        """Start background position monitoring"""
        self.position_tracker.start_monitoring(interval=interval)
        self.logger.info(f"✅ Position monitoring started (interval: {interval}s)")
    
    def stop_monitoring(self) -> None:
        """Stop background position monitoring"""
        self.position_tracker.stop_monitoring()
        self.logger.info("🛑 Position monitoring stopped")
    
    def monitor_once(self) -> Tuple[List[str], List[str]]:
        """Run one monitoring cycle (for manual checks)"""
        return self.position_tracker.monitor_positions()
    
    # =========================================================================
    # MARKET CLOSE ROUTINE
    # =========================================================================
    
    def market_close_routine(self) -> Tuple[int, float]:
        """
        Execute market close routine (close all positions at current price)
        
        Called at 2:50 PM IST (before market close at 3:30 PM)
        
        Returns:
            (closed_count, total_pnl)
        """
        try:
            self.logger.info("\n" + "="*70)
            self.logger.info("MARKET CLOSE ROUTINE EXECUTING")
            self.logger.info("="*70)
            
            # Get open positions
            open_pos = self.position_tracker.get_open_positions()
            
            if not open_pos:
                self.logger.info("✅ No open positions to close")
                return 0, 0.0
            
            # Fetch current prices
            success, msg, live_positions = self.api_client.get_positions()
            
            if not success:
                self.logger.error(f"❌ Cannot fetch prices: {msg}")
                return 0, 0.0
            
            # Build close price map
            close_prices = {}
            for pos in live_positions:
                pos_id = pos.get("positionId")
                close_prices[pos_id] = pos.get("currentPrice")
            
            # Close all positions
            closed_count, total_pnl = self.position_tracker.close_all_positions(
                close_prices
            )
            
            # Save state
            self._save_state()
            
            self.logger.info(f"\n✅ MARKET CLOSE COMPLETE")
            self.logger.info(f"   Positions Closed: {closed_count}")
            self.logger.info(f"   Total P&L: ₹{total_pnl:.2f}")
            
            return closed_count, total_pnl
            
        except Exception as e:
            self.logger.error(f"❌ Market close error: {e}")
            return 0, 0.0
    
    # =========================================================================
    # ACCOUNT INFORMATION
    # =========================================================================
    
    def get_account_info(self) -> Dict:
        """Fetch account information"""
        success, msg, account = self.api_client.get_account_info()
        
        if success:
            return account or {}
        else:
            self.logger.error(f"❌ {msg}")
            return {}
    
    def get_funds(self) -> Dict:
        """Fetch funds/balance information"""
        success, msg, funds = self.api_client.get_funds()
        
        if success:
            return funds or {}
        else:
            self.logger.error(f"❌ {msg}")
            return {}
    
    # =========================================================================
    # CALLBACKS
    # =========================================================================
    
    def _setup_callbacks(self) -> None:
        """Setup event callbacks"""
        # SL hit callback
        def on_sl_hit(position: Dict):
            self.logger.critical(f"🚨 SL HIT CALLBACK: {position['symbol']}")
            pnl = position.get("finalPnL", 0.0)
            self.logger.info(f"   P&L: ₹{pnl:.2f}")
            self._save_state()
        
        # Target hit callback
        def on_target_hit(position: Dict):
            self.logger.info(f"🎯 TARGET HIT CALLBACK: {position['symbol']}")
            pnl = position.get("finalPnL", 0.0)
            self.logger.info(f"   P&L: ₹{pnl:.2f}")
            self._save_state()
        
        self.position_tracker.on_sl_hit(on_sl_hit)
        self.position_tracker.on_target_hit(on_target_hit)
    
    # =========================================================================
    # STATE PERSISTENCE
    # =========================================================================
    
    def _save_state(self) -> None:
        """Save trading state to file"""
        try:
            state = {
                "timestamp": datetime.now(IST).isoformat(),
                "positions": {
                    "open": self.position_tracker.get_open_positions(),
                    "closed": self.position_tracker.get_closed_positions()
                },
                "orders": {
                    "active": self.order_manager.get_active_orders(),
                    "executed": self.order_manager.get_executed_orders(),
                    "rejected": self.order_manager.get_rejected_orders()
                },
                "summary": {
                    "positions": self.position_tracker.get_position_summary(),
                    "daily_pnl": self.position_tracker.get_daily_pnl()
                }
            }
            
            with open(self.state_file, 'w') as f:
                json.dump(state, f, indent=2)
            
            self.logger.debug(f"✅ State saved to {self.state_file}")
            
        except Exception as e:
            self.logger.error(f"❌ State save error: {e}")
    
    def _load_state(self) -> None:
        """Load trading state from file"""
        try:
            if not os.path.exists(self.state_file):
                self.logger.info(f"ℹ️  No previous state found ({self.state_file})")
                return
            
            with open(self.state_file, 'r') as f:
                state = json.load(f)
            
            self.logger.info(f"✅ Loaded previous state from {self.state_file}")
            
            # Load positions
            for pos in state.get("positions", {}).get("open", []):
                self.position_tracker.tracked_positions[pos["positionId"]] = pos
            
            for pos in state.get("positions", {}).get("closed", []):
                self.position_tracker.closed_positions[pos["positionId"]] = pos
            
            self.logger.info(
                f"   Open Positions: {len(self.position_tracker.tracked_positions)}"
            )
            self.logger.info(
                f"   Closed Positions: {len(self.position_tracker.closed_positions)}"
            )
            
        except Exception as e:
            self.logger.error(f"❌ State load error: {e}")
    
    # =========================================================================
    # REPORTING
    # =========================================================================
    
    def get_summary(self) -> Dict:
        """Get comprehensive trading summary"""
        return {
            "timestamp": datetime.now(IST).isoformat(),
            "positions": self.position_tracker.get_position_summary(),
            "account": self.get_account_info(),
            "funds": self.get_funds(),
            "daily_pnl": self.position_tracker.get_daily_pnl()
        }
    
    def print_summary(self) -> None:
        """Print comprehensive summary"""
        self.logger.info("\n" + "="*70)
        self.logger.info("DHAN INTEGRATION SUMMARY")
        self.logger.info("="*70)
        
        summary = self.get_summary()
        
        self.logger.info(f"\n📊 POSITIONS")
        pos_summary = summary["positions"]
        self.logger.info(f"  Open: {pos_summary['openPositions']}")
        self.logger.info(f"  Closed: {pos_summary['closedPositions']}")
        self.logger.info(f"  Wins: {pos_summary['wins']} | Losses: {pos_summary['losses']}")
        self.logger.info(f"  Win Rate: {pos_summary['winRate']}")
        
        self.logger.info(f"\n💰 ACCOUNT")
        account = summary.get("account", {})
        self.logger.info(f"  Client ID: {account.get('dhanClientId', 'N/A')}")
        self.logger.info(f"  Ledger Balance: ₹{account.get('ledgerBalance', 0)}")
        
        funds = summary.get("funds", {})
        self.logger.info(f"  Available: ₹{funds.get('marginAvailable', 0)}")
        self.logger.info(f"  Used Margin: ₹{funds.get('marginUsed', 0)}")
        
        self.logger.info(f"\n📈 TRADING")
        self.logger.info(f"  Daily P&L: ₹{summary['daily_pnl']:.2f}")
    
    # =========================================================================
    # HELPER METHODS
    # =========================================================================
    
    def _get_exchange_segment(self, symbol: str) -> ExchangeSegment:
        """
        Get exchange segment for symbol
        
        Args:
            symbol: Trading symbol
            
        Returns:
            ExchangeSegment
        """
        # Commodity symbols
        if symbol in ["GOLD", "SILVER", "CRUDE", "NATURALGAS"]:
            return ExchangeSegment.MCXSX
        
        # Index options (NIFTY50, BANKNIFTY, etc.)
        elif symbol in ["NIFTY50", "BANKNIFTY", "NIFTYNXT50"]:
            return ExchangeSegment.NSE_FO
        
        # Equity stocks
        else:
            return ExchangeSegment.NSE_EQ


# ============================================================================
# SINGLETON INSTANCE
# ============================================================================

_integration: Optional[DhanIntegration] = None


def get_dhan_integration(
    access_token: str = None,
    client_id: str = None,
    practice_mode: bool = True,
    max_loss: float = 500.0,
    max_position: int = 5
) -> DhanIntegration:
    """
    Get or create DhanIntegration instance
    
    Args:
        access_token: DhanHQ JWT token
        client_id: DhanHQ client ID (optional, will extract from JWT if not provided)
        practice_mode: Paper trading mode
        max_loss: Max loss per trade
        max_position: Max position size
        
    Returns:
        DhanIntegration instance
    """
    global _integration
    
    if _integration is None:
        _integration = DhanIntegration(
            access_token=access_token,
            client_id=client_id,
            practice_mode=practice_mode,
            max_loss_per_trade=max_loss,
            max_position_size=max_position
        )
    
    return _integration
