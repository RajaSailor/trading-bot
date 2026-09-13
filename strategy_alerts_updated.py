"""
Strategy Alerts Router - Route alerts to correct Telegram channels based on strategy.

Channel Routing:
- NIFTY50, BANKNIFTY Options → INDEX OPTIONS ALERTS
- Commodity (GOLD, CRUDE, SILVER) → COMMODITY OPTIONS ALERTS
- NIFTY50 Stocks → NIFTY50 STOCKS OPTIONS
- Intraday 5X Leverage → NIFTY50 5X LEVERAGE
- Pay Later/Margin → NIFTY50 PAY LATER
- Crypto (BTC, ETH) → CRYPTO 24/7
- System Messages → SERVICE_ALERTS
- Trade Control → TRADE_CONTROL
"""

import logging
from typing import Dict, Optional, List
from enum import Enum
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class TradingCategory(Enum):
    """Trading categories for routing"""
    INDEX_OPTIONS = "index_options"
    COMMODITY_OPTIONS = "commodity_options"
    NIFTY_STOCKS = "nifty_stocks"
    INTRADAY_5X = "intraday_5x"
    PAY_LATER = "pay_later"
    CRYPTO = "crypto"
    SERVICE_ALERTS = "service_alerts"
    TRADE_CONTROL = "trade_control"


@dataclass
class ChannelConfig:
    """Channel configuration"""
    name: str
    category: TradingCategory
    bot_token_env: str
    channel_id_env: str
    default_channel_id: int
    description: str


class StrategyAlertsRouter:
    """Route trading alerts to correct Telegram channels"""
    
    # Channel configuration mapping
    CHANNEL_CONFIG = {
        TradingCategory.INDEX_OPTIONS: ChannelConfig(
            name="INDEX OPTIONS ALERTS",
            category=TradingCategory.INDEX_OPTIONS,
            bot_token_env="BOT_INDEX_OPTIONS_TOKEN",
            channel_id_env="CHANNEL_INDEX_OPTIONS_ID",
            default_channel_id=-1003966854994,
            description="NIFTY50 and BANKNIFTY options trading signals"
        ),
        TradingCategory.COMMODITY_OPTIONS: ChannelConfig(
            name="COMMODITY OPTIONS ALERTS",
            category=TradingCategory.COMMODITY_OPTIONS,
            bot_token_env="BOT_COMMODITY_TOKEN",
            channel_id_env="CHANNEL_COMMODITY_ID",
            default_channel_id=-1001234567890,
            description="GOLD, CRUDE, SILVER, NATURALGAS options signals"
        ),
        TradingCategory.NIFTY_STOCKS: ChannelConfig(
            name="NIFTY50 STOCKS OPTIONS",
            category=TradingCategory.NIFTY_STOCKS,
            bot_token_env="BOT_NIFTY_STOCKS_TOKEN",
            channel_id_env="CHANNEL_NIFTY_STOCKS_ID",
            default_channel_id=-1001234567891,
            description="NIFTY50 stock options trading signals"
        ),
        TradingCategory.INTRADAY_5X: ChannelConfig(
            name="NIFTY50 5X LEVERAGE",
            category=TradingCategory.INTRADAY_5X,
            bot_token_env="BOT_5X_LEVERAGE_TOKEN",
            channel_id_env="CHANNEL_5X_LEVERAGE_ID",
            default_channel_id=-1001234567892,
            description="5X intraday leverage trading signals"
        ),
        TradingCategory.PAY_LATER: ChannelConfig(
            name="NIFTY50 PAY LATER",
            category=TradingCategory.PAY_LATER,
            bot_token_env="BOT_PAY_LATER_TOKEN",
            channel_id_env="CHANNEL_PAY_LATER_ID",
            default_channel_id=-1001234567893,
            description="Pay Later / Margin trading signals"
        ),
        TradingCategory.CRYPTO: ChannelConfig(
            name="CRYPTO 24/7",
            category=TradingCategory.CRYPTO,
            bot_token_env="BOT_CRYPTO_TOKEN",
            channel_id_env="CHANNEL_CRYPTO_ID",
            default_channel_id=-1001234567894,
            description="Cryptocurrency (BTC, ETH) 24/7 trading signals"
        ),
        TradingCategory.SERVICE_ALERTS: ChannelConfig(
            name="SERVICE ALERTS",
            category=TradingCategory.SERVICE_ALERTS,
            bot_token_env="BOT_SERVICE_ALERTS_TOKEN",
            channel_id_env="CHANNEL_SERVICE_ALERTS_ID",
            default_channel_id=-1001234567895,
            description="System alerts and operational notifications"
        ),
        TradingCategory.TRADE_CONTROL: ChannelConfig(
            name="TRADE CONTROL",
            category=TradingCategory.TRADE_CONTROL,
            bot_token_env="BOT_TRADE_CONTROL_TOKEN",
            channel_id_env="CHANNEL_TRADE_CONTROL_ID",
            default_channel_id=-1001234567896,
            description="Trade execution confirmations and controls"
        ),
    }
    
    # Symbol to category mapping
    SYMBOL_CATEGORY_MAP = {
        # Index options
        "NIFTY50": TradingCategory.INDEX_OPTIONS,
        "NIFTY": TradingCategory.INDEX_OPTIONS,
        "BANKNIFTY": TradingCategory.INDEX_OPTIONS,
        "SENSEX": TradingCategory.INDEX_OPTIONS,
        
        # Commodity options
        "GOLD": TradingCategory.COMMODITY_OPTIONS,
        "CRUDE": TradingCategory.COMMODITY_OPTIONS,
        "CRUDE OIL": TradingCategory.COMMODITY_OPTIONS,
        "SILVER": TradingCategory.COMMODITY_OPTIONS,
        "NATURALGAS": TradingCategory.COMMODITY_OPTIONS,
        
        # NIFTY50 stocks
        "RELIANCE": TradingCategory.NIFTY_STOCKS,
        "TCS": TradingCategory.NIFTY_STOCKS,
        "INFY": TradingCategory.NIFTY_STOCKS,
        "WIPRO": TradingCategory.NIFTY_STOCKS,
        "HDFC": TradingCategory.NIFTY_STOCKS,
        "ICICIBANK": TradingCategory.NIFTY_STOCKS,
        "AXISBANK": TradingCategory.NIFTY_STOCKS,
        "LT": TradingCategory.NIFTY_STOCKS,
        "SUNPHARMA": TradingCategory.NIFTY_STOCKS,
        "ITC": TradingCategory.NIFTY_STOCKS,
        
        # Crypto
        "BTC": TradingCategory.CRYPTO,
        "BITCOIN": TradingCategory.CRYPTO,
        "ETH": TradingCategory.CRYPTO,
        "ETHEREUM": TradingCategory.CRYPTO,
    }
    
    # Strategy to category mapping
    STRATEGY_CATEGORY_MAP = {
        "5-MIN Breakout": TradingCategory.INTRADAY_5X,
        "Premium Breakout": TradingCategory.INDEX_OPTIONS,
        "5X Leverage": TradingCategory.INTRADAY_5X,
        "Pay Later": TradingCategory.PAY_LATER,
        "Margin": TradingCategory.PAY_LATER,
    }
    
    def __init__(self):
        """Initialize router"""
        self.logger = logging.getLogger(__name__)
        self.logger.info("✅ Strategy Alerts Router initialized")
    
    def get_category_for_signal(
        self,
        symbol: str,
        strategy: str = "",
        signal_type: str = ""
    ) -> TradingCategory:
        """
        Determine channel category for a signal
        
        Args:
            symbol: Trading symbol (e.g., "NIFTY50", "GOLD")
            strategy: Strategy name (e.g., "5-MIN Breakout")
            signal_type: Signal type (e.g., "BUY", "SELL")
            
        Returns:
            TradingCategory enum value
        """
        # First, check strategy mapping
        if strategy:
            category = self.STRATEGY_CATEGORY_MAP.get(strategy)
            if category:
                return category
        
        # Then check symbol mapping
        symbol_upper = symbol.upper().strip()
        category = self.SYMBOL_CATEGORY_MAP.get(symbol_upper)
        if category:
            return category
        
        # Default to index options for unknown symbols
        self.logger.warning(f"⚠️ Unknown symbol/strategy: {symbol}/{strategy}, defaulting to INDEX_OPTIONS")
        return TradingCategory.INDEX_OPTIONS
    
    def get_channel_config(self, category: TradingCategory) -> ChannelConfig:
        """
        Get channel configuration for category
        
        Args:
            category: TradingCategory
            
        Returns:
            ChannelConfig object
        """
        config = self.CHANNEL_CONFIG.get(category)
        if not config:
            self.logger.error(f"❌ No channel config for category: {category}")
            return self.CHANNEL_CONFIG[TradingCategory.INDEX_OPTIONS]
        return config
    
    def route_alert(
        self,
        symbol: str,
        strategy: str,
        signal_data: Dict,
        formatted_message: str
    ) -> Dict:
        """
        Route alert to correct channel
        
        Args:
            symbol: Trading symbol
            strategy: Strategy name
            signal_data: Signal data dictionary
            formatted_message: Formatted alert message
            
        Returns:
            Routing information dict
        """
        try:
            # Determine category
            category = self.get_category_for_signal(symbol, strategy)
            config = self.get_channel_config(category)
            
            routing_info = {
                "success": True,
                "symbol": symbol,
                "strategy": strategy,
                "category": category.value,
                "channel_name": config.name,
                "channel_id": config.default_channel_id,
                "bot_token_env": config.bot_token_env,
                "channel_id_env": config.channel_id_env,
                "message": formatted_message,
                "metadata": {
                    "signal_type": signal_data.get("signal_type", "UNKNOWN"),
                    "entry_price": signal_data.get("entry_price", 0),
                    "sl": signal_data.get("sl", 0),
                    "target": signal_data.get("target", 0),
                    "strategy": strategy,
                    "timeframe": signal_data.get("timeframe", "Intraday"),
                }
            }
            
            self.logger.info(
                f"✅ Alert routed to {config.name}\n"
                f"   Symbol: {symbol}\n"
                f"   Strategy: {strategy}\n"
                f"   Category: {category.value}"
            )
            
            return routing_info
            
        except Exception as e:
            self.logger.error(f"❌ Error routing alert: {e}")
            return {
                "success": False,
                "error": str(e),
                "symbol": symbol,
                "strategy": strategy
            }
    
    def get_all_channels(self) -> List[Dict]:
        """
        Get configuration for all channels
        
        Returns:
            List of channel configuration dicts
        """
        channels = []
        for category, config in self.CHANNEL_CONFIG.items():
            channels.append({
                "name": config.name,
                "category": config.category.value,
                "description": config.description,
                "default_channel_id": config.default_channel_id,
                "bot_token_env": config.bot_token_env,
                "channel_id_env": config.channel_id_env,
            })
        return channels
    
    def validate_symbol(self, symbol: str) -> tuple[bool, str]:
        """
        Validate if symbol is supported
        
        Args:
            symbol: Trading symbol
            
        Returns:
            (is_valid, message)
        """
        symbol_upper = symbol.upper().strip()
        
        if symbol_upper in self.SYMBOL_CATEGORY_MAP:
            category = self.SYMBOL_CATEGORY_MAP[symbol_upper]
            config = self.CHANNEL_CONFIG[category]
            return True, f"✅ {symbol} routes to {config.name}"
        
        return False, f"❌ Unknown symbol: {symbol}"
    
    def add_strategy_metadata(
        self,
        signal_data: Dict,
        strategy: str,
        category: TradingCategory
    ) -> Dict:
        """
        Add strategy metadata to signal
        
        Args:
            signal_data: Original signal data
            strategy: Strategy name
            category: Channel category
            
        Returns:
            Enhanced signal data with metadata
        """
        enhanced = signal_data.copy()
        enhanced.update({
            "strategy": strategy,
            "category": category.value,
            "channel": self.CHANNEL_CONFIG[category].name,
            "routing_timestamp": __import__("datetime").datetime.now().isoformat(),
        })
        return enhanced
    
    def should_skip_alert(self, symbol: str, signal_type: str) -> bool:
        """
        Determine if alert should be skipped
        
        Args:
            symbol: Trading symbol
            signal_type: BUY or SELL
            
        Returns:
            True if alert should be skipped
        """
        # Add logic for alert filtering if needed
        return False
    
    def format_alert_for_channel(
        self,
        signal_data: Dict,
        category: TradingCategory
    ) -> str:
        """
        Format alert message for specific channel
        
        Args:
            signal_data: Signal data
            category: Channel category
            
        Returns:
            Formatted message
        """
        config = self.CHANNEL_CONFIG[category]
        
        lines = [
            f"📊 <b>{config.name}</b>",
            "",
            f"<b>Symbol:</b> {signal_data.get('symbol', 'N/A')}",
            f"<b>Signal:</b> {signal_data.get('signal_type', 'UNKNOWN')}",
            f"<b>Entry:</b> {signal_data.get('entry_price', 0):.2f}",
            f"<b>SL:</b> {signal_data.get('sl', 0):.2f}",
            f"<b>Target:</b> {signal_data.get('target', 0):.2f}",
            "",
            f"Strategy: {signal_data.get('strategy', 'Unknown')}",
            f"Timeframe: {signal_data.get('timeframe', 'Intraday')}",
        ]
        
        return "\n".join(filter(None, lines))


# Singleton instance
_router: Optional[StrategyAlertsRouter] = None


def get_alerts_router() -> StrategyAlertsRouter:
    """Get or create alerts router instance"""
    global _router
    if _router is None:
        _router = StrategyAlertsRouter()
    return _router
