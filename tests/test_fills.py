"""Unit tests for depth-aware fill simulation."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fills import BookLevel, walk_book, simulate_buy_usdc, extract_asks, extract_bids


class TestWalkBook(unittest.TestCase):
    def test_buy_walks_asks_ascending(self):
        asks = [
            BookLevel(0.40, 100),
            BookLevel(0.42, 50),
            BookLevel(0.50, 200),
        ]
        r = walk_book(asks, 120, side="BUY", fee_bps=0, slippage_bps=0, tick_size=0.01)
        self.assertTrue(r.ok)
        self.assertAlmostEqual(r.filled_qty, 120.0)
        # 100 @ 0.40 + 20 @ 0.42
        expected_avg = (100 * 0.40 + 20 * 0.42) / 120
        self.assertAlmostEqual(r.avg_price, expected_avg, places=5)
        self.assertEqual(r.levels_consumed, 2)

    def test_sell_walks_bids_descending(self):
        bids = [
            BookLevel(0.55, 10),
            BookLevel(0.50, 100),
        ]
        r = walk_book(bids, 30, side="SELL", fee_bps=0, slippage_bps=0, tick_size=0.01)
        self.assertTrue(r.ok)
        self.assertAlmostEqual(r.filled_qty, 30.0)
        expected_avg = (10 * 0.55 + 20 * 0.50) / 30
        self.assertAlmostEqual(r.avg_price, expected_avg, places=5)

    def test_insufficient_depth_partial(self):
        asks = [BookLevel(0.50, 10)]
        r = walk_book(asks, 100, side="BUY", allow_partial=True, slippage_bps=0, tick_size=0.01)
        self.assertTrue(r.ok)
        self.assertTrue(r.partial)
        self.assertAlmostEqual(r.filled_qty, 10.0)

    def test_insufficient_depth_no_partial(self):
        asks = [BookLevel(0.50, 10)]
        r = walk_book(asks, 100, side="BUY", allow_partial=False, slippage_bps=0, tick_size=0.01)
        self.assertFalse(r.ok)
        self.assertEqual(r.rejected_reason, "insufficient_depth_no_partial")

    def test_empty_book(self):
        r = walk_book([], 10, side="BUY")
        self.assertFalse(r.ok)
        self.assertEqual(r.rejected_reason, "empty_book")

    def test_slippage_worsens_buy(self):
        asks = [BookLevel(0.50, 100)]
        r0 = walk_book(asks, 10, side="BUY", slippage_bps=0, tick_size=0.01)
        r1 = walk_book(asks, 10, side="BUY", slippage_bps=100, tick_size=0.01)  # 1%
        self.assertGreater(r1.avg_price, r0.avg_price)

    def test_buy_usdc_budget_walk(self):
        book = {
            "asks": [{"price": "0.50", "size": "100"}, {"price": "0.60", "size": "100"}],
            "bids": [{"price": "0.49", "size": "100"}],
        }
        r = simulate_buy_usdc("tok", 50.0, book=book, slippage_bps=0, fee_bps=0, tick_size=0.01, min_order_size=1)
        self.assertTrue(r.ok)
        # ~100 shares at 0.50 would be $50
        self.assertAlmostEqual(r.filled_qty, 100.0, places=2)
        self.assertAlmostEqual(r.avg_price, 0.50, places=4)

    def test_book_unavailable_no_whale_fallback(self):
        # Verify the no-fallback behaviour without making a real network
        # request during the test suite.
        r2 = simulate_buy_usdc("tok", 100.0, book={"asks": [], "bids": []})
        self.assertFalse(r2.ok)
        self.assertIn(r2.rejected_reason, ("empty_asks", "book_unavailable"))

    def test_parse_levels(self):
        book = {
            "asks": [{"price": "0.7", "size": "5"}, {"price": "0.6", "size": "5"}],
            "bids": [{"price": "0.4", "size": "5"}, {"price": "0.5", "size": "5"}],
        }
        asks = extract_asks(book)
        bids = extract_bids(book)
        self.assertEqual(asks[0].price, 0.6)  # lowest ask first
        self.assertEqual(bids[0].price, 0.5)  # highest bid first


if __name__ == "__main__":
    unittest.main()
