import unittest

from signal_processor import SignalQueueProcessor


class SignalProcessingPipelineTests(unittest.TestCase):
    def test_parse_validate_and_process_queue(self):
        processor = SignalQueueProcessor()
        signal = processor.parse_webhook_signal({"symbol": "NIFTY", "action": "buy", "strategy": "breakout"})
        self.assertIsNotNone(signal)
        self.assertTrue(processor.enqueue_signal(signal))
        self.assertEqual(processor.queue_size(), 1)

        processed = processor.process_queue()
        self.assertEqual(len(processed), 1)
        self.assertEqual(processed[0]["action"], "BUY")
        self.assertIn("processed_at", processed[0])
        self.assertNotIn("processed_at", signal)

    def test_invalid_signal_rejected(self):
        processor = SignalQueueProcessor()
        signal = processor.parse_webhook_signal({"symbol": "NIFTY", "action": "hold"})
        self.assertIsNone(signal)

    def test_queue_preserves_enqueued_snapshot(self):
        processor = SignalQueueProcessor()
        signal = processor.parse_webhook_signal({"symbol": "NIFTY", "action": "buy"})
        self.assertTrue(processor.enqueue_signal(signal))

        signal["action"] = "SELL"
        processed = processor.process_next_signal()
        self.assertEqual(processed["action"], "BUY")


if __name__ == "__main__":
    unittest.main()
