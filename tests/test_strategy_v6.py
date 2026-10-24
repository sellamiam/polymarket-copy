import unittest
import config
import bot_engine


class StrategyV6TestCase(unittest.TestCase):
    def test_config_v6_defaults(self):
        cfg = config.DEFAULT_CONFIG
        self.assertGreaterEqual(cfg["performance_strategy_version"], 6)
        self.assertTrue(cfg["exclude_weather_bets"])
        self.assertTrue(cfg["exclude_entertainment_bets"])
        self.assertEqual(cfg["max_market_exposure"], 350.0)
        self.assertEqual(cfg["risk_per_trade_pct"], 2.5)
        self.assertEqual(cfg["core_max_copy_price"], 0.78)
        self.assertEqual(cfg["consensus_max_copy_price"], 0.88)
        self.assertEqual(cfg["max_open_positions_per_whale"], 2)
        self.assertEqual(cfg["break_even_ratchet_pct"], 20.0)
        self.assertEqual(cfg["catastrophic_stop_loss_pct"], 35.0)
        self.assertEqual(cfg["min_whale_copy_pnl"], -100.0)
        self.assertEqual(cfg["stagnant_holding_hours"], 72.0)
        self.assertEqual(cfg["stagnant_drawdown_pct"], 10.0)
        self.assertEqual(cfg["trailing_profit_lock_pct"], 10.0)

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
        # Tweet count lotteries
        self.assertTrue(
            bot_engine.is_entertainment_noise_market(
                "Will Elon Musk post 65-89 tweets from Oct 20 to Oct 27?", "elon-musk-tweets-oct"
            )
        )
        # Music rankings & Billboard Hot 100
        self.assertTrue(
            bot_engine.is_entertainment_noise_market(
                "Will Drake have #1 song this week?", "drake-billboard-hot-100"
            )
        )
        # Software release date guessing lotteries
        self.assertTrue(
            bot_engine.is_entertainment_noise_market(
                "Will Claude 3.5 Sonnet released between Nov 1 and Nov 7?", "claude-release-dates"
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

    def test_weather_and_hurricane_filter(self):
        self.assertTrue(bot_engine.is_weather_market("Will Vance peak at Category 4?", "hurricane-vance-category-4"))
        self.assertTrue(bot_engine.is_weather_market("Will Tropical Storm Ernesto make landfall?", "tropical-storm-ernesto"))
        self.assertTrue(bot_engine.is_weather_market("Will highest temperature in Hong Kong be 30°C?", "hk-temp-oct-24"))
        self.assertFalse(bot_engine.is_weather_market("Will Bitcoin hit $85,000 in October?", "btc-85k-october"))

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

    def test_trailing_profit_lock_and_stagnant_exits(self):
        import time
        now = int(time.time())
        state = {
            "cash_usdc": 1000.0,
            "positions": {
                # Peak +25%, currently +8% -> Should trigger TRAILING PROFIT LOCK
                "tok_winner_pullback": {
                    "market_title": "US x Iran ceasefire continues through November 15",
                    "market_slug": "iran-ceasefire",
                    "outcome": "No",
                    "avg_price": 0.40,
                    "current_price": 0.432,
                    "bid_price": 0.432,  # +8.0% PnL
                    "peak_pnl_pct": 25.0,  # Peak was +25%
                    "quantity": 100.0,
                    "invested_usdc": 40.0,
                    "strategy_exits_enabled": True,
                    "opened_at": now - 3600 * 20,
                    "trader_address": "0x60a92c8620846d81f5ea17b0564e0d4b7c545a71",
                },
                # Held 90h, down -12% -> Should trigger STAGNANT RECYCLE
                "tok_stagnant_bleeder": {
                    "market_title": "Will Chiang Wan-an win the next Taipei Mayor election?",
                    "market_slug": "taipei-mayor-chiang",
                    "outcome": "No",
                    "avg_price": 0.50,
                    "current_price": 0.44,
                    "bid_price": 0.44,  # -12.0% PnL
                    "peak_pnl_pct": 0.0,
                    "quantity": 200.0,
                    "invested_usdc": 100.0,
                    "strategy_exits_enabled": True,
                    "opened_at": now - 3600 * 90,  # 90 hours old
                    "trader_address": "0xfbedb045813457be883ad83dd1078e88a2a1a665",
                },
                # Healthy winner, up +22%, peak +22% -> Should HOLD (neither TP 35% nor pullback <= 10%)
                "tok_healthy_winner": {
                    "market_title": "Will Bitcoin reach $85,000 in October?",
                    "market_slug": "btc-85k-october",
                    "outcome": "No",
                    "avg_price": 0.50,
                    "current_price": 0.61,
                    "bid_price": 0.61,  # +22.0% PnL
                    "peak_pnl_pct": 22.0,
                    "quantity": 100.0,
                    "invested_usdc": 50.0,
                    "strategy_exits_enabled": True,
                    "opened_at": now - 3600 * 20,
                    "trader_address": "0xa19cbababc312f9df185e49d7004c249ed1ade6b",
                },
            },
            "trades": [],
            "logs": [],
        }
        cfg = dict(config.DEFAULT_CONFIG)
        from unittest.mock import patch
        from fills import FillResult

        def fake_simulate_sell_qty(token_id, qty, **kwargs):
            pos = state["positions"].get(token_id, {})
            price = pos.get("bid_price", pos.get("avg_price", 0.5))
            return FillResult(
                ok=True,
                side="SELL",
                requested_qty=qty,
                filled_qty=qty,
                avg_price=price,
                worst_price=price,
                notional=qty * price,
                fee_usdc=0.0,
                levels_consumed=1,
                partial=False,
            )

        with patch("fills.simulate_sell_qty", side_effect=fake_simulate_sell_qty):
            bot_engine.recycle_positions_and_exit_strategies(config=cfg, state=state)

        # Winner pullback exited to protect profit
        self.assertNotIn("tok_winner_pullback", state["positions"])
        # Stagnant bleeder exited to recycle capital
        self.assertNotIn("tok_stagnant_bleeder", state["positions"])
        # Healthy winner preserved
        self.assertIn("tok_healthy_winner", state["positions"])

        # Check recorded trade exit reasons
        exit_reasons = [t["trader_name"] for t in state["trades"]]
        self.assertTrue(any("TRAILING PROFIT LOCK" in r for r in exit_reasons))
        self.assertTrue(any("STAGNANT RECYCLE" in r for r in exit_reasons))


if __name__ == "__main__":
    unittest.main()


