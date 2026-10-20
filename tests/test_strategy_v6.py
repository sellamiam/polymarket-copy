import unittest
import config
import bot_engine


class StrategyV6TestCase(unittest.TestCase):
    def test_config_v6_defaults(self):
        cfg = config.DEFAULT_CONFIG
        self.assertEqual(cfg["performance_strategy_version"], 6)
        self.assertTrue(cfg["exclude_weather_bets"])
        self.assertTrue(cfg["exclude_entertainment_bets"])
        self.assertEqual(cfg["max_market_exposure"], 250.0)
        self.assertEqual(cfg["risk_per_trade_pct"], 2.0)
        self.assertEqual(cfg["core_max_copy_price"], 0.78)
        self.assertEqual(cfg["consensus_max_copy_price"], 0.88)
        self.assertEqual(cfg["max_open_positions_per_whale"], 2)
        self.assertEqual(cfg["break_even_ratchet_pct"], 20.0)
        self.assertEqual(cfg["catastrophic_stop_loss_pct"], 35.0)

    def test_entertainment_noise_filter(self):
        self.assertTrue(
            bot_engine.is_entertainment_noise_market(
                "Will GTA VI Trailer get 50 million views on week 1?", "gta-trailer-views"
            )
        )
        self.assertTrue(
            bot_engine.is_entertainment_noise_market(
                "Highest box office opening weekend", "box-office-opening"
            )
        )
        self.assertFalse(
            bot_engine.is_entertainment_noise_market(
                "Will Lula win the second round of Brazil election?", "lula-brazil-election"
            )
        )
        self.assertFalse(
            bot_engine.is_entertainment_noise_market(
                "Will Likud win 25-29 seats in Knesset?", "likud-seats-israel"
            )
        )


if __name__ == "__main__":
    unittest.main()
