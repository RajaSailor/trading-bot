"""
Market Calendar - Manages when screener runs based on market hours
Supports multiple market types:
- NSE Equity: 9:15 AM - 3:40 PM IST (Mon-Fri)
- MCX Commodities: 9:15 AM - 11:30 PM IST (Mon-Fri)
- TradingView Crypto: 24/7 (no time restrictions)
"""

import logging
from datetime import datetime, date, time
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


class MarketCalendar:
    """Market trading hours and calendar management"""
    
    # NSE Equity Market hours
    NSE_OPEN = time(9, 15)       # 9:15 AM
    NSE_CLOSE = time(15, 40)     # 3:40 PM
    
    # MCX Commodity Market hours
    MCX_OPEN = time(9, 15)       # 9:15 AM
    MCX_CLOSE = time(23, 30)     # 11:30 PM
    
    # TradingView Crypto (24/7)
    CRYPTO_OPEN = time(0, 0)     # 12:00 AM
    CRYPTO_CLOSE = time(23, 59)  # 11:59 PM
    
    # Pre-market and post-market for alerts
    PRE_MARKET_OPEN = time(9, 0)   # 9:00 AM
    POST_MARKET_CLOSE = time(23, 59) # 11:59 PM
    
    # Weekdays (0=Monday, 6=Sunday)
    TRADING_DAYS = {0, 1, 2, 3, 4}  # Mon-Fri
    
    # SEBI Market Holidays for 2026 (NSE/BSE/MCX closed)
    SEBI_HOLIDAYS_2026 = {
        date(2026, 1, 26),   # Republic Day (26 Jan)
        date(2026, 3, 11),   # Maha Shivaratri (11 Mar)
        date(2026, 3, 29),   # Holi (29 Mar)
        date(2026, 3, 30),   # Holi Holiday (30 Mar)
        date(2026, 4, 2),    # Good Friday (2 Apr)
        date(2026, 4, 14),   # Ambedkar Jayanti (14 Apr)
        date(2026, 5, 1),    # May Day (1 May)
        date(2026, 5, 15),   # Eid ul-Fitr (15 May) - approx
        date(2026, 8, 15),   # Independence Day (15 Aug)
        date(2026, 8, 27),   # Janmashtami (27 Aug)
        date(2026, 9, 2),    # Ganesh Chaturthi (2 Sep)
        date(2026, 10, 2),   # Gandhi Jayanti (2 Oct)
        date(2026, 10, 13),  # Dussehra (13 Oct)
        date(2026, 10, 24),  # Diwali (24 Oct)
        date(2026, 10, 25),  # Diwali Holiday (25 Oct)
        date(2026, 11, 11),  # Guru Nanak Jayanti (11 Nov)
        date(2026, 12, 25),  # Christmas (25 Dec)
    }
    
    @staticmethod
    def is_sebi_holiday(check_date: date = None) -> bool:
        """Check if date is a SEBI holiday"""
        if check_date is None:
            check_date = datetime.now(IST).date()
        return check_date in MarketCalendar.SEBI_HOLIDAYS_2026
    
    @staticmethod
    def is_nse_open() -> bool:
        """
        Check if NSE equity market is currently open
        Hours: 9:15 AM - 3:40 PM IST, Mon-Fri, excluding SEBI holidays
        """
        now = datetime.now(IST)
        today = now.date()
        
        # Check if SEBI holiday
        if MarketCalendar.is_sebi_holiday(today):
            logger.debug(f"NSE closed: SEBI Holiday ({today})")
            return False
        
        # Check if weekday (Mon-Fri)
        if now.weekday() not in MarketCalendar.TRADING_DAYS:
            logger.debug(f"NSE closed: Weekend ({now.strftime('%A')})")
            return False
        
        # Check if within NSE market hours
        current_time = now.time()
        if MarketCalendar.NSE_OPEN <= current_time <= MarketCalendar.NSE_CLOSE:
            return True
        
        logger.debug(f"NSE closed: Outside hours (Current: {current_time})")
        return False
    
    @staticmethod
    def is_mcx_open() -> bool:
        """
        Check if MCX commodity market is currently open
        Hours: 9:15 AM - 11:30 PM IST, Mon-Fri, excluding SEBI holidays
        """
        now = datetime.now(IST)
        today = now.date()
        
        # Check if SEBI holiday
        if MarketCalendar.is_sebi_holiday(today):
            logger.debug(f"MCX closed: SEBI Holiday ({today})")
            return False
        
        # Check if weekday (Mon-Fri)
        if now.weekday() not in MarketCalendar.TRADING_DAYS:
            logger.debug(f"MCX closed: Weekend ({now.strftime('%A')})")
            return False
        
        # Check if within MCX market hours
        current_time = now.time()
        if MarketCalendar.MCX_OPEN <= current_time <= MarketCalendar.MCX_CLOSE:
            return True
        
        logger.debug(f"MCX closed: Outside hours (Current: {current_time})")
        return False
    
    @staticmethod
    def is_crypto_open() -> bool:
        """
        Check if crypto market is available (TradingView - 24/7)
        No time restrictions, always open
        """
        return True
    
    @staticmethod
    def is_market_open() -> bool:
        """Check if ANY market is open (NSE equity)"""
        return MarketCalendar.is_nse_open()
    
    @staticmethod
    def is_trading_day(check_date: date = None) -> bool:
        """Check if date is a trading day (Mon-Fri, excluding SEBI holidays)"""
        if check_date is None:
            check_date = datetime.now(IST).date()
        
        # Check if SEBI holiday
        if MarketCalendar.is_sebi_holiday(check_date):
            return False
        
        # Check if weekday
        day_of_week = check_date.weekday()
        is_trading = day_of_week in MarketCalendar.TRADING_DAYS
        day_name = check_date.strftime("%A")
        
        if is_trading:
            logger.info(f"✅ Trading day: {day_name} ({check_date})")
        else:
            logger.info(f"❌ Non-trading day: {day_name} ({check_date})")
        
        return is_trading
    
    @staticmethod
    def can_send_alerts() -> bool:
        """Check if we can send alerts (ANY market open, Mon-Fri, excluding SEBI holidays)"""
        now = datetime.now(IST)
        today = now.date()
        
        # Check if SEBI holiday
        if MarketCalendar.is_sebi_holiday(today):
            return False
        
        # Check if weekday
        if now.weekday() not in MarketCalendar.TRADING_DAYS:
            return False
        
        # Can send alerts anytime if any market is open
        return True
    
    @staticmethod
    def get_market_status() -> dict:
        """Get current market status for all markets with IST timezone"""
        now = datetime.now(IST)
        today = now.date()
        day_name = now.strftime("%A")
        current_time = now.strftime("%H:%M:%S")
        
        is_holiday = MarketCalendar.is_sebi_holiday(today)
        is_trading_day = now.weekday() in MarketCalendar.TRADING_DAYS and not is_holiday
        is_nse_open = MarketCalendar.is_nse_open()
        is_mcx_open = MarketCalendar.is_mcx_open()
        is_crypto_open = MarketCalendar.is_crypto_open()
        can_alert = MarketCalendar.can_send_alerts()
        
        return {
            "timestamp": now.isoformat(),
            "date": today.isoformat(),
            "day": day_name,
            "current_time_12h": now.strftime("%I:%M:%S %p"),
            "current_time_24h": current_time,
            "is_trading_day": is_trading_day,
            "is_sebi_holiday": is_holiday,
            "is_weekend": now.weekday() >= 5,
            "markets": {
                "nse_equity": {
                    "is_open": is_nse_open,
                    "open_time": "09:15",
                    "close_time": "15:40",
                    "status": "🟢 OPEN" if is_nse_open else "🔴 CLOSED"
                },
                "mcx_commodity": {
                    "is_open": is_mcx_open,
                    "open_time": "09:15",
                    "close_time": "23:30",
                    "status": "🟢 OPEN" if is_mcx_open else "🔴 CLOSED"
                },
                "tradingview_crypto": {
                    "is_open": is_crypto_open,
                    "open_time": "00:00",
                    "close_time": "23:59",
                    "status": "🟢 OPEN 24/7"
                }
            },
            "can_send_alerts": can_alert,
            "timezone": "Asia/Kolkata (IST, UTC+5:30)",
        }
    
    @staticmethod
    def get_next_market_open() -> str:
        """Get when next market opens (considering holidays)"""
        now = datetime.now(IST)
        current_date = now.date()
        current_weekday = now.weekday()
        current_time = now.time()
        
        # If NSE market is open now, return today's close
        if MarketCalendar.is_nse_open():
            return f"Today at {MarketCalendar.NSE_CLOSE.strftime('%H:%M IST')} ({current_date})"
        
        # If MCX is open, return MCX close
        if MarketCalendar.is_mcx_open():
            return f"Today at {MarketCalendar.MCX_CLOSE.strftime('%H:%M IST')} ({current_date})"
        
        # If crypto is open (always), return next MCX open
        if MarketCalendar.is_crypto_open():
            # Find next trading day
            days_ahead = 1
            while days_ahead <= 365:
                check_date = current_date + __import__('datetime').timedelta(days=days_ahead)
                if MarketCalendar.is_trading_day(check_date):
                    if days_ahead == 1:
                        return f"Tomorrow at {MarketCalendar.MCX_OPEN.strftime('%H:%M IST')} ({check_date})"
                    else:
                        return f"In {days_ahead} days at {MarketCalendar.MCX_OPEN.strftime('%H:%M IST')} ({check_date})"
                days_ahead += 1
        
        return "Next trading day (TBD)"
    
    @staticmethod
    def get_holidays_remaining() -> dict:
        """Get remaining SEBI holidays in 2026"""
        today = datetime.now(IST).date()
        remaining = sorted([h for h in MarketCalendar.SEBI_HOLIDAYS_2026 if h > today])
        
        return {
            "total_remaining": len(remaining),
            "holidays": [h.isoformat() for h in remaining[:10]],
        }


if __name__ == "__main__":
    # Test market calendar
    print("\n" + "="*80)
    print("MARKET CALENDAR STATUS (IST - Asia/Kolkata)")
    print("="*80)
    
    status = MarketCalendar.get_market_status()
    print(f"Date: {status['date']} ({status['day']})")
    print(f"Current Time (12h): {status['current_time_12h']}")
    print(f"Current Time (24h): {status['current_time_24h']}")
    print(f"Is Trading Day: {status['is_trading_day']}")
    print(f"Is SEBI Holiday: {status['is_sebi_holiday']}")
    print(f"Is Weekend: {status['is_weekend']}")
    
    print("\n📊 MARKET STATUS:")
    for market, details in status['markets'].items():
        print(f"  {market}:")
        print(f"    Status: {details['status']}")
        print(f"    Hours: {details['open_time']} - {details['close_time']}")
    
    print(f"\n✅ Can Send Alerts: {status['can_send_alerts']}")
    print(f"Next Market Opens: {MarketCalendar.get_next_market_open()}")
    
    print("\nRemaining SEBI Holidays (2026):")
    holidays = MarketCalendar.get_holidays_remaining()
    for holiday in holidays['holidays']:
        print(f"  - {holiday}")
    print("="*80 + "\n")
