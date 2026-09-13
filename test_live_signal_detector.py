import unittest
from datetime import UTC, datetime

from live_signal_detector import LiveSignalDetector


class LiveSignalDetectorTests(unittest.TestCase):
    def test_should_emit_blocks_duplicates(self):
        detector = LiveSignalDetector()

        first = detector.should_emit("k1", "2026-09-13T10:00:00", "2026-09-13T10:00:00", now=datetime(2026, 9, 13, 10, 1, 0))
        second = detector.should_emit("k1", "2026-09-13T10:00:00", "2026-09-13T10:00:00", now=datetime(2026, 9, 13, 10, 2, 0))

        self.assertTrue(first)
        self.assertFalse(second)

    def test_should_emit_rejects_old_iso_timestamp(self):
        detector = LiveSignalDetector(freshness_minutes=5)
        now = datetime(2026, 9, 13, 10, 0, 0)

        emitted = detector.should_emit("k2", "2026-09-13T09:00:00", "2026-09-13T09:00:00", now=now)

        self.assertFalse(emitted)

    def test_should_emit_handles_aware_now_with_iso_timestamp(self):
        detector = LiveSignalDetector(freshness_minutes=5)
        now = datetime(2026, 9, 13, 10, 0, 0, tzinfo=UTC)

        emitted = detector.should_emit("k3", "2026-09-13T09:58:00", "2026-09-13T09:58:00", now=now)

        self.assertTrue(emitted)


if __name__ == "__main__":
    unittest.main()
