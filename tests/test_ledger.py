"""Unit tests for append-only ledger, lots, sell attribution, migration."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ledger


class LedgerTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "test_ledger.db")
        ledger.configure(self.db)
        # force new connection
        if ledger._conn is not None:
            ledger._conn.close()
            ledger._conn = None
        ledger.get_connection()
        ledger.init_account(10000.0)

    def tearDown(self):
        if ledger._conn is not None:
            ledger._conn.close()
            ledger._conn = None
        self.tmp.cleanup()

    def test_idempotency_key(self):
        k = ledger.make_idempotency_key("polymarket", "0xabc", "tok1", "BUY")
        self.assertEqual(k, "polymarket|0xabc|tok1|BUY")
        self.assertTrue(ledger.mark_processed(k, note="first"))
        self.assertFalse(ledger.mark_processed(k, note="dup"))
        self.assertTrue(ledger.is_processed(k))

    def test_append_event_dedup(self):
        key = "polymarket|tx1|asset|BUY|fill"
        id1 = ledger.append_event("fill", {"x": 1}, idempotency_key=key)
        id2 = ledger.append_event("fill", {"x": 2}, idempotency_key=key)
        self.assertIsNotNone(id1)
        self.assertIsNone(id2)

    def test_open_and_close_whale_lots_only(self):
        # Two whales on same token
        ledger.open_lot(
            token_id="T1",
            whale_address="0xaaa",
            quantity=100,
            avg_price=0.5,
            invested_usdc=50,
            whale_name="A",
            market_title="M",
        )
        ledger.open_lot(
            token_id="T1",
            whale_address="0xbbb",
            quantity=200,
            avg_price=0.4,
            invested_usdc=80,
            whale_name="B",
            market_title="M",
        )
        positions = ledger.aggregate_positions_from_lots()
        self.assertIn("T1", positions)
        self.assertAlmostEqual(positions["T1"]["quantity"], 300.0)
        self.assertEqual(len(positions["T1"]["source_whales"]), 2)

        # Whale A sells half of their book -> close only A's lots
        closed = ledger.close_lots_for_whale("0xaaa", "T1", 50)
        self.assertEqual(len(closed), 1)
        self.assertAlmostEqual(closed[0]["qty"], 50.0)
        self.assertAlmostEqual(closed[0]["cost_basis"], 25.0)

        remaining = ledger.get_open_lots_for_token("T1")
        by_whale = {}
        for lot in remaining:
            by_whale[lot["whale_address"]] = by_whale.get(lot["whale_address"], 0) + lot["remaining_qty"]
        self.assertAlmostEqual(by_whale["0xaaa"], 50.0)
        self.assertAlmostEqual(by_whale["0xbbb"], 200.0)

        # Closing B must not touch A
        closed_b = ledger.close_lots_for_whale("0xbbb", "T1", 200)
        self.assertAlmostEqual(sum(c["qty"] for c in closed_b), 200.0)
        still_a = ledger.get_open_lots_for_whale_token("0xaaa", "T1")
        self.assertAlmostEqual(sum(l["remaining_qty"] for l in still_a), 50.0)

    def test_copy_lot_qty_fraction(self):
        ledger.open_lot(
            token_id="T2",
            whale_address="0xccc",
            quantity=40,
            avg_price=0.5,
            invested_usdc=20,
        )
        # Whale had 100, sells 25 -> 25% of our 40 = 10
        qty = ledger.copy_lot_qty_for_whale_sell("0xccc", "T2", whale_sell_qty=25, whale_holdings_before=100)
        self.assertAlmostEqual(qty, 10.0)

    def test_whale_inventory_independent(self):
        ledger.update_whale_inventory("0xddd", "T3", 500)
        ledger.update_whale_inventory("0xddd", "T3", -100)
        inv = ledger.get_whale_inventory("0xddd")
        self.assertAlmostEqual(inv["0xddd"]["T3"], 400.0)

    def test_migrate_json_positions_unaudited(self):
        # fresh db without lots
        if ledger._conn:
            ledger._conn.close()
            ledger._conn = None
        os.remove(self.db)
        ledger.configure(self.db)
        ledger.get_connection()

        state = {
            "cash_usdc": 8673.0,
            "positions": {
                "TOK1": {
                    "token_id": "TOK1",
                    "condition_id": "0xc1",
                    "market_title": "Test Market",
                    "market_slug": "test",
                    "outcome": "Yes",
                    "outcome_index": 0,
                    "avg_price": 0.5,
                    "quantity": 100,
                    "invested_usdc": 50,
                    "trader_address": "0xabc",
                    "trader_name": "Whale",
                    "opened_at": 1700000000,
                }
            },
            "trades": [],
            "logs": [{"timestamp": 1700000000, "message": "only one log"}],
            "whale_positions": {},
            "processed_tx_hashes": ["0xtx1"],
            "portfolio_value_history": [],
        }
        report = ledger.migrate_from_json_state(state, starting_capital=10000)
        self.assertEqual(report["migrated_positions"], 1)
        self.assertEqual(report["migrated_trades"], 0)
        self.assertTrue(any("Zero retained trades" in w for w in report["warnings"]))
        lots = ledger.get_all_open_lots()
        self.assertEqual(len(lots), 1)
        self.assertTrue(lots[0]["grandfathered"])
        proj = ledger.project_state()
        self.assertAlmostEqual(proj["cash_usdc"], 8673.0)
        self.assertIn("TOK1", proj["positions"])
        self.assertEqual(len(proj["trades"]), 0)

    def test_archive_reset_requires_confirm(self):
        with self.assertRaises(ValueError):
            ledger.archive_and_reset(10000.0, confirm=False)
        path = ledger.archive_and_reset(5000.0, confirm=True)
        self.assertTrue(path.endswith(os.path.basename(self.db)) or "archive" in path)
        self.assertAlmostEqual(ledger.get_cash(), 5000.0)
        self.assertEqual(len(ledger.get_all_open_lots()), 0)

    def test_record_trade_and_decision(self):
        ledger.record_decision(
            "rejected",
            "below min score",
            whale_address="0x1",
            token_id="T",
            side="BUY",
            tx_hash="0xhash",
            feature_snapshot={"score": 10},
        )
        ledger.record_trade({
            "type": "BUY",
            "trader_address": "0x1",
            "quantity": 10,
            "price": 0.5,
            "usdc_size": 5,
            "tx_hash": "0xhash2",
            "market_title": "M",
        })
        trades = ledger.get_trades()
        self.assertEqual(len(trades), 1)
        rep = ledger.reconcile_report()
        self.assertGreaterEqual(rep["events"], 2)
        self.assertEqual(rep["buy_count"], 1)


if __name__ == "__main__":
    unittest.main()
