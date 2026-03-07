#!/usr/bin/env python3
"""
Tests for the newly created supporting modules:
  - market_condition_engine
  - realtime_strategy_learner
  - threshold_tuner
  - data_feed (interface only, no network calls)
"""

import json
import os
import sqlite3
import tempfile
import unittest

os.environ.setdefault('TESTING', '1')

from market_condition_engine import (
    evaluate_conditions,
    get_news_status,
    ConditionState,
    MarketConditionEngine,
)
from realtime_strategy_learner import RealtimeStrategyLearner
from threshold_tuner import (
    load_learned_thresholds,
    get_learned_quality_adjustment,
    is_strategy_disabled,
    is_strategy_preferred,
)
from data_feed import TF_CONFIG, init_feed


class TestConditionState(unittest.TestCase):
    def test_defaults(self):
        s = ConditionState()
        self.assertEqual(s.condition, "NORMAL")
        self.assertFalse(s.blocked)
        self.assertEqual(s.quality_bonus, 0)
        self.assertEqual(s.sl_multiplier, 2.5)
        self.assertEqual(s.tp_multiplier, 1.5)
        self.assertEqual(s.position_size_mult, 1.0)


class TestEvaluateConditions(unittest.TestCase):
    def test_normal_vix(self):
        state = evaluate_conditions(18.0, 30.0, "MES", "LONG", {})
        self.assertEqual(state.condition, "NORMAL")
        self.assertFalse(state.blocked)
        self.assertEqual(state.quality_bonus, 0)

    def test_low_vol(self):
        state = evaluate_conditions(12.0, 30.0, "MNQ", "LONG", {})
        self.assertEqual(state.condition, "LOW_VOL")
        self.assertFalse(state.blocked)
        self.assertEqual(state.quality_bonus, 5)

    def test_elevated_vix(self):
        state = evaluate_conditions(25.0, 30.0, "MES", "SHORT", {})
        self.assertEqual(state.condition, "ELEVATED_VIX")
        self.assertEqual(state.quality_bonus, -2)
        self.assertFalse(state.blocked)

    def test_high_vix(self):
        state = evaluate_conditions(35.0, 30.0, "MGC", "SHORT", {})
        self.assertEqual(state.condition, "HIGH_VIX")
        self.assertFalse(state.blocked)
        self.assertEqual(state.quality_bonus, -5)
        self.assertEqual(state.position_size_mult, 0.75)

    def test_extreme_vix_blocks_equity_longs(self):
        state = evaluate_conditions(42.0, 30.0, "MES", "LONG", {})
        self.assertTrue(state.blocked)
        self.assertEqual(state.condition, "EXTREME_VIX")

    def test_extreme_vix_allows_commodity_shorts(self):
        state = evaluate_conditions(42.0, 30.0, "MGC", "SHORT", {})
        self.assertFalse(state.blocked)
        self.assertEqual(state.condition, "EXTREME_VIX")
        self.assertEqual(state.position_size_mult, 0.5)

    def test_zero_vix_is_unknown(self):
        state = evaluate_conditions(0, 30.0, "MES", "LONG", {})
        self.assertEqual(state.condition, "NORMAL")

    def test_engine_class_delegates(self):
        engine = MarketConditionEngine()
        state = engine.evaluate(18.0, 30.0, "MES", "LONG", {})
        self.assertEqual(state.condition, "NORMAL")


class TestGetNewsStatus(unittest.TestCase):
    def test_returns_tuple(self):
        blocked, event = get_news_status()
        self.assertIsInstance(blocked, bool)
        self.assertIsInstance(event, str)


class TestRealtimeStrategyLearner(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        self.learner = RealtimeStrategyLearner(self.db_path)

    def tearDown(self):
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def test_record_and_report(self):
        self.learner.record_trade({
            "trade_id": "100",
            "instrument": "MNQ",
            "direction": "Long",
            "entry_price": 20000.0,
            "exit_price": 20050.0,
            "pnl": 100.0,
            "entry_hour": 10,
            "hold_time_minutes": 15,
            "strategy": "TL43",
        })
        self.learner.record_trade({
            "trade_id": "101",
            "instrument": "MNQ",
            "direction": "Long",
            "entry_price": 20000.0,
            "exit_price": 19950.0,
            "pnl": -100.0,
            "entry_hour": 11,
            "hold_time_minutes": 8,
            "strategy": "TL43",
        })
        self.learner.generate_report()

        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM strategy_performance")
        self.assertEqual(c.fetchone()[0], 2)
        c.execute("SELECT trades, wins, losses FROM strategy_summary WHERE strategy='TL43'")
        row = c.fetchone()
        self.assertEqual(row[0], 2)
        self.assertEqual(row[1], 1)
        self.assertEqual(row[2], 1)
        conn.close()

    def test_duplicate_trade_ignored(self):
        trade = {"trade_id": "200", "instrument": "MES", "pnl": 50.0}
        self.learner.record_trade(trade)
        self.learner.record_trade(trade)

        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM strategy_performance WHERE trade_id='200'")
        self.assertEqual(c.fetchone()[0], 1)
        conn.close()

    def test_session_classification(self):
        self.assertEqual(self.learner._classify_session(10), "NY")
        self.assertEqual(self.learner._classify_session(5), "London")
        self.assertEqual(self.learner._classify_session(22), "Asian")


class TestThresholdTuner(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        self.tmp.write(json.dumps({
            "strategy_thresholds": {
                "TL43_MNQ_NY": {"quality_adj": -5, "disabled": False, "wr": 0.72},
                "BAD_MES_NY": {"quality_adj": 20, "disabled": True, "wr": 0.25},
            },
            "instrument_thresholds": {},
            "disabled_strategies": ["BAD"],
            "preferred_strategies": ["TL43"],
        }))
        self.tmp.close()

    def tearDown(self):
        os.unlink(self.tmp.name)

    def test_load_and_query(self):
        load_learned_thresholds(self.tmp.name)
        self.assertEqual(get_learned_quality_adjustment("TL43", "MNQ", "NY"), -5)
        self.assertEqual(get_learned_quality_adjustment("UNKNOWN", "MES", "NY"), 0)
        self.assertTrue(is_strategy_disabled("BAD"))
        self.assertFalse(is_strategy_disabled("TL43"))
        self.assertTrue(is_strategy_preferred("TL43"))
        self.assertFalse(is_strategy_preferred("BAD"))

    def test_missing_file(self):
        load_learned_thresholds("/tmp/nonexistent_file_12345.json")
        self.assertEqual(get_learned_quality_adjustment("TL43", "MNQ", "NY"), 0)


class TestDataFeed(unittest.TestCase):
    def test_tf_config_has_entries(self):
        self.assertGreater(len(TF_CONFIG), 10)
        self.assertIn("5m", TF_CONFIG)
        self.assertIn("1h", TF_CONFIG)
        self.assertIn("1d", TF_CONFIG)

    def test_init_feed_no_args(self):
        init_feed()
        init_feed(None)


class TestValidatorWithMCE(unittest.TestCase):
    """Verify that the validator's MCE integration works end-to-end."""

    def test_mce_available(self):
        import validator
        self.assertTrue(validator._MCE_AVAILABLE)

    def test_risk_management_with_mce_active(self):
        from validator import validate_risk_management
        alert = {
            "strategy": "MES-5M",
            "ticker": "MES",
            "action": "LONG",
            "entry": 5000.0,
            "atr": 3.0,
            "adx": 30,
        }
        ok, data, reason = validate_risk_management(alert)
        self.assertTrue(ok, f"Should pass: {reason}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
