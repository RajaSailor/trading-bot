"""
Market Calendar - Manages when screener runs based on market hours
Supports Indian market hours (9:15 AM - 3:39 PM IST)
Monday-Friday only (weekends closed)
"""

import logging
from datetime import datetime, time
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


class MarketCalendar:
    """Market trading hours and calendar management"""
    
    # Market hours in IST
    MARKET_OPEN = time(9, 15)      # 9:15 AM
    MARKET_CLOSE = time(15, 39)    # 3:39 PM
    
    # Pre-market and post-market
    PRE_MARKET_OPEN = time(9, 0)   # 9:00 AM
    POST_MARKET_CLOSE = time(16, 0) # 4:00 PM (allow alerts till 4 PM)
    
    # Weekdays (0=Monday, 6=Sunday)
    TRADING_DAYS = {0, 1, 2, 3, 4}  # Mon-Fri
    
    @staticmethod
    def is_market_open() -> bool:
        """Check if market is currently open (9:15 AM - 3:39 PM IST, Mon-Fri)"""
        now = datetime.now(IST)
        
        # Check if weekday (Mon-Fri)
        if now.weekday() not in MarketCalendar.TRADING_DAYS:
            logger.debug(f"Market closed: Weekend ({now.strftime('%A')})")
            return False
        
        # Check if within market hours
        current_time = now.time()
        if MarketCalendar.MARKET_OPEN <= current_time <= MarketCalendar.MARKET_CLOSE:
            return True
        
        logger.debug(f"Market closed: Outside hours (Current: {current_time})")
        return False
    
    @staticmethod
    def is_trading_day() -> bool:
        """Check if today is a trading day (Mon-Fri)"""
        now = datetime.now(IST)
        is_trading = now.weekday() in MarketCalendar.TRADING_DAYS
        day_name = now.strftime("%A")
        
        if is_trading:
            logger.info(f"✅ Trading day: {day_name}")
        else:
            logger.info(f"❌ Non-trading day: {day_name} (Weekend)")
        
        return is_trading
    
    @staticmethod
    def can_send_alerts() -> bool:
        """Check if we can send alerts (9 AM - 4 PM IST, Mon-Fri)"""
        now = datetime.now(IST)
        
        # Check if weekday
        if now.weekday() not in MarketCalendar.TRADING_DAYS:
            return False
        
        # Check if within alert window
        current_time = now.time()
        return MarketCalendar.PRE_MARKET_OPEN <= current_time <= MarketCalendar.POST_MARKET_CLOSE
    
    @staticmethod
    def get_market_status() -> dict:
        """Get current market status"""
        now = datetime.now(IST)
        day_name = now.strftime("%A")
        current_time = now.strftime("%H:%M:%S")
        
        is_trading_day = now.weekday() in MarketCalendar.TRADING_DAYS
        is_open = MarketCalendar.is_market_open()
        can_alert = MarketCalendar.can_send_alerts()
        
        return {
            "timestamp": now.isoformat(),
            "day": day_name,
            "current_time": current_time,
            "is_trading_day": is_trading_day,
            "is_market_open": is_open,
            "can_send_alerts": can_alert,
            "market_open_time": MarketCalendar.MARKET_OPEN.isoformat(),
            "market_close_time": MarketCalendar.MARKET_CLOSE.isoformat(),
            "timezone": "Asia/Kolkata",
        }
    
    @staticmethod
    def get_next_market_open() -> str:
        """Get when next market opens"""
        now = datetime.now(IST)
        current_weekday = now.weekday()
        current_time = now.time()
        
        # If market is open now, return today's close
        if current_weekday in MarketCalendar.TRADING_DAYS and current_time < MarketCalendar.MARKET_CLOSE:
            return f"Today at {MarketCalendar.MARKET_CLOSE.strftime('%H:%M IST')}"
        
        # Find next trading day
        days_ahead = 0
        while True:
            next_day = (current_weekday + days_ahead + 1) % 7
            if next_day in MarketCalendar.TRADING_DAYS:
                days_ahead += 1
                break
            days_ahead += 1
        
        if days_ahead == 1:
            return f"Tomorrow at {MarketCalendar.MARKET_OPEN.strftime('%H:%M IST')}"
        else:
            return f"In {days_ahead} days at {MarketCalendar.MARKET_OPEN.strftime('%H:%M IST')}"


if __name__ == "__main__":
    # Test market calendar
    print("\n" + "="*70)
    print("MARKET CALENDAR STATUS")
    print("="*70)
    
    status = MarketCalendar.get_market_status()
    for key, value in status.items():
        print(f"{key:.<30} {value}")
    
    print("\nMarket Status:")
    print(f"  Is Trading Day: {status['is_trading_day']}")
    print(f"  Is Market Open: {status['is_market_open']}")
    print(f"  Can Send Alerts: {status['can_send_alerts']}")
    print(f"  Next Market: {MarketCalendar.get_next_market_open()}")
    print("="*70 + "\n")
