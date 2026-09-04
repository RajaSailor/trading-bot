import unittest

from position_manager import PositionManager


class TestPositionManager(unittest.TestCase):
    def _signal(self, n):
        return {
            "position_id": f"P{n}",
            "symbol": "NIFTY",
            "timeframe": "5-MINUTE",
            "side": "CALL",
            "entry": 100,
            "stop_loss": 90,
            "target_1": 110,
            "target_2": 120,
            "target_3": 130,
            "breakout_time": f"T{n}",
        }

    def test_max_5_positions_then_confirmation(self):
        manager = PositionManager(max_positions=5)
        for i in range(5):
            result = manager.register_signal(self._signal(i))
            self.assertFalse(result["requires_confirmation"])

        sixth = manager.register_signal(self._signal(6))
        self.assertTrue(sixth["requires_confirmation"])
        self.assertIsNotNone(manager.pending_confirmation)
        self.assertTrue(manager.confirm_pending_position())
        self.assertEqual(manager.open_position_count(), 6)

    def test_stop_loss_event(self):
        manager = PositionManager(max_positions=5)
        manager.register_signal(self._signal(1))
        events = manager.update_price("NIFTY", "5-MINUTE", 89)
        self.assertTrue(any(e["type"] == "SL_MISSED" for e in events))

    def test_put_targets_track_downside(self):
        manager = PositionManager(max_positions=5)
        put_signal = self._signal(2)
        put_signal["side"] = "PUT"
        put_signal["target_1"] = 95
        put_signal["target_2"] = 90
        put_signal["target_3"] = 85
        put_signal["stop_loss"] = 110
        manager.register_signal(put_signal)
        events = manager.update_price("NIFTY", "5-MINUTE", 84)
        self.assertTrue(any(e["type"] == "TARGET_HIT" and e["target"] == "T3" for e in events))


if __name__ == "__main__":
    unittest.main()
