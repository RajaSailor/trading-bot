# Troubleshooting

## Common Issues
- **Webhook 403**: Verify `WEBHOOK_SECRET` and request header/payload secret.
- **No Telegram alerts**: Validate bot token/chat ID and channel mapping.
- **Dhan health degraded**: Check `ACCESS_TOKEN`, `DHAN_CLIENT_ID`, and account session validity.
- **High latency**: Inspect dashboard metrics and log analyzer p95 latency.

## Debug Mode
- Set environment to non-production and increase logging verbosity.
- Use `/health`, `/api/status`, and `/api/stats` for quick diagnostics.

## Log Analysis
- Use `tools/log_analyzer.py` to detect error spikes and latency trends.
- Investigate repeated `critical`, `error`, or `exception` patterns first.

## Performance Tuning
- Reduce polling/concurrency pressure during closed-market windows.
- Profile slow API endpoints and route retries through alert thresholds.
