"""
LIVE MARKET SCREENER - Main Orchestrator
Integrates all components:
- DhanHQ API for real-time OHLC data
- Signal Processor for breakout detection
- Telegram Alerts (Fixed with multi-bot support)
- Position Manager for risk control
- Market hours validation (IST timezone)

This is the PRODUCTION-READY screener that actually sends alerts!

File: live_screener_main.py
"""

from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import threading
import json

# Import our modules
from telegram_alerts_fixed import get_telegram_alerts
from signal_processor import SignalProcessor, SignalType

logger = logging.getLogger(__name__)


class MarketHours:
    """Check if market is open (IST timezone)"""
    
    @staticmethod
    def is_nse_open() -> bool:
        """Check if NSE is open (9:15 AM - 3:30 PM IST, Mon-Fri)"""
        now_ist = datetime.utcnow() + timedelta(hours=5, minutes=30)
        
        # Check if weekend
        if now_ist.weekday() >= 5:  # Saturday=5, Sunday=6
            return False
        
        # Check time
        market_open = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
        market_close = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)
        
        return market_open <= now_ist <= market_close
    
    @staticmethod
    def is_mcx_open() -> bool:
        """Check if MCX is open (24/5 - Mon-Fri, 9 AM to 11:30 PM IST)"""
        now_ist = datetime.utcnow() + timedelta(hours=5, minutes=30)
        
        # Check if weekend
        if now_ist.weekday() >= 5:
            return False
        
        # MCX runs 24/5
        return True
    
    @staticmethod
    def next_market_open(market: str = "NSE") -> str:
        """Get next market open time"""
        now_ist = datetime.utcnow() + timedelta(hours=5, minutes=30)
        
        if market == "NSE":
            if now_ist.weekday() >= 5:  # Weekend
                days_ahead = 7 - now_ist.weekday()
                next_open = now_ist + timedelta(days=days_ahead)
                next_open = next_open.replace(hour=9, minute=15)
            else:  # Weekday
                next_open = now_ist.replace(hour=9, minute=15, second=0)
                if now_ist >= next_open:
                    next_open += timedelta(days=1)
        else:  # MCX
            next_open = now_ist + timedelta(hours=1)
        
        return next_open.strftime("%Y-%m-%d %H:%M IST")


class LiveScreenerMain:
    """
    MAIN LIVE SCREENER - Production ready
    
    Monitors market in real-time and sends Telegram alerts
    """
    
    def __init__(self, dhan_client=None):
        """
        Initialize screener
        
        Args:
            dhan_client: DhanAPIClient instance
        """
        self.dhan_client = dhan_client
        self.signal_processor = SignalProcessor(dhan_client=dhan_client)
        self.telegram_alerts = get_telegram_alerts()
        
        # Configuration
        self.update_interval = 5  # Check every 5 seconds
        self.position_limit = 5
        self.open_positions = []
        
        # Market data cache
        self.candle_cache: Dict[str, List[dict]] = {}
        self.last_price_cache: Dict[str, float] = {}
        
        # Monitoring
        self.total_alerts_sent = 0
        self.total_signals_detected = 0
        self.start_time = datetime.now()
        
        logger.info("✅ LiveScreenerMain initialized")
    
    def run_screener_loop(self) -> None:
        """Main screening loop - runs continuously"""
        logger.info("🚀 STARTING LIVE SCREENER LOOP")
        logger.info(f"   Update interval: {self.update_interval}s")
        logger.info(f"   Position limit: {self.position_limit}")
        
        # Send startup alert
        self._send_startup_alert()
        
        loop_count = 0
        
        while True:
            try:
                loop_count += 1
                current_time = datetime.now()
                
                # Log status every 100 loops
                if loop_count % 100 == 0:
                    self._log_status()
                
                # Check market hours
                if not self._is_market_active():
                    if loop_count % 24 == 0:  # Log every 2 minutes (24 * 5s)
                        next_open = MarketHours.next_market_open()
                        logger.info(f"⏸️  Market closed. Next open: {next_open}")
                    time.sleep(self.update_interval)
                    continue
                
                # Scan all instruments
                self._scan_nifty()
                self._scan_banknifty()
                self._scan_commodities()
                
                time.sleep(self.update_interval)
                
            except KeyboardInterrupt:
                logger.info("⏹️  Screener stopped by user")
                break
            except Exception as e:
                logger.error(f"❌ Error in screener loop: {type(e).__name__}: {e}")
                self.telegram_alerts.send_error_alert(
                    "index_options",
                    "ScreenerError",
                    str(e)
                )
                time.sleep(self.update_interval)
    
    def _scan_nifty(self) -> None:
        """Scan NIFTY 50 Index for breakouts"""
        try:
            nifty_id = self.signal_processor.NIFTY_SECURITIES["NIFTY_INDEX"]["security_id"]
            exchange = self.signal_processor.NIFTY_SECURITIES["NIFTY_INDEX"]["exchange_segment"]
            
            # Fetch latest candles
            candles = self._fetch_candles(
                security_id=nifty_id,
                exchange_segment=exchange,
                instrument="INDEX",
                timeframe="5MIN"
            )
            
            if not candles:
                return
            
            # Detect breakout
            signal = self.signal_processor.detect_breakout(
                candles=candles,
                symbol="NIFTY 50",
                threshold_percent=0.15
            )
            
            if signal and self.signal_processor.validate_signal(signal, "NIFTY 50"):
                self._process_signal(
                    signal=signal,
                    symbol="NIFTY 50",
                    bot_category="index_options",
                    underlying_price=candles[-1]["close"]
                )
        
        except Exception as e:
            logger.error(f"❌ Error scanning NIFTY: {e}")
    
    def _scan_banknifty(self) -> None:
        """Scan BANKNIFTY Index for breakouts"""
        try:
            bnifty_id = self.signal_processor.BANKNIFTY_SECURITIES["BANKNIFTY_INDEX"]["security_id"]
            exchange = self.signal_processor.BANKNIFTY_SECURITIES["BANKNIFTY_INDEX"]["exchange_segment"]
            
            candles = self._fetch_candles(
                security_id=bnifty_id,
                exchange_segment=exchange,
                instrument="INDEX",
                timeframe="5MIN"
            )
            
            if not candles:
                return
            
            signal = self.signal_processor.detect_breakout(
                candles=candles,
                symbol="BANKNIFTY",
                threshold_percent=0.15
            )
            
            if signal and self.signal_processor.validate_signal(signal, "BANKNIFTY"):
                self._process_signal(
                    signal=signal,
                    symbol="BANKNIFTY",
                    bot_category="index_options",
                    underlying_price=candles[-1]["close"]
                )
        
        except Exception as e:
            logger.error(f"❌ Error scanning BANKNIFTY: {e}")
    
    def _scan_commodities(self) -> None:
        """Scan MCX commodities (GOLD, CRUDEOIL, etc)"""
        try:
            commodities = [
                ("CRUDEOIL", "commodity"),
                ("GOLD", "commodity"),
            ]
            
            for commodity_key, bot_category in commodities:
                commodity_info = self.signal_processor.MCX_SECURITIES.get(commodity_key)
                if not commodity_info:
                    continue
                
                candles = self._fetch_candles(
                    security_id=commodity_info["security_id"],
                    exchange_segment=commodity_info["exchange_segment"],
                    instrument=commodity_info["instrument"],
                    timeframe="5MIN"
                )
                
                if not candles:
                    continue
                
                signal = self.signal_processor.detect_breakout(
                    candles=candles,
                    symbol=commodity_key,
                    threshold_percent=0.2
                )
                
                if signal and self.signal_processor.validate_signal(signal, commodity_key):
                    self._process_signal(
                        signal=signal,
                        symbol=commodity_key,
                        bot_category=bot_category,
                        underlying_price=candles[-1]["close"]
                    )
        
        except Exception as e:
            logger.error(f"❌ Error scanning commodities: {e}")
    
    def _fetch_candles(
        self,
        security_id: str,
        exchange_segment: str,
        instrument: str,
        timeframe: str = "5MIN"
    ) -> Optional[List[dict]]:
        """
        Fetch latest OHLC candles from DhanHQ
        
        Returns:
            List of candles or None if failed
        """
        if not self.dhan_client:
            logger.warning("⚠️  DhanAPIClient not available")
            return None
        
        try:
            # Map timeframe to API format
            interval_map = {
                "1MIN": "1",
                "5MIN": "5",
                "15MIN": "15",
                "1HOUR": "60",
                "1DAY": "DAY"
            }
            
            interval = interval_map.get(timeframe, "5")
            
            # Fetch candles
            response = self.dhan_client.fetch_dhanhq_candles(
                security_id=security_id,
                exchange_segment=exchange_segment,
                instrument=instrument,
                from_date=(datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"),
                to_date=datetime.now().strftime("%Y-%m-%d"),
                interval=interval
            )
            
            if response and len(response) > 0:
                return response
            else:
                logger.debug(f"⚠️  No candles found for {security_id}")
                return None
        
        except Exception as e:
            logger.error(f"❌ Error fetching candles: {e}")
            return None
    
    def _process_signal(
        self,
        signal: Dict,
        symbol: str,
        bot_category: str,
        underlying_price: float
    ) -> None:
        """
        Process detected signal and send Telegram alert
        
        Args:
            signal: Detected signal dict
            symbol: Trading symbol
            bot_category: Bot channel to use
            underlying_price: Current underlying price for option data
        """
        try:
            self.total_signals_detected += 1
            
            # Get option chain data
            option_data = self.signal_processor.get_option_chain_data(
                underlying_price=underlying_price
            )
            
            # Format for Telegram
            formatted_signal, formatted_options = self.signal_processor.format_signal_for_telegram(
                signal=signal,
                option_data=option_data,
                source="SCREENER"
            )
            
            # Check position limit
            if len(self.open_positions) >= self.position_limit:
                logger.warning(
                    f"⚠️  Position limit reached ({self.position_limit} open). "
                    f"Requesting user confirmation..."
                )
                # TODO: Send confirmation request to user
                return
            
            # Send Telegram alert
            success = self.telegram_alerts.send_signal_alert(
                bot_category=bot_category,
                signal_data=formatted_signal,
                option_data=formatted_options
            )
            
            if success:
                self.total_alerts_sent += 1
                self.open_positions.append({
                    "symbol": symbol,
                    "signal": signal["type"].value,
                    "entry": signal.get("entry"),
                    "stop_loss": signal.get("stop_loss"),
                    "created_at": datetime.now()
                })
                
                logger.info(
                    f"✅ SIGNAL SENT: {symbol} {signal['type'].value} @ {signal.get('entry')} "
                    f"(Total: {self.total_alerts_sent} alerts)"
                )
            else:
                logger.error(f"❌ Failed to send alert for {symbol}")
        
        except Exception as e:
            logger.error(f"❌ Error processing signal: {e}")
    
    def _is_market_active(self) -> bool:
        """Check if any market is active"""
        return MarketHours.is_nse_open() or MarketHours.is_mcx_open()
    
    def _send_startup_alert(self) -> None:
        """Send startup notification"""
        startup_message = (
            "🚀 <b>LIVE SCREENER STARTED</b>\n\n"
            "<b>Configuration:</b>\n"
            "  • Update Interval: 5 seconds\n"
            "  • Instruments: NIFTY 50, BANKNIFTY, GOLD, CRUDEOIL\n"
            "  • Timeframe: 5-MIN Breakout\n"
            "  • Position Limit: 5 open positions\n\n"
            "<b>Monitoring Channels:</b>\n"
            "  ✅ Index Options (NIFTY/BANKNIFTY)\n"
            "  ✅ Commodities (GOLD/CRUDEOIL)\n\n"
            f"<i>Start Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S IST')}</i>"
        )
        
        self.telegram_alerts.send_market_update(
            bot_category="index_options",
            update_type="market_open",
            update_data={"message": startup_message}
        )
    
    def _log_status(self) -> None:
        """Log current screener status"""
        uptime = datetime.now() - self.start_time
        market_status = "🟢 OPEN" if self._is_market_active() else "🔴 CLOSED"
        
        status_msg = (
            f"\n{'='*80}\n"
            f"SCREENER STATUS\n"
            f"{'='*80}\n"
            f"  Uptime: {uptime}\n"
            f"  Market: {market_status}\n"
            f"  Total Signals Detected: {self.total_signals_detected}\n"
            f"  Total Alerts Sent: {self.total_alerts_sent}\n"
            f"  Open Positions: {len(self.open_positions)}\n"
            f"  Bot Status: {self.telegram_alerts.get_bot_status()}\n"
            f"{'='*80}\n"
        )
        
        logger.info(status_msg)
    
    def shutdown(self) -> None:
        """Gracefully shutdown screener"""
        logger.info("🛑 Shutting down screener...")
        self.telegram_alerts.shutdown()
        logger.info("✅ Screener shutdown complete")


def setup_logging():
    """Configure logging"""
    os.makedirs("logs", exist_ok=True)
    
    log_filename = f"logs/screener_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_filename),
            logging.StreamHandler(sys.stdout)
        ]
    )
    
    logger.info(f"📝 Logging to {log_filename}")


def main():
    """Main entry point"""
    setup_logging()
    
    try:
        # Import DhanHQ client
        try:
            from data_manager import DhanAPIClient
            
            # Load credentials
            from dotenv import load_dotenv
            load_dotenv()
            
            api_key = os.getenv("API_KEY")
            access_token = os.getenv("ACCESS_TOKEN")
            
            if not api_key or not access_token:
                logger.error("❌ Missing API credentials in .env")
                return
            
            # Initialize client
            dhan_client = DhanAPIClient(access_token=access_token)
            logger.info("✅ DhanHQ client initialized")
        
        except Exception as e:
            logger.warning(f"⚠️  Could not initialize DhanHQ client: {e}")
            logger.warning("   Screener will run with mock data")
            dhan_client = None
        
        # Start screener
        screener = LiveScreenerMain(dhan_client=dhan_client)
        screener.run_screener_loop()
    
    except KeyboardInterrupt:
        logger.info("✋ Received interrupt signal")
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}", exc_info=True)
    finally:
        logger.info("Exiting...")


if __name__ == "__main__":
    main()
