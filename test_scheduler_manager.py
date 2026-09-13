import unittest
from datetime import datetime
from unittest.mock import patch

from scheduler_manager import SchedulerManager


class SchedulerManagerTests(unittest.TestCase):
    def test_tick_starts_and_stops_by_window(self):
        started = []
        stopped = []
        scheduler = SchedulerManager(lambda: started.append(True), lambda: stopped.append(True))

        with patch("scheduler_manager.MarketCalendar.is_trading_day", return_value=True):
            scheduler.tick(datetime(2026, 9, 14, 9, 5))
            self.assertTrue(started)
            scheduler.tick(datetime(2026, 9, 14, 23, 45))
            self.assertTrue(stopped)

    def test_tick_skips_non_trading_day(self):
        started = []
        scheduler = SchedulerManager(lambda: started.append(True), lambda: None)

        with patch("scheduler_manager.MarketCalendar.is_trading_day", return_value=False):
            scheduler.tick(datetime(2026, 9, 13, 10, 0))

        self.assertFalse(started)


if __name__ == "__main__":
    unittest.main()
