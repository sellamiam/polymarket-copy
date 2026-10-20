import unittest
import config
import bot_engine


class StrategyV6TestCase(unittest.TestCase):
    def test_config_v6_defaults(self):
        cfg = config.DEFAULT_CONFIG
        self.assertEqual(cfg["performance_strategy_version"], 6)
        self.assertTrue(cfg["exclude_weather_bets"])
        self.assertTrue(cfg["exclude_entertainment_bets"])
        self.assertEqual(cfg["max_market_exposure"], 350.0)
        self.assertEqual(cfg["risk_per_trade_pct"], 2.5)
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

    def test_prune_v6_violations(self):
        state = {
            "cash_usdc": 1000.0,
            "positions": {
                "tok_weather": {
                    "market_title": "Will highest temperature in Paris be 15C on Oct 20?",
                    "market_slug": "paris-temperature-oct-20",
                    "outcome": "No",
                    "avg_price": 0.69,
                    "current_price": 0.69,
                    "quantity": 100.0,
                    "invested_usdc": 69.0,
                    "trader_address": "0x4f1d5ae26fc31472966e951af3183308736d8de2",
                    "source_whales": {"0x4f1d5ae26fc31472966e951af3183308736d8de2": {}},
                },
                "tok_high_price": {
                    "market_title": "Will Candidate X win Senate seat?",
                    "market_slug": "senate-candidate-x",
                    "outcome": "Yes",
                    "avg_price": 0.84,
                    "current_price": 0.84,
                    "quantity": 100.0,
                    "invested_usdc": 84.0,
                    "trader_address": "0x1111111111111111111111111111111111111111",
                    "source_whales": {"0x1111111111111111111111111111111111111111": {}},
                },
                "tok_value_play": {
                    "market_title": "Will Alphabet be third largest company?",
                    "market_slug": "alphabet-third-largest",
                    "outcome": "No",
                    "avg_price": 0.21,
                    "current_price": 0.21,
                    "quantity": 400.0,
                    "invested_usdc": 84.0,
                    "trader_address": "0x2222222222222222222222222222222222222222",
                    "source_whales": {"0x2222222222222222222222222222222222222222": {}},
                },
            },
            "trades": [],
        }
        cfg = dict(config.DEFAULT_CONFIG)
        pruned = bot_engine.prune_v6_violations(state=state, config=cfg)
        self.assertEqual(len(pruned), 2)
        # Weather and high-price positions pruned
        self.assertNotIn("tok_weather", state["positions"])
        self.assertNotIn("tok_high_price", state["positions"])
        # +EV Value play preserved!
        self.assertIn("tok_value_play", state["positions"])
        # Cash credited
        self.assertGreater(state["cash_usdc"], 1000.0)


if __name__ == "__main__":
    unittest.main()

