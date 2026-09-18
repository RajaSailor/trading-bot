import tempfile
import threading
import unittest

from database import TradingDatabase


class TradingDatabaseThreadSafetyTests(unittest.TestCase):
    def test_fetch_latest_positions_is_safe_across_threads(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = TradingDatabase(f"{tmpdir}/trading.db")
            for index in range(5):
                db.snapshot_position(
                    {
                        "symbol": f"SYM{index}",
                        "quantity": index + 1,
                        "average_price": 100 + index,
                    }
                )

            errors = []

            def _reader():
                try:
                    rows = db.fetch_latest_positions()
                    self.assertEqual(5, len(rows))
                except Exception as exc:  # pragma: no cover - failure capture
                    errors.append(exc)

            threads = [threading.Thread(target=_reader) for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            self.assertEqual([], errors)
