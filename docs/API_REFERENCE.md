# API Reference

## Authentication
- Public status routes are unauthenticated.
- Admin webhook test/history routes require `WEBHOOK_SECRET` (header `X-Webhook-Secret` or payload `secret`).

## Core Endpoints
- `GET /health` → service + market + screener health
- `GET /api/status` → running state and timestamps
- `GET /api/stats` → accumulated screener stats
- `GET /api/positions` → active position snapshot
- `GET /api/alerts` → recent alert history
- `POST /api/control/start` → start screener
- `POST /api/control/stop` → stop screener
- `POST /webhook/tradingview` → TradingView payload ingest
- `POST /api/webhook/test` → protected test webhook trigger
- `GET /api/webhook/history?limit=50` → protected webhook history

## Error Handling
- API returns JSON errors with HTTP 4xx/5xx codes.
- Webhook processing sanitizes 5xx responses to avoid leaking internals.

## Rate Limiting
- No framework-level limiter is currently enforced.
- Recommended deployment guardrails:
  - Edge rate limits on webhook/admin endpoints.
  - Alerting when API latency or error rates cross thresholds.
