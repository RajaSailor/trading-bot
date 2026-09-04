import os
import threading
from datetime import datetime

from flask import Flask, jsonify

from screener_background import confirm_next_position, screener_state, start_screener, stop_screener

app = Flask(__name__)
_screener_thread = None
_startup_lock = threading.Lock()
_auto_start_on_request = os.getenv("AUTO_START_ON_REQUEST", "true").lower() == "true"


@app.before_request
def startup():
    global _screener_thread
    if not _auto_start_on_request:
        return
    if _screener_thread is not None and _screener_thread.is_alive():
        return
    with _startup_lock:
        if _screener_thread is None or not _screener_thread.is_alive():
            _screener_thread = start_screener()


@app.route("/health", methods=["GET"])
def health_check():
    return jsonify(
        {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "service": "trading-bot-screener",
            "version": "3.0.0",
            "strategy": "57-instrument multi-timeframe breakout",
            "screener_running": screener_state.get("running", False),
        }
    )


@app.route("/api/status", methods=["GET"])
def api_status():
    return jsonify({"screener": screener_state, "timestamp": datetime.now().isoformat()})


@app.route("/api/screener/start", methods=["POST"])
def start_endpoint():
    global _screener_thread
    if screener_state["running"]:
        return jsonify({"error": "Screener already running"}), 400
    _screener_thread = start_screener()
    return jsonify({"status": "started", "timestamp": datetime.now().isoformat()})


@app.route("/api/screener/stop", methods=["POST"])
def stop_endpoint():
    stop_screener()
    return jsonify({"status": "stopped", "timestamp": datetime.now().isoformat()})


@app.route("/api/positions/confirm-next", methods=["POST"])
def confirm_next():
    confirmed = confirm_next_position()
    return jsonify({"confirmed": confirmed, "timestamp": datetime.now().isoformat()})


@app.route("/info", methods=["GET"])
def info():
    return jsonify(
        {
            "name": "Trading Bot Screener",
            "version": "3.0.0",
            "strategy": {
                "candle_logic": "last 7 candles, most recent RED, breakout above RED HIGH within next 5 candles",
                "put_logic": "same as CALL (RED candle breakout)",
                "targets": "+10/+20/+30 points",
                "position_limit": 5,
            },
            "timeframes": {
                "5min": {
                    "interval_seconds": 10,
                    "instruments": "NIFTY, BANKNIFTY, SENSEX + 50 NIFTY stocks",
                    "channels": [-1003966854994, -1003804613787],
                },
                "15min": {
                    "interval_seconds": 60,
                    "instruments": "GOLD, SILVER, CRUDE OIL, NATURAL GAS + 50 NIFTY intraday 5X",
                    "channels": [-1004403277287, -1004466883026],
                },
                "30min": {
                    "interval_seconds": 60,
                    "instruments": "50 NIFTY pay-later",
                    "channels": [-1003814243881],
                },
                "crypto_5min": {
                    "interval_seconds": 60,
                    "symbols": ["BTC/USD", "ETH/USD"],
                    "channel": -1004482078964,
                },
            },
        }
    )


@app.route("/", methods=["GET"])
def home():
    return jsonify(
        {
            "service": "Trading Bot Screener",
            "status": "running" if screener_state.get("running") else "stopped",
            "endpoints": {
                "health": "/health",
                "status": "/api/status",
                "start": "/api/screener/start",
                "stop": "/api/screener/stop",
                "confirm_next_position": "/api/positions/confirm-next",
                "info": "/info",
            },
        }
    )


if __name__ == "__main__":
    if os.getenv("AUTO_START_SCREENER", "false").lower() == "true":
        with _startup_lock:
            _screener_thread = start_screener()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False, use_reloader=False)
