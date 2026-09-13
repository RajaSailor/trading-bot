import unittest
from datetime import datetime

from live_signal_detector import LiveSignalDetector


class LiveSignalDetectorTests(unittest.TestCase):
    def test_should_emit_blocks_duplicates(self):
        detector = LiveSignalDetector()

        first = detector.should_emit("k1", "t1", "t2")
        second = detector.should_emit("k1", "t1", "t2")

        self.assertTrue(first)
        self.assertFalse(second)

    def test_should_emit_rejects_old_iso_timestamp(self):
        detector = LiveSignalDetector(freshness_minutes=5)
        now = datetime(2026, 9, 13, 10, 0, 0)

        emitted = detector.should_emit("k2", "2026-09-13T09:00:00", "2026-09-13T09:00:00", now=now)

        self.assertFalse(emitted)


if __name__ == "__main__":
    unittest.main()
