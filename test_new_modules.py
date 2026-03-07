#!/usr/bin/env python3
"""Tests for the newly created dependency modules."""

import os
import json
import sqlite3
import unittest
from datetime import datetime

os.environ.setdefault('TESTING', '1')


class TestMarketConditionEngine(unittest.TestCase):
    def test_normal_conditions(self):
        from market_condition_engine import evaluate_conditions
        result = evaluate_conditions(18.0, 30.0, 'MES', 'LONG', {})
        self.assertFalse(result.blocked)
        self.assertEqual(result.condition, 'NORMAL')

    def test_elevated_vix(self):
        from market_condition_engine import evaluate_conditions
        result = evaluate_conditions(25.0, 30.0, 'MES', 'LONG', {})
        self.assertFalse(result.blocked)
        self.assertEqual(result.condition, 'ELEVATED_VOL')
        self.assertEqual(result.quality_bonus, -5)

    def test_high_vol(self):
        from market_condition_engine import evaluate_conditions
        result = evaluate_conditions(32.0, 30.0, 'MES', 'LONG', {})
        self.assertFalse(result.blocked)
        self.assertEqual(result.condition, 'HIGH_VOLATILITY')

    def test_extreme_vix_blocks_equity_long(self):
        from market_condition_engine import evaluate_conditions
        result = evaluate_conditions(42.0, 30.0, 'MES', 'LONG', {})
        self.assertTrue(result.blocked)
        self.assertIn('VIX', result.reason)

    def test_extreme_vix_allows_commodity_short(self):
        from market_condition_engine import evaluate_conditions
        result = evaluate_conditions(42.0, 30.0, 'MGC', 'SHORT', {})
        self.assertFalse(result.blocked)

    def test_low_vol_bonus(self):
        from market_condition_engine import evaluate_conditions
        result = evaluate_conditions(12.0, 30.0, 'MES', 'LONG', {})
        self.assertEqual(result.condition, 'LOW_VOLATILITY')
        self.assertGreater(result.quality_bonus, 0)

    def test_strong_trend_bonus(self):
        from market_condition_engine import evaluate_conditions
        result = evaluate_conditions(18.0, 45.0, 'MES', 'LONG', {})
        self.assertGreaterEqual(result.quality_bonus, 5)

    def test_zero_vix_treated_as_unknown(self):
        from market_condition_engine import evaluate_conditions
        result = evaluate_conditions(0.0, 30.0, 'MES', 'LONG', {})
        self.assertFalse(result.blocked)

    def test_condition_state_defaults(self):
        from market_condition_engine import ConditionState
        state = ConditionState()
        self.assertFalse(state.blocked)
        self.assertEqual(state.sl_multiplier, 2.5)
        self.assertEqual(state.tp_multiplier, 1.5)
        self.assertEqual(state.position_size_mult, 1.0)

    def test_get_news_status_returns_tuple(self):
        from market_condition_engine import get_news_status
        blocked, name = get_news_status()
        self.assertIsInstance(blocked, bool)
        self.assertIsInstance(name, str)

    def test_market_condition_engine_class(self):
        from market_condition_engine import MarketConditionEngine
        engine = MarketConditionEngine()
        result = engine.evaluate(18.0, 30.0, 'MES', 'LONG', {})
        self.assertFalse(result.blocked)
        self.assertEqual(engine.last_vix, 18.0)
        self.assertIsNotNone(engine.last_check)


class TestRealtimeStrategyLearner(unittest.TestCase):
    DB_PATH = '/tmp/test_learner_unittest.db'

    def setUp(self):
        if os.path.exists(self.DB_PATH):
            os.remove(self.DB_PATH)

    def tearDown(self):
        if os.path.exists(self.DB_PATH):
            os.remove(self.DB_PATH)

    def test_init_creates_tables(self):
        from realtime_strategy_learner import RealtimeStrategyLearner
        learner = RealtimeStrategyLearner(db_path=self.DB_PATH)
        conn = sqlite3.connect(self.DB_PATH)
        c = conn.cursor()
        c.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in c.fetchall()}
        conn.close()
        self.assertIn('strategy_performance', tables)
        self.assertIn('adaptive_params', tables)

    def test_record_winning_trade(self):
        from realtime_strategy_learner import RealtimeStrategyLearner
        learner = RealtimeStrategyLearner(db_path=self.DB_PATH)
        learner.record_trade({
            'trade_id': '100', 'instrument': 'MNQ', 'direction': 'Long',
            'pnl': 50.0, 'entry_hour': 10, 'strategy': 'TL43'
        })
        stats = learner.get_strategy_stats('TL43')
        self.assertEqual(stats['trades'], 1)
        self.assertEqual(stats['wins'], 1)
        self.assertEqual(stats['win_rate'], 1.0)

    def test_record_losing_trade(self):
        from realtime_strategy_learner import RealtimeStrategyLearner
        learner = RealtimeStrategyLearner(db_path=self.DB_PATH)
        learner.record_trade({
            'trade_id': '200', 'instrument': 'MES', 'direction': 'Short',
            'pnl': -30.0, 'entry_hour': 14, 'strategy': 'TL40'
        })
        stats = learner.get_strategy_stats('TL40')
        self.assertEqual(stats['trades'], 1)
        self.assertEqual(stats['losses'], 1)
        self.assertEqual(stats['win_rate'], 0.0)

    def test_duplicate_trade_id_ignored(self):
        from realtime_strategy_learner import RealtimeStrategyLearner
        learner = RealtimeStrategyLearner(db_path=self.DB_PATH)
        trade = {'trade_id': '300', 'instrument': 'MNQ', 'direction': 'Long',
                 'pnl': 50.0, 'entry_hour': 10, 'strategy': 'TL43'}
        learner.record_trade(trade)
        learner.record_trade(trade)
        stats = learner.get_strategy_stats('TL43')
        self.assertEqual(stats['trades'], 1)

    def test_session_classification(self):
        from realtime_strategy_learner import RealtimeStrategyLearner
        learner = RealtimeStrategyLearner(db_path=self.DB_PATH)
        self.assertEqual(learner._classify_session(10), 'NY')
        self.assertEqual(learner._classify_session(5), 'London')
        self.assertEqual(learner._classify_session(22), 'Asian')

    def test_generate_report_no_crash(self):
        from realtime_strategy_learner import RealtimeStrategyLearner
        learner = RealtimeStrategyLearner(db_path=self.DB_PATH)
        learner.generate_report()

    def test_stats_filter_by_instrument(self):
        from realtime_strategy_learner import RealtimeStrategyLearner
        learner = RealtimeStrategyLearner(db_path=self.DB_PATH)
        learner.record_trade({
            'trade_id': '400', 'instrument': 'MNQ', 'direction': 'Long',
            'pnl': 50.0, 'entry_hour': 10, 'strategy': 'AI-MNQ-5m'
        })
        learner.record_trade({
            'trade_id': '401', 'instrument': 'MES', 'direction': 'Long',
            'pnl': 30.0, 'entry_hour': 10, 'strategy': 'AI-MNQ-5m'
        })
        stats_mnq = learner.get_strategy_stats('AI-MNQ-5m', instrument='MNQ')
        stats_mes = learner.get_strategy_stats('AI-MNQ-5m', instrument='MES')
        self.assertEqual(stats_mnq['trades'], 1)
        self.assertEqual(stats_mes['trades'], 1)


class TestThresholdTuner(unittest.TestCase):
    def test_load_defaults_when_no_file(self):
        from threshold_tuner import load_learned_thresholds, get_learned_quality_adjustment
        load_learned_thresholds('/nonexistent/path.json')
        adj = get_learned_quality_adjustment('TL43', 'MNQ', 'NY')
        self.assertEqual(adj, 0)

    def test_is_strategy_disabled_default(self):
        from threshold_tuner import is_strategy_disabled
        self.assertFalse(is_strategy_disabled('TL43'))

    def test_is_strategy_preferred_default(self):
        from threshold_tuner import is_strategy_preferred
        self.assertFalse(is_strategy_preferred('TL43'))

    def test_load_from_file(self):
        import threshold_tuner
        test_data = {
            'strategy_thresholds': {
                'BadStrat_MES_NY': {'quality_adj': 15, 'disabled': True, 'wr': 0.2, 'trades': 20}
            },
            'instrument_thresholds': {},
            'disabled_strategies': ['BadStrat'],
            'preferred_strategies': ['GoodStrat'],
        }
        test_path = '/tmp/test_thresholds.json'
        with open(test_path, 'w') as f:
            json.dump(test_data, f)

        threshold_tuner.load_learned_thresholds(test_path)
        self.assertTrue(threshold_tuner.is_strategy_disabled('BadStrat'))
        self.assertTrue(threshold_tuner.is_strategy_preferred('GoodStrat'))
        adj = threshold_tuner.get_learned_quality_adjustment('BadStrat', 'MES', 'NY')
        self.assertEqual(adj, 15)

        os.remove(test_path)


class TestDataFeed(unittest.TestCase):
    def test_init_feed_no_crash(self):
        from data_feed import init_feed
        init_feed()

    def test_tf_config_available(self):
        from data_feed import TF_CONFIG
        self.assertIn('5m', TF_CONFIG)
        self.assertIn('1h', TF_CONFIG)
        self.assertIn('1d', TF_CONFIG)

    def test_ticker_map_available(self):
        from data_feed import TICKER_MAP
        self.assertEqual(TICKER_MAP['MES'], 'ES=F')
        self.assertEqual(TICKER_MAP['MNQ'], 'NQ=F')
        self.assertEqual(TICKER_MAP['MGC'], 'GC=F')


class TestSetupRagDatabase(unittest.TestCase):
    DB_PATH = '/tmp/test_rag.db'

    def setUp(self):
        if os.path.exists(self.DB_PATH):
            os.remove(self.DB_PATH)

    def tearDown(self):
        if os.path.exists(self.DB_PATH):
            os.remove(self.DB_PATH)

    def test_init_creates_tables(self):
        from setup_rag_database import init_rag_database
        init_rag_database(self.DB_PATH)
        conn = sqlite3.connect(self.DB_PATH)
        c = conn.cursor()
        c.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in c.fetchall()}
        conn.close()
        self.assertIn('signal_log', tables)
        self.assertIn('strategy_context', tables)
        self.assertIn('pattern_context', tables)

    def test_get_strategy_context_empty(self):
        from setup_rag_database import get_strategy_context
        ctx = get_strategy_context('UNKNOWN', 'MES')
        self.assertEqual(ctx, {})

    def test_get_pattern_context_empty(self):
        from setup_rag_database import get_pattern_context
        ctx = get_pattern_context('unknown_pattern', 'MES', 'NY')
        self.assertEqual(ctx, {})

    def test_log_signal_no_crash(self):
        from setup_rag_database import log_signal_to_rag
        log_signal_to_rag(
            strategy='TL43', instrument='MNQ', action='LONG',
            quality_score=85, pattern='ema_crossover',
            ai_approved=True, ai_confidence=0.75
        )


class TestValidatorWithMCE(unittest.TestCase):
    """Verify the validator properly uses market_condition_engine now."""

    def test_mce_available(self):
        from validator import _MCE_AVAILABLE
        self.assertTrue(_MCE_AVAILABLE)

    def test_risk_management_still_works(self):
        from validator import validate_risk_management
        alert = {
            'strategy': 'MGC-10M', 'ticker': 'MGC', 'action': 'SHORT',
            'entry': 2900.0, 'atr': 20.0, 'adx': 44.7,
        }
        ok, updated, reason = validate_risk_management(alert)
        self.assertTrue(ok, reason)


if __name__ == '__main__':
    unittest.main(verbosity=2)
