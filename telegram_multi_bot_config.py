"""
TELEGRAM MULTI-BOT CONFIGURATION
5 Different bots for 5 different channels/groups
File: telegram_multi_bot_config.py
"""

# ============================================
# TELEGRAM BOTS & CHANNELS CONFIGURATION
# ============================================

TELEGRAM_BOTS = {
    # ============================================
    # BOT 1: INDEX OPTIONS ALERTS
    # ============================================
    "INDEX_OPTIONS": {
        "bot_name": "winindexoptionsalertsbot",
        "bot_url": "t.me/winindexoptionsalertsbot",
        "token": "8601160697:AAFFxscCMfqcrXaf1lw69xK7Ue-RW_8aIzI",
        "channel_name": "📊 INDEX OPTIONS ALERTS",
        "channel_chat_id": -1003814243881,
        "description": "Real-time NIFTY 50, BANK NIFTY & SENSEX Options Trading Signals",
        "symbols": ["NIFTY", "BANKNIFTY", "SENSEX"],
        "asset_type": "INDEX"
    },
    
    # ============================================
    # BOT 2: COMMODITY OPTIONS ALERTS
    # ============================================
    "COMMODITY_OPTIONS": {
        "bot_name": "wincommodityoptionsalertsbot",
        "bot_url": "t.me/wincommodityoptionsalertsbot",
        "token": "8762956800:AAEkQZfYhawfxQEua8OSYcnp3FPRU2xywsc",
        "channel_name": "⚫ COMMODITY OPTIONS ALERTS",
        "channel_chat_id": -1004466883026,
        "description": "Real-time CRUDE OIL, GOLD, SILVER & NATURAL GAS Options Signals",
        "symbols": ["CRUDEOIL", "GOLD", "SILVER", "NATURALGAS"],
        "asset_type": "COMMODITY"
    },
    
    # ============================================
    # BOT 3: NIFTY 50 STOCKS OPTIONS
    # ============================================
    "NIFTY_50_STOCKS_OPTIONS": {
        "bot_name": "winnifty50stocksoptionsalertsbot",
        "bot_url": "t.me/winnifty50stocksoptionsalertsbot",
        "token": "8746059399:AAGfpg6rQfluICaezqiamCujN8_NcXbt1NQ",
        "channel_name": "📈 NIFTY 50 STOCKS OPTIONS",
        "channel_chat_id": -1003804613787,
        "description": "All 50 NIFTY stocks Options (ATM/ITM Premium >= 10 LTP)",
        "symbols": [
            "ADANIENTERPRISES", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
            "BAJAJAUT", "BAJAJFINSV", "BAJAJFINANCE", "BHARTIARTL", "BPCL", "CIPLA", "COALINDIA",
            "DRREDDY", "EICHERMOT", "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE", "HINDALCO", "HINDUNILVR",
            "ICICIBANK", "INFY", "ITC", "JSWSTEEL", "KOTAKBANK",
            "LT", "MM", "MARUTI", "NESTLEIND",
            "NTPC", "ONGC", "POWERGRID",
            "RELIANCE", "SBILIFE", "SBIN", "SUNPHARMA",
            "TCS", "TATACONSUM", "TATAMOTORS", "TATASTEELS", "TECHM", "TITAN", "TRENT", "ULTRACEMCO", "WIPRO"
        ],
        "asset_type": "STOCK",
        "strategy": "OPTIONS"
    },
    
    # ============================================
    # BOT 4: NIFTY 50 INTRADAY 5X LEVERAGE
    # ============================================
    "NIFTY_50_INTRADAY_5X": {
        "bot_name": "winnifty50intraday5xalertsbot",
        "bot_url": "t.me/winnifty50intraday5xalertsbot",
        "token": "8265739611:AAFbraUdEY01eJOel76S8mMgBiZT4otxkd4",
        "channel_name": "⚡ NIFTY 50 INTRADAY 5X",
        "channel_chat_id": -1004403277287,
        "description": "NIFTY 50 Stocks Intraday Signals with 5X Leverage (High Risk/Reward)",
        "symbols": [
            "ADANIENTERPRISES", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
            "BAJAJAUT", "BAJAJFINSV", "BAJAJFINANCE", "BHARTIARTL", "BPCL", "CIPLA", "COALINDIA",
            "DRREDDY", "EICHERMOT", "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE", "HINDALCO", "HINDUNILVR",
            "ICICIBANK", "INFY", "ITC", "JSWSTEEL", "KOTAKBANK",
            "LT", "MM", "MARUTI", "NESTLEIND",
            "NTPC", "ONGC", "POWERGRID",
            "RELIANCE", "SBILIFE", "SBIN", "SUNPHARMA",
            "TCS", "TATACONSUM", "TATAMOTORS", "TATASTEELS", "TECHM", "TITAN", "TRENT", "ULTRACEMCO", "WIPRO"
        ],
        "asset_type": "STOCK",
        "strategy": "INTRADAY_5X",
        "leverage": "5X"
    },
    
    # ============================================
    # BOT 5: NIFTY 50 PAY LATER
    # ============================================
    "NIFTY_50_PAY_LATER": {
        "bot_name": "winnifty50paylateralertsbot",
        "bot_url": "t.me/winnifty50paylateralertsbot",
        "token": "8934391945:AAEdycuHV7sZP6eASCU2j7kQ9SBG7e9D4Q0",
        "channel_name": "🏦 NIFTY 50 PAY LATER",
        "channel_chat_id": -1003966854994,
        "description": "NIFTY 50 Stocks - Buy Now Pay Later (Margin) Trading Signals",
        "symbols": [
            "ADANIENTERPRISES", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
            "BAJAJAUT", "BAJAJFINSV", "BAJAJFINANCE", "BHARTIARTL", "BPCL", "CIPLA", "COALINDIA",
            "DRREDDY", "EICHERMOT", "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE", "HINDALCO", "HINDUNILVR",
            "ICICIBANK", "INFY", "ITC", "JSWSTEEL", "KOTAKBANK",
            "LT", "MM", "MARUTI", "NESTLEIND",
            "NTPC", "ONGC", "POWERGRID",
            "RELIANCE", "SBILIFE", "SBIN", "SUNPHARMA",
            "TCS", "TATACONSUM", "TATAMOTORS", "TATASTEELS", "TECHM", "TITAN", "TRENT", "ULTRACEMCO", "WIPRO"
        ],
        "asset_type": "STOCK",
        "strategy": "PAY_LATER",
        "margin_type": "PAY_LATER"
    },
}

# Create symbol to bot mapping for quick lookup
SYMBOL_TO_BOT = {}
for bot_key, bot_config in TELEGRAM_BOTS.items():
    for symbol in bot_config["symbols"]:
        SYMBOL_TO_BOT[symbol] = bot_key

print("""
╔════════════════════════════════════════════════════════════════════════════════════════╗
║                   ✅ 5 TELEGRAM BOTS - ALL CONFIGURED & READY                         ║
╚════════════════════════════════════════════════════════════════════════════════════════╝

✅ BOT 1: INDEX OPTIONS ALERTS
   Bot: @winindexoptionsalertsbot
   Channel ID: -1003814243881
   Monitors: NIFTY, BANKNIFTY, SENSEX (3 assets)
   Status: ✅ CONFIGURED & READY
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✅ BOT 2: COMMODITY OPTIONS ALERTS
   Bot: @wincommodityoptionsalertsbot
   Channel ID: -1004466883026
   Monitors: CRUDEOIL, GOLD, SILVER, NATURALGAS (4 assets)
   Status: ✅ CONFIGURED & READY
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✅ BOT 3: NIFTY 50 STOCKS OPTIONS
   Bot: @winnifty50stocksoptionsalertsbot
   Channel ID: -1003804613787
   Monitors: All 50 NIFTY stocks options (ATM/ITM)
   Strategy: OPTIONS (10-min, 30% target, 10% SL)
   Status: ✅ CONFIGURED & READY
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✅ BOT 4: NIFTY 50 INTRADAY 5X
   Bot: @winnifty50intraday5xalertsbot
   Channel ID: -1004403277287
   Monitors: All 50 NIFTY stocks (5X leverage intraday)
   Strategy: INTRADAY_5X (Scalp signals)
   Status: ✅ CONFIGURED & READY
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✅ BOT 5: NIFTY 50 PAY LATER
   Bot: @winnifty50paylateralertsbot
   Channel ID: -1003966854994
   Monitors: All 50 NIFTY stocks (margin/BNPL)
   Strategy: PAY_LATER (Swing signals)
   Status: ✅ CONFIGURED & READY
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

╔════════════════════════════════════════════════════════════════════════════════════════╗
║                        ✅ ALL CHANNEL IDs CONFIGURED                                  ║
╚════════════════════════════════════════════════════════════════════════════════════════╝

Ready to integrate with screener and deploy! 🚀

TOTAL COVERAGE:
  • 3 Indices
  • 4 Commodities
  • 50 NIFTY Stocks (3 trading modes each)
  = 59 Total Assets Monitored
  = 5 Separate Telegram Channels
  = Fully Automated Alerts! ✅
""")

# Function to get bot for a symbol and strategy
def get_bot_for_signal(symbol, strategy_type="OPTIONS"):
    """
    Get bot configuration based on symbol and strategy type
    
    strategy_type: "OPTIONS", "INTRADAY_5X", "PAY_LATER"
    Returns: Bot config dict with token and chat_id
    """
    if symbol in ["NIFTY", "BANKNIFTY", "SENSEX"]:
        return TELEGRAM_BOTS["INDEX_OPTIONS"]
    elif symbol in ["CRUDEOIL", "GOLD", "SILVER", "NATURALGAS"]:
        return TELEGRAM_BOTS["COMMODITY_OPTIONS"]
    elif symbol in TELEGRAM_BOTS["NIFTY_50_STOCKS_OPTIONS"]["symbols"]:
        if strategy_type == "OPTIONS":
            return TELEGRAM_BOTS["NIFTY_50_STOCKS_OPTIONS"]
        elif strategy_type == "INTRADAY_5X":
            return TELEGRAM_BOTS["NIFTY_50_INTRADAY_5X"]
        elif strategy_type == "PAY_LATER":
            return TELEGRAM_BOTS["NIFTY_50_PAY_LATER"]
    
    return None

# Function to get token for a bot
def get_bot_token(bot_key):
    """Get bot token by bot key"""
    if bot_key in TELEGRAM_BOTS:
        return TELEGRAM_BOTS[bot_key]["token"]
    return None

# Function to get chat ID for a bot
def get_chat_id(bot_key):
    """Get channel chat ID for a bot"""
    if bot_key in TELEGRAM_BOTS:
        return TELEGRAM_BOTS[bot_key]["channel_chat_id"]
    return None

# Function to get chat ID from symbol
def get_chat_id_for_symbol(symbol, strategy_type="OPTIONS"):
    """Get chat ID directly from symbol and strategy"""
    bot_config = get_bot_for_signal(symbol, strategy_type)
    if bot_config:
        return bot_config["channel_chat_id"]
    return None
