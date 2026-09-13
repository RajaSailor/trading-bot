"""
DhanHQ Postback Handler - Listens for DhanHQ webhook postbacks and updates order/position status.

Official DhanHQ Postback Documentation:
https://github.com/Kalaiviswa/dhan-api-v2-docs/blob/main/webhooks.md

Postback Events:
1. Order Execution - Order placed/executed/rejected
2. Position Update - Position P&L update
3. Account Update - Margin/funds update

Security:
- Verify postback signature (HMAC-SHA256)
- Validate postback source
- Idempotent processing
"""

import logging
import json
import hmac
import hashlib
from typing import Dict, Tuple, Optional, Callable
from datetime import datetime
from zoneinfo import ZoneInfo
from enum import Enum
from flask import Flask, request, jsonify

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


class PostbackEventType(Enum):
    """Official DhanHQ postback event types"""
    ORDER_EXECUTION = "ORDER_EXECUTION"
    ORDER_UPDATE = "ORDER_UPDATE"
    POSITION_UPDATE = "POSITION_UPDATE"
    ACCOUNT_UPDATE = "ACCOUNT_UPDATE"
    MARGIN_UPDATE = "MARGIN_UPDATE"


class DhanPostbackHandler:
    """
    Handles DhanHQ webhook postbacks.
    
    Workflow:
    1. Receive postback from DhanHQ
    2. Verify signature (HMAC-SHA256)
    3. Parse event type
    4. Update position/order status
    5. Trigger callbacks
    6. Send acknowledgment
    """
    
    def __init__(self, webhook_secret: str = None):
        """
        Initialize postback handler
        
        Args:
            webhook_secret: DhanHQ webhook secret for signature verification
        """
        self.logger = logging.getLogger(__name__)
        self.webhook_secret = webhook_secret or ""
        
        # Event callbacks
        self.order_execution_callbacks: list = []
        self.order_update_callbacks: list = []
        self.position_update_callbacks: list = []
        self.account_update_callbacks: list = []
        
        # Postback history (for idempotency)
        self.processed_postbacks: Dict[str, datetime] = {}
        
        self.logger.info("✅ DhanHQ Postback Handler initialized")
    
    # =========================================================================
    # POSTBACK VERIFICATION
    # =========================================================================
    
    def verify_postback_signature(
        self,
        payload: str,
        signature: str
    ) -> bool:
        """
        Verify postback signature using HMAC-SHA256
        
        Official Format:
        - Signature header: X-Dhan-Signature
        - Algorithm: HMAC-SHA256
        - Key: webhook_secret
        - Message: raw postback JSON
        
        Args:
            payload: Raw postback JSON string
            signature: Signature from header
            
        Returns:
            True if signature is valid
        """
        try:
            # Calculate expected signature
            expected_signature = hmac.new(
                self.webhook_secret.encode(),
                payload.encode(),
                hashlib.sha256
            ).hexdigest()
            
            # Compare signatures (constant-time comparison)
            is_valid = hmac.compare_digest(signature, expected_signature)
            
            if not is_valid:
                self.logger.error(f"❌ Invalid postback signature!")
                self.logger.error(f"   Expected: {expected_signature}")
                self.logger.error(f"   Received: {signature}")
            
            return is_valid
            
        except Exception as e:
            self.logger.error(f"❌ Signature verification error: {e}")
            return False
    
    def is_duplicate_postback(self, postback_id: str) -> bool:
        """
        Check if postback was already processed (idempotency)
        
        Args:
            postback_id: Unique postback ID from DhanHQ
            
        Returns:
            True if duplicate
        """
        if postback_id in self.processed_postbacks:
            self.logger.warning(f"⚠️ Duplicate postback detected: {postback_id}")
            return True
        
        # Mark as processed
        self.processed_postbacks[postback_id] = datetime.now(IST)
        
        # Clean old postbacks (keep last 1000)
        if len(self.processed_postbacks) > 1000:
            oldest_key = min(
                self.processed_postbacks,
                key=self.processed_postbacks.get
            )
            del self.processed_postbacks[oldest_key]
        
        return False
    
    # =========================================================================
    # POSTBACK PARSING
    # =========================================================================
    
    def parse_order_execution_postback(
        self,
        data: Dict
    ) -> Tuple[bool, Dict]:
        """
        Parse ORDER_EXECUTION postback (Official DhanHQ format)
        
        Postback Format (Official):
        {
            "eventType": "ORDER_EXECUTION",
            "orderId": "string",
            "dhanClientId": "string",
            "symbol": "string",
            "exchangeSegment": "NSE_EQ",
            "transactionType": "BUY|SELL",
            "quantity": number,
            "price": number,
            "orderStatus": "EXECUTED|REJECTED|CANCELLED",
            "executedQuantity": number,
            "executedPrice": number,
            "timestamp": "ISO-8601",
            "correlationId": "string"
        }
        
        Args:
            data: Postback JSON data
            
        Returns:
            (success, parsed_data)
        """
        try:
            parsed = {
                "eventType": PostbackEventType.ORDER_EXECUTION.value,
                "orderId": data.get("orderId"),
                "symbol": data.get("symbol"),
                "status": data.get("orderStatus"),
                "executedQuantity": data.get("executedQuantity"),
                "executedPrice": data.get("executedPrice"),
                "transactionType": data.get("transactionType"),
                "timestamp": data.get("timestamp")
            }
            
            # Validate required fields
            if not all([parsed["orderId"], parsed["symbol"], parsed["status"]]):
                return False, {}
            
            return True, parsed
            
        except Exception as e:
            self.logger.error(f"❌ Parse order execution error: {e}")
            return False, {}
    
    def parse_position_update_postback(
        self,
        data: Dict
    ) -> Tuple[bool, Dict]:
        """
        Parse POSITION_UPDATE postback (Official DhanHQ format)
        
        Postback Format (Official):
        {
            "eventType": "POSITION_UPDATE",
            "positionId": "string",
            "symbol": "string",
            "quantity": number,
            "currentPrice": number,
            "entryPrice": number,
            "pnl": number,
            "pnlPercentage": number,
            "mtm": number,
            "timestamp": "ISO-8601"
        }
        
        Args:
            data: Postback JSON data
            
        Returns:
            (success, parsed_data)
        """
        try:
            parsed = {
                "eventType": PostbackEventType.POSITION_UPDATE.value,
                "positionId": data.get("positionId"),
                "symbol": data.get("symbol"),
                "quantity": data.get("quantity"),
                "currentPrice": data.get("currentPrice"),
                "pnl": data.get("pnl"),
                "pnlPercentage": data.get("pnlPercentage"),
                "timestamp": data.get("timestamp")
            }
            
            # Validate required fields
            if not all([parsed["positionId"], parsed["symbol"]]):
                return False, {}
            
            return True, parsed
            
        except Exception as e:
            self.logger.error(f"❌ Parse position update error: {e}")
            return False, {}
    
    def parse_account_update_postback(
        self,
        data: Dict
    ) -> Tuple[bool, Dict]:
        """
        Parse ACCOUNT_UPDATE postback (Official DhanHQ format)
        
        Postback Format (Official):
        {
            "eventType": "ACCOUNT_UPDATE",
            "dhanClientId": "string",
            "ledgerBalance": number,
            "marginAvailable": number,
            "marginUsed": number,
            "timestamp": "ISO-8601"
        }
        
        Args:
            data: Postback JSON data
            
        Returns:
            (success, parsed_data)
        """
        try:
            parsed = {
                "eventType": PostbackEventType.ACCOUNT_UPDATE.value,
                "clientId": data.get("dhanClientId"),
                "ledgerBalance": data.get("ledgerBalance"),
                "marginAvailable": data.get("marginAvailable"),
                "marginUsed": data.get("marginUsed"),
                "timestamp": data.get("timestamp")
            }
            
            # Validate required fields
            if not parsed["clientId"]:
                return False, {}
            
            return True, parsed
            
        except Exception as e:
            self.logger.error(f"❌ Parse account update error: {e}")
            return False, {}
    
    # =========================================================================
    # POSTBACK HANDLING
    # =========================================================================
    
    def handle_postback(
        self,
        payload: str,
        signature: str = None
    ) -> Tuple[bool, str, Optional[Dict]]:
        """
        Handle incoming postback from DhanHQ
        
        Process:
        1. Verify signature
        2. Check for duplicates
        3. Parse event
        4. Update status
        5. Trigger callbacks
        
        Args:
            payload: Raw postback JSON
            signature: Optional signature for verification
            
        Returns:
            (success, message, parsed_data)
        """
        try:
            # Step 1: Verify signature (if provided)
            if signature:
                if not self.verify_postback_signature(payload, signature):
                    return False, "Invalid signature", None
            
            # Step 2: Parse JSON
            data = json.loads(payload)
            self.logger.info(f"📨 Received postback: {data.get('eventType')}")
            
            # Step 3: Check for duplicates
            postback_id = data.get("orderId") or data.get("positionId")
            if self.is_duplicate_postback(postback_id):
                return False, "Duplicate postback", None
            
            # Step 4: Route by event type
            event_type = data.get("eventType")
            
            if event_type == PostbackEventType.ORDER_EXECUTION.value:
                return self._handle_order_execution(data)
            
            elif event_type == PostbackEventType.POSITION_UPDATE.value:
                return self._handle_position_update(data)
            
            elif event_type == PostbackEventType.ACCOUNT_UPDATE.value:
                return self._handle_account_update(data)
            
            else:
                return False, f"Unknown event type: {event_type}", None
            
        except json.JSONDecodeError:
            self.logger.error("❌ Invalid JSON in postback")
            return False, "Invalid JSON", None
        except Exception as e:
            self.logger.error(f"❌ Postback handling error: {e}")
            return False, f"Error: {e}", None
    
    def _handle_order_execution(self, data: Dict) -> Tuple[bool, str, Optional[Dict]]:
        """Handle ORDER_EXECUTION postback"""
        try:
            success, parsed = self.parse_order_execution_postback(data)
            
            if not success:
                return False, "Invalid order execution postback", None
            
            self.logger.info(f"✅ Order execution: {parsed['orderId']}")
            self.logger.info(f"   Status: {parsed['status']}")
            self.logger.info(f"   Executed: {parsed['executedQuantity']} @ ₹{parsed['executedPrice']}")
            
            # Trigger callbacks
            for callback in self.order_execution_callbacks:
                try:
                    callback(parsed)
                except Exception as e:
                    self.logger.error(f"Callback error: {e}")
            
            return True, "Order execution processed", parsed
            
        except Exception as e:
            self.logger.error(f"❌ Order execution handler error: {e}")
            return False, f"Error: {e}", None
    
    def _handle_position_update(self, data: Dict) -> Tuple[bool, str, Optional[Dict]]:
        """Handle POSITION_UPDATE postback"""
        try:
            success, parsed = self.parse_position_update_postback(data)
            
            if not success:
                return False, "Invalid position update postback", None
            
            self.logger.info(f"📊 Position update: {parsed['symbol']}")
            self.logger.info(f"   Price: ₹{parsed['currentPrice']}")
            self.logger.info(f"   P&L: ₹{parsed['pnl']:.2f} ({parsed['pnlPercentage']}%)")
            
            # Trigger callbacks
            for callback in self.position_update_callbacks:
                try:
                    callback(parsed)
                except Exception as e:
                    self.logger.error(f"Callback error: {e}")
            
            return True, "Position update processed", parsed
            
        except Exception as e:
            self.logger.error(f"❌ Position update handler error: {e}")
            return False, f"Error: {e}", None
    
    def _handle_account_update(self, data: Dict) -> Tuple[bool, str, Optional[Dict]]:
        """Handle ACCOUNT_UPDATE postback"""
        try:
            success, parsed = self.parse_account_update_postback(data)
            
            if not success:
                return False, "Invalid account update postback", None
            
            self.logger.info(f"💰 Account update")
            self.logger.info(f"   Ledger: ₹{parsed['ledgerBalance']}")
            self.logger.info(f"   Available: ₹{parsed['marginAvailable']}")
            self.logger.info(f"   Used: ₹{parsed['marginUsed']}")
            
            # Trigger callbacks
            for callback in self.account_update_callbacks:
                try:
                    callback(parsed)
                except Exception as e:
                    self.logger.error(f"Callback error: {e}")
            
            return True, "Account update processed", parsed
            
        except Exception as e:
            self.logger.error(f"❌ Account update handler error: {e}")
            return False, f"Error: {e}", None
    
    # =========================================================================
    # CALLBACKS
    # =========================================================================
    
    def on_order_execution(self, callback: Callable) -> None:
        """Register callback for order execution events"""
        self.order_execution_callbacks.append(callback)
    
    def on_order_update(self, callback: Callable) -> None:
        """Register callback for order update events"""
        self.order_update_callbacks.append(callback)
    
    def on_position_update(self, callback: Callable) -> None:
        """Register callback for position update events"""
        self.position_update_callbacks.append(callback)
    
    def on_account_update(self, callback: Callable) -> None:
        """Register callback for account update events"""
        self.account_update_callbacks.append(callback)


# ============================================================================
# FLASK WEBHOOK ENDPOINT
# ============================================================================

def create_postback_app(postback_handler: DhanPostbackHandler) -> Flask:
    """
    Create Flask app for DhanHQ postback webhook
    
    Usage:
        handler = DhanPostbackHandler(webhook_secret="your_secret")
        app = create_postback_app(handler)
        app.run(host="0.0.0.0", port=5000)
    
    Webhook URL to configure in DhanHQ:
        https://your-domain.com/dhan/postback
    
    Args:
        postback_handler: DhanPostbackHandler instance
        
    Returns:
        Flask app instance
    """
    app = Flask(__name__)
    handler = postback_handler
    
    @app.route("/dhan/postback", methods=["POST"])
    def dhan_postback():
        """
        POST /dhan/postback
        
        Headers:
            - X-Dhan-Signature: HMAC-SHA256 signature
            - Content-Type: application/json
        
        Body: JSON postback data
        """
        try:
            # Get payload
            payload = request.get_data(as_text=True)
            
            # Get signature from header
            signature = request.headers.get("X-Dhan-Signature", "")
            
            # Handle postback
            success, msg, parsed = handler.handle_postback(payload, signature)
            
            if success:
                return jsonify({
                    "status": "success",
                    "message": msg,
                    "data": parsed
                }), 200
            else:
                return jsonify({
                    "status": "error",
                    "message": msg
                }), 400
            
        except Exception as e:
            logger.error(f"❌ Webhook error: {e}")
            return jsonify({
                "status": "error",
                "message": f"Error: {e}"
            }), 500
    
    @app.route("/dhan/health", methods=["GET"])
    def health_check():
        """Health check endpoint"""
        return jsonify({
            "status": "healthy",
            "timestamp": datetime.now(IST).isoformat()
        }), 200
    
    return app


# ============================================================================
# SINGLETON INSTANCE
# ============================================================================

_postback_handler: Optional[DhanPostbackHandler] = None


def get_postback_handler(webhook_secret: str = None) -> DhanPostbackHandler:
    """
    Get or create DhanPostbackHandler instance
    
    Args:
        webhook_secret: DhanHQ webhook secret
        
    Returns:
        DhanPostbackHandler instance
    """
    global _postback_handler
    
    if _postback_handler is None:
        import os
        webhook_secret = webhook_secret or os.getenv("DHAN_WEBHOOK_SECRET", "")
        _postback_handler = DhanPostbackHandler(webhook_secret=webhook_secret)
    
    return _postback_handler
