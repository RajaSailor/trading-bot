def format_signal_message(symbol, signal_data):
    """Format signal for Telegram with new format - Premium based"""
    entry_premium = signal_data.get('premium', signal_data['entry'])
    target_premium = entry_premium * 1.30  # 30% above entry premium
    stoploss_premium = entry_premium * 0.90  # 10% below entry premium
    
    signal_type = signal_data['buy_side']  # CALL or PUT
    strike_price = signal_data['strike_price']
    
    # Add CE/PE suffix
    option_type = "CE" if signal_type == "CALL" else "PE"
    
    msg = f"""
<b>{'🚀 CALL ENTRY' if signal_type == 'CALL' else '📉 PUT ENTRY'}</b>

<b>Title:</b> {symbol} | {SYMBOLS[symbol]['type']}
<b>Strike Price:</b> {strike_price:.0f} {option_type}
<b>Premium:</b> {entry_premium:.2f} (LTP)

<b>📊 POSITION DETAILS:</b>
  <b>Entry:</b> {entry_premium:.2f}
  <b>Target:</b> {target_premium:.2f} (30% gain)
  <b>Stop Loss:</b> {stoploss_premium:.2f} (10% loss)

<b>⏰ Time:</b> {signal_data['timestamp']}
<b>📈 Current Premium:</b> {entry_premium:.2f}
<b>⏱️ Timeframe:</b> 10-MIN

<b>📢 IMPORTANT DISCLAIMER & NOTICE</b>
I am <b>NOT a SEBI-registered investment advisor or research analyst.</b>
This alert is created strictly for educational, informational, and learning purposes only.
<b>Always consult a SEBI-registered advisor before trading.</b>
"""
    return msg
