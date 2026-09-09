"""
DhanHQ API Client - Handles all API communication with Dhan
Supports:
- NSE Equity data (via dhanhq library)
- MCX Commodity data (GOLD, SILVER, CRUDE OIL, NATURAL GAS)
- Order management and trading
"""

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import requests
import json

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


class DhanAPIClient:
    """DhanHQ REST API Client for MCX Commodity and NSE data"""
    
    # API Configuration
    BASE_URL = "https://api.dhan.co/v2"
    HISTORICAL_CHARTS_ENDPOINT = "/charts/historical"
    
    # MCX Security IDs for Commodities
    MCX_SECURITIES = {
        "GOLD": {
            "security_id": "565901",
            "exchange_segment": "MCX_COMM",
            "instrument": "FUTCOM",
            "expiry_code": -1,  # Current month
        },
        "SILVER": {
            "security_id": "565902",
            "exchange_segment": "MCX_COMM",
            "instrument": "FUTCOM",
            "expiry_code": -1,
        },
        "CRUDE OIL": {
            "security_id": "565899",
            "exchange_segment": "MCX_COMM",
            "instrument": "FUTCOM",
            "expiry_code": -1,
        },
        "NATURAL GAS": {
            "security_id": "565900",
            "exchange_segment": "MCX_COMM",
            "instrument": "FUTCOM",
            "expiry_code": -1,
        },
    }
    
    def __init__(self, access_token: str):
        """Initialize DhanHQ API client with access token"""
        self.access_token = access_token
        self.headers = {
            "access-token": access_token,
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        logger.info("✅ DhanHQ client initialized successfully")
    
    def get_historical_candles(self, symbol: str, interval: str = "5min", days_back: int = 5) -> list:
        """
        Fetch historical candles for MCX commodity from DhanHQ
        
        Args:
            symbol: GOLD, SILVER, CRUDE OIL, NATURAL GAS
            interval: 5min, 15min, 30min, 1hour, daily
            days_back: Number of days to fetch
            
        Returns:
            List of OHLCV candles: [{"open": x, "high": x, "low": x, "close": x, "volume": x, "timestamp": x}, ...]
        """
        
        if symbol not in self.MCX_SECURITIES:
            logger.error(f"❌ Unknown symbol: {symbol}. Available: {list(self.MCX_SECURITIES.keys())}")
            return []
        
        try:
            sec_info = self.MCX_SECURITIES[symbol]
            
            # Calculate date range
            to_date = datetime.now(IST).date()
            from_date = to_date - timedelta(days=days_back)
            
            # Prepare request payload
            payload = {
                "securityId": sec_info["security_id"],
                "exchangeSegment": sec_info["exchange_segment"],
                "instrument": sec_info["instrument"],
                "expiryCode": sec_info["expiry_code"],
                "fromDate": from_date.strftime("%Y-%m-%d"),
                "toDate": to_date.strftime("%Y-%m-%d"),
                "oi": False  # Open interest data
            }
            
            logger.debug(f"📊 Fetching {symbol} historical data: {payload}")
            
            # Make API request
            url = f"{self.BASE_URL}{self.HISTORICAL_CHARTS_ENDPOINT}"
            response = requests.post(
                url,
                json=payload,
                headers=self.headers,
                timeout=10
            )
            
            # Check response status
            if response.status_code != 200:
                logger.error(f"❌ DhanHQ API error ({response.status_code}) for {symbol}: {response.text}")
                return []
            
            # Parse response
            data = response.json()
            
            if not data or "open" not in data:
                logger.warning(f"⚠️ No candle data returned from DhanHQ for {symbol}")
                return []
            
            # Convert API response to candle format
            candles = []
            opens = data.get("open", [])
            highs = data.get("high", [])
            lows = data.get("low", [])
            closes = data.get("close", [])
            volumes = data.get("volume", [])
            timestamps = data.get("timestamp", [])
            
            for i in range(len(opens)):
                candle = {
                    "open": opens[i],
                    "high": highs[i],
                    "low": lows[i],
                    "close": closes[i],
                    "volume": volumes[i],
                    "timestamp": timestamps[i],  # Seconds since 1980-01-01
                }
                candles.append(candle)
            
            logger.info(f"✅ Fetched {len(candles)} candles for {symbol}")
            return candles
        
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Network error fetching {symbol} data: {e}")
            return []
        except Exception as e:
            logger.error(f"❌ Error fetching {symbol} historical data: {e}")
            return []
    
    def get_intraday_candles(self, symbol: str, interval_minutes: int = 5) -> list:
        """
        Fetch intraday candles for MCX commodity
        Uses last day's historical data as intraday fallback
        
        Args:
            symbol: GOLD, SILVER, CRUDE OIL, NATURAL GAS
            interval_minutes: 5, 15, 30, 60
            
        Returns:
            List of OHLCV candles
        """
        logger.debug(f"📈 Fetching intraday candles for {symbol} ({interval_minutes}min)")
        
        # Use 1-day lookback for intraday
        return self.get_historical_candles(symbol, interval=f"{interval_minutes}min", days_back=1)
    
    def get_market_status(self) -> dict:
        """
        Get MCX market status
        
        Returns:
            Market status dictionary
        """
        try:
            url = f"{self.BASE_URL}/globalstocks/marketstatus"
            response = requests.get(
                url,
                headers=self.headers,
                timeout=5
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                logger.warning(f"⚠️ Market status check failed: {response.status_code}")
                return {}
        
        except Exception as e:
            logger.error(f"❌ Error fetching market status: {e}")
            return {}
    
    def get_fund_limit(self) -> dict:
        """
        Get account fund limit and available balance
        
        Returns:
            Fund limit dictionary with available cash
        """
        try:
            url = f"{self.BASE_URL}/globalstocks/fundlimit"
            response = requests.get(
                url,
                headers=self.headers,
                timeout=5
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                logger.warning(f"⚠️ Fund limit fetch failed: {response.status_code}")
                return {}
        
        except Exception as e:
            logger.error(f"❌ Error fetching fund limit: {e}")
            return {}
    
    def place_order(self, symbol: str, transaction_type: str, order_type: str, 
                   quantity: int, price: float = None, trigger_price: float = None) -> dict:
        """
        Place an order on MCX commodity
        
        Args:
            symbol: GOLD, SILVER, CRUDE OIL, NATURAL GAS
            transaction_type: BUY or SELL
            order_type: MARKET, LIMIT, STOP_LOSS, STOP_LOSS_MARKET
            quantity: Number of contracts
            price: Limit price (for LIMIT orders)
            trigger_price: Trigger price (for STOP_LOSS orders)
            
        Returns:
            Order response dictionary with orderId and orderStatus
        """
        
        if symbol not in self.MCX_SECURITIES:
            logger.error(f"❌ Unknown symbol: {symbol}")
            return {"status": "error", "message": "Unknown symbol"}
        
        try:
            sec_info = self.MCX_SECURITIES[symbol]
            
            payload = {
                "transactionType": transaction_type,
                "orderType": order_type,
                "securityId": sec_info["security_id"],
                "quantity": quantity,
            }
            
            # Add price for limit orders
            if order_type == "LIMIT" and price:
                payload["price"] = price
            
            # Add trigger price for stop loss orders
            if order_type in ["STOP_LOSS", "STOP_LOSS_MARKET"] and trigger_price:
                payload["triggerPrice"] = trigger_price
            
            logger.info(f"📝 Placing order: {symbol} {transaction_type} {quantity} @ {order_type}")
            
            url = f"{self.BASE_URL}/globalstocks/orders"
            response = requests.post(
                url,
                json=payload,
                headers=self.headers,
                timeout=10
            )
            
            if response.status_code == 200:
                result = response.json()
                logger.info(f"✅ Order placed: {result.get('orderId')} - Status: {result.get('orderStatus')}")
                return result
            else:
                logger.error(f"❌ Order placement failed: {response.status_code} - {response.text}")
                return {"status": "error", "message": f"HTTP {response.status_code}"}
        
        except Exception as e:
            logger.error(f"❌ Error placing order for {symbol}: {e}")
            return {"status": "error", "message": str(e)}
    
    def get_holdings(self) -> list:
        """
        Get current holdings
        
        Returns:
            List of holdings
        """
        try:
            url = f"{self.BASE_URL}/globalstocks/holdings"
            response = requests.get(
                url,
                headers=self.headers,
                timeout=5
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                logger.warning(f"⚠️ Holdings fetch failed: {response.status_code}")
                return []
        
        except Exception as e:
            logger.error(f"❌ Error fetching holdings: {e}")
            return []


if __name__ == "__main__":
    # Test DhanHQ client
    import os
    from dotenv import load_dotenv
    
    load_dotenv()
    
    access_token = os.getenv("ACCESS_TOKEN")
    if not access_token:
        print("❌ ACCESS_TOKEN not set in .env")
        exit(1)
    
    client = DhanAPIClient(access_token)
    
    # Test market status
    print("\n" + "="*80)
    print("🧪 Testing DhanHQ API Client")
    print("="*80)
    
    print("\n📊 Fetching GOLD candles (last 5 days)...")
    candles = client.get_historical_candles("GOLD", days_back=5)
    if candles:
        print(f"✅ Got {len(candles)} candles")
        print(f"Latest close: {candles[-1]['close']}")
    else:
        print("❌ No candles returned")
    
    print("\n💰 Fetching fund limit...")
    fund_limit = client.get_fund_limit()
    if fund_limit:
        print(f"✅ Available cash: {fund_limit}")
    else:
        print("❌ Could not fetch fund limit")
    
    print("\n" + "="*80 + "\n")
