from __future__ import annotations

import hmac
import logging
import os
import sys
import threading
import time
from datetime import datetime

from flask import Flask, jsonify, request

from main_screener import screener_controller
from market_calendar import MarketCalendar


# Force Python to not use cached bytecode
sys.dont_write_bytecode = True

app = Flask(__name__)
logger = logging.getLogger(__name__)

# Configure logging to show all levels
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Market monitor thread
_market_monitor_thread = None
_market_monitor_stop = threading.Event()


def _authorize_webhook_admin(payload: dict | None = None) -> bool:
    expected_secret = os.getenv("WEBHOOK_SECRET")
    if expected_secret:
        provided_secret = (
            (payload or {}).get("secret")
            or request.headers.get("X-Webhook-Secret")
        )
        return isinstance(provided_secret, str) and hmac.compare_digest(provided_secret, expected_secret)
    return (
        os.getenv("FLASK_ENV", "production").lower() != "production"
        and os.getenv("ENABLE_UNAUTHENTICATED_WEBHOOK_ADMIN", "false").lower() == "true"
    )


def _sanitize_webhook_response(result: dict) -> tuple[dict, int]:
    status_code = int(result.get("status_code", 200))
    payload = dict(result)
    payload.pop("status_code", None)
    if status_code >= 500:
        payload["error"] = "internal webhook processing error"
    return payload, status_code


def _start_market_monitor() -> None:
    """
    Monitor market hours and auto-start/stop screener
    
    Monitoring logic:
    - CRYPTO (TradingView): Always running 24/7
    - COMMODITY (MCX): 9:15 AM - 11:30 PM IST (Mon-Fri)
    - EQUITY (NSE): 9:15 AM - 3:40 PM IST (Mon-Fri)
    """
    global _market_monitor_thread
    
    def monitor_loop():
        crypto_running = False
        commodity_running = False
        equity_running = False
        
        while not _market_monitor_stop.is_set():
            try:
                market_status = MarketCalendar.get_market_status()
                
                # Check individual market status
                nse_open = market_status["markets"]["nse_equity"]["is_open"]
                mcx_open = market_status["markets"]["mcx_commodity"]["is_open"]
                crypto_open = market_status["markets"]["tradingview_crypto"]["is_open"]
                
                # CRYPTO: Always running (24/7)
                if crypto_open and not crypto_running:
                    logger.info("🟢 CRYPTO market available (24/7) - Starting crypto screener")
                    screener_controller.start()
                    crypto_running = True
                
                # COMMODITY: MCX hours (9:15 - 23:30)
                if mcx_open and not commodity_running:
                    logger.info("🟢 COMMODITY market opened (MCX 9:15-23:30) - Monitoring gold/silver/crude")
                    if not crypto_running:
                        screener_controller.start()
                    commodity_running = True
                
                elif not mcx_open and commodity_running:
                    logger.info("🔴 COMMODITY market closed (MCX closed)")
                    commodity_running = False
                    # Only stop if equity also not running
                    if not equity_running and not crypto_running:
                        screener_controller.stop()
                
                # EQUITY: NSE hours (9:15 - 15:40)
                if nse_open and not equity_running:
                    logger.info("🟢 EQUITY market opened (NSE 9:15-15:40) - Starting NIFTY/BANKNIFTY screener")
                    if not crypto_running and not commodity_running:
                        screener_controller.start()
                    equity_running = True
                
                elif not nse_open and equity_running:
                    logger.info("🔴 EQUITY market closed (NSE closed)")
                    equity_running = False
                    # Only stop if commodities also not running
                    if not commodity_running and not crypto_running:
                        screener_controller.stop()
                
                # Screener health check - keep running if ANY market is open
                status = screener_controller.get_status()
                should_run = crypto_running or commodity_running or equity_running
                
                if should_run and not status.get("running"):
                    logger.warning("⚠️ Screener not running but market(s) are open - restarting")
                    screener_controller.start()
                
                elif not should_run and status.get("running"):
                    logger.info("ℹ️ All markets closed - stopping screener")
                    screener_controller.stop()
                
            except Exception as e:
                logger.error(f"Market monitor error: {e}")
            
            time.sleep(30)  # Check every 30 seconds
    
    _market_monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
    _market_monitor_thread.start()
    logger.info("✅ Market monitor started (CRYPTO 24/7, COMMODITY 9:15-23:30, EQUITY 9:15-15:40)")


@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "market": MarketCalendar.get_market_status(),
        "screener": screener_controller.get_status(),
    }), 200


@app.route('/api/status', methods=['GET'])
def api_status():
    return jsonify({
        "status": screener_controller.get_status(),
        "market": MarketCalendar.get_market_status(),
        "timestamp": datetime.utcnow().isoformat(),
    }), 200


@app.route('/api/stats', methods=['GET'])
def api_stats():
    return jsonify(screener_controller.get_stats()), 200


@app.route('/api/positions', methods=['GET'])
def api_positions():
    return jsonify({"positions": screener_controller.get_positions()}), 200


@app.route('/api/alerts', methods=['GET'])
def api_alerts():
    return jsonify({"alerts": screener_controller.get_alerts(limit=100)}), 200


@app.route('/webhook/tradingview', methods=['POST'])
def tradingview_webhook():
    payload = request.get_json(silent=True) or {}
    result = screener_controller.process_tradingview_webhook(
        payload,
        headers=dict(request.headers),
        remote_addr=request.remote_addr,
        test_mode=False,
    )
    payload, status_code = _sanitize_webhook_response(result)
    return jsonify(payload), status_code


@app.route('/api/webhook/history', methods=['GET'])
def webhook_history():
    if not _authorize_webhook_admin():
        return jsonify({"error": "webhook history endpoint is not authorized"}), 403
    try:
        limit = int(request.args.get("limit", "50"))
    except ValueError:
        return jsonify({"error": "limit must be an integer"}), 400
    if limit <= 0:
        return jsonify({"error": "limit must be greater than zero"}), 400
    return jsonify({"history": screener_controller.get_webhook_history(limit=limit)}), 200


@app.route('/api/webhook/test', methods=['POST'])
def webhook_test():
    payload = request.get_json(silent=True) or {}
    if not _authorize_webhook_admin(payload):
        return jsonify({"error": "webhook test endpoint is not authorized"}), 403
    if not payload:
        payload = {
            "ticker": "NIFTY",
            "signal_type": "CALL",
            "entry_price": 18220,
            "stop_loss": 18180,
            "target_1": 18230,
            "target_2": 18240,
            "target_3": 18250,
            "timeframe": "5-MIN",
            "category": "INDEX_OPTIONS",
            "reference_candle": "RED",
            "breakout_candle_time": "10:40:00",
            "previous_candle_high": 18220,
            "previous_candle_low": 18180,
            "current_price": 18225,
        }
    result = screener_controller.process_tradingview_webhook(
        payload,
        headers=dict(request.headers),
        remote_addr=request.remote_addr,
        test_mode=True,
    )
    response_payload, status_code = _sanitize_webhook_response(result)
    return jsonify(response_payload), status_code


@app.route('/health/webhook', methods=['GET'])
def webhook_health():
    return jsonify(screener_controller.get_webhook_health()), 200


@app.route('/api/control/start', methods=['POST'])
def api_start():
    started = screener_controller.start()
    if not started:
        return jsonify({"started": False, "error": "screener already running"}), 409
    return jsonify({"started": True}), 200


@app.route('/api/control/stop', methods=['POST'])
def api_stop():
    stopped = screener_controller.stop()
    if not stopped:
        return jsonify({"stopped": False, "error": "screener not running"}), 409
    return jsonify({"stopped": True}), 200


@app.route('/api/control/pause', methods=['POST'])
def api_pause():
    return jsonify({"paused": screener_controller.pause()}), 200


@app.route('/api/control/resume', methods=['POST'])
def api_resume():
    return jsonify({"resumed": screener_controller.resume()}), 200


@app.route('/api/market/status', methods=['GET'])
def api_market_status():
    """Get detailed market status for all markets"""
    return jsonify(MarketCalendar.get_market_status()), 200


@app.route('/api/control/mobile', methods=['POST'])
def api_mobile_control():
    return jsonify({"message": "Use /api/control/start|stop|pause|resume endpoints"}), 200


@app.errorhandler(404)
def not_found(_):
    return jsonify({"error": "Endpoint not found"}), 404


if __name__ == '__main__':
    logger.info("="*80)
    logger.info("🚀 TRADING BOT SCREENER APP STARTING (CRYPTO 24/7, COMMODITY 9:15-23:30, EQUITY 9:15-15:40)")
    logger.info("="*80)
    
    # Start market monitor (handles all 3 market types)
    _start_market_monitor()
    
    # Get market status
    market_status = MarketCalendar.get_market_status()
    markets = market_status["markets"]
    
    # Log market status
    logger.info("\n📊 CURRENT MARKET STATUS:")
    logger.info(f"  Date: {market_status['date']} ({market_status['day']})")
    logger.info(f"  Time: {market_status['current_time_12h']}")
    logger.info(f"\n  🔹 NSE EQUITY: {markets['nse_equity']['status']}")
    logger.info(f"  🔹 MCX COMMODITY: {markets['mcx_commodity']['status']}")
    logger.info(f"  🔹 TRADINGVIEW CRYPTO: {markets['tradingview_crypto']['status']}")
    
    # Start screener if ANY market is open
    if markets['nse_equity']['is_open'] or markets['mcx_commodity']['is_open'] or markets['tradingview_crypto']['is_open']:
        logger.info("\n🟢 Market(s) are OPEN - Auto-starting screener")
        screener_controller.start()
    else:
        logger.info("\n🔴 All markets are CLOSED - Screener on standby")
    
    logger.info("="*80)
    logger.info("✅ APP INITIALIZATION COMPLETE - Flask server starting...")
    logger.info("="*80 + "\n")
    
    app.run(host='0.0.0.0', port=int(os.getenv("PORT", "5000")))
