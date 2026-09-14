import unittest

from order_executor import Order, OrderExecutor, OrderStatus


class OrderExecutorTests(unittest.TestCase):
    def test_reject_invalid_order(self):
        executor = OrderExecutor()
        order = Order(symbol="NIFTY", side="BUY", quantity=0, price=10)
        result = executor.execute_order(order)
        self.assertEqual(result.status, OrderStatus.REJECTED)

    def test_partial_fill_lifecycle(self):
        executor = OrderExecutor()
        order = Order(symbol="NIFTY", side="BUY", quantity=10, price=100)
        result = executor.execute_order(order, available_liquidity=4)
        self.assertEqual(result.status, OrderStatus.PARTIALLY_FILLED)
        self.assertEqual(result.filled_quantity, 4)

        updated = executor.handle_partial_fill(order.order_id, additional_fill=6)
        self.assertEqual(updated.status, OrderStatus.FILLED)
        self.assertEqual(updated.filled_quantity, 10)


if __name__ == "__main__":
    unittest.main()
