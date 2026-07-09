import sys
import os
import time
import unittest
from unittest.mock import patch

sys.path.append(os.path.abspath('.'))

import bot_engine
import config

class TestBotExits(unittest.TestCase):
    def setUp(self):
        self.mock_config = {
            "take_profit_pct": 15.0,
            "value_play_take_profit_pct": 40.0,
            "stop_loss_pct": 15.0,
            "catastrophic_stop_loss_pct": 35.0,
            "stop_loss_grace_hours": 4.0,
            "max_holding_hours": 24,
            "maturity_threshold": 0.98,
            "grandfather_open_positions": False
        }
        
    @patch('bot_engine.fetch_clob_midpoint')
    @patch('bot_engine.fetch_clob_price')
    def test_update_live_valuations(self, mock_fetch_price, mock_fetch_midpoint):
        # Setup: midpoint = 0.55, bid = 0.50
        mock_fetch_midpoint.return_value = 0.55
        mock_fetch_price.return_value = 0.50
        
        state = {
            "positions": {
                "token_1": {
                    "avg_price": 0.40,
                    "quantity": 100,
                    "current_price": 0.40
                }
            }
        }
        
        bot_engine.update_live_valuations(state)
        
        pos = state["positions"]["token_1"]
        self.assertEqual(pos["current_price"], 0.55)
        self.assertEqual(pos["bid_price"], 0.50)

    @patch('bot_engine.fetch_clob_price')
    def test_recycle_exit_tp_triggered_on_bid_price(self, mock_fetch_price):
        # If we entered at 0.60, and bid is 0.70 (+16.67% TP triggered)
        mock_fetch_price.return_value = 0.70
        now = int(time.time())
        
        state = {
            "cash_usdc": 1000.0,
            "positions": {
                "token_1": {
                    "avg_price": 0.60,
                    "quantity": 100,
                    "invested_usdc": 60.0,
                    "current_price": 0.70,
                    "bid_price": 0.70,
                    "opened_at": now - 3600, # 1 hour ago (not old enough for time limit)
                    "market_title": "Test Market",
                    "market_slug": "test-market",
                    "outcome": "Yes"
                }
            },
            "trades": [],
            "logs": []
        }
        
        # Test TP exit
        cfg = self.mock_config.copy()
        cfg["take_profit_pct"] = 15.0
        
        recycled = bot_engine.recycle_positions_and_exit_strategies(cfg, state)
        self.assertTrue(recycled)
        self.assertNotIn("token_1", state["positions"])
        self.assertAlmostEqual(state["cash_usdc"], 1070.0) # 1000 + 100 * 0.70
        self.assertAlmostEqual(state["trades"][0]["realized_pnl"], 10.0, places=4) # 70 - 60 = +10
        self.assertTrue("TP" in state["trades"][0]["trader_name"])

    @patch('bot_engine.fetch_clob_price')
    def test_recycle_exit_tp_ignored_on_stale_midpoint(self, mock_fetch_price):
        # Setup: midpoint is 0.75 (+25%), but actual bid is 0.55 (-8.3%).
        # Standard TP should NOT trigger because the bid is below TP threshold.
        mock_fetch_price.return_value = 0.55
        now = int(time.time())
        
        state = {
            "cash_usdc": 1000.0,
            "positions": {
                "token_1": {
                    "avg_price": 0.60,
                    "quantity": 100,
                    "invested_usdc": 60.0,
                    "current_price": 0.75, # Midpoint is +25%
                    "bid_price": 0.55,     # Bid is -8.3%
                    "opened_at": now - 3600, # 1 hour ago
                    "market_title": "Test Market",
                    "market_slug": "test-market",
                    "outcome": "Yes"
                }
            },
            "trades": [],
            "logs": []
        }
        
        cfg = self.mock_config.copy()
        cfg["take_profit_pct"] = 15.0
        
        recycled = bot_engine.recycle_positions_and_exit_strategies(cfg, state)
        self.assertFalse(recycled)
        self.assertIn("token_1", state["positions"]) # Still open!

    @patch('bot_engine.fetch_clob_price')
    def test_catastrophic_stop_loss(self, mock_fetch_price):
        # We entered at 0.80, bid is 0.40 (-50%).
        # Age is only 1 hour (less than stop_loss_grace_hours = 4.0).
        # Standard stop loss shouldn't trigger, but catastrophic SL (-35%) should trigger immediately!
        mock_fetch_price.return_value = 0.40
        now = int(time.time())
        
        state = {
            "cash_usdc": 1000.0,
            "positions": {
                "token_1": {
                    "avg_price": 0.80,
                    "quantity": 100,
                    "invested_usdc": 80.0,
                    "current_price": 0.40,
                    "bid_price": 0.40,
                    "opened_at": now - 3600, # 1 hour ago
                    "market_title": "Test Market",
                    "market_slug": "test-market",
                    "outcome": "Yes"
                }
            },
            "trades": [],
            "logs": []
        }
        
        cfg = self.mock_config.copy()
        
        # Run exits
        recycled = bot_engine.recycle_positions_and_exit_strategies(cfg, state)
        self.assertTrue(recycled)
        self.assertNotIn("token_1", state["positions"])
        self.assertAlmostEqual(state["cash_usdc"], 1040.0) # sold at 0.40
        self.assertAlmostEqual(state["trades"][0]["realized_pnl"], -40.0, places=4) # loss of 40 USDC
        self.assertTrue("SL" in state["trades"][0]["trader_name"])

if __name__ == '__main__':
    unittest.main()
