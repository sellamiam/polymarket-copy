import unittest
import bot_engine
import config


class StrategyV4TestCase(unittest.TestCase):
    def test_short_term_crypto_filter(self):
        # 5-minute / hourly / intraday coin flips should be identified as short term
        self.assertTrue(bot_engine.is_short_term_crypto_coinflip(
            title="Bitcoin Up or Down - October 16, 10:05PM-10:10PM ET",
            slug="btc-updown-1005pm"
        ))
        self.assertTrue(bot_engine.is_short_term_crypto_coinflip(
            title="Solana 15-minute price above or below 150",
            slug="sol-15m-above"
        ))
        self.assertTrue(bot_engine.is_short_term_crypto_coinflip(
            title="Ethereum hourly price at 11:00 PM",
            slug="eth-hourly-11pm"
        ))

        # Macro / Regulatory / Legal crypto prediction markets should NOT be filtered
        self.assertFalse(bot_engine.is_short_term_crypto_coinflip(
            title="Will US pass Strategic Bitcoin Reserve bill by December 31?",
            slug="us-pass-strategic-bitcoin-reserve-bill-2026"
        ))
        self.assertFalse(bot_engine.is_short_term_crypto_coinflip(
            title="Will SEC approve Ethereum staking ETF in 2026?",
            slug="sec-approve-ethereum-staking-etf-2026"
        ))
        self.assertFalse(bot_engine.is_short_term_crypto_coinflip(
            title="Will Ripple settle SEC litigation by November 2026?",
            slug="ripple-sec-settle-nov-2026"
        ))

    def test_config_v4_defaults(self):
        cfg = config.DEFAULT_CONFIG
        self.assertGreaterEqual(cfg["performance_strategy_version"], 4)
        self.assertEqual(cfg["multi_whale_confirm_count"], 1)
        self.assertFalse(cfg["multi_whale_require_all"])
        self.assertTrue(cfg["single_whale_elite_bypass"])
        self.assertTrue(cfg["exclude_weather_bets"])
        self.assertFalse(cfg["exclude_crypto_bets"])
        self.assertTrue(cfg["exclude_sports_bets"])
        self.assertEqual(cfg["min_copy_price"], 0.35)
        self.assertEqual(cfg["max_copy_price"], 0.88)
        self.assertEqual(cfg["min_whale_trade_size"], 1.0)


if __name__ == "__main__":
    unittest.main()
