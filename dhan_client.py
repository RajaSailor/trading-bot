"""
DhanHQ API Client - Handles all DhanHQ API interactions
Documents: Charts Historical (intraday_minute_data), Technical Metrics, News, Top Instruments
"""

import logging
import os
import time
from datetime import datetime
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


class DhanAPIClient:
    """
    DhanHQ API v2 Client
    
    API Documentation Reference:
    - Base URL: https://api.dhan.co/v2
    - Authentication: access-token header (required for all requests)
    - Rate limiting: Check X-Rate-Limit headers
    """
    
    BASE_URL = "https://api.dhan.co/v2"
    
    def __init__(self, access_token: str):
        """
        Initialize DhanHQ API Client
        
        Args:
            access_token: API access token from DhanHQ (from environment variable ACCESS_TOKEN)
        """
        self.access_token = access_token
        self.headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "access-token": access_token,
        }
        self._session = None
        self._init_session()
    
    def _init_session(self):
        """Initialize HTTP session"""
        try:
            import requests
            self._session = requests.Session()
            self._session.headers.update(self.headers)
        except ImportError:
            logger.error("requests library not found. Install with: pip install requests")
    
    def _make_request(self, method: str, endpoint: str, data: Optional[Dict] = None, params: Optional[Dict] = None) -> Optional[Dict]:
        """
        Make HTTP request to DhanHQ API
        
        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            endpoint: API endpoint (without base URL)
            data: Request body (for POST/PUT)
            params: Query parameters (for GET)
        
        Returns:
            Response JSON or None if error
        """
        if not self._session:
            logger.error("Session not initialized")
            return None
        
        url = f"{self.BASE_URL}{endpoint}"
        
        try:
            if method == "GET":
                response = self._session.get(url, params=params)
            elif method == "POST":
                response = self._session.post(url, json=data)
            elif method == "PUT":
                response = self._session.put(url, json=data)
            elif method == "DELETE":
                response = self._session.delete(url)
            else:
                logger.error(f"Unsupported HTTP method: {method}")
                return None
            
            # Check rate limiting headers
            if "X-Rate-Limit-Remaining" in response.headers:
                remaining = response.headers["X-Rate-Limit-Remaining"]
                logger.debug(f"API rate limit remaining: {remaining}")
            
            response.raise_for_status()
            return response.json()
        
        except Exception as e:
            logger.error(f"API request failed: {method} {endpoint} - {e}")
            return None
    
    # ====== CHARTS / HISTORICAL DATA API ======
    
    def get_historical_candles(
        self,
        security_id: str,
        exchange_segment: str,
        instrument: str,
        from_date: str,
        to_date: str,
        expiry_code: Optional[int] = None,
    ) -> Optional[Dict]:
        """
        Get historical OHLC chart data
        
        API: POST /v2/charts/historical
        
        Args:
            security_id: DhanHQ security ID (e.g., "13" for NIFTY)
            exchange_segment: NSE_EQ, NSE_FNO, BSE_EQ, BSE_FNO, MCX_COMM, IDX_I
            instrument: INDEX, FUTIDX, OPTIDX, EQUITY, FUTSTK, OPTSTK, FUTCOM, OPTFUT
            from_date: Start date (YYYY-MM-DD format)
            to_date: End date (YYYY-MM-DD format)
            expiry_code: For derivatives (optional, refer to charts annexure)
        
        Returns:
            {
                "open": [array of open prices],
                "high": [array of high prices],
                "low": [array of low prices],
                "close": [array of close prices],
                "volume": [array of volumes],
                "timestamp": [array of unix timestamps],
                "open_interest": [array of OI] (optional, for derivatives)
            }
        """
        payload = {
            "securityId": security_id,
            "exchangeSegment": exchange_segment,
            "instrument": instrument,
            "fromDate": from_date,
            "toDate": to_date,
            "oi": False,  # Set to True to include open interest
        }
        
        if expiry_code is not None:
            payload["expiryCode"] = expiry_code
        
        logger.debug(f"Fetching historical data: {security_id} ({instrument}) from {from_date} to {to_date}")
        return self._make_request("POST", "/charts/historical", data=payload)
    
    def get_intraday_minute_data(
        self,
        security_id: str,
        exchange_segment: str,
        instrument: str,
        from_date: str,
        to_date: str,
        interval: int = 5,
        expiry_code: Optional[int] = None,
    ) -> Optional[Dict]:
        """
        Get intraday minute-level OHLC data (last 5 trading days)
        
        API: POST /v2/charts/intraday
        
        Args:
            security_id: DhanHQ security ID
            exchange_segment: NSE_EQ, NSE_FNO, BSE_EQ, BSE_FNO, MCX_COMM, IDX_I
            instrument: INDEX, FUTIDX, OPTIDX, EQUITY, FUTSTK, OPTSTK, FUTCOM, OPTFUT
            from_date: Start date (YYYY-MM-DD)
            to_date: End date (YYYY-MM-DD)
            interval: Candle interval in minutes (1, 5, 15, 25, 60)
            expiry_code: For derivatives (optional)
        
        Returns:
            Same format as get_historical_candles (OHLCV + timestamps)
        
        Note:
            - Limited to last 5 trading days
            - Most commonly used for intraday analysis
        """
        payload = {
            "securityId": security_id,
            "exchangeSegment": exchange_segment,
            "instrument": instrument,
            "fromDate": from_date,
            "toDate": to_date,
            "interval": interval,
            "oi": False,
        }
        
        if expiry_code is not None:
            payload["expiryCode"] = expiry_code
        
        logger.debug(f"Fetching intraday data: {security_id} ({interval}min) on {from_date}")
        return self._make_request("POST", "/charts/intraday", data=payload)
    
    # ====== MARKET DATA / QUOTE API ======
    
    def get_market_status(self) -> Optional[Dict]:
        """
        Get current market status
        
        API: GET /v2/globalstocks/marketstatus
        
        Returns:
            {
                "marketOpenTime": "09:15",
                "marketCloseTime": "15:30",
                "status": "open" or "closed",
                "holidayFlag": false
            }
        """
        logger.debug("Fetching market status")
        return self._make_request("GET", "/globalstocks/marketstatus")
    
    def get_quote_data(
        self,
        mode: str,
        security_ids: List[str],
        exchange_segment: str,
    ) -> Optional[Dict]:
        """
        Get live quote data (LTP, OHLC, or full)
        
        API: POST /v2/marketfeed/quote
        
        Args:
            mode: LTP (last price), OHLC (OHLC snapshot), or FULL (all data)
            security_ids: List of security IDs to fetch
            exchange_segment: NSE_EQ, NSE_FNO, BSE_EQ, BSE_FNO, MCX_COMM, IDX_I
        
        Returns:
            {
                "data": [
                    {
                        "security_id": "13",
                        "last_price": 21500.50,
                        "open": 21450.00,
                        "high": 21600.00,
                        "low": 21400.00,
                        "close": 21500.50,
                        "volume": 1000000,
                        "timestamp": "2026-09-07T15:30:00Z"
                    }
                ]
            }
        """
        payload = {
            "mode": mode,
            "securityId": security_ids,
            "exchangeSegment": exchange_segment,
        }
        
        logger.debug(f"Fetching {mode} quote for {len(security_ids)} securities")
        return self._make_request("POST", "/marketfeed/quote", data=payload)
    
    # ====== ORDER MANAGEMENT API ======
    
    def place_order(
        self,
        dhan_client_id: str,
        transaction_type: str,
        exchange_segment: str,
        product_type: str,
        order_type: str,
        security_id: str,
        quantity: int,
        price: float = 0.0,
        trigger_price: float = 0.0,
        validity: str = "DAY",
        correlation_id: Optional[str] = None,
    ) -> Optional[Dict]:
        """
        Place a new order
        
        API: POST /v2/globalstocks/orders
        
        Args:
            dhan_client_id: Your DhanHQ client ID
            transaction_type: BUY or SELL
            exchange_segment: NSE_EQ, NSE_FNO, BSE_EQ, BSE_FNO, MCX_COMM
            product_type: CNC (delivery), INTRADAY, MARGIN, MTF, CO (cover order), BO (bracket order)
            order_type: MARKET, LIMIT, STOP_LOSS, STOP_LOSS_MARKET
            security_id: DhanHQ security ID
            quantity: Number of units
            price: Order price (required for LIMIT orders)
            trigger_price: Trigger price (for STOP_LOSS orders)
            validity: DAY or IOC (Immediate or Cancel)
            correlation_id: Your tracking ID (max 30 chars)
        
        Returns:
            {
                "orderId": "generated_order_id",
                "orderStatus": "TRANSIT" | "PENDING" | "REJECTED" | "TRADED"
            }
        """
        payload = {
            "dhanClientId": dhan_client_id,
            "transactionType": transaction_type,
            "exchangeSegment": exchange_segment,
            "productType": product_type,
            "orderType": order_type,
            "securityId": security_id,
            "quantity": quantity,
            "validity": validity,
        }
        
        if price > 0:
            payload["price"] = price
        if trigger_price > 0:
            payload["triggerPrice"] = trigger_price
        if correlation_id:
            payload["correlationId"] = correlation_id
        
        logger.info(f"Placing order: {transaction_type} {quantity} units of {security_id} at {price}")
        return self._make_request("POST", "/globalstocks/orders", data=payload)
    
    def get_order_book(self) -> Optional[List[Dict]]:
        """
        Get all orders in order book
        
        API: GET /v2/globalstocks/orders
        
        Returns:
            List of order objects with status, price, quantity, etc.
        """
        logger.debug("Fetching order book")
        result = self._make_request("GET", "/globalstocks/orders")
        return result if isinstance(result, list) else None
    
    def get_order_by_id(self, order_id: str) -> Optional[Dict]:
        """
        Get specific order details by order ID
        
        API: GET /v2/globalstocks/orders/{order-id}
        
        Args:
            order_id: Order ID from place_order response
        
        Returns:
            Full order details including status, execution price, etc.
        """
        logger.debug(f"Fetching order: {order_id}")
        return self._make_request("GET", f"/globalstocks/orders/{order_id}")
    
    def modify_order(
        self,
        dhan_client_id: str,
        order_id: str,
        order_type: str,
        transaction_type: str,
        security_id: str,
        quantity: Optional[int] = None,
        price: Optional[float] = None,
    ) -> Optional[Dict]:
        """
        Modify a pending order
        
        API: PUT /v2/globalstocks/orders/{order-id}
        
        Args:
            dhan_client_id: Your DhanHQ client ID
            order_id: Order ID to modify
            order_type: New order type (MARKET, LIMIT, STOP_LOSS, STOP_LOSS_MARKET)
            transaction_type: BUY or SELL
            security_id: Security ID
            quantity: New quantity (optional)
            price: New price (optional)
        
        Returns:
            {
                "orderId": "order_id",
                "orderStatus": "MODIFIED" | other status
            }
        """
        payload = {
            "dhanClientId": dhan_client_id,
            "orderType": order_type,
            "transactionType": transaction_type,
            "securityId": security_id,
        }
        
        if quantity is not None:
            payload["quantity"] = quantity
        if price is not None:
            payload["price"] = price
        
        logger.info(f"Modifying order {order_id}")
        return self._make_request("PUT", f"/globalstocks/orders/{order_id}", data=payload)
    
    def cancel_order(self, order_id: str) -> Optional[Dict]:
        """
        Cancel a pending order
        
        API: DELETE /v2/globalstocks/orders/{order-id}
        
        Args:
            order_id: Order ID to cancel
        
        Returns:
            {
                "orderId": "order_id",
                "orderStatus": "CANCELLED"
            }
        """
        logger.info(f"Cancelling order {order_id}")
        return self._make_request("DELETE", f"/globalstocks/orders/{order_id}")
    
    # ====== POSITION MANAGEMENT API ======
    
    def get_positions(self) -> Optional[List[Dict]]:
        """
        Get all open positions
        
        API: GET /v2/positions
        
        Returns:
            List of position objects with:
            - tradingSymbol, securityId
            - positionType: LONG, SHORT, CLOSED
            - buyQty, sellQty, netQty
            - buyAvg, costPrice
            - unrealizedProfit, realizedProfit
        """
        logger.debug("Fetching all positions")
        result = self._make_request("GET", "/positions")
        return result if isinstance(result, list) else None
    
    def convert_position(
        self,
        dhan_client_id: str,
        from_product_type: str,
        exchange_segment: str,
        position_type: str,
        security_id: str,
        convert_qty: int,
        to_product_type: str,
    ) -> Optional[Dict]:
        """
        Convert position between product types (e.g., INTRADAY to CNC)
        
        API: POST /v2/positions/convert
        
        Args:
            dhan_client_id: Your DhanHQ client ID
            from_product_type: CNC, INTRADAY, MARGIN, MTF, CO, BO
            exchange_segment: NSE_EQ, NSE_FNO, BSE_EQ, BSE_FNO, MCX_COMM
            position_type: LONG, SHORT, CLOSED
            security_id: Security to convert
            convert_qty: Quantity to convert
            to_product_type: Target product type
        """
        payload = {
            "dhanClientId": dhan_client_id,
            "fromProductType": from_product_type,
            "exchangeSegment": exchange_segment,
            "positionType": position_type,
            "securityId": security_id,
            "convertQty": convert_qty,
            "toProductType": to_product_type,
        }
        
        logger.info(f"Converting position {security_id} from {from_product_type} to {to_product_type}")
        return self._make_request("POST", "/positions/convert", data=payload)
    
    def exit_all_positions(self) -> Optional[Dict]:
        """
        Exit all active positions and cancel all pending orders
        
        API: DELETE /v2/positions
        
        Returns:
            {"message": "...", "status": "SUCCESS" | "ERROR"}
        """
        logger.warning("Exiting ALL positions and cancelling all pending orders")
        return self._make_request("DELETE", "/positions")
    
    # ====== HOLDINGS API ======
    
    def get_holdings(self) -> Optional[List[Dict]]:
        """
        Get all holdings (stocks held from previous sessions)
        
        API: GET /v2/holdings
        
        Returns:
            List of holding objects with:
            - tradingSymbol, securityId, exchange
            - totalQty, availableQty, collateralQty
            - avgCostPrice, lastTradedPrice
        """
        logger.debug("Fetching holdings")
        result = self._make_request("GET", "/holdings")
        return result if isinstance(result, list) else None
    
    # ====== TRADES API ======
    
    def get_trade_history(self) -> Optional[List[Dict]]:
        """
        Get all executed trades for the day
        
        API: GET /v2/globalstocks/trades
        
        Returns:
            List of trade objects with:
            - orderId, tradingSymbol, securityId
            - tradedQuantity, tradedPrice, tradeDate
            - brokerage, otherCharges, tradeValue
        """
        logger.debug("Fetching trade history")
        result = self._make_request("GET", "/globalstocks/trades")
        return result if isinstance(result, list) else None
    
    def get_trades_by_security(self, security_id: str) -> Optional[List[Dict]]:
        """
        Get trades for a specific security
        
        API: GET /v2/globalstocks/trades/{security-id}
        
        Args:
            security_id: DhanHQ security ID
        
        Returns:
            List of trade objects for that security
        """
        logger.debug(f"Fetching trades for security {security_id}")
        result = self._make_request("GET", f"/globalstocks/trades/{security_id}")
        return result if isinstance(result, list) else None
    
    # ====== FUND MANAGEMENT API ======
    
    def get_fund_limit(self) -> Optional[Dict]:
        """
        Get fund/margin limits and available balance
        
        API: GET /v2/globalstocks/fundlimit
        
        Returns:
            {
                "availableCash": float,
                "cashOnAccount": float,
                "actualCash": float,
                "settledCash": float,
                "unsettledCash": float,
                "marginUtilized": float
            }
        """
        logger.debug("Fetching fund limits")
        return self._make_request("GET", "/globalstocks/fundlimit")
    
    # ====== MARGIN CALCULATOR API ======
    
    def calculate_margin(
        self,
        dhan_client_id: str,
        exchange_segment: str,
        transaction_type: str,
        product_type: str,
        order_type: str,
        security_id: str,
        quantity: int,
        price: float,
        trigger_price: float = 0.0,
    ) -> Optional[Dict]:
        """
        Calculate margin requirement for an order before placing
        
        API: POST /v2/margincalculator
        
        Args:
            dhan_client_id: Your DhanHQ client ID
            exchange_segment: NSE_EQ, NSE_FNO, etc.
            transaction_type: BUY or SELL
            product_type: CNC, INTRADAY, MARGIN, etc.
            order_type: MARKET, LIMIT, STOP_LOSS, STOP_LOSS_MARKET
            security_id: Security ID
            quantity: Order quantity
            price: Order price
            trigger_price: For STOP_LOSS orders
        
        Returns:
            {
                "totalMargin": float,
                "spanMargin": float,
                "exposureMargin": float,
                "availableBalance": float,
                "variableMargin": float,
                "insufficientBalance": float,
                "brokerage": float,
                "leverage": "string"
            }
        """
        payload = {
            "dhanClientId": dhan_client_id,
            "exchangeSegment": exchange_segment,
            "transactionType": transaction_type,
            "productType": product_type,
            "orderType": order_type,
            "securityId": security_id,
            "quantity": quantity,
            "price": price,
        }
        
        if trigger_price > 0:
            payload["triggerPrice"] = trigger_price
        
        logger.debug(f"Calculating margin for {security_id}")
        return self._make_request("POST", "/margincalculator", data=payload)
    
    # ====== TECHNICAL METRICS API ======
    
    def get_technical_metrics(
        self,
        security_id: str,
        exchange_segment: str,
        instrument: str,
        timeframe: str,
        indicators: List[str],
    ) -> Optional[Dict]:
        """
        Get technical indicators (SMA, EMA, RSI, MACD, Bollinger Bands, etc.)
        
        API: POST /v2/technical/metrics
        
        Args:
            security_id: DhanHQ security ID
            exchange_segment: NSE_EQ or IDX_I (for indices)
            instrument: EQUITY or INDEX
            timeframe: 1, 5, 15 (intraday - current session only), D (daily)
            indicators: List of indicators to fetch:
                - SMA_5, SMA_10, SMA_20, SMA_50, SMA_100, SMA_200
                - EMA_5, EMA_10, EMA_20, EMA_50, EMA_100, EMA_200
                - RSI_14, MACD_26, MACD_12, MACD_HIST
                - BB_UPPER, BB_LOWER (Bollinger Bands)
                - ATR_14, STOCHASTIC, STOCHRSI_14
                - PIVOT
        
        Returns:
            {
                "securityId": "13",
                "timeframe": "D",
                "data": {
                    "SMA": [{"period": 20, "value": 22.5855, "action": "Bearish"}],
                    "RSI": {"value": 44.9067, "action": "Neutral"},
                    "MACD_HIST": {"value": -0.0241, "action": "Bearish"},
                    "PIVOT": {
                        "Classic": {
                            "PP": 22.98,
                            "R1": 23.14, "R2": 23.31, "R3": 23.47,
                            "S1": 22.81, "S2": 22.65, "S3": 22.48
                        }
                    }
                }
            }
        """
        payload = {
            "securityId": security_id,
            "exchangeSegment": exchange_segment,
            "instrument": instrument,
            "timeframe": timeframe,
            "indicators": indicators,
        }
        
        logger.debug(f"Fetching technical metrics for {security_id}: {indicators}")
        return self._make_request("POST", "/technical/metrics", data=payload)
    
    # ====== NEWS API ======
    
    def get_news_headlines(
        self,
        dhan_client_id: str,
        categories: List[str],
        limit: int = 10,
        universe: Optional[str] = None,
        stock_list: Optional[List[str]] = None,
    ) -> Optional[Dict]:
        """
        Get news headlines
        
        API: POST /v2/news/headlines
        
        Args:
            dhan_client_id: Your DhanHQ client ID
            categories: News categories - ["ALL"] or specific ones:
                - COMPANIES, EQUITY_MARKETS, IPO, GLOBAL
                - INDIAN_ECONOMY, GLOBAL_ECONOMY, CURRENCY
                - COMMODITIES, CRYPTOCURRENCIES, etc.
            limit: Number of news items (1-50)
            universe: PORTFOLIO or WATCHLIST (optional)
            stock_list: Filter by specific security IDs (optional)
        
        Returns:
            {
                "data": {
                    "latestNews": [
                        {
                            "newsObject": {
                                "title": "...",
                                "text": "...",
                                "overallSentiment": "positive" | "negative" | "neutral"
                            },
                            "category": "companies",
                            "subCategory": "orders-deals",
                            "publishDate": "2026-09-07",
                            "stockName": "RELIANCE",
                            "smSymbol": "RIL"
                        }
                    ]
                }
            }
        """
        payload = {
            "dhanClientId": dhan_client_id,
            "categories": categories,
            "limit": limit,
        }
        
        if universe:
            payload["universe"] = universe
        if stock_list:
            payload["stockList"] = stock_list
        
        logger.debug(f"Fetching news: categories={categories}, limit={limit}")
        return self._make_request("POST", "/news/headlines", data=payload)
    
    # ====== TOP INSTRUMENTS API ======
    
    def get_top_instruments(
        self,
        exchange_segment: str,
        instruments: List[str],
        category: str,
        limit: int = 20,
        expiry: Optional[str] = None,
        universe: Optional[str] = None,
    ) -> Optional[Dict]:
        """
        Get top instruments ranked by various metrics
        
        API: POST /v2/market/instruments/topinstruments
        
        Args:
            exchange_segment: NSE_FNO, BSE_FNO, MCX_COMM, NSE_EQ, BSE_EQ
            instruments: List of instrument types (same group only):
                - Options: OPTIDX, OPTSTK, OPTFUT
                - Futures: FUTIDX, FUTSTK, FUTCOM
                - Equity: EQUITY
            category: Ranking category:
                - HIGHEST_OI, OI_GAINERS, OI_LOSERS
                - TOP_VOLUME, PRICE_GAINERS, PRICE_LOSERS
            limit: Number of results (1-100)
            expiry: Expiry date in YYYY-MM-DD (for derivatives)
            universe: ALL, NIFTY_50, NIFTY_BANK, NIFTY_MIDCAP, etc. (for equity)
        
        Returns:
            {
                "exchangeSegment": "NSE_FNO",
                "category": "HIGHEST_OI",
                "data": [
                    {
                        "securityId": "13",
                        "tradingSymbol": "NIFTY23SEP21000CE",
                        "ltp": 250.50,
                        "openInterest": 5000000,
                        "volume": 1000000,
                        "changePercent": 2.5
                    }
                ]
            }
        """
        payload = {
            "exchangeSegment": exchange_segment,
            "instrument": instruments,
            "category": category,
            "limit": limit,
        }
        
        if expiry:
            payload["expiry"] = expiry
        if universe:
            payload["universe"] = universe
        
        logger.debug(f"Fetching top instruments: {instruments} by {category}")
        return self._make_request("POST", "/market/instruments/topinstruments", data=payload)
    
    # ====== FUNDAMENTAL METRICS API ======
    
    def get_fundamental_metrics(
        self,
        security_id: str,
        exchange_segment: str,
        metrics: List[str],
    ) -> Optional[Dict]:
        """
        Get fundamental metrics for a stock
        
        API: POST /v2/fundamental/metrics
        
        Args:
            security_id: DhanHQ security ID
            exchange_segment: NSE_EQ or BSE_EQ
            metrics: Metrics to fetch - CO, RATIOS, SHP
                - CO: Company Overview (market cap, EPS, dividend yield, 52-week high/low)
                - RATIOS: PE ratio, PB ratio, ROE, ROCE, debt-to-equity
                - SHP: Shareholding pattern (promoter, FII, DII holdings)
        
        Returns:
            {
                "securityId": "13",
                "data": {
                    "CO": {
                        "MARKET_CAP": 36318.4,
                        "EPS": 42.5,
                        "DIVIDEND_YIELD": 0.35,
                        "FIFTY_TWO_WEEK_HIGH": 1611.8,
                        "FIFTY_TWO_WEEK_LOW": 1249.8
                    },
                    "RATIOS": {
                        "PE_RATIO": 5.54,
                        "ROE": 12.4,
                        "ROCE": 14.8
                    },
                    "SHP": {
                        "PROMOTER_HOLDING": 50.35,
                        "FII_HOLDING": 22.1,
                        "DII_HOLDING": 15.2
                    }
                }
            }
        """
        payload = {
            "securityId": security_id,
            "exchangeSegment": exchange_segment,
            "instrument": "EQUITY",
            "metrics": metrics,
        }
        
        logger.debug(f"Fetching fundamental metrics for {security_id}: {metrics}")
        return self._make_request("POST", "/fundamental/metrics", data=payload)
    
    # ====== CONDITIONAL/ALERT ORDERS API ======
    
    def create_conditional_order(
        self,
        dhan_client_id: str,
        condition: Dict,
        orders: List[Dict],
    ) -> Optional[Dict]:
        """
        Create a conditional order (alert-based order)
        
        API: POST /v2/alerts/orders
        
        When condition is met, the associated orders are placed automatically.
        
        Args:
            dhan_client_id: Your DhanHQ client ID
            condition: Condition dictionary:
                {
                    "comparisonType": "PRICE_WITH_VALUE",
                    "exchangeSegment": "NSE_EQ",
                    "securityId": "1333",
                    "operator": "GREATER_THAN",
                    "comparingValue": 2500.0,
                    "expDate": "2026-12-31",
                    "frequency": "ONCE"
                }
            orders: List of orders to place when condition triggers:
                [{
                    "transactionType": "BUY",
                    "exchangeSegment": "NSE_EQ",
                    "productType": "INTRADAY",
                    "orderType": "MARKET",
                    "securityId": "13",
                    "quantity": 1,
                    "validity": "DAY",
                    "price": "21500"
                }]
        
        Returns:
            {
                "alertId": "alert_123456",
                "alertStatus": "ACTIVE" | "TRIGGERED" | "EXPIRED"
            }
        """
        payload = {
            "dhanClientId": dhan_client_id,
            "condition": condition,
            "orders": orders,
        }
        
        logger.info(f"Creating conditional order: {condition}")
        return self._make_request("POST", "/alerts/orders", data=payload)
    
    def get_conditional_orders(self) -> Optional[List[Dict]]:
        """
        Get all conditional orders
        
        API: GET /v2/alerts/orders
        
        Returns:
            List of conditional order objects with status and triggers
        """
        logger.debug("Fetching all conditional orders")
        result = self._make_request("GET", "/alerts/orders")
        return result if isinstance(result, list) else None
    
    def cancel_conditional_order(self, alert_id: str) -> Optional[Dict]:
        """
        Cancel a conditional order
        
        API: DELETE /v2/alerts/orders/{alertId}
        
        Args:
            alert_id: Alert ID from create_conditional_order response
        
        Returns:
            {"alertId": "...", "alertStatus": "CANCELLED"}
        """
        logger.info(f"Cancelling conditional order {alert_id}")
        return self._make_request("DELETE", f"/alerts/orders/{alert_id}")


def create_dhan_client() -> Optional[DhanAPIClient]:
    """
    Factory function to create DhanHQ API client from environment
    
    Requires:
    - ACCESS_TOKEN environment variable
    
    Returns:
        DhanAPIClient instance or None if token not found
    """
    access_token = os.getenv("ACCESS_TOKEN")
    
    if not access_token:
        logger.error("ACCESS_TOKEN environment variable not set")
        return None
    
    try:
        client = DhanAPIClient(access_token)
        logger.info("✅ DhanHQ API client initialized successfully")
        return client
    except Exception as e:
        logger.error(f"Failed to initialize DhanHQ API client: {e}")
        return None
