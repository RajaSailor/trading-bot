import os
import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from exchange_calendar import (
    BSE,
    IST,
    MCX,
    NSE,
    exchange_for_instrument,
    is_exchange_open,
    is_trading_day,
    next_session_start,
    open_exchanges,
    previous_trading_day,
    seconds_until_next_open,
)


def _ist(*args):
    return datetime(*args, tzinfo=IST)


class ExchangeCalendarTests(unittest.TestCase):
    def test_segment_mapping(self):
        self.assertEqual(NSE, exchange_for_instrument(SimpleNamespace(symbol="NIFTY", category="index_options")))
        self.assertEqual(NSE, exchange_for_instrument(SimpleNamespace(symbol="BANKNIFTY", category="index_options")))
        self.assertEqual(BSE, exchange_for_instrument(SimpleNamespace(symbol="SENSEX", category="index_options")))
        self.assertEqual(NSE, exchange_for_instrument(SimpleNamespace(symbol="SBIN", category="nifty50_stock_options")))
        self.assertEqual(MCX, exchange_for_instrument(SimpleNamespace(symbol="GOLD", category="commodity_options")))

    def test_weekends_are_closed_for_every_exchange(self):
        self.assertEqual(set(), open_exchanges(_ist(2026, 10, 3, 11, 0)))  # Saturday
        self.assertEqual(set(), open_exchanges(_ist(2026, 10, 4, 20, 0)))  # Sunday

    def test_full_holiday_closes_nse_bse_and_mcx(self):
        self.assertEqual(set(), open_exchanges(_ist(2026, 10, 2, 11, 0)))   # Gandhi Jayanti
        self.assertEqual(set(), open_exchanges(_ist(2026, 10, 2, 19, 0)))

    def test_nse_holiday_with_mcx_evening_session(self):
        self.assertEqual(set(), open_exchanges(_ist(2026, 10, 20, 11, 0)))       # Dussehra morning
        self.assertEqual({MCX}, open_exchanges(_ist(2026, 10, 20, 18, 0)))       # MCX evening open

    def test_regular_sessions(self):
        self.assertEqual(set(), open_exchanges(_ist(2026, 10, 5, 8, 59)))
        self.assertEqual({MCX}, open_exchanges(_ist(2026, 10, 5, 9, 5)))
        self.assertEqual({NSE, BSE, MCX}, open_exchanges(_ist(2026, 10, 5, 9, 15)))
        self.assertEqual({NSE, BSE, MCX}, open_exchanges(_ist(2026, 10, 5, 15, 30)))
        self.assertEqual({MCX}, open_exchanges(_ist(2026, 10, 5, 15, 31)))
        self.assertTrue(is_exchange_open(MCX, _ist(2026, 10, 5, 23, 29)))
        self.assertFalse(is_exchange_open(MCX, _ist(2026, 10, 5, 23, 31)))  # US DST -> 23:30 close

    def test_mcx_extends_to_2355_when_us_on_standard_time(self):
        self.assertTrue(is_exchange_open(MCX, _ist(2026, 11, 16, 23, 50)))
        self.assertFalse(is_exchange_open(MCX, _ist(2026, 11, 16, 23, 56)))

    def test_utc_inputs_are_converted_to_ist(self):
        self.assertTrue(is_exchange_open(NSE, datetime(2026, 10, 5, 4, 0, tzinfo=timezone.utc)))

    def test_previous_trading_day_skips_weekend_and_holidays(self):
        self.assertEqual(date(2026, 10, 1), previous_trading_day(NSE, date(2026, 10, 5)))   # skips Fri holiday + weekend
        self.assertEqual(date(2026, 10, 5), previous_trading_day(NSE, date(2026, 10, 6)))

    def test_next_session_and_idle_wait(self):
        saturday = _ist(2026, 10, 3, 12, 0)
        self.assertEqual(_ist(2026, 10, 5, 9, 0), next_session_start(MCX, saturday))
        self.assertEqual(_ist(2026, 10, 5, 9, 15), next_session_start(NSE, saturday))
        self.assertEqual((_ist(2026, 10, 5, 9, 0) - saturday).total_seconds(), seconds_until_next_open(saturday))
        self.assertEqual(0.0, seconds_until_next_open(_ist(2026, 10, 5, 10, 0)))

    def test_extra_holidays_from_env(self):
        with patch.dict(os.environ, {"EXCHANGE_EXTRA_HOLIDAYS": "NSE:2026-10-06, ALL:2026-10-07"}):
            self.assertFalse(is_trading_day(NSE, date(2026, 10, 6)))
            self.assertTrue(is_trading_day(MCX, date(2026, 10, 6)))
            self.assertFalse(is_trading_day(MCX, date(2026, 10, 7)))
            self.assertFalse(is_trading_day(BSE, date(2026, 10, 7)))


if __name__ == "__main__":
    unittest.main()
