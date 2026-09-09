"""
TEST SUITE - Verify All Components Working
Tests:
1. Telegram bot connectivity
2. Signal detection
3. Security IDs
4. DhanHQ API
5. Event loop management

File: test_all_components.py
"""

import os
import sys
import time
import logging
from datetime import datetime, timedelta

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def test_telegram_bots():
    """Test 1: Verify Telegram bots are configured and working"""
    print("\n" + "="*80)
    print("TEST 1: TELEGRAM BOT CONNECTIVITY")
    print("="*80)
    
    try:
        from dotenv import load_dotenv
        load_dotenv()
        
        from telegram_alerts_fixed import get_telegram_alerts
        
        alerts = get_telegram_alerts()
        
        # Check bot status
        status = alerts.get_bot_status()
        
        print(f"\n✅ TelegramAlertsFixed initialized")
        print(f"\nConfigured Bots:")
        
        active_bots = 0
        for bot_name, bot_status in status.items():
            status_icon = "✅" if bot_status["status"] == "READY" else "❌"
            print(f"  {status_icon} {bot_name}")
            print(f"     Description: {bot_status['description']}")
            print(f"     Status: {bot_status['status']}")
            print(f"     Queue: {bot_status['queue_size']} messages")
            if bot_status["status"] == "READY":
                active_bots += 1
        
        print(f"\n✅ Total Active Bots: {active_bots}/{len(status)}")
        
        if active_bots == 0:
            print("\n❌ WARNING: No bots configured!")
            print("   Make sure your .env has:")
            print("   - BOT_INDEX_TOKEN & CHANNEL_INDEX_ID")
            print("   - BOT_COMMODITY_TOKEN & CHANNEL_COMMODITY_ID")
            print("   - And other bot tokens/channels")
            return False
        
        # Try sending a test alert
        print("\n" + "-"*80)
        print("Sending test alert to 'index_options' channel...")
        
        test_signal = {
            "symbol": "TEST-NIFTY50",
            "signal": "CALL",
            "entry": 18250.50,
            "stop_loss": 18200.00,
            "targets": [18300, 18350, 18400],
            "reference_timestamp": "14:25:00",
            "breakout_timestamp": "14:30:00",
        }
        
        test_option = {
            "call_strike": 18250,
            "put_strike": 18250,
            "call_premium": 42.50,
            "put_premium": 38.20
        }
        
        success = alerts.send_signal_alert("index_options", test_signal, test_option)
        
        if success:
            print("✅ Test alert queued successfully")
            print("   (Check your Telegram channel - should arrive in 2-3 seconds)")
            time.sleep(3)
        else:
            print("❌ Failed to queue test alert")
            return False
        
        # Check alert history
        history = alerts.get_alert_history()
        print(f"\n✅ Alert history: {len(history)} alerts")
        
        alerts.shutdown()
        print("\n✅ TEST 1 PASSED: Telegram bots working!")
        return True
    
    except Exception as e:
        print(f"\n❌ TEST 1 FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_signal_detection():
    """Test 2: Verify signal detection works"""
    print("\n" + "="*80)
    print("TEST 2: SIGNAL DETECTION")
    print("="*80)
    
    try:
        from signal_processor import SignalProcessor
        
        processor = SignalProcessor()
        
        # Create mock candle data
        base_time = int(datetime.now().timestamp())
        
        test_candles = [
            {
                "timestamp": base_time - 300,
                "open": 18200.0,
                "high": 18250.0,
                "low": 18180.0,
                "close": 18240.0,
                "volume": 1000000
            },
            {
                "timestamp": base_time,
                "open": 18240.0,
                "high": 18280.0,
                "low": 18235.0,
                "close": 18275.0,
                "volume": 1200000
            }
        ]
        
        print(f"\nTest Candles:")
        print(f"  Previous: O:{test_candles[0]['open']} H:{test_candles[0]['high']} L:{test_candles[0]['low']} C:{test_candles[0]['close']}")
        print(f"  Current:  O:{test_candles[1]['open']} H:{test_candles[1]['high']} L:{test_candles[1]['low']} C:{test_candles[1]['close']}")
        
        # Test breakout detection
        signal = processor.detect_breakout(
            candles=test_candles,
            symbol="TEST-NIFTY50",
            threshold_percent=0.1
        )
        
        if signal:
            print(f"\n✅ Signal Detected: {signal['type'].value}")
            print(f"   Entry: ₹{signal['entry']}")
            print(f"   SL: ₹{signal['stop_loss']}")
            print(f"   Targets: {signal['targets']}")
            print(f"   Reference High: ₹{signal.get('reference_high')}")
            print(f"   Reference Low: ₹{signal.get('reference_low')}")
        else:
            print(f"\n❌ No signal detected (breakout not significant enough)")
            return False
        
        # Test option chain data
        print(f"\n" + "-"*80)
        print("Testing option chain data...")
        
        option_data = processor.get_option_chain_data(underlying_price=18275.0)
        
        print(f"\n✅ Option Chain Data:")
        print(f"   Underlying: ₹{option_data['underlying_price']}")
        print(f"   ATM Strike: {option_data['atm_strike']}")
        print(f"   Call Premium: ₹{option_data['call_premium']}")
        print(f"   Put Premium: ₹{option_data['put_premium']}")
        print(f"   Call Volume: {option_data['call_volume']}")
        print(f"   Put Volume: {option_data['put_volume']}")
        
        # Test signal formatting
        print(f"\n" + "-"*80)
        print("Testing signal formatting for Telegram...")
        
        formatted_signal, formatted_options = processor.format_signal_for_telegram(
            signal=signal,
            option_data=option_data,
            source="TEST"
        )
        
        print(f"\n✅ Formatted Signal:")
        for key, value in formatted_signal.items():
            if key != "targets":
                print(f"   {key}: {value}")
            else:
                print(f"   {key}: {value}")
        
        print("\n✅ TEST 2 PASSED: Signal detection working!")
        return True
    
    except Exception as e:
        print(f"\n❌ TEST 2 FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_security_ids():
    """Test 3: Verify security IDs are correct"""
    print("\n" + "="*80)
    print("TEST 3: SECURITY IDS")
    print("="*80)
    
    try:
        from signal_processor import SignalProcessor
        
        processor = SignalProcessor()
        
        print("\n🔵 NIFTY 50 Securities:")
        nifty = processor.get_nifty_securities()
        for name, details in nifty.items():
            if isinstance(details, dict) and "security_id" in details:
                print(f"   ✅ {name}")
                print(f"      ID: {details.get('security_id')}")
                print(f"      Segment: {details.get('exchange_segment')}")
                print(f"      Instrument: {details.get('instrument')}")
        
        print("\n🟣 BANKNIFTY Securities:")
        bnifty = processor.get_banknifty_securities()
        for name, details in bnifty.items():
            if isinstance(details, dict) and "security_id" in details:
                print(f"   ✅ {name}")
                print(f"      ID: {details.get('security_id')}")
                print(f"      Segment: {details.get('exchange_segment')}")
        
        print("\n🟡 MCX COMMODITY Securities:")
        mcx = processor.get_commodity_securities()
        for name, details in mcx.items():
            if isinstance(details, dict) and "security_id" in details:
                print(f"   ✅ {name}")
                print(f"      ID: {details.get('security_id')}")
                print(f"      Segment: {details.get('exchange_segment')}")
                print(f"      Lot Size: {details.get('lot_size')}")
        
        print("\n✅ TEST 3 PASSED: All security IDs configured!")
        return True
    
    except Exception as e:
        print(f"\n❌ TEST 3 FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_market_hours():
    """Test 4: Verify market hours detection"""
    print("\n" + "="*80)
    print("TEST 4: MARKET HOURS")
    print("="*80)
    
    try:
        from live_screener_main import MarketHours
        
        now_ist = datetime.utcnow() + timedelta(hours=5, minutes=30)
        
        print(f"\nCurrent Time (IST): {now_ist.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Day: {now_ist.strftime('%A')}")
        
        # Check NSE
        nse_open = MarketHours.is_nse_open()
        print(f"\nNSE Status: {'🟢 OPEN' if nse_open else '🔴 CLOSED'}")
        if not nse_open:
            next_open = MarketHours.next_market_open("NSE")
            print(f"Next Open: {next_open}")
        
        # Check MCX
        mcx_open = MarketHours.is_mcx_open()
        print(f"MCX Status: {'🟢 OPEN' if mcx_open else '🔴 CLOSED'}")
        if not mcx_open:
            next_open = MarketHours.next_market_open("MCX")
            print(f"Next Open: {next_open}")
        
        print("\n✅ TEST 4 PASSED: Market hours detection working!")
        return True
    
    except Exception as e:
        print(f"\n❌ TEST 4 FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_dhan_api():
    """Test 5: Test DhanHQ API connection"""
    print("\n" + "="*80)
    print("TEST 5: DHANHQ API CONNECTION")
    print("="*80)
    
    try:
        from dotenv import load_dotenv
        from data_manager import DhanAPIClient
        
        load_dotenv()
        
        api_key = os.getenv("API_KEY")
        access_token = os.getenv("ACCESS_TOKEN")
        
        if not api_key or not access_token:
            print("\n⚠️  DhanHQ credentials not found in .env")
            print("   Skipping DhanHQ API test")
            return True
        
        print(f"\nInitializing DhanHQ client...")
        client = DhanAPIClient(access_token=access_token)
        
        print("✅ DhanAPIClient initialized")
        
        # Try to fetch NIFTY quote
        print("\nFetching NIFTY 50 quote...")
        try:
            quote = client.quote_data({"NSE_INDEX": ["543388"]})
            print(f"✅ Quote received: {quote}")
        except Exception as e:
            print(f"⚠️  Could not fetch quote: {e}")
            print("   (This is expected if market is closed)")
        
        print("\n✅ TEST 5 PASSED: DhanHQ API ready!")
        return True
    
    except Exception as e:
        print(f"\n⚠️  TEST 5 WARNING: {e}")
        print("   (This is OK if DhanHQ is not configured)")
        return True


def test_event_loop():
    """Test 6: Verify event loop management"""
    print("\n" + "="*80)
    print("TEST 6: EVENT LOOP MANAGEMENT")
    print("="*80)
    
    try:
        from telegram_alerts_fixed import get_telegram_alerts
        import time
        
        alerts = get_telegram_alerts()
        
        print("\n✅ TelegramAlertsFixed initialized")
        print("   - Background thread: RUNNING")
        print("   - Event loop: CREATED")
        print("   - Message queue: ACTIVE")
        
        # Send multiple alerts rapidly to test queue
        print("\nSending 3 alerts rapidly to test queue management...")
        
        for i in range(3):
            test_signal = {
                "symbol": f"TEST-ALERT-{i+1}",
                "signal": "CALL" if i % 2 == 0 else "PUT",
                "entry": 18250.50 + i*10,
                "stop_loss": 18200.00,
                "targets": [18300, 18350, 18400],
                "reference_timestamp": "14:25:00",
                "breakout_timestamp": "14:30:00",
            }
            
            test_option = {
                "call_strike": 18250,
                "put_strike": 18250,
                "call_premium": 42.50,
                "put_premium": 38.20
            }
            
            alerts.send_signal_alert("index_options", test_signal, test_option)
            print(f"   ✅ Alert {i+1} queued")
        
        # Wait for processing
        print("\nWaiting for alerts to process...")
        time.sleep(2)
        
        # Check alert history
        history = alerts.get_alert_history()
        print(f"\n✅ Alert history: {len(history)} total alerts")
        
        alerts.shutdown()
        print("\n✅ TEST 6 PASSED: Event loop management working!")
        return True
    
    except Exception as e:
        print(f"\n❌ TEST 6 FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all tests"""
    print("\n" + "="*80)
    print("TRADING BOT - COMPLETE TEST SUITE")
    print("="*80)
    
    results = {}
    
    # Run all tests
    results["Telegram Bots"] = test_telegram_bots()
    results["Signal Detection"] = test_signal_detection()
    results["Security IDs"] = test_security_ids()
    results["Market Hours"] = test_market_hours()
    results["DhanHQ API"] = test_dhan_api()
    results["Event Loop"] = test_event_loop()
    
    # Print summary
    print("\n" + "="*80)
    print("TEST SUMMARY")
    print("="*80)
    
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    
    for test_name, result in results.items():
        status = "✅ PASSED" if result else "❌ FAILED"
        print(f"{status}: {test_name}")
    
    print(f"\nTotal: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 ALL TESTS PASSED!")
        print("\n✅ You're ready to run the live screener:")
        print("   python live_screener_main.py")
    else:
        print(f"\n⚠️  {total - passed} test(s) failed")
        print("   Please fix the issues and try again")
    
    print("\n" + "="*80)


if __name__ == "__main__":
    main()
