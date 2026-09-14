import os
import tempfile
import unittest

from state_manager import StateManager


class StateManagerTests(unittest.TestCase):
    def test_state_persistence_and_recovery(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "state.json")
            manager = StateManager(path)
            manager.set_bot_status("running")
            manager.set_config("mode", "live")
            manager.start_session("session-1")
            manager.set_error("sample error")

            recovered = StateManager(path).recover()
            self.assertEqual(recovered["bot_status"], "running")
            self.assertEqual(recovered["configuration"]["mode"], "live")
            self.assertEqual(recovered["session"]["session_id"], "session-1")
            self.assertTrue(recovered["error_state"]["active"])

    def test_recover_with_corrupt_state_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "state.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{not-valid-json")

            manager = StateManager(path)
            recovered = manager.recover()
            self.assertEqual(recovered["bot_status"], "stopped")


if __name__ == "__main__":
    unittest.main()
