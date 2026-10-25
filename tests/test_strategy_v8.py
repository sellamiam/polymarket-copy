import unittest
from unittest.mock import patch
import config
import bot_engine
from fills import FillResult


class StrategyV8TestCase(unittest.TestCase):
    def test_config_v8_defaults(self):
        cfg = config.DEFAULT_CONFIG
        self.assertGreaterEqual(cfg["performance_strategy_version"], 8)
        self.assertEqual(cfg["stop_loss_pct"], 25.0)
        self.assertEqual(cfg["stop_loss_grace_hours"], 0.0)
        self.assertEqual(cfg["min_whale_copy_pnl"], -35.0)
        self.assertEqual(cfg["whale_prune_min_trades"], 2)
        self.assertTrue(cfg["enable_no_outcome_edge"])
        self.assertEqual(cfg["max_yes_copy_price"], 0.65)
        self.assertEqual(cfg["elite_whale_sizing_multiplier"], 1.75)
        self.assertEqual(cfg["unproven_whale_sizing_multiplier"], 0.60)
        self.assertIn("0x6276ea490d6ca3a539f1d571a89d53d26e9f9db5", cfg["blacklisted_whales"])
        self.assertIn("0x5a218c7ad04135830a45c41aaed7294df7809318", cfg["blacklisted_whales"])
        self.assertIn("0x1387d145aaf01f6e33b66525dda6e1f51f6955f8", cfg["blacklisted_whales"])

    def test_v8_dynamic_sizing_outcomes_and_alpha(self):
        trader_proven = {
            "address": "0xalpha123",
            "name": "Tokor",
            "pnl": 50000.0,
            "vol": 100000.0,
            "rank": 5,
        }
        mock_state = {
            "trades": [
                {
                    "type": "SELL",
                    "original_trader_address": "0xalpha123",
                    "realized_pnl": 50.0,
                },
                {
                    "type": "SELL",
                    "original_trader_address": "0xalpha123",
                    "realized_pnl": 55.0,
                },
            ],
            "positions": {},
        }

        # Proven whale + "No" outcome should get compound scaling
        mult_no = bot_engine.calculate_dynamic_sizing_multiplier(
            trader_proven, whale_trade_usdc=1000.0, outcome="No", state=mock_state
        )
        self.assertGreaterEqual(mult_no, 1.8)

        # Unproven whale should get probationary sizing (0.6x)
        trader_unproven = {
            "address": "0xnewbie",
            "name": "NewWhale",
            "pnl": 5000.0,
            "vol": 20000.0,
            "rank": 40,
        }
        mult_unproven = bot_engine.calculate_dynamic_sizing_multiplier(
            trader_unproven, whale_trade_usdc=200.0, outcome="Yes", state=mock_state
        )
        self.assertLess(mult_unproven, 0.70)

    @patch("fills.simulate_sell_qty")
    def test_prune_v8_toxic_whales(self, mock_sell):
        mock_sell.return_value = FillResult(
            ok=True,
            side="SELL",
            requested_qty=200.0,
            filled_qty=200.0,
            avg_price=0.50,
            worst_price=0.50,
            notional=100.0,
            fee_usdc=0.0,
            levels_consumed=1,
            partial=False,
            rejected_reason=None,
        )

        cfg = dict(config.DEFAULT_CONFIG)
        state = {
            "cash_usdc": 1000.0,
            "positions": {
                "toxic_token_1": {
                    "market_title": "Will Anthropic IPO in 2026?",
                    "market_slug": "anthropic-ipo-2026",
                    "outcome": "Yes",
                    "quantity": 200.0,
                    "avg_price": 0.50,
                    "invested_usdc": 100.0,
                    "trader_address": "0x6276ea490d6ca3a539f1d571a89d53d26e9f9db5", # VerilyWinningly
                    "source_whales": {"0x6276ea490d6ca3a539f1d571a89d53d26e9f9db5": 200.0},
                },
                "safe_token_2": {
                    "market_title": "Will Candidate Win election?",
                    "market_slug": "candidate-win",
                    "outcome": "No",
                    "quantity": 100.0,
                    "avg_price": 0.40,
                    "invested_usdc": 40.0,
                    "trader_address": "0xgoodwhale",
                    "source_whales": {"0xgoodwhale": 100.0},
                },
            },
            "trades": [],
            "cooldown_markets": {},
        }

        pruned = bot_engine.prune_v6_violations(state=state, config=cfg)
        self.assertEqual(len(pruned), 1)
        self.assertEqual(pruned[0]["market_slug"], "anthropic-ipo-2026")
        self.assertNotIn("toxic_token_1", state["positions"])
        self.assertIn("safe_token_2", state["positions"])
        # Verify market cooldown is set
        self.assertIn("anthropic-ipo-2026", state["cooldown_markets"])


if __name__ == "__main__":
    unittest.main()
