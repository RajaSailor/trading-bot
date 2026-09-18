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
import atexit
from dotenv import load_dotenv
from datetime import datetime
from uuid import uuid4

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

try:
    from strategy_manager import StrategyManager
except ImportError:
    StrategyManager = None

try:
    from risk_manager import RiskManager
except ImportError:
    RiskManager = None

try:
    from order_executor import OrderExecutor, Order
except ImportError:
    OrderExecutor = None
    Order = None

try:
    from signal_processor import SignalQueueProcessor
except ImportError:
    SignalQueueProcessor = None

try:
    from database import TradingDatabase
except ImportError:
    TradingDatabase = None

try:
    from state_manager import StateManager
except ImportError:
    StateManager = None

try:
    from monitoring.metrics import MetricsCollector
except ImportError:
    MetricsCollector = None

try:
    from monitoring.alerts import AlertManager, TelegramAlertChannel
except ImportError:
    AlertManager = None
    TelegramAlertChannel = None

try:
    from telegram_handler import TelegramHandler
except ImportError:
    TelegramHandler = None

try:
    from token_manager_auto import TokenManagerAuto
except ImportError:
    TokenManagerAuto = None

try:
    from runtime_services import MarketScannerWorker, QueueConsumerWorker, SignalNotifier
except ImportError:
    MarketScannerWorker = None
    QueueConsumerWorker = None
    SignalNotifier = None

from timezone_utils import now_local_iso

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
strategy_manager = None
risk_manager = None
order_executor = None
signal_queue_processor = None
trading_db = None
state_manager = None
metrics_collector = None
alert_manager = None
telegram_handler = None
signal_notifier = None
queue_consumer_worker = None
market_scanner_worker = None
token_manager = None
phase_components = {}
runtime_config = {}
initialized = False


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value == "":
        return float(default)
    try:
        parsed = float(value)
    except ValueError:
        return float(default)
    if parsed > 1 and default <= 1:
        return parsed / 100.0
    return parsed


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return int(default)
    try:
        return int(value)
    except ValueError:
        return int(default)


def _load_runtime_config() -> dict:
    scanner_enabled = _env_bool("ENABLE_MARKET_SCANNER", False)
    practice_mode = _env_bool("PRACTICE_MODE", True)
    return {
        "practice_mode": practice_mode,
        "auto_trading_enabled": (not practice_mode) and _env_bool("AUTO_TRADING_ENABLED", False),
        "max_loss_per_trade": _env_float("MAX_LOSS_PER_TRADE", 0.01),
        "max_position_size": max(1, _env_int("MAX_POSITION_SIZE", 5)),
        "min_rr_ratio": _env_float("MIN_RR_RATIO", 1.5),
        "starting_capital": _env_float("STARTING_CAPITAL", 100000.0),
        "daily_loss_limit": _env_float("DAILY_LOSS_LIMIT", 0.05),
        "max_drawdown": _env_float("MAX_DRAWDOWN", 0.2),
        "max_portfolio_heat": _env_float("MAX_PORTFOLIO_HEAT", 0.06),
        "kelly_fraction": _env_float("KELLY_FRACTION", 0.5),
        "market_start_time": os.getenv("MARKET_START_TIME", "09:15"),
        "market_end_time": os.getenv("MARKET_END_TIME", "15:30"),
        "timezone": os.getenv("TIMEZONE", "Asia/Kolkata"),
        "db_path": os.getenv("TRADING_DB_PATH", "trading.db"),
        "scanner_enabled": scanner_enabled and bool(os.getenv("ACCESS_TOKEN")),
        "scanner_poll_seconds": max(1, _env_int("SCANNER_POLL_SECONDS", 5)),
        "queue_poll_seconds": max(1, _env_int("QUEUE_POLL_SECONDS", 1)),
        "token_renewal_enabled": _env_bool("ENABLE_DHAN_TOKEN_RENEWAL", True),
        "channels": {
            "trade_control": os.getenv("CHANNEL_TRADE_CONTROL_ID"),
            "service_alerts": os.getenv("CHANNEL_SERVICE_ALERTS_ID"),
            "commodity": os.getenv("CHANNEL_COMMODITY_ID"),
            "index": os.getenv("CHANNEL_INDEX_ID"),
            "nifty50_options": os.getenv("CHANNEL_NIFTY50_OPTIONS_ID"),
            "nifty50_5x": os.getenv("CHANNEL_NIFTY50_5X_ID"),
            "nifty50_pay_later": os.getenv("CHANNEL_NIFTY50_PAY_LATER_ID"),
            "crypto": os.getenv("CHANNEL_CRYPTO_ID"),
        },
    }


def _apply_refreshed_token(token: str) -> None:
    if not token or dhan_integration is None:
        return
    api_client = getattr(dhan_integration, "api_client", None)
    if api_client is None:
        return
    if hasattr(api_client, "set_access_token"):
        api_client.set_access_token(token)
        return
    setattr(api_client, "access_token", token)
    headers = getattr(api_client, "headers", None)
    if isinstance(headers, dict):
        if "access-token" in headers:
            headers["access-token"] = token
        if "Authorization" in headers:
            headers["Authorization"] = "Bearer " + token


def _queue_signal_from_payload(payload: dict, notify_acceptance: bool = True):
    if signal_queue_processor is None:
        return None, ("Signal queue processor not initialized", 503)

    signal = signal_queue_processor.parse_webhook_signal(payload)
    if signal is None:
        return None, ("Invalid signal payload", 400)

    signal["entry_price"] = payload.get("entry_price", payload.get("price"))
    signal["target_price"] = payload.get("target_price")
    signal["stop_loss"] = payload.get("stop_loss")
    signal["quantity"] = payload.get("quantity")
    signal["category"] = payload.get("category") or signal.get("category")
    signal["metadata"].update(payload.get("metadata", {}))

    accepted, reason = True, "OK"
    entry_price = 0.0
    stop_loss = 0.0
    quantity = 0
    if signal.get("action") in {"BUY", "SELL"}:
        raw_entry = signal.get("entry_price")
        if raw_entry is not None:
            try:
                entry_price = float(raw_entry)
            except (TypeError, ValueError):
                return None, ("Invalid entry price", 400)
        if entry_price <= 0:
            return None, ("Missing or invalid entry price", 400)

        raw_stop = signal.get("stop_loss", entry_price)
        try:
            stop_loss = float(raw_stop)
        except (TypeError, ValueError):
            return None, ("Invalid stop loss", 400)

        raw_quantity = signal.get("quantity")
        if raw_quantity is not None:
            try:
                quantity = int(raw_quantity)
            except (TypeError, ValueError):
                return None, ("Invalid quantity", 400)
        if quantity < 0:
            return None, ("Invalid quantity", 400)
        if quantity <= 0 and risk_manager is not None:
            quantity = risk_manager.calculate_position_size(entry_price, stop_loss)
        if quantity <= 0:
            quantity = 1
        quantity = min(quantity, int(runtime_config.get("max_position_size", quantity)))
        signal["entry_price"] = entry_price
        signal["stop_loss"] = stop_loss
        signal["quantity"] = quantity

    if risk_manager is not None and signal.get("action") in {"BUY", "SELL"}:
        proposed_trade_risk = max(0.0, abs(entry_price - stop_loss) * quantity)
        accepted, reason = risk_manager.can_take_trade(proposed_trade_risk, [])
        if not accepted:
            if signal_notifier is not None:
                signal_notifier.notify_service_alert("Risk check rejected signal", reason)
            return None, (reason, 400)

    if not signal_queue_processor.enqueue_signal(signal):
        return None, ("Signal rejected by queue", 409)

    if trading_db is not None:
        try:
            trading_db.log_signal(signal)
        except Exception as db_error:
            logger.warning("Signal accepted but could not be persisted: %s", db_error.__class__.__name__)

    if signal_notifier is not None:
        if notify_acceptance:
            signal_notifier.notify_signal_accepted(signal)
        if signal.get("metadata", {}).get("source") == "scanner":
            signal_notifier.notify_strategy_signal(signal)

    return {
        "status": "accepted",
        "signal": signal,
        "queue_size": signal_queue_processor.queue_size(),
        "proposed_quantity": quantity if signal.get("action") in {"BUY", "SELL"} else 0,
        "risk": {"accepted": accepted, "reason": reason},
    }, None

# ============================================================================
# INITIALIZATION
# ============================================================================

def initialize_app():
    """Initialize all application components"""
    global dhan_bridge, postback_handler, dhan_integration
    global strategy_manager, risk_manager, order_executor, signal_queue_processor
    global trading_db, state_manager, metrics_collector, alert_manager
    global telegram_handler, signal_notifier, queue_consumer_worker, market_scanner_worker, token_manager
    global phase_components, runtime_config, initialized

    if initialized:
        logger.info("♻️ Re-initializing application components...")
        if trading_db:
            try:
                trading_db.close()
            except Exception:
                pass
        if state_manager:
            try:
                state_manager.set_bot_status("stopped")
                state_manager.end_session()
            except Exception:
                pass
        dhan_bridge = None
        postback_handler = None
        dhan_integration = None
        strategy_manager = None
        risk_manager = None
        order_executor = None
        signal_queue_processor = None
        trading_db = None
        state_manager = None
        metrics_collector = None
        alert_manager = None
        telegram_handler = None
        signal_notifier = None
        if queue_consumer_worker:
            queue_consumer_worker.stop()
        if market_scanner_worker:
            market_scanner_worker.stop()
        if token_manager:
            token_manager.stop()
        queue_consumer_worker = None
        market_scanner_worker = None
        token_manager = None
        phase_components.clear()
        initialized = False
    
    logger.info("=" * 70)
    logger.info("🚀 DhanHQ Trading Bot - Initializing...")
    logger.info("=" * 70)
    
    try:
        runtime_config = _load_runtime_config()

        # 1. Initialize DhanHQ Integration
        logger.info("1️⃣ Initializing DhanHQ Integration...")
        dhan_integration = get_dhan_integration(
            practice_mode=runtime_config["practice_mode"],
            max_loss=runtime_config["max_loss_per_trade"],
            max_position=runtime_config["max_position_size"],
        )
        phase_components["dhan_integration"] = True
        logger.info("   ✅ DhanHQ Integration initialized")
        
        # 2. Initialize Telegram Bridge
        logger.info("2️⃣ Initializing Telegram Bridge...")
        dhan_bridge = DhanTelegramBridge(dhan_integration)
        phase_components["telegram_bridge"] = True
        logger.info("   ✅ Telegram Bridge initialized")
        
        # 3. Initialize Postback Handler
        logger.info("3️⃣ Initializing Postback Handler...")
        webhook_secret = os.getenv("WEBHOOK_SECRET", "")
        postback_handler = DhanPostbackHandler(webhook_secret)
        phase_components["postback_handler"] = True
        logger.info("   ✅ Postback Handler initialized")

        logger.info("4️⃣ Initializing Strategy Manager...")
        if StrategyManager is not None:
            strategy_manager = StrategyManager()
            phase_components["strategy_manager"] = True
            logger.info("   ✅ Strategy Manager initialized")
        else:
            phase_components["strategy_manager"] = False
            logger.warning("   ⚠️ Strategy Manager module unavailable")

        logger.info("5️⃣ Initializing Risk Manager...")
        if RiskManager is not None:
            risk_manager = RiskManager(
                capital=runtime_config["starting_capital"],
                risk_per_trade=runtime_config["max_loss_per_trade"],
                daily_loss_limit=runtime_config["daily_loss_limit"],
                max_drawdown=runtime_config["max_drawdown"],
                max_portfolio_heat=runtime_config["max_portfolio_heat"],
                kelly_fraction=runtime_config["kelly_fraction"],
            )
            phase_components["risk_manager"] = True
            logger.info("   ✅ Risk Manager initialized")
        else:
            phase_components["risk_manager"] = False
            logger.warning("   ⚠️ Risk Manager module unavailable")

        logger.info("6️⃣ Initializing Order Executor...")
        if OrderExecutor is not None:
            order_executor = OrderExecutor()
            phase_components["order_executor"] = True
            logger.info("   ✅ Order Executor initialized")
        else:
            phase_components["order_executor"] = False
            logger.warning("   ⚠️ Order Executor module unavailable")

        logger.info("7️⃣ Initializing Signal Queue Processor...")
        if SignalQueueProcessor is not None:
            signal_queue_processor = SignalQueueProcessor()
            phase_components["signal_queue_processor"] = True
            logger.info("   ✅ Signal Queue Processor initialized")
        else:
            phase_components["signal_queue_processor"] = False
            logger.warning("   ⚠️ Signal queue module unavailable")

        logger.info("8️⃣ Initializing Trading Database...")
        if TradingDatabase is not None:
            db_path = runtime_config["db_path"]
            try:
                trading_db = TradingDatabase(db_path=db_path)
                phase_components["trading_database"] = True
                logger.info("   ✅ Trading Database initialized")
            except Exception as db_error:
                logger.warning(f"   ⚠️ Trading database fallback to in-memory: {db_error}")
                trading_db = TradingDatabase()
                phase_components["trading_database"] = True
                logger.info("   ✅ Trading Database initialized (in-memory)")
        else:
            phase_components["trading_database"] = False
            logger.warning("   ⚠️ Trading database module unavailable")

        logger.info("9️⃣ Initializing State Manager...")
        if StateManager is not None:
            state_file = os.getenv("STATE_FILE", "bot_state.json")
            state_manager = StateManager(state_file)
            state_manager.set_bot_status("running")
            state_manager.start_session(str(uuid4()))
            state_manager.set_config("practice_mode", runtime_config["practice_mode"])
            state_manager.set_config("timezone", runtime_config["timezone"])
            phase_components["state_manager"] = True
            logger.info("   ✅ State Manager initialized")
        else:
            phase_components["state_manager"] = False
            logger.warning("   ⚠️ State manager module unavailable")

        logger.info("🔟 Initializing Monitoring + Alerts...")
        if MetricsCollector is not None:
            metrics_collector = MetricsCollector()
            phase_components["metrics_collector"] = True
            logger.info("   ✅ Metrics Collector initialized")
        else:
            phase_components["metrics_collector"] = False
            logger.warning("   ⚠️ Metrics collector module unavailable")

        if TelegramHandler is not None:
            telegram_handler = TelegramHandler()
            signal_notifier = SignalNotifier(telegram_handler, metrics_collector) if SignalNotifier is not None else None
        else:
            telegram_handler = None
            signal_notifier = None

        channels = []
        if (
            AlertManager is not None
            and TelegramAlertChannel is not None
            and os.getenv("BOT_SERVICE_ALERTS_TOKEN")
            and runtime_config["channels"].get("service_alerts")
        ):
            channels.append(
                TelegramAlertChannel(
                    bot_token=os.getenv("BOT_SERVICE_ALERTS_TOKEN", ""),
                    chat_id=runtime_config["channels"]["service_alerts"],
                )
            )

        if AlertManager is not None:
            alert_manager = AlertManager(channels=channels)
            phase_components["alert_manager"] = len(channels) > 0
            if channels:
                logger.info("   ✅ Alert Manager initialized")
            else:
                logger.warning("   ⚠️ Alert Manager initialized without delivery channels")
        else:
            phase_components["alert_manager"] = False
            logger.warning("   ⚠️ Alert manager module unavailable")

        if (
            QueueConsumerWorker is not None
            and signal_queue_processor is not None
            and order_executor is not None
            and signal_notifier is not None
        ):
            queue_consumer_worker = QueueConsumerWorker(
                queue_processor=signal_queue_processor,
                order_executor=order_executor,
                trading_db=trading_db,
                runtime_config=runtime_config,
                notifier=signal_notifier,
                metrics_collector=metrics_collector,
                dhan_integration=dhan_integration,
            )
            queue_started = queue_consumer_worker.start(runtime_config["queue_poll_seconds"])
            phase_components["queue_consumer"] = True
            logger.info("   ✅ Queue consumer %s", "started" if queue_started else "already running")
        else:
            phase_components["queue_consumer"] = False
            logger.warning("   ⚠️ Queue consumer unavailable")

        if MarketScannerWorker is not None and signal_notifier is not None and signal_queue_processor is not None:
            market_scanner_worker = MarketScannerWorker(
                signal_acceptor=lambda payload, notify: _queue_signal_from_payload(payload, notify)[0] is not None,
                runtime_config=runtime_config,
            )
            scanner_started = market_scanner_worker.start(runtime_config["scanner_poll_seconds"])
            phase_components["market_scanner"] = market_scanner_worker.enabled
            logger.info(
                "   %s Market scanner %s",
                "✅" if market_scanner_worker.enabled else "⚠️",
                "started" if scanner_started else "disabled",
            )
        else:
            phase_components["market_scanner"] = False
            logger.warning("   ⚠️ Market scanner unavailable")

        if TokenManagerAuto is not None:
            token_manager = TokenManagerAuto(on_token_update=_apply_refreshed_token)
            renewal_started = runtime_config["token_renewal_enabled"] and token_manager.start()
            phase_components["token_renewal"] = runtime_config["token_renewal_enabled"]
            logger.info(
                "   %s Token renewal %s",
                "✅" if runtime_config["token_renewal_enabled"] else "⚠️",
                "started" if renewal_started else "idle",
            )
        else:
            phase_components["token_renewal"] = False
            logger.warning("   ⚠️ Token renewal unavailable")
        
        logger.info("=" * 70)
        logger.info("✅ All components initialized successfully!")
        logger.info(f"🧪 Practice Mode: {runtime_config['practice_mode']}")
        logger.info(f"🤖 Auto trading enabled: {runtime_config['auto_trading_enabled']}")
        configured_channels = [name for name, value in runtime_config["channels"].items() if value]
        logger.info(f"📣 Channel keys configured: {configured_channels}")
        logger.info("🗄️ Trading DB path: %s", runtime_config["db_path"])
        logger.info("=" * 70)
        initialized = True
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
            "timestamp": now_local_iso(),
            "service": "dhan-trading-bot",
            "version": "1.0.0",
            "dhan_connected": dhan_integration is not None,
            "telegram_connected": dhan_bridge is not None,
            "telegram_routing": signal_notifier.status() if signal_notifier is not None else {},
            "workers": {
                "queue_consumer": queue_consumer_worker.status() if queue_consumer_worker is not None else {"running": False},
                "market_scanner": market_scanner_worker.status() if market_scanner_worker is not None else {"running": False},
                "token_renewal": token_manager.status() if token_manager is not None else {"running": False},
            },
        }), 200
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")
        return jsonify({
            "status": "unhealthy",
            "error": str(e),
            "timestamp": now_local_iso()
        }), 500

@app.route('/dhan/health', methods=['GET'])
def dhan_health():
    """
    DhanHQ-specific health check
    
    Official DhanHQ API Reference:
    GET https://api.dhan.co/v2/account
    Returns: {"status": "success", "data": {...account info...}}
    """
    try:
        if dhan_integration is None:
            return jsonify({
                "status": "disconnected",
                "message": "DhanHQ integration not initialized",
                "timestamp": now_local_iso()
            }), 503
        
        # Test DhanHQ connection by fetching account info
        # dhan_integration.get_account_info() returns Dict (not tuple)
        account = dhan_integration.get_account_info()
        
        if account and isinstance(account, dict) and account.get('dhanClientId'):
            return jsonify({
                "status": "healthy",
                "dhan_connected": True,
                "account_info": {
                    "dhanClientId": account.get('dhanClientId'),
                    "ledgerBalance": account.get('ledgerBalance'),
                    "marginAvailable": account.get('marginAvailable'),
                    "marginUsed": account.get('marginUsed')
                },
                "timestamp": now_local_iso()
            }), 200
        else:
            return jsonify({
                "status": "unhealthy",
                "dhan_connected": False,
                "message": "Could not fetch account information from DhanHQ",
                "timestamp": now_local_iso()
            }), 503
            
    except Exception as e:
        logger.error(f"DhanHQ health check failed: {str(e)}")
        return jsonify({
            "status": "error",
            "error": str(e),
            "timestamp": now_local_iso()
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
                "timestamp": now_local_iso()
            }), 200
        else:
            logger.warning(f"⚠️ Postback processing failed: {msg}")
            return jsonify({
                "status": "error",
                "message": msg,
                "timestamp": now_local_iso()
            }), 400
            
    except Exception as e:
        logger.error(f"❌ Postback handler error: {str(e)}", exc_info=True)
        return jsonify({
            "status": "error",
            "error": str(e),
            "timestamp": now_local_iso()
        }), 500

@app.route('/api/status', methods=['GET'])
def api_status():
    """Get current bot status"""
    try:
        if dhan_integration is None:
            return jsonify({
                "status": "not_ready",
                "message": "Bot not initialized",
                "timestamp": now_local_iso()
            }), 503
        
        status_info = {
            "status": "running",
            "timestamp": now_local_iso(),
            "bot": {
                "telegram_connected": dhan_bridge is not None,
                "postback_handler_ready": postback_handler is not None,
                "telegram_routing": signal_notifier.status() if signal_notifier is not None else {},
            },
            "dhan": {
                "practice_mode": runtime_config.get("practice_mode", True),
                "auto_trading_enabled": runtime_config.get("auto_trading_enabled", False),
            },
            "phase_components": phase_components,
            "workers": {
                "queue_consumer": queue_consumer_worker.status() if queue_consumer_worker is not None else {"running": False},
                "market_scanner": market_scanner_worker.status() if market_scanner_worker is not None else {"running": False},
                "token_renewal": token_manager.status() if token_manager is not None else {"running": False},
            },
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
            "timestamp": now_local_iso()
        }), 500

@app.route('/webhook', methods=['POST'])
def process_signal_webhook():
    """Phase 3+ webhook endpoint for queue-based signal processing."""
    try:
        payload = request.get_json(silent=True) or {}
        response, error = _queue_signal_from_payload(payload, notify_acceptance=True)
        if error:
            message, status = error
            payload = {"status": "rejected" if status in {400, 409} else "error", "message": message}
            return jsonify(payload), status
        return jsonify(response), 202
    except Exception as e:
        logger.error(f"Webhook processing failed: {str(e)}", exc_info=True)
        if metrics_collector is not None:
            metrics_collector.record_error("webhook", "processing_error")
        return jsonify({"status": "error", "error": "Webhook processing failed"}), 500

@app.route('/strategy', methods=['GET'])
def strategy_status():
    if strategy_manager is None:
        return jsonify({"status": "unavailable", "strategies": []}), 503
    registered = strategy_manager.get_registered_strategies() if hasattr(strategy_manager, "get_registered_strategies") else []
    configs = strategy_manager.get_strategy_configs() if hasattr(strategy_manager, "get_strategy_configs") else {}
    return jsonify({
        "status": "ok",
        "strategy_count": len(registered),
        "config_count": len(configs),
    }), 200

@app.route('/risk', methods=['GET'])
def risk_status():
    if risk_manager is None:
        return jsonify({"status": "unavailable"}), 503
    return jsonify({
        "status": "ok",
        "practice_mode": runtime_config.get("practice_mode", True),
        "auto_trading_enabled": runtime_config.get("auto_trading_enabled", False),
        "risk_per_trade": risk_manager.risk_per_trade,
        "daily_loss_limit": risk_manager.daily_loss_limit,
        "max_drawdown": risk_manager.max_drawdown,
        "max_position_size": runtime_config.get("max_position_size"),
        "min_rr_ratio": runtime_config.get("min_rr_ratio"),
        "drawdown": risk_manager.current_drawdown(),
        "daily_loss_limit_breached": risk_manager.is_daily_loss_limit_breached(),
    }), 200

@app.route('/orders', methods=['GET'])
def order_status():
    if order_executor is None:
        return jsonify({"status": "unavailable", "orders": []}), 503
    orders = []
    for order in order_executor.orders.values():
        orders.append({
            "order_id": order.order_id,
            "symbol": order.symbol,
            "side": order.side,
            "quantity": order.quantity,
            "filled_quantity": order.filled_quantity,
            "status": order.status.value,
            "route": order.route,
            "created_at": order.created_at,
            "updated_at": order.updated_at,
        })
    return jsonify({"status": "ok", "count": len(orders), "orders": orders}), 200

@app.route('/positions', methods=['GET'])
def positions_status():
    if trading_db is None:
        return jsonify({"status": "unavailable", "positions": []}), 503
    if hasattr(trading_db, "fetch_latest_positions"):
        rows = trading_db.fetch_latest_positions()
        positions = [dict(row) for row in rows]
    else:
        rows = [dict(row) for row in trading_db.fetch_all("position_snapshots")]
        latest_by_symbol = {}
        for row in rows:
            latest_by_symbol[row["symbol"]] = row
        positions = list(latest_by_symbol.values())
    return jsonify({"status": "ok", "count": len(positions), "positions": positions}), 200

@app.route('/metrics', methods=['GET'])
def metrics_status():
    if metrics_collector is None:
        return jsonify({"status": "unavailable", "metrics": {}}), 503
    return jsonify(metrics_collector.snapshot()), 200


@app.route('/telegram/test', methods=['POST'])
def telegram_test():
    if runtime_config.get("practice_mode") is not True:
        return jsonify({"status": "forbidden", "message": "Telegram test is practice-only"}), 403
    configured_secret = os.getenv("TELEGRAM_TEST_SECRET", "")
    if not configured_secret:
        return jsonify({"status": "unavailable", "message": "Telegram test secret not configured"}), 503
    provided_secret = request.headers.get("X-Webhook-Secret", "")
    if provided_secret != configured_secret:
        return jsonify({"status": "forbidden", "message": "Invalid test secret"}), 403
    if signal_notifier is None:
        return jsonify({"status": "unavailable", "message": "Telegram routing not initialized"}), 503
    payload = request.get_json(silent=True) or {}
    channel = payload.get("channel", "service_alerts")
    message = payload.get("message", "Practice-safe Telegram routing test")
    sent = signal_notifier.send_test_message(channel, message)
    if not sent:
        return jsonify({"status": "error", "message": "Telegram delivery failed", "channel": channel}), 502
    return jsonify({"status": "sent", "channel": channel, "timestamp": now_local_iso()}), 200

@app.route('/dashboard/health', methods=['GET'])
def dashboard_health():
    metrics_ready = metrics_collector is not None
    return jsonify({
        "status": "ok" if metrics_ready else "degraded",
        "healthy": metrics_ready,
        "timestamp": now_local_iso(),
    }), 200 if metrics_ready else 503

@app.route('/api/stats', methods=['GET'])
def api_stats():
    """Get trading statistics"""
    try:
        if dhan_integration is None:
            return jsonify({
                "status": "not_ready",
                "timestamp": now_local_iso()
            }), 503
        
        stats = dhan_integration.get_statistics()
        return jsonify({
            "status": "success",
            "stats": stats,
            "timestamp": now_local_iso()
        }), 200
        
    except Exception as e:
        logger.error(f"Stats endpoint error: {str(e)}")
        return jsonify({
            "status": "error",
            "error": str(e),
            "timestamp": now_local_iso()
        }), 500

@app.route('/', methods=['GET'])
def index():
    """Root endpoint - API documentation"""
    return jsonify({
        "name": "DhanHQ Trading Bot",
        "version": "1.0.0",
        "status": "running",
        "timestamp": now_local_iso(),
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
            },
            "webhook": {
                "url": "/webhook",
                "method": "POST",
                "description": "Queue/process Phase 3+ webhook signals"
            },
            "strategy": {
                "url": "/strategy",
                "method": "GET",
                "description": "Strategy registry status"
            },
            "risk": {
                "url": "/risk",
                "method": "GET",
                "description": "Risk manager configuration/status"
            },
            "orders": {
                "url": "/orders",
                "method": "GET",
                "description": "Order executor lifecycle status"
            },
            "positions": {
                "url": "/positions",
                "method": "GET",
                "description": "Database-backed position snapshots"
            },
            "metrics": {
                "url": "/metrics",
                "method": "GET",
                "description": "Monitoring metrics snapshot"
            },
            "dashboard_health": {
                "url": "/dashboard/health",
                "method": "GET",
                "description": "Monitoring dashboard health status"
            },
            "telegram_test": {
                "url": "/telegram/test",
                "method": "POST",
                "description": "Practice-safe Telegram routing test"
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
        "available_endpoints": [
            "/", "/health", "/dhan/health", "/dhan/postback", "/api/status", "/api/stats",
            "/webhook", "/strategy", "/risk", "/orders", "/positions", "/metrics", "/dashboard/health", "/telegram/test"
        ],
        "timestamp": now_local_iso()
    }), 404

@app.errorhandler(500)
def internal_error(error):
    """500 error handler"""
    logger.error(f"Internal server error: {str(error)}", exc_info=True)
    return jsonify({
        "status": "error",
        "error": "Internal server error",
        "timestamp": now_local_iso()
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

    if market_scanner_worker:
        market_scanner_worker.stop()
        logger.info("✅ Market scanner stopped")

    if queue_consumer_worker:
        queue_consumer_worker.stop()
        logger.info("✅ Queue consumer stopped")

    if token_manager:
        token_manager.stop()
        logger.info("✅ Token renewal stopped")
    
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

    if trading_db:
        try:
            trading_db.close()
            logger.info("✅ Trading Database closed")
        except Exception as e:
            logger.error(f"Error closing Trading Database: {e}")

    if state_manager:
        try:
            state_manager.set_bot_status("stopped")
            state_manager.end_session()
            logger.info("✅ State Manager updated")
        except Exception as e:
            logger.error(f"Error updating State Manager: {e}")
    
    logger.info("=" * 70)
    logger.info("✅ Bot shut down complete")
    logger.info("=" * 70)

# ============================================================================
# WSGI APPLICATION ENTRY POINT (For Gunicorn)
# ============================================================================

def create_app():
    """Application factory for WSGI servers"""
    if not initialize_app():
        logger.error("❌ Failed to initialize application")
        raise RuntimeError("Application initialization failed")
    return app

# Initialize app for Gunicorn
if os.getenv('FLASK_ENV') != 'development':
    # Production mode: Initialize for Gunicorn
    initialize_app()
    atexit.register(shutdown_handler)

# ============================================================================
# MAIN ENTRY POINT (For Direct Python Execution)
# ============================================================================

def main():
    """Main entry point for direct Python execution"""
    
    # Display startup banner
    logger.info("=" * 70)
    logger.info("🚀 DhanHQ Trading Bot v1.0.0")
    logger.info("=" * 70)
    logger.info(f"📅 Started at: {now_local_iso()}")
    logger.info(f"🌍 Timezone: {os.getenv('TIMEZONE', 'Asia/Kolkata')}")
    logger.info(f"🧪 Practice Mode: {os.getenv('PRACTICE_MODE', 'true')}")
    logger.info(f"⚙️  Port: {os.getenv('PORT', '5000')}")
    logger.info(f"📡 Server Mode: Direct Python Execution")
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
