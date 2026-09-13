from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


IST = ZoneInfo("Asia/Kolkata")


class ServiceMonitorHandler:
    """Build and send operational service alerts."""

    def __init__(self, telegram_handler) -> None:
        self.telegram_handler = telegram_handler

    def send_status_alert(
        self,
        bot_running: bool,
        active_positions: int,
        pending_trades: int,
        token_expiry_hours: int | None = None,
    ) -> bool:
        token_line = "Token status: unknown"
        if token_expiry_hours is not None:
            token_line = (
                f"Current token expires in: {token_expiry_hours} hours\n"
                "New token will auto-generate @ 8:00 AM tomorrow"
            )

        message = (
            "🔔 SERVICE ALERT\n\n"
            "⚠️ Token Expiry Reminder\n"
            f"{token_line}\n\n"
            f"Bot Status: {'✅ RUNNING' if bot_running else '⏹️ STOPPED'}\n"
            "Market Hours: 9:00 AM - 11:30 PM IST\n"
            f"Active Positions: {active_positions}\n"
            f"Pending Trades: {pending_trades}\n\n"
            "Next scheduled refresh: Tomorrow 8:00 AM IST\n"
            f"Updated: {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S IST')}"
        )
        return self.telegram_handler.send_to_channel("service_alerts", message)
