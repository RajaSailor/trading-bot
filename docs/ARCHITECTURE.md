# Architecture

## Components
- **Webhook/API Layer** (`screener_app.py`, `webhook_handler.py`): receives requests and exposes health/status endpoints.
- **Screeners & Strategy Engines** (`screener_*.py`, `strategy_*.py`, `premium_strategy_engine.py`): generate signals.
- **Execution Layer** (`dhan_*`, `trade_*`, `position_manager.py`): order lifecycle and risk controls.
- **Notification Layer** (`telegram_handler.py`, `service_monitor_handler.py`): outbound alerts.
- **Monitoring Layer** (`monitoring/metrics.py`, `monitoring/alerts.py`, `monitoring/dashboard.py`): telemetry, alerts, and runtime analytics.

## Data Flow
1. TradingView/DhanHQ events enter Flask endpoints.
2. Signals are validated and enriched with market data.
3. Strategy/risk checks decide execution eligibility.
4. Orders are placed and position state is updated.
5. Metrics and alerts are emitted for operations visibility.

## Integration Points
- DhanHQ API v2 (orders, positions, account health)
- Telegram bots/channels per category routing
- Optional Slack/email/Telegram operational alert channels

## Design Patterns
- Controller pattern for screener orchestration.
- Adapter-style API clients for DhanHQ/Telegram.
- In-memory metrics snapshot with optional Prometheus exporters.
