import unittest

from trailing_stop_manager import TrailingStopManager


class TrailingStopManagerTests(unittest.TestCase):
    def test_moves_stop_to_c2c_and_trails_every_five_points(self):
        manager = TrailingStopManager()
        state = manager.initialize("CALL", entry_price=145.5, stop_loss=135.5, targets=[165.5, 185.5, 205.5])
        manager.on_entry_filled(state, 145.5)

        snapshot = manager.evaluate(state, 156)

        self.assertEqual(150.5, snapshot["current_stop_loss"])
        self.assertEqual(160.5, snapshot["next_trigger_price"])
        self.assertTrue(snapshot["c2c_moved"])

    def test_exits_on_stop_loss_breach(self):
        manager = TrailingStopManager()
        state = manager.initialize("CALL", entry_price=100, stop_loss=90, targets=[120, 140, 160])
        manager.on_entry_filled(state, 100)

        snapshot = manager.evaluate(state, 89)

        self.assertTrue(snapshot["exited"])
        self.assertEqual("STOP_LOSS_HIT", snapshot["exit_reason"])

    def test_put_side_trails_down_and_hits_target(self):
        manager = TrailingStopManager()
        state = manager.initialize("PUT", entry_price=100, stop_loss=110, targets=[80, 60, 40])
        manager.on_entry_filled(state, 100)

        snapshot = manager.evaluate(state, 79)

        self.assertEqual(85.0, snapshot["current_stop_loss"])
        self.assertTrue(snapshot["exited"])
        self.assertEqual("TARGET_HIT", snapshot["exit_reason"])

    def test_keeps_initial_stop_until_first_favorable_move(self):
        manager = TrailingStopManager()
        state = manager.initialize("CALL", entry_price=200, stop_loss=190, targets=[220, 240, 260])
        manager.on_entry_filled(state, 200)

        snapshot = manager.evaluate(state, 202)

        self.assertEqual(190.0, snapshot["current_stop_loss"])
        self.assertFalse(snapshot["c2c_moved"])


if __name__ == "__main__":
    unittest.main()
