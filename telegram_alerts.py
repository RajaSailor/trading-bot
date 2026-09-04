from datetime import datetime
from typing import Optional
import requests
import pytz

IST = pytz.timezone("Asia/Kolkata")


class TelegramAlertService:
    def __init__(self, token: Optional[str] = None):
        self.token = token

    def format_signal(self, signal: dict) -> str:
        side_header = "🚀 CALL ENTRY" if signal["side"] == "CALL" else "📉 PUT ENTRY"
        strike_line = "N/A (SPOT)" if signal.get("asset_class") == "CRYPTO" else f"{signal['ce_strike']} CE / {signal['pe_strike']} PE"
        premium_line = "N/A" if signal.get("asset_class") == "CRYPTO" else f"₹{signal['premium']:.2f}"

        return (
            f"{side_header}\n\n"
            f"Symbol: {signal['symbol']} | {signal['timeframe']} Breakout\n"
            f"Strike: {strike_line}\n"
            f"Premium (LTP): {premium_line}\n\n"
            "📊 POSITION DETAILS:\n"
            f"Entry: {signal['entry']} (Previous RED HIGH)\n"
            f"Target 1: {signal['target_1']} (+10 points)\n"
            f"Target 2: {signal['target_2']} (+20 points)\n"
            f"Target 3: {signal['target_3']} (+30 points)\n"
            f"Stop Loss: {signal['stop_loss']} (Previous RED LOW)\n\n"
            f"⏰ Time (IST): {datetime.now(IST).strftime('%H:%M:%S')}\n"
            f"🕐 Timeframe: {signal['timeframe']} BREAKOUT\n"
            f"Candle Analysis: RED candle at {signal['red_candle_time']}, breakout at {signal['breakout_time']}\n\n"
            "📢 DISCLAIMER: Educational purposes only."
        )

    def send(self, chat_id: int, message: str) -> bool:
        if not self.token:
            return False
        try:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            response = requests.post(url, json={"chat_id": chat_id, "text": message}, timeout=8)
            return response.status_code == 200
        except Exception:
            return False
