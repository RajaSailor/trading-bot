"""
PRODUCTION SCREENER - HTTP SERVER & API
Handles HTTP requests and serves dashboard API
File: screener_app.py
"""

import os
from flask import Flask, jsonify, request
import logging
from screener_background import (
    start_screener, 
    stop_screener, 
    screener_state,
    PUBLIC_IP
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
        "version": "1.0.0"
    }), 200

@app.route('/ping', methods=['GET'])
def ping():
    """Simple ping endpoint"""
    return jsonify({"pong": True}), 200

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
        "public_ip": screener_state["public_ip"],
        "last_scan": screener_state["last_scan_time"],
        "timestamp": datetime.now().isoformat()
    }), 200

@app.route('/api/logs', methods=['GET'])
def api_logs():
    """Get recent logs/errors"""
    return jsonify({
        "errors": screener_state["errors"][-20:],  # Last 20 errors
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
    return jsonify({"status": "started"}), 200

@app.route('/api/screener/stop', methods=['POST'])
def stop_endpoint():
    """Stop screener"""
    stop_screener()
    return jsonify({"status": "stopped"}), 200

@app.route('/api/screener/restart', methods=['POST'])
def restart_endpoint():
    """Restart screener"""
    global screener_thread
    stop_screener()
    screener_thread = start_screener()
    return jsonify({"status": "restarted"}), 200

# ============================================================================
# INFO ENDPOINTS
# ============================================================================

@app.route('/', methods=['GET'])
def home():
    """Home endpoint"""
    return jsonify({
        "service": "Trading Bot Screener",
        "version": "1.0.0",
        "status": "running",
        "public_ip": PUBLIC_IP,
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
        "name": "Trading Bot Screener",
        "description": "10-minute breakout strategy for NSE, BSE, MCX",
        "public_ip": PUBLIC_IP,
        "capabilities": [
            "Real-time market scanning",
            "10-minute breakout detection",
            "CALL/PUT signal generation",
            "Telegram notifications",
            "Daily health checks",
            "Automatic backups"
        ],
        "markets": ["NIFTY", "BANKNIFTY", "SENSEX", "CRUDEOIL", "NIFTY50 STOCKS"],
        "symbols_monitored": 28,
        "scan_frequency": "Every 10 seconds"
    }), 200

# ============================================================================
# ERROR HANDLERS
# ============================================================================

@app.errorhandler(404)
def not_found(error):
    """Handle 404 errors"""
    return jsonify({"error": "Not found"}), 404

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
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
