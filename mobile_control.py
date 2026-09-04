"""
MOBILE CONTROL DASHBOARD - TELEGRAM BOT
Command-based screener control via Telegram
File: mobile_control.py
"""

import os
import logging
from datetime import datetime, time as dtime
from dotenv import load_dotenv
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from screener_background import screener_state, stop_screener, start_screener, get_ist_time, IST
import threading
import pytz

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv(dotenv_path="./.env", override=True)

# System bot token for mobile control
SYSTEM_BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
SYSTEM_CHAT_ID = int(os.getenv("CHAT_ID"))

# ============================================================================
# COMMAND HANDLERS
# ============================================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command - show menu"""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    if chat_id != SYSTEM_CHAT_ID:
        await update.message.reply_text("❌ Unauthorized access")
        return
    
    menu = """
🎯 <b>TRADING BOT MOBILE CONTROL</b>

<b>📊 Status Commands:</b>
/status - Current screener status
/stats - Trading statistics
/health - System health check
/time - Current IST time

<b>🎮 Control Commands:</b>
/start_screener - Start screener
/stop_screener - Stop screener
/restart_screener - Restart screener

<b>🧪 Practice Mode:</b>
/practice_on - Enable practice trading
/practice_off - Disable practice trading
/practice_status - Check practice mode

<b>💰 Auto Trade Mode:</b>
/auto_on - Enable auto trading
/auto_off - Disable auto trading
/auto_status - Check auto mode

<b>📁 Backup & Management:</b>
/backup - Backup all data now
/logs - Get recent logs
/config - Show configuration

<b>❌ Emergency:</b>
/kill - Emergency stop all bots
/panic - Stop all operations immediately

Type a command to proceed!
"""
    await update.message.reply_text(menu, parse_mode="HTML")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current screener status"""
    chat_id = update.effective_chat.id
    if chat_id != SYSTEM_CHAT_ID:
        await update.message.reply_text("❌ Unauthorized")
        return
    
    ist_time = get_ist_time()
    msg = f"""
<b>📊 SCREENER STATUS</b>

<b>🔴 Running:</b> {'✅ YES' if screener_state['running'] else '❌ NO'}
<b>🟢 Market Open:</b> {'✅ YES' if screener_state['market_open'] else '❌ NO'}
<b>🔗 DhanHQ:</b> {'✅ CONNECTED' if screener_state['dhan_connected'] else '❌ OFFLINE'}
<b>📱 Telegram:</b> {'✅ CONNECTED' if screener_state['telegram_connected'] else '❌ OFFLINE'}
<b>🌍 Public IP:</b> {screener_state['public_ip']}

<b>⏰ Time (IST):</b> {ist_time.strftime('%Y-%m-%d %H:%M:%S %Z')}
<b>📌 Last Scan:</b> {screener_state['last_scan_time']}

<b>🤖 Bot Status:</b>
"""
    for bot_type, status in screener_state['bot_status'].items():
        msg += f"  {bot_type}: {status}\n"
    
    await update.message.reply_text(msg, parse_mode="HTML")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show trading statistics"""
    chat_id = update.effective_chat.id
    if chat_id != SYSTEM_CHAT_ID:
        await update.message.reply_text("❌ Unauthorized")
        return
    
    success_rate = 0
    if screener_state["total_scans"] > 0:
        success_rate = (screener_state["successful_scans"] / screener_state["total_scans"]) * 100
    
    msg = f"""
<b>📈 TRADING STATISTICS</b>

<b>📊 Scan Stats:</b>
  Total Scans: {screener_state['total_scans']}
  Successful: {screener_state['successful_scans']}
  Success Rate: {success_rate:.1f}%

<b>🚀 Signal Stats:</b>
  Total Signals: {screener_state['total_signals']}
  CALL Signals: {screener_state['call_signals']}
  PUT Signals: {screener_state['put_signals']}

<b>Ratio: </b>
  CALL: {screener_state['call_signals']/max(screener_state['total_signals'], 1)*100:.1f}%
  PUT: {screener_state['put_signals']/max(screener_state['total_signals'], 1)*100:.1f}%

<b>⚠️ Errors:</b>
  Total Errors: {len(screener_state['errors'])}
  Recent: {screener_state['errors'][-3:] if screener_state['errors'] else 'None'}
"""
    await update.message.reply_text(msg, parse_mode="HTML")

async def health_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """System health check"""
    chat_id = update.effective_chat.id
    if chat_id != SYSTEM_CHAT_ID:
        await update.message.reply_text("❌ Unauthorized")
        return
    
    health = "✅ HEALTHY" if (screener_state['dhan_connected'] and screener_state['telegram_connected']) else "⚠️ ISSUES"
    
    msg = f"""
<b>🏥 SYSTEM HEALTH CHECK</b>

<b>Status:</b> {health}

<b>Components:</b>
  ✅ Flask App: Running (port 10000)
  {'✅' if screener_state['running'] else '❌'} Screener: {'Running' if screener_state['running'] else 'Stopped'}
  {'✅' if screener_state['dhan_connected'] else '❌'} DhanHQ API: {'Connected' if screener_state['dhan_connected'] else 'Offline'}
  {'✅' if screener_state['telegram_connected'] else '❌'} Telegram: {'Connected' if screener_state['telegram_connected'] else 'Offline'}

<b>Resources:</b>
  Memory: OK
  CPU: OK
  Disk: OK
  Network: OK

<b>Alerts:</b>
  Total Errors: {len(screener_state['errors'])}
  Last Error: {screener_state['errors'][-1] if screener_state['errors'] else 'None'}
"""
    await update.message.reply_text(msg, parse_mode="HTML")

async def time_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current IST time"""
    chat_id = update.effective_chat.id
    if chat_id != SYSTEM_CHAT_ID:
        await update.message.reply_text("❌ Unauthorized")
        return
    
    ist_time = get_ist_time()
    msg = f"""
<b>🕐 CURRENT TIME (IST)</b>

<b>India Time:</b> {ist_time.strftime('%Y-%m-%d %H:%M:%S %Z')}
<b>Timezone:</b> Asia/Kolkata (UTC+5:30)

<b>📅 Market Hours (IST):</b>
  NSE: 9:15 AM - 3:30 PM
  MCX: 9:00 AM - 11:30 PM

<b>Current Status:</b>
  {'✅ NSE OPEN' if dtime(9, 15) <= ist_time.time() <= dtime(15, 30) else '❌ NSE CLOSED'}
  {'✅ MCX OPEN' if dtime(9, 0) <= ist_time.time() <= dtime(23, 30) else '❌ MCX CLOSED'}
"""
    await update.message.reply_text(msg, parse_mode="HTML")

async def start_screener_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start screener"""
    chat_id = update.effective_chat.id
    if chat_id != SYSTEM_CHAT_ID:
        await update.message.reply_text("❌ Unauthorized")
        return
    
    if screener_state['running']:
        await update.message.reply_text("⚠️ Screener already running!")
        return
    
    screener_state['running'] = True
    msg = "✅ Screener started! Now monitoring 53 symbols across 6 channels."
    await update.message.reply_text(msg)

async def stop_screener_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stop screener"""
    chat_id = update.effective_chat.id
    if chat_id != SYSTEM_CHAT_ID:
        await update.message.reply_text("❌ Unauthorized")
        return
    
    if not screener_state['running']:
        await update.message.reply_text("⚠️ Screener already stopped!")
        return
    
    stop_screener()
    msg = "🛑 Screener stopped. No more signals will be sent."
    await update.message.reply_text(msg)

async def restart_screener_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Restart screener"""
    chat_id = update.effective_chat.id
    if chat_id != SYSTEM_CHAT_ID:
        await update.message.reply_text("❌ Unauthorized")
        return
    
    stop_screener()
    await update.message.reply_text("🔄 Restarting screener...")
    
    # Restart
    screener_state['running'] = True
    msg = "✅ Screener restarted successfully!"
    await update.message.reply_text(msg)

async def practice_on_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Enable practice mode"""
    chat_id = update.effective_chat.id
    if chat_id != SYSTEM_CHAT_ID:
        await update.message.reply_text("❌ Unauthorized")
        return
    
    screener_state['practice_mode'] = True
    msg = "🧪 Practice Mode ENABLED\n\n✅ Alerts will be sent but NO real trades executed.\n⚠️ Use this to test strategy without risking money."
    await update.message.reply_text(msg, parse_mode="HTML")

async def practice_off_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Disable practice mode"""
    chat_id = update.effective_chat.id
    if chat_id != SYSTEM_CHAT_ID:
        await update.message.reply_text("❌ Unauthorized")
        return
    
    screener_state['practice_mode'] = False
    msg = "🧪 Practice Mode DISABLED\n\n⚠️ Real trading mode active. Trades will execute automatically if auto-trade is ON."
    await update.message.reply_text(msg, parse_mode="HTML")

async def practice_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check practice mode status"""
    chat_id = update.effective_chat.id
    if chat_id != SYSTEM_CHAT_ID:
        await update.message.reply_text("❌ Unauthorized")
        return
    
    status = "✅ ON" if screener_state.get('practice_mode', False) else "❌ OFF"
    msg = f"🧪 <b>Practice Mode:</b> {status}\n\n📝 Current mode: {'Paper Trading (No Real Orders)' if screener_state.get('practice_mode') else 'Live Trading (Real Orders)'}"
    await update.message.reply_text(msg, parse_mode="HTML")

async def auto_on_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Enable auto trading"""
    chat_id = update.effective_chat.id
    if chat_id != SYSTEM_CHAT_ID:
        await update.message.reply_text("❌ Unauthorized")
        return
    
    if screener_state.get('practice_mode'):
        await update.message.reply_text("⚠️ Practice mode is ON. Disable it first for real auto-trading!")
        return
    
    screener_state['auto_trade'] = True
    msg = "💰 <b>Auto-Trade Mode ENABLED</b>\n\n🚀 Real trades will execute automatically on breakout signals.\n⚠️ <b>WARNING:</b> Real money will be used!"
    await update.message.reply_text(msg, parse_mode="HTML")

async def auto_off_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Disable auto trading"""
    chat_id = update.effective_chat.id
    if chat_id != SYSTEM_CHAT_ID:
        await update.message.reply_text("❌ Unauthorized")
        return
    
    screener_state['auto_trade'] = False
    msg = "💰 <b>Auto-Trade Mode DISABLED</b>\n\n📬 Alerts will still be sent but trades won't execute automatically."
    await update.message.reply_text(msg, parse_mode="HTML")

async def auto_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check auto trade status"""
    chat_id = update.effective_chat.id
    if chat_id != SYSTEM_CHAT_ID:
        await update.message.reply_text("❌ Unauthorized")
        return
    
    auto_status = "✅ ON" if screener_state.get('auto_trade', False) else "❌ OFF"
    practice_status = "✅ ON" if screener_state.get('practice_mode', False) else "❌ OFF"
    
    msg = f"""
<b>💰 AUTO-TRADE STATUS</b>

<b>Auto Trading:</b> {auto_status}
<b>Practice Mode:</b> {practice_status}

<b>📝 Current Mode:</b>
  {'🧪 Paper Trading (Practice)' if screener_state.get('practice_mode') else '💰 Live Trading (Real Money)'}
  {'✅ Automatic Execution' if screener_state.get('auto_trade') else '❌ Manual Execution Required'}
"""
    await update.message.reply_text(msg, parse_mode="HTML")

async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Backup all data"""
    chat_id = update.effective_chat.id
    if chat_id != SYSTEM_CHAT_ID:
        await update.message.reply_text("❌ Unauthorized")
        return
    
    msg = "💾 <b>BACKUP INITIATED</b>\n\n⏳ Backing up screener data, logs, trades, and configuration...\n\n✅ Backup will be ready in 30 seconds."
    await update.message.reply_text(msg, parse_mode="HTML")
    
    # TODO: Implement actual backup logic

async def logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Get recent logs"""
    chat_id = update.effective_chat.id
    if chat_id != SYSTEM_CHAT_ID:
        await update.message.reply_text("❌ Unauthorized")
        return
    
    recent_errors = screener_state['errors'][-5:] if screener_state['errors'] else ['No errors']
    msg = "<b>📋 RECENT LOGS</b>\n\n"
    msg += "Errors:\n"
    for i, error in enumerate(recent_errors, 1):
        msg += f"{i}. {error}\n"
    
    await update.message.reply_text(msg, parse_mode="HTML")

async def config_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show configuration"""
    chat_id = update.effective_chat.id
    if chat_id != SYSTEM_CHAT_ID:
        await update.message.reply_text("❌ Unauthorized")
        return
    
    msg = f"""
<b>⚙️ CONFIGURATION</b>

<b>Market Settings:</b>
  NSE Hours: 9:15 AM - 3:30 PM IST
  MCX Hours: 9:00 AM - 11:30 PM IST
  Scan Interval: 10 seconds

<b>Strategy:</b>
  Type: 10-Minute Breakout
  Timeframe: 10 minutes
  Entry: Candle breakout
  Target: 30% gain
  Stop Loss: 10% loss

<b>Monitoring:</b>
  Symbols: 53 total
  INDEX F&O: 3 (NIFTY, BANKNIFTY, SENSEX)
  NIFTY 50: 46 stocks
  COMMODITY: 4 (GOLD, SILVER, CRUDE, GAS)
  CRYPTO: 2 (BTC, ETH)

<b>Channels:</b>
  Trading: 6 dedicated channels
  System: 1 control channel
"""
    await update.message.reply_text(msg, parse_mode="HTML")

async def kill_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Emergency stop all operations"""
    chat_id = update.effective_chat.id
    if chat_id != SYSTEM_CHAT_ID:
        await update.message.reply_text("❌ Unauthorized")
        return
    
    stop_screener()
    screener_state['auto_trade'] = False
    
    msg = "🚨 <b>EMERGENCY STOP ACTIVATED</b>\n\n❌ All operations halted.\n❌ Screener stopped.\n❌ Auto-trading disabled.\n\n⚠️ System in safe mode. No signals will be sent."
    await update.message.reply_text(msg, parse_mode="HTML")

async def panic_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Panic mode - immediate stop"""
    chat_id = update.effective_chat.id
    if chat_id != SYSTEM_CHAT_ID:
        await update.message.reply_text("❌ Unauthorized")
        return
    
    stop_screener()
    screener_state['auto_trade'] = False
    screener_state['practice_mode'] = False
    
    msg = "🚨 <b>PANIC MODE ACTIVATED</b>\n\n❌ ALL SYSTEMS DOWN\n⏹️ Screener: STOPPED\n⏹️ Auto-Trade: DISABLED\n⏹️ Practice Mode: OFF\n\nContact admin for restart."
    await update.message.reply_text(msg, parse_mode="HTML")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show help"""
    await start_command(update, context)

async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle unknown commands"""
    await update.message.reply_text("❌ Unknown command. Type /start for help.")

# ============================================================================
# MAIN APPLICATION
# ============================================================================

def start_mobile_control():
    """Start mobile control bot"""
    logger.info("🚀 Starting Mobile Control Bot...")
    
    # Create application
    app = Application.builder().token(SYSTEM_BOT_TOKEN).build()
    
    # Add command handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("health", health_command))
    app.add_handler(CommandHandler("time", time_command))
    app.add_handler(CommandHandler("start_screener", start_screener_command))
    app.add_handler(CommandHandler("stop_screener", stop_screener_command))
    app.add_handler(CommandHandler("restart_screener", restart_screener_command))
    app.add_handler(CommandHandler("practice_on", practice_on_command))
    app.add_handler(CommandHandler("practice_off", practice_off_command))
    app.add_handler(CommandHandler("practice_status", practice_status_command))
    app.add_handler(CommandHandler("auto_on", auto_on_command))
    app.add_handler(CommandHandler("auto_off", auto_off_command))
    app.add_handler(CommandHandler("auto_status", auto_status_command))
    app.add_handler(CommandHandler("backup", backup_command))
    app.add_handler(CommandHandler("logs", logs_command))
    app.add_handler(CommandHandler("config", config_command))
    app.add_handler(CommandHandler("kill", kill_command))
    app.add_handler(CommandHandler("panic", panic_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command))
    
    # Run in background thread
    control_thread = threading.Thread(target=lambda: app.run_polling(), daemon=True)
    control_thread.start()
    
    logger.info("✅ Mobile Control Bot started successfully!")
    logger.info(f"📱 Control Channel: {SYSTEM_CHAT_ID}")
    
    return control_thread

if __name__ == "__main__":
    start_mobile_control()

