import unittest
import config
import bot_engine
from clob_trader import clob_trader


class StrategyV5TestCase(unittest.TestCase):
    def test_config_v5_defaults(self):
        cfg = config.DEFAULT_CONFIG
        self.assertGreaterEqual(cfg["performance_strategy_version"], 5)
        self.assertEqual(cfg["take_profit_pct"], 35.0)
        self.assertEqual(cfg["value_play_take_profit_pct"], 50.0)
        self.assertLessEqual(cfg["stop_loss_pct"], 30.0)
        self.assertLessEqual(cfg["catastrophic_stop_loss_pct"], 45.0)
        self.assertEqual(cfg["maturity_threshold"], 0.95)
        self.assertEqual(cfg["max_adverse_slippage_bps"], 200.0)
        self.assertEqual(cfg["min_whale_volume"], 25000.0)
        self.assertEqual(cfg["min_whale_roi"], 0.12)
        self.assertGreaterEqual(cfg["min_whale_win_rate"], 50.0)
        self.assertIn("ALL", cfg["leaderboard_timeframes"])
        self.assertIn("MONTH", cfg["leaderboard_timeframes"])
        self.assertNotIn("WEEK", cfg["leaderboard_timeframes"])

    def test_clob_trader_status(self):
        status = clob_trader.get_status()
        self.assertIn("live_configured", status)
        self.assertIn("mode", status)
        self.assertIn("host", status)

    def test_clob_simulated_order(self):
        res = clob_trader.place_order(
            token_id="test_token_123",
            side="BUY",
            price=0.50,
            size_usdc=100.0,
            order_type="LIMIT",
        )
        self.assertTrue(res["ok"])
        self.assertTrue(res["simulated"])
        self.assertEqual(res["shares"], 200.0)


if __name__ == "__main__":
    unittest.main()
