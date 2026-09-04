"""
PRODUCTION SCREENER - HTTP SERVER & API
Handles HTTP requests and serves dashboard API
Flask app with multi-bot test endpoints
File: screener_app.py
"""

import os
from flask import Flask, jsonify, request
import logging
from screener_background import (
    start_screener, 
    stop_screener, 
    screener_state
)
from datetime import datetime

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)

# Start screener on app initialization
screener_thread = None

@app.before_request
def startup():
    """Initialize screener on first request"""
    global screener_thread
    if screener_thread is None:
        logger.info("🚀 Starting screener background thread...")
        screener_thread = start_screener()

# ============================================================================
# HEALTH CHECK ENDPOINTS
# ============================================================================

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint for Render"""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "service": "trading-bot-screener",
        "version": "2.0.0",
        "mode": "MULTI-BOT (6 channels + System alerts)",
        "screener_running": screener_state.get("running", False)
    }), 200

@app.route('/ping', methods=['GET'])
def ping():
    """Simple ping endpoint"""
    return jsonify({"pong": True, "timestamp": datetime.now().isoformat()}), 200

# ============================================================================
# API ENDPOINTS - SCREENER STATUS
# ============================================================================

@app.route('/api/status', methods=['GET'])
def api_status():
    """Get current screener status"""
    return jsonify({
        "screener": screener_state,
        "timestamp": datetime.now().isoformat()
    }), 200

@app.route('/api/stats', methods=['GET'])
def api_stats():
    """Get screener statistics"""
    success_rate = 0
    if screener_state["total_scans"] > 0:
        success_rate = (screener_state["successful_scans"] / screener_state["total_scans"]) * 100
    
    return jsonify({
        "total_scans": screener_state["total_scans"],
        "successful_scans": screener_state["successful_scans"],
        "success_rate": f"{success_rate:.1f}%",
        "call_signals": screener_state["call_signals"],
        "put_signals": screener_state["put_signals"],
        "total_signals": screener_state["total_signals"],
        "market_open": screener_state["market_open"],
        "dhan_connected": screener_state["dhan_connected"],
        "telegram_connected": screener_state["telegram_connected"],
        "public_ip": screener_state.get("public_ip", "UNKNOWN"),
        "last_scan": screener_state["last_scan_time"],
        "bot_status": screener_state.get("bot_status", {}),
        "timestamp": datetime.now().isoformat()
    }), 200

@app.route('/api/logs', methods=['GET'])
def api_logs():
    """Get recent logs/errors"""
    return jsonify({
        "errors": screener_state["errors"][-50:],
        "total_errors": len(screener_state["errors"]),
        "timestamp": datetime.now().isoformat()
    }), 200

# ============================================================================
# API ENDPOINTS - CONTROL
# ============================================================================

@app.route('/api/screener/start', methods=['POST'])
def start_endpoint():
    """Start screener"""
    if screener_state["running"]:
        return jsonify({"error": "Screener already running"}), 400
    
    screener_state["running"] = True
    return jsonify({"status": "started", "timestamp": datetime.now().isoformat()}), 200

@app.route('/api/screener/stop', methods=['POST'])
def stop_endpoint():
    """Stop screener"""
    stop_screener()
    return jsonify({"status": "stopped", "timestamp": datetime.now().isoformat()}), 200

@app.route('/api/screener/restart', methods=['POST'])
def restart_endpoint():
    """Restart screener"""
    global screener_thread
    stop_screener()
    import time
    time.sleep(2)
    screener_thread = start_screener()
    return jsonify({"status": "restarted", "timestamp": datetime.now().isoformat()}), 200

# ============================================================================
# INFO ENDPOINTS
# ============================================================================

@app.route('/', methods=['GET'])
def home():
    """Home endpoint"""
    return jsonify({
        "service": "Trading Bot Screener (Multi-Bot Version)",
        "version": "2.0.0",
        "status": "running" if screener_state.get("running") else "stopped",
        "mode": "MULTI-BOT (6 Telegram Channels + 1 System Channel)",
        "public_ip": screener_state.get("public_ip", "UNKNOWN"),
        "endpoints": {
            "health": "/health",
            "ping": "/ping",
            "status": "/api/status",
            "stats": "/api/stats",
            "logs": "/api/logs",
            "start": "/api/screener/start (POST)",
            "stop": "/api/screener/stop (POST)",
            "restart": "/api/screener/restart (POST)"
        }
    }), 200

@app.route('/info', methods=['GET'])
def info():
    """Service information"""
    return jsonify({
        "name": "Trading Bot Screener (Multi-Bot Edition)",
        "version": "2.0.0",
        "description": "10-minute breakout strategy with smart multi-channel routing",
        "public_ip": screener_state.get("public_ip", "UNKNOWN"),
        "trading_channels": {
            "INDEX_FO": {
                "channel": "📊 INDEX OPTIONS ALERTS",
                "channel_id": "-1003966854994",
                "bot": "@winindexoptionsalertsbot",
                "monitors": ["NIFTY", "BANKNIFTY", "SENSEX"]
            },
            "NIFTY50_STOCKS": {
                "channel": "📈 NIFTY 50 STOCKS OPTIONS",
                "channel_id": "-1003804613787",
                "bot": "@winnifty50stocksoptionsalertsbot",
                "monitors": ["All 50 NIFTY Stocks"]
            },
            "COMMODITY_FO": {
                "channel": "⚫ COMMODITY OPTIONS ALERTS",
                "channel_id": "-1004403277287",
                "bot": "@wincommodityoptionsalertsbot",
                "monitors": ["CRUDEOIL", "GOLD", "SILVER", "NATURALGAS"]
            },
            "NIFTY50_INTRADAY": {
                "channel": "⚡️ NIFTY 50 INTRADAY 5X",
                "channel_id": "-1004466883026",
                "bot": "@winnifty50intraday5xalertsbot",
                "monitors": ["All 50 NIFTY Stocks (Spot Intraday)"]
            },
            "NIFTY50_PAYLATER": {
                "channel": "🏦 NIFTY 50 PAY LATER",
                "channel_id": "-1003814243881",
                "bot": "@winnifty50paylateralertsbot",
                "monitors": ["All 50 NIFTY Stocks (Margin/BNPL)"]
            },
            "CRYPTO": {
                "channel": "💰 CRYPTO MARKET ALERTS",
                "channel_id": "-1004482078964",
                "bot": "@wincryptomarketalertsbot",
                "monitors": ["BTCUSD", "ETHUSD"]
            }
        },
        "system_channel": {
            "channel": "🔧 System Alerts & Mobile Control",
            "channel_id": "-1004321977761",
            "messages": ["Daily 5 AM health check", "Error alerts", "Mobile control commands"]
        },
        "strategy": "10-MINUTE CANDLE BREAKOUT (RED/GREEN analysis)",
        "markets": ["NSE F&O", "BSE", "MCX", "TradingView Crypto"],
        "capabilities": [
            "Real-time market scanning",
            "10-minute breakout detection",
            "Smart multi-channel routing",
            "CALL/PUT signal generation",
            "Telegram notifications to 6 channels",
            "System alerts to control channel",
            "Daily health checks (5 AM)",
            "Automatic error alerts"
        ],
        "scan_frequency": "Every 10 seconds",
        "market_hours": {
            "NSE": "9:15 AM - 3:30 PM IST",
            "MCX": "9:00 AM - 11:30 PM IST"
        }
    }), 200

# ============================================================================
# ERROR HANDLERS
# ============================================================================

@app.errorhandler(404)
def not_found(error):
    """Handle 404 errors"""
    return jsonify({"error": "Endpoint not found"}), 404

@app.errorhandler(500)
def internal_error(error):
    """Handle 500 errors"""
    return jsonify({"error": "Internal server error"}), 500

# ============================================================================
# MAIN
# ============================================================================

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"🚀 Starting Flask app on port {port}")
    logger.info(f"Mode: MULTI-BOT (6 Trading Channels + 1 System Channel)")
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
