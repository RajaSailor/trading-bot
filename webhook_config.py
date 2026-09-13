"""
Webhook Configuration - Setup and configuration for webhook endpoints.

Configures:
- Flask/FastAPI endpoints for receiving alerts
- Webhook authentication
- Alert parsing and routing
- Error handling and logging
- Health checks
"""

import logging
import os
from typing import Dict, Optional, Callable
from functools import wraps
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# IST timezone
IST = ZoneInfo("Asia/Kolkata")


class WebhookConfig:
    """Webhook configuration and setup"""
    
    def __init__(self):
        """Initialize webhook configuration"""
        self.logger = logging.getLogger(__name__)
        
        # Environment variables (with defaults)
        self.WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "5000"))
        self.WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "0.0.0.0")
        self.WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/webhook/alert")
        self.WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "default_secret_change_me")
        
        # Telegram settings
        self.TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.TELEGRAM_CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID", "0"))
        
        # Alert settings
        self.ALERT_TIMEOUT = int(os.getenv("ALERT_TIMEOUT", "300"))  # 5 minutes
        self.MAX_PENDING_TRADES = int(os.getenv("MAX_PENDING_TRADES", "50"))
        
        # Trading settings
        self.MAX_TRADES_PER_DAY = int(os.getenv("MAX_TRADES_PER_DAY", "10"))
        self.MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", "3"))
        self.INITIAL_LOT_SIZE = int(os.getenv("INITIAL_LOT_SIZE", "1"))
        
        # DhanHQ settings
        self.DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID", "")
        self.DHAN_API_KEY = os.getenv("DHAN_API_KEY", "")
        
        # Market hours (IST)
        self.MARKET_OPEN_HOUR = 9
        self.MARKET_OPEN_MINUTE = 15
        self.ENTRY_CUTOFF_HOUR = 14
        self.ENTRY_CUTOFF_MINUTE = 50
        self.MARKET_CLOSE_HOUR = 15
        self.MARKET_CLOSE_MINUTE = 30
        
        # Allowed symbols (for filtering)
        self.ALLOWED_SYMBOLS = [
            "NIFTY50", "NIFTY", "BANKNIFTY",
            "GOLD", "CRUDE", "SILVER", "NATURALGAS",
            "RELIANCE", "TCS", "INFY", "WIPRO",
            "BTC", "ETH"
        ]
        
        # Allowed strategies
        self.ALLOWED_STRATEGIES = [
            "5-MIN Breakout",
            "Premium Breakout",
            "5X Leverage",
            "Pay Later",
            "Margin"
        ]
        
        self.logger.info("✅ Webhook Configuration initialized")
        self._validate_config()
    
    def _validate_config(self) -> None:
        """Validate configuration"""
        if not self.TELEGRAM_BOT_TOKEN:
            self.logger.warning("⚠️ TELEGRAM_BOT_TOKEN not set - Telegram integration disabled")
        
        if not self.DHAN_CLIENT_ID or not self.DHAN_API_KEY:
            self.logger.warning("⚠️ DhanHQ credentials not set - Paper trading mode only")
        
        if self.WEBHOOK_SECRET == "default_secret_change_me":
            self.logger.warning("🚨 WEBHOOK_SECRET is default - CHANGE IN PRODUCTION!")
    
    # =========================================================================
    # AUTHENTICATION
    # =========================================================================
    
    def verify_webhook_signature(self, secret: str) -> bool:
        """
        Verify webhook signature
        
        Args:
            secret: Secret provided in webhook request
            
        Returns:
            True if signature is valid
        """
        return secret == self.WEBHOOK_SECRET
    
    def create_auth_decorator(self) -> Callable:
        """
        Create decorator for webhook authentication
        
        Returns:
            Decorator function
        """
        def decorator(f):
            @wraps(f)
            def decorated_function(*args, **kwargs):
                # Extract secret from headers or query params
                from flask import request
                
                secret = request.headers.get("X-Webhook-Secret") or request.args.get("secret")
                
                if not self.verify_webhook_signature(secret):
                    self.logger.warning(f"❌ Invalid webhook signature from {request.remote_addr}")
                    return {"error": "Invalid signature"}, 401
                
                return f(*args, **kwargs)
            
            return decorated_function
        return decorator
    
    # =========================================================================
    # ALERT VALIDATION
    # =========================================================================
    
    def validate_alert(self, alert_data: Dict) -> tuple[bool, str]:
        """
        Validate alert data
        
        Args:
            alert_data: Alert data dict
            
        Returns:
            (is_valid, message)
        """
        try:
            # Check required fields
            required_fields = ["symbol", "signal_type", "entry_price", "sl"]
            for field in required_fields:
                if field not in alert_data:
                    return False, f"Missing required field: {field}"
            
            # Validate symbol
            symbol = alert_data.get("symbol", "").upper()
            if symbol not in self.ALLOWED_SYMBOLS:
                return False, f"Symbol not allowed: {symbol}"
            
            # Validate signal type
            signal_type = alert_data.get("signal_type", "").upper()
            if signal_type not in ["BUY", "SELL"]:
                return False, f"Invalid signal type: {signal_type}"
            
            # Validate prices
            try:
                entry_price = float(alert_data.get("entry_price", 0))
                sl = float(alert_data.get("sl", 0))
                
                if entry_price <= 0 or sl <= 0:
                    return False, "Prices must be positive"
                
                sl_points = abs(entry_price - sl)
                if sl_points < 5:
                    return False, "SL must be at least 5 points from entry"
                
            except ValueError:
                return False, "Invalid price format"
            
            # Validate market hours
            now = datetime.now(IST)
            market_open = now.replace(hour=self.MARKET_OPEN_HOUR, minute=self.MARKET_OPEN_MINUTE, second=0, microsecond=0)
            entry_cutoff = now.replace(hour=self.ENTRY_CUTOFF_HOUR, minute=self.ENTRY_CUTOFF_MINUTE, second=0, microsecond=0)
            market_close = now.replace(hour=self.MARKET_CLOSE_HOUR, minute=self.MARKET_CLOSE_MINUTE, second=0, microsecond=0)
            
            if now < market_open or now > entry_cutoff:
                return False, f"Alert outside trading hours (9:15 AM - 2:50 PM IST)"
            
            return True, "✅ Valid alert"
            
        except Exception as e:
            self.logger.error(f"❌ Error validating alert: {e}")
            return False, f"Validation error: {e}"
    
    # =========================================================================
    # WEBHOOK ENDPOINTS SETUP
    # =========================================================================
    
    def get_flask_config(self) -> Dict:
        """
        Get Flask app configuration
        
        Returns:
            Flask config dict
        """
        return {
            "HOST": self.WEBHOOK_HOST,
            "PORT": self.WEBHOOK_PORT,
            "DEBUG": False,
            "JSON_SORT_KEYS": False,
        }
    
    def get_webhook_endpoints(self) -> Dict[str, Dict]:
        """
        Get webhook endpoint configuration
        
        Returns:
            Dict of endpoint configs
        """
        return {
            "receive_alert": {
                "path": self.WEBHOOK_PATH,
                "methods": ["POST"],
                "requires_auth": True,
                "description": "Receive trading alert from webhook"
            },
            "health_check": {
                "path": "/health",
                "methods": ["GET"],
                "requires_auth": False,
                "description": "Health check endpoint"
            },
            "status": {
                "path": "/status",
                "methods": ["GET"],
                "requires_auth": False,
                "description": "Get system status"
            },
            "config": {
                "path": "/config",
                "methods": ["GET"],
                "requires_auth": True,
                "description": "Get current configuration"
            }
        }
    
    # =========================================================================
    # EXAMPLE WEBHOOK DATA
    # =========================================================================
    
    def get_example_alert(self) -> Dict:
        """
        Get example alert data for testing
        
        Returns:
            Example alert dict
        """
        return {
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
            "timestamp": datetime.now(IST).isoformat()
        }
    
    # =========================================================================
    # CONFIGURATION DISPLAY
    # =========================================================================
    
    def get_config_summary(self) -> str:
        """
        Get configuration summary for logging
        
        Returns:
            Formatted config summary
        """
        summary = (
            "🔧 <b>WEBHOOK CONFIGURATION</b>\n\n"
            f"<b>Webhook:</b>\n"
            f"  Host: {self.WEBHOOK_HOST}\n"
            f"  Port: {self.WEBHOOK_PORT}\n"
            f"  Path: {self.WEBHOOK_PATH}\n\n"
            f"<b>Trading:</b>\n"
            f"  Max Trades/Day: {self.MAX_TRADES_PER_DAY}\n"
            f"  Max Open Positions: {self.MAX_OPEN_POSITIONS}\n"
            f"  Initial Lot Size: {self.INITIAL_LOT_SIZE}\n"
            f"  Alert Timeout: {self.ALERT_TIMEOUT}s\n\n"
            f"<b>Market Hours (IST):</b>\n"
            f"  Entry: {self.MARKET_OPEN_HOUR:02d}:{self.MARKET_OPEN_MINUTE:02d} - {self.ENTRY_CUTOFF_HOUR:02d}:{self.ENTRY_CUTOFF_MINUTE:02d}\n"
            f"  Close: {self.MARKET_CLOSE_HOUR:02d}:{self.MARKET_CLOSE_MINUTE:02d}\n\n"
            f"<b>Supported Symbols:</b>\n"
            f"  {', '.join(self.ALLOWED_SYMBOLS[:5])}...\n\n"
            f"<b>Integration:</b>\n"
            f"  DhanHQ: {'✅ Enabled' if self.DHAN_CLIENT_ID else '❌ Disabled'}\n"
            f"  Telegram: {'✅ Enabled' if self.TELEGRAM_BOT_TOKEN else '❌ Disabled'}"
        )
        return summary
    
    # =========================================================================
    # RUNTIME HELPERS
    # =========================================================================
    
    def is_market_open(self) -> bool:
        """Check if market is currently open"""
        now = datetime.now(IST)
        market_open = now.replace(hour=self.MARKET_OPEN_HOUR, minute=self.MARKET_OPEN_MINUTE, second=0, microsecond=0)
        entry_cutoff = now.replace(hour=self.ENTRY_CUTOFF_HOUR, minute=self.ENTRY_CUTOFF_MINUTE, second=0, microsecond=0)
        
        return market_open <= now <= entry_cutoff
    
    def time_to_market_close(self) -> int:
        """Get minutes until market close"""
        now = datetime.now(IST)
        close_time = now.replace(hour=self.ENTRY_CUTOFF_HOUR, minute=self.ENTRY_CUTOFF_MINUTE, second=0, microsecond=0)
        
        if now > close_time:
            return 0
        
        delta = close_time - now
        return int(delta.total_seconds() / 60)
    
    def get_current_market_phase(self) -> str:
        """Get current market phase"""
        now = datetime.now(IST)
        
        market_open = now.replace(hour=self.MARKET_OPEN_HOUR, minute=self.MARKET_OPEN_MINUTE, second=0, microsecond=0)
        entry_cutoff = now.replace(hour=self.ENTRY_CUTOFF_HOUR, minute=self.ENTRY_CUTOFF_MINUTE, second=0, microsecond=0)
        market_close = now.replace(hour=self.MARKET_CLOSE_HOUR, minute=self.MARKET_CLOSE_MINUTE, second=0, microsecond=0)
        
        if now < market_open:
            return f"⏳ Pre-Market ({(market_open - now).seconds // 60}m to open)"
        elif now <= entry_cutoff:
            return f"📊 Trading Active ({(entry_cutoff - now).seconds // 60}m to close)"
        elif now <= market_close:
            return f"🔄 Exit Phase ({(market_close - now).seconds // 60}m to close)"
        else:
            return "🌙 Market Closed"


# Singleton instance
_config: Optional[WebhookConfig] = None


def get_webhook_config() -> WebhookConfig:
    """Get or create webhook config instance"""
    global _config
    if _config is None:
        _config = WebhookConfig()
    return _config


# ============================================================================
# SAMPLE FLASK INTEGRATION
# ============================================================================

def create_flask_app():
    """
    Create and configure Flask app for webhooks
    
    Example usage:
        from webhook_config import create_flask_app
        app = create_flask_app()
        app.run()
    
    Returns:
        Configured Flask app
    """
    try:
        from flask import Flask, request, jsonify
        from telegram_bidirectional_handler import get_telegram_handler
        
        app = Flask(__name__)
        config = get_webhook_config()
        handler = get_telegram_handler()
        
        logger.info("✅ Flask app initialized")
        
        # Health check endpoint
        @app.route("/health", methods=["GET"])
        def health_check():
            return jsonify({
                "status": "healthy",
                "timestamp": datetime.now(IST).isoformat(),
                "market_phase": config.get_current_market_phase()
            })
        
        # Status endpoint
        @app.route("/status", methods=["GET"])
        def status():
            system_status = handler.get_system_status()
            return jsonify(system_status)
        
        # Alert webhook endpoint
        @app.route(config.WEBHOOK_PATH, methods=["POST"])
        def receive_alert():
            try:
                # Verify signature
                secret = request.headers.get("X-Webhook-Secret") or request.args.get("secret")
                if not config.verify_webhook_signature(secret):
                    logger.warning(f"❌ Invalid webhook signature from {request.remote_addr}")
                    return jsonify({"error": "Invalid signature"}), 401
                
                # Get alert data
                alert_data = request.get_json()
                
                # Validate alert
                is_valid, validation_msg = config.validate_alert(alert_data)
                if not is_valid:
                    logger.warning(f"❌ Invalid alert: {validation_msg}")
                    return jsonify({"error": validation_msg}), 400
                
                # Process alert via handler
                success, msg, trade_id = handler.handle_webhook_alert(alert_data)
                
                if success:
                    return jsonify({
                        "success": True,
                        "message": msg,
                        "trade_id": trade_id,
                        "timestamp": datetime.now(IST).isoformat()
                    })
                else:
                    return jsonify({
                        "success": False,
                        "error": msg,
                        "timestamp": datetime.now(IST).isoformat()
                    }), 400
                
            except Exception as e:
                logger.error(f"❌ Error in webhook: {e}")
                return jsonify({"error": str(e)}), 500
        
        return app
    
    except ImportError:
        logger.error("❌ Flask not installed. Install with: pip install flask")
        return None
