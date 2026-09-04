from __future__ import annotations

import os
from datetime import datetime

from flask import Flask, jsonify, request

from main_screener import screener_controller


app = Flask(__name__)


@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
    }), 200


@app.route('/api/status', methods=['GET'])
def api_status():
    return jsonify({
        "status": screener_controller.get_status(),
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
    status_code = result.pop("status_code", 200)
    return jsonify(result), status_code


@app.route('/api/webhook/history', methods=['GET'])
def webhook_history():
    limit = int(request.args.get("limit", "50"))
    return jsonify({"history": screener_controller.get_webhook_history(limit=limit)}), 200


@app.route('/api/webhook/test', methods=['POST'])
def webhook_test():
    payload = request.get_json(silent=True) or {
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
    status_code = result.pop("status_code", 200)
    return jsonify(result), status_code


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


@app.route('/api/control/mobile', methods=['POST'])
def api_mobile_control():
    return jsonify({"message": "Use /api/control/start|stop|pause|resume endpoints"}), 200


@app.errorhandler(404)
def not_found(_):
    return jsonify({"error": "Endpoint not found"}), 404


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv("PORT", "5000")))
