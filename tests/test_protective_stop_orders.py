import importlib
import importlib.util
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TRADING_SYSTEM = ROOT / "trading-system"
sys.path.insert(0, str(TRADING_SYSTEM))


def load_strict_spike_backtest():
    path = ROOT / "strategies" / "S22-spike-v13" / "backtest_spike_v13_strict.py"
    spec = importlib.util.spec_from_file_location("backtest_spike_v13_strict", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ProtectiveStopOrderTests(unittest.TestCase):
    def setUp(self):
        self.binance_api = importlib.import_module("binance_api")

    def test_stop_market_order_uses_exchange_safe_params(self):
        calls = []

        def fake_signed_post(endpoint, params=None, retries=3):
            calls.append((endpoint, params, retries))
            return {"orderId": 123, "status": "NEW"}

        self.binance_api.signed_post = fake_signed_post

        result = self.binance_api.place_stop_loss_order(
            "LABUSDT",
            quantity="12.34560000",
            direction="long",
            stop_price="0.12345000",
        )

        self.assertEqual(result["orderId"], 123)
        self.assertEqual(calls[0][0], "/fapi/v1/algoOrder")
        params = calls[0][1]
        self.assertEqual(params["symbol"], "LABUSDT")
        self.assertEqual(params["side"], "SELL")
        self.assertEqual(params["positionSide"], "BOTH")
        self.assertEqual(params["algoType"], "CONDITIONAL")
        self.assertEqual(params["type"], "STOP_MARKET")
        self.assertEqual(params["quantity"], "12.34560000")
        self.assertEqual(params["triggerPrice"], "0.12345000")
        self.assertEqual(params["reduceOnly"], "true")
        self.assertEqual(params["workingType"], "MARK_PRICE")

    def test_stop_failure_is_not_considered_protected(self):
        cron_scan = importlib.import_module("cron_scan")
        trade = {
            "symbol": "TONUSDT",
            "direction": "short",
            "quantity": 10,
            "status": "open",
        }

        closed = []
        cron_scan.execute_close = lambda symbol, direction, quantity: closed.append((symbol, direction, quantity)) or {
            "success": True,
            "order": {"orderId": 456},
        }

        ok = cron_scan.handle_protective_stop_failure(
            trade,
            {"error": 400, "msg": "Order would immediately trigger."},
        )

        self.assertFalse(ok)
        self.assertEqual(trade["status"], "closed")
        self.assertEqual(trade["exit_reason"], "protective_stop_failed")
        self.assertTrue(trade["unprotected_stop_failed"])
        self.assertEqual(closed, [("TONUSDT", "short", 10)])

    def test_recent_loss_symbols_are_not_hardcoded_without_trade_evidence(self):
        cron_scan = importlib.import_module("cron_scan")
        config = importlib.import_module("config")
        with tempfile.TemporaryDirectory() as tmp:
            config.BLACKLIST_FILE = Path(tmp) / "dynamic_blacklist.json"
            config.BLACKLIST_FILE.write_text('{"symbols": [], "quarantine": {}}')

            blacklist = cron_scan.load_blacklist()

        self.assertNotIn("RIVERUSDT", blacklist)
        self.assertNotIn("TONUSDT", blacklist)
        self.assertNotIn("ARCUSDT", blacklist)

    def test_symbol_enters_quarantine_by_repeated_losses_and_expires(self):
        cron_scan = importlib.import_module("cron_scan")
        config = importlib.import_module("config")
        now = datetime.now(timezone(timedelta(hours=8)))

        with tempfile.TemporaryDirectory() as tmp:
            config.BLACKLIST_FILE = Path(tmp) / "dynamic_blacklist.json"
            config.BLACKLIST_MIN_TRADES = 3
            config.BLACKLIST_MAX_LOSS_USD = 10
            config.BLACKLIST_MAX_WIN_RATE = 0.40
            config.BLACKLIST_QUARANTINE_HOURS = 48
            config.BLACKLIST_SINGLE_LOSS_USD = 8
            data = {
                "trades": [
                    {"symbol": "RIVERUSDT", "status": "closed", "exit_time": (now - timedelta(hours=5)).isoformat(), "pnl_usd": -5},
                    {"symbol": "RIVERUSDT", "status": "closed", "exit_time": (now - timedelta(hours=4)).isoformat(), "pnl_usd": -4},
                    {"symbol": "RIVERUSDT", "status": "closed", "exit_time": (now - timedelta(hours=3)).isoformat(), "pnl_usd": -3},
                    {"symbol": "BILLUSDT", "status": "closed", "exit_time": (now - timedelta(hours=2)).isoformat(), "pnl_usd": 4},
                ]
            }

            blacklist = cron_scan.update_dynamic_blacklist(data)
            self.assertIn("RIVERUSDT", blacklist)
            self.assertNotIn("BILLUSDT", blacklist)
            self.assertIn("RIVERUSDT", cron_scan.load_blacklist())

            saved = config.BLACKLIST_FILE.read_text()
            self.assertIn("quarantined_until", saved)

            config.BLACKLIST_FILE.write_text(
                '{"symbols":["RIVERUSDT"],"quarantine":{"RIVERUSDT":{"quarantined_until":"2000-01-01T00:00:00+08:00"}}}'
            )
            self.assertNotIn("RIVERUSDT", cron_scan.load_blacklist())

    def test_weak_wick_15m_spike_is_rejected(self):
        cron_scan = importlib.import_module("cron_scan")
        cron_scan.get_klines = lambda symbol, interval, limit: [
            [0, "100", "101", "99", "100", "10", 0, "1000"],
            [1, "100", "105", "99", "101.6", "10", 0, "1200"],
            [2, "101.6", "101.8", "101.2", "101.4", "10", 0, "1100"],
        ]

        cand = cron_scan.build_closed_15m_spike_candidate("FAKEUSDT", 0, 0, 1_000_000)

        self.assertIsNone(cand)

    def test_clean_high_volume_15m_spike_is_accepted(self):
        cron_scan = importlib.import_module("cron_scan")
        cron_scan.get_klines = lambda symbol, interval, limit: [
            [0, "100", "100.4", "99.8", "100.1", "10", 0, "1000"],
            [1, "100", "102.1", "99.9", "102.0", "10", 0, "2600"],
            [2, "102.0", "102.2", "101.7", "101.9", "10", 0, "1100"],
        ]

        cand = cron_scan.build_closed_15m_spike_candidate("FAKEUSDT", 0, 0, 1_000_000)

        self.assertIsNotNone(cand)
        self.assertEqual(cand["type"], "closed_15m_spike")
        self.assertEqual(cand["direction"], "long")
        self.assertGreaterEqual(cand["volume_ratio"], 2.0)

    def test_strict_backtest_reuses_clean_spike_shape_filters(self):
        strict = load_strict_spike_backtest()
        history = [
            strict.Candle(time=i, open=100, high=100.5, low=99.5, close=100.1, volume=1000)
            for i in range(20)
        ]
        weak_wick = strict.Candle(time=21, open=100, high=105, low=99, close=101.6, volume=2600)
        clean_spike = strict.Candle(time=22, open=100, high=102.1, low=99.9, close=102.0, volume=2600)

        strict.SPIKE_VOLUME_RATIO_MIN = 1.8
        strict.SPIKE_BODY_RATIO_MIN = 0.55
        strict.SPIKE_CLOSE_POSITION_MIN = 0.65

        self.assertFalse(strict.spike_shape_passes(weak_wick, history))
        self.assertTrue(strict.spike_shape_passes(clean_spike, history))

    def test_strict_backtest_can_enable_short_spike_direction(self):
        strict = load_strict_spike_backtest()
        strict.SPIKE_THRESHOLD = 0.015
        strict.ALLOW_SHORTS = False
        self.assertIsNone(strict.signal_direction(-0.02))

        strict.ALLOW_SHORTS = True
        self.assertEqual(strict.signal_direction(-0.02), "short")
        self.assertEqual(strict.signal_direction(0.02), "long")

    def test_g60c_profile_sets_small_clean_spike_risk_controls(self):
        keys = ["STRATEGY_PROFILE", "V8_POSITION_PCT_MAX"]
        old_env = {key: os.environ.get(key) for key in keys}
        try:
            os.environ["STRATEGY_PROFILE"] = "G60C"
            os.environ.pop("V8_POSITION_PCT_MAX", None)
            config = importlib.reload(importlib.import_module("config"))

            self.assertEqual(config.STRATEGY_PROFILE, "G60C")
            self.assertEqual(config.MAX_LOSS_PER_TRADE, 12.0)
            self.assertEqual(config.V11I_MAX_SL_PCT, 6.5)
            self.assertEqual(config.V11I_MAX_ATR_PCT, 4.0)
            self.assertEqual(config.V8_POSITION_PCT_MAX, 4.0)
            self.assertEqual(config.BLACKLIST_SHORT_POSITION_FACTOR, 0.35)
        finally:
            for key, value in old_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            importlib.reload(importlib.import_module("config"))


if __name__ == "__main__":
    unittest.main()
