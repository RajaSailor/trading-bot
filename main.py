#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DhanHQ Trading Bot - Main Application Entry Point
Production-Ready Automated Trading System with Telegram Integration

PHASE 3: Production Deployment
Status: ✅ Production Ready
Last Updated: September 2026

Official DhanHQ v2 API Reference: https://github.com/Kalaiviswa/dhan-api-v2-docs
"""

import os
import sys
import logging
from dotenv import load_dotenv
from datetime import datetime

# Load environment variables from .env file
load_dotenv()

# ============================================================================
# IMPORT APPLICATION MODULES
# ============================================================================

try:
    from flask import Flask, request, jsonify
    from dhan_telegram_bridge import DhanTelegramBridge
    from dhan_postback_handler import DhanPostbackHandler
    from dhan_integration import get_dhan_integration
    from logging_setup import setup_logging
except ImportError as e:
    print(f"❌ FATAL ERROR: Missing required module: {e}")
    print("Install dependencies: pip install -r requirements.txt")
    sys.exit(1)

# ============================================================================
# CONFIGURATION
# ============================================================================

# Setup logging
logger = setup_logging(
    log_level=os.getenv('LOG_LEVEL', 'INFO'),
    log_file='logs/trading-bot.log'
)

# Flask app configuration
app = Flask(__name__)
app.config['JSON_SORT_KEYS'] = False

# Global instances
dhan_bridge = None
postback_handler = None
dhan_integration = None

# ============================================================================
# INITIALIZATION
# ============================================================================

def initialize_app():
    """Initialize all application components"""
    global dhan_bridge, postback_handler, dhan_integration
    
    logger.info("=" * 70)
    logger.info("🚀 DhanHQ Trading Bot - Initializing...")
    logger.info("=" * 70)
    
    try:
        # 1. Initialize DhanHQ Integration
        logger.info("1️⃣ Initializing DhanHQ Integration...")
        dhan_integration = get_dhan_integration()
        logger.info("   ✅ DhanHQ Integration initialized")
        
        # 2. Initialize Telegram Bridge
        logger.info("2️⃣ Initializing Telegram Bridge...")
        dhan_bridge = DhanTelegramBridge(dhan_integration)
        logger.info("   ✅ Telegram Bridge initialized")
        
        # 3. Initialize Postback Handler
        logger.info("3️⃣ Initializing Postback Handler...")
        postback_handler = DhanPostbackHandler(dhan_integration, dhan_bridge)
        logger.info("   ✅ Postback Handler initialized")
        
        logger.info("=" * 70)
        logger.info("✅ All components initialized successfully!")
        logger.info("=" * 70)
        return True
        
    except Exception as e:
        logger.error(f"❌ Initialization failed: {str(e)}", exc_info=True)
        return False

# ============================================================================
# FLASK ROUTES
# ============================================================================

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    try:
        return jsonify({
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "service": "dhan-trading-bot",
            "version": "1.0.0",
            "dhan_connected": dhan_integration is not None,
            "telegram_connected": dhan_bridge is not None
        }), 200
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")
        return jsonify({
            "status": "unhealthy",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }), 500

@app.route('/dhan/health', methods=['GET'])
def dhan_health():
    """DhanHQ-specific health check"""
    try:
        if dhan_integration is None:
            return jsonify({
                "status": "disconnected",
                "message": "DhanHQ integration not initialized",
                "timestamp": datetime.now().isoformat()
            }), 503
        
        # Test DhanHQ connection
        success, msg, account = dhan_integration.get_account_info()
        
        if success:
            return jsonify({
                "status": "healthy",
                "dhan_connected": True,
                "account_info": account,
                "timestamp": datetime.now().isoformat()
            }), 200
        else:
            return jsonify({
                "status": "unhealthy",
                "dhan_connected": False,
                "message": msg,
                "timestamp": datetime.now().isoformat()
            }), 503
            
    except Exception as e:
        logger.error(f"DhanHQ health check failed: {str(e)}")
        return jsonify({
            "status": "error",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }), 500

@app.route('/dhan/postback', methods=['POST'])
def dhan_postback():
    """
    DhanHQ Webhook Postback Handler
    Receives ORDER_EXECUTION, POSITION_UPDATE, ACCOUNT_UPDATE events
    
    Official DhanHQ Format:
    - Headers: X-Dhan-Signature (HMAC-SHA256)
    - Body: JSON postback event
    """
    try:
        if postback_handler is None:
            logger.error("Postback handler not initialized")
            return jsonify({"error": "Service not ready"}), 503
        
        # Get postback data
        data = request.get_json()
        signature = request.headers.get('X-Dhan-Signature', '')
        
        logger.info(f"📨 Postback received: {data.get('eventType', 'UNKNOWN')}")
        
        # Handle postback
        success, msg = postback_handler.handle_postback(data, signature)
        
        if success:
            logger.info(f"✅ Postback processed: {msg}")
            return jsonify({
                "status": "success",
                "message": msg,
                "timestamp": datetime.now().isoformat()
            }), 200
        else:
            logger.warning(f"⚠️ Postback processing failed: {msg}")
            return jsonify({
                "status": "error",
                "message": msg,
                "timestamp": datetime.now().isoformat()
            }), 400
            
    except Exception as e:
        logger.error(f"❌ Postback handler error: {str(e)}", exc_info=True)
        return jsonify({
            "status": "error",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }), 500

@app.route('/api/status', methods=['GET'])
def api_status():
    """Get current bot status"""
    try:
        if dhan_integration is None:
            return jsonify({
                "status": "not_ready",
                "message": "Bot not initialized",
                "timestamp": datetime.now().isoformat()
            }), 503
        
        status_info = {
            "status": "running",
            "timestamp": datetime.now().isoformat(),
            "bot": {
                "telegram_connected": dhan_bridge is not None,
                "postback_handler_ready": postback_handler is not None
            },
            "dhan": {
                "practice_mode": os.getenv('PRACTICE_MODE', 'true').lower() == 'true',
                "auto_trading_enabled": os.getenv('AUTO_TRADING_ENABLED', 'false').lower() == 'true'
            }
        }
        
        # Get position summary
        try:
            positions = dhan_integration.get_positions()
            status_info["trading"] = {
                "open_positions": len(positions.get('positions', [])),
                "positions": positions
            }
        except Exception as e:
            logger.warning(f"Could not fetch positions: {str(e)}")
            status_info["trading"] = {"error": "Could not fetch positions"}
        
        return jsonify(status_info), 200
        
    except Exception as e:
        logger.error(f"Status endpoint error: {str(e)}")
        return jsonify({
            "status": "error",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }), 500

@app.route('/api/stats', methods=['GET'])
def api_stats():
    """Get trading statistics"""
    try:
        if dhan_integration is None:
            return jsonify({
                "status": "not_ready",
                "timestamp": datetime.now().isoformat()
            }), 503
        
        stats = dhan_integration.get_statistics()
        return jsonify({
            "status": "success",
            "stats": stats,
            "timestamp": datetime.now().isoformat()
        }), 200
        
    except Exception as e:
        logger.error(f"Stats endpoint error: {str(e)}")
        return jsonify({
            "status": "error",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }), 500

@app.route('/', methods=['GET'])
def index():
    """Root endpoint - API documentation"""
    return jsonify({
        "name": "DhanHQ Trading Bot",
        "version": "1.0.0",
        "status": "running",
        "timestamp": datetime.now().isoformat(),
        "endpoints": {
            "health": {
                "url": "/health",
                "method": "GET",
                "description": "Health check endpoint"
            },
            "dhan_health": {
                "url": "/dhan/health",
                "method": "GET",
                "description": "DhanHQ connection health check"
            },
            "dhan_postback": {
                "url": "/dhan/postback",
                "method": "POST",
                "description": "DhanHQ webhook postback handler"
            },
            "api_status": {
                "url": "/api/status",
                "method": "GET",
                "description": "Get current bot status and positions"
            },
            "api_stats": {
                "url": "/api/stats",
                "method": "GET",
                "description": "Get trading statistics"
            }
        },
        "documentation": {
            "readme": "https://github.com/RajaSailor/trading-bot/blob/main/README.md",
            "dhan_api": "https://github.com/Kalaiviswa/dhan-api-v2-docs",
            "deployment": "https://github.com/RajaSailor/trading-bot/blob/main/dhan_deployment_guide.md"
        }
    }), 200

@app.errorhandler(404)
def not_found(error):
    """404 error handler"""
    return jsonify({
        "status": "error",
        "error": "Endpoint not found",
        "available_endpoints": ["/", "/health", "/dhan/health", "/dhan/postback", "/api/status", "/api/stats"],
        "timestamp": datetime.now().isoformat()
    }), 404

@app.errorhandler(500)
def internal_error(error):
    """500 error handler"""
    logger.error(f"Internal server error: {str(error)}", exc_info=True)
    return jsonify({
        "status": "error",
        "error": "Internal server error",
        "timestamp": datetime.now().isoformat()
    }), 500

# ============================================================================
# STARTUP & SHUTDOWN
# ============================================================================

@app.before_request
def before_request():
    """Log incoming requests"""
    logger.debug(f"➡️  {request.method} {request.path}")

@app.after_request
def after_request(response):
    """Log outgoing responses"""
    logger.debug(f"⬅️  {response.status_code} {request.path}")
    return response

def shutdown_handler():
    """Handle graceful shutdown"""
    logger.info("=" * 70)
    logger.info("🛑 Shutting down DhanHQ Trading Bot...")
    logger.info("=" * 70)
    
    if dhan_bridge:
        try:
            dhan_bridge.shutdown()
            logger.info("✅ Telegram Bridge shut down")
        except Exception as e:
            logger.error(f"Error shutting down Telegram Bridge: {e}")
    
    if dhan_integration:
        try:
            dhan_integration.shutdown()
            logger.info("✅ DhanHQ Integration shut down")
        except Exception as e:
            logger.error(f"Error shutting down DhanHQ Integration: {e}")
    
    logger.info("=" * 70)
    logger.info("✅ Bot shut down complete")
    logger.info("=" * 70)

# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

def main():
    """Main entry point"""
    
    # Display startup banner
    logger.info("=" * 70)
    logger.info("🚀 DhanHQ Trading Bot v1.0.0")
    logger.info("=" * 70)
    logger.info(f"📅 Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"🌍 Timezone: {os.getenv('TIMEZONE', 'Asia/Kolkata')}")
    logger.info(f"🧪 Practice Mode: {os.getenv('PRACTICE_MODE', 'true')}")
    logger.info(f"⚙️  Port: {os.getenv('PORT', '5000')}")
    logger.info("=" * 70)
    
    # Initialize application
    if not initialize_app():
        logger.error("❌ Failed to initialize application")
        sys.exit(1)
    
    # Get configuration
    port = int(os.getenv('PORT', 5000))
    host = os.getenv('SERVER_HOST', '0.0.0.0')
    debug = os.getenv('FLASK_ENV', 'production') == 'development'
    
    logger.info(f"📡 Starting Flask server on {host}:{port}...")
    logger.info(f"🔗 API Documentation: http://localhost:{port}/")
    logger.info(f"🏥 Health Check: http://localhost:{port}/health")
    logger.info("=" * 70)
    
    try:
        # Run Flask app
        app.run(
            host=host,
            port=port,
            debug=debug,
            use_reloader=False  # Disable reloader in production
        )
    except KeyboardInterrupt:
        logger.info("\n⚠️  Keyboard interrupt received")
        shutdown_handler()
        sys.exit(0)
    except Exception as e:
        logger.error(f"❌ Application error: {str(e)}", exc_info=True)
        shutdown_handler()
        sys.exit(1)

if __name__ == '__main__':
    main()
