from __future__ import annotations

from datetime import datetime

from flask import Flask, jsonify

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


@app.route('/api/control/start', methods=['POST'])
def api_start():
    return jsonify({"started": screener_controller.start()}), 200


@app.route('/api/control/stop', methods=['POST'])
def api_stop():
    return jsonify({"stopped": screener_controller.stop()}), 200


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
    app.run(host='0.0.0.0', port=5000)
