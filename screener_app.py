from __future__ import annotations

import hmac
import os
from datetime import datetime

from flask import Flask, jsonify, request

from main_screener import screener_controller


app = Flask(__name__)


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


@app.route('/api/control/mobile', methods=['POST'])
def api_mobile_control():
    return jsonify({"message": "Use /api/control/start|stop|pause|resume endpoints"}), 200


@app.errorhandler(404)
def not_found(_):
    return jsonify({"error": "Endpoint not found"}), 404


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv("PORT", "5000")))
