#!/usr/bin/env python3
"""Tests for the learning agent components: trade_outcome_tracker, rag_updater,
adaptive_params, and learning_agent orchestrator."""

import os
import sys
import json
import sqlite3
import tempfile
import shutil
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

os.environ.setdefault('TESTING', '1')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


class TestTradeOutcomeTracker(unittest.TestCase):
    """Tests for trade_outcome_tracker.py"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.learning_db = os.path.join(self.tmpdir, 'test_learning.db')
        self.validator_db = os.path.join(self.tmpdir, 'test_validator.db')

        import trade_outcome_tracker as tot
        tot.LEARNING_DB = self.learning_db
        tot.VALIDATOR_DB = self.validator_db
        self.tot = tot

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_get_instrument(self):
        self.assertEqual(self.tot._get_instrument('MES'), 'MES')
        self.assertEqual(self.tot._get_instrument('/MNQ'), 'MNQ')
        self.assertEqual(self.tot._get_instrument('MCL Jun25'), 'MCL')
        self.assertEqual(self.tot._get_instrument(''), 'UNKNOWN')

    def test_current_session_returns_string(self):
        session = self.tot._current_session()
        self.assertIn(session, ('NY', 'London', 'Asia', 'UAE'))

    def test_tracker_init(self):
        tracker = self.tot.TradeOutcomeTracker()
        self.assertIsNotNone(tracker.learner)
        self.assertIsInstance(tracker._tracked_trades, set)

    def test_check_sl_tp_long_tp_hit(self):
        tracker = self.tot.TradeOutcomeTracker()
        result = tracker._check_sl_tp('LONG', 100.0, 95.0, 110.0, 111.0)
        self.assertEqual(result, 'TP_HIT')

    def test_check_sl_tp_long_sl_hit(self):
        tracker = self.tot.TradeOutcomeTracker()
        result = tracker._check_sl_tp('LONG', 100.0, 95.0, 110.0, 94.0)
        self.assertEqual(result, 'SL_HIT')

    def test_check_sl_tp_long_still_open(self):
        tracker = self.tot.TradeOutcomeTracker()
        result = tracker._check_sl_tp('LONG', 100.0, 95.0, 110.0, 102.0)
        self.assertIsNone(result)

    def test_check_sl_tp_short_tp_hit(self):
        tracker = self.tot.TradeOutcomeTracker()
        result = tracker._check_sl_tp('SHORT', 100.0, 105.0, 90.0, 89.0)
        self.assertEqual(result, 'TP_HIT')

    def test_check_sl_tp_short_sl_hit(self):
        tracker = self.tot.TradeOutcomeTracker()
        result = tracker._check_sl_tp('SHORT', 100.0, 105.0, 90.0, 106.0)
        self.assertEqual(result, 'SL_HIT')

    def test_calculate_pnl_long_win(self):
        tracker = self.tot.TradeOutcomeTracker()
        pnl = tracker._calculate_pnl('LONG', 5100.0, 5110.0, 'MES', 1)
        self.assertAlmostEqual(pnl, 50.0)

    def test_calculate_pnl_short_win(self):
        tracker = self.tot.TradeOutcomeTracker()
        pnl = tracker._calculate_pnl('SHORT', 5110.0, 5100.0, 'MES', 1)
        self.assertAlmostEqual(pnl, 50.0)

    def test_calculate_pnl_long_loss(self):
        tracker = self.tot.TradeOutcomeTracker()
        pnl = tracker._calculate_pnl('LONG', 5100.0, 5095.0, 'MES', 1)
        self.assertAlmostEqual(pnl, -25.0)

    def test_process_closed_trade(self):
        tracker = self.tot.TradeOutcomeTracker()
        trade = {
            'trade_id': 'test_001',
            'instrument': 'MES',
            'direction': 'LONG',
            'entry_price': 5100.0,
            'exit_price': 5110.0,
            'pnl': 50.0,
            'entry_hour': 10,
            'hold_time_minutes': 15.0,
            'strategy': 'TREND_PULLBACK',
            'quality_score': 85,
            'adx': 32.0,
            'confidence': 0.75,
        }
        tracker.process_closed_trade(trade)
        self.assertIn('test_001', tracker._tracked_trades)

        conn = sqlite3.connect(self.learning_db)
        c = conn.cursor()
        c.execute("SELECT * FROM strategy_performance WHERE trade_id = 'test_001'")
        row = c.fetchone()
        conn.close()
        self.assertIsNotNone(row)

    def test_duplicate_trade_ignored(self):
        tracker = self.tot.TradeOutcomeTracker()
        trade = {
            'trade_id': 'dup_001',
            'instrument': 'MNQ',
            'direction': 'SHORT',
            'pnl': -20.0,
            'strategy': 'BREAKOUT',
        }
        tracker.process_closed_trade(trade)
        tracker.process_closed_trade(trade)

        conn = sqlite3.connect(self.learning_db)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM strategy_performance WHERE trade_id = 'dup_001'")
        count = c.fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)

    def test_process_projectx_trades(self):
        tracker = self.tot.TradeOutcomeTracker()
        raw = [{
            'id': 'px_001',
            'contractName': 'MESZ25',
            'pnl': 75.0,
            'type': 'Long',
            'entryPrice': 5100.0,
            'exitPrice': 5115.0,
            'enteredAt': '2026-03-07T10:00:00',
            'tradeDuration': '00:12:30.000',
        }]
        count = tracker.process_projectx_trades(raw)
        self.assertEqual(count, 1)
        self.assertIn('px_001', tracker._tracked_trades)

    def test_get_summary_empty(self):
        tracker = self.tot.TradeOutcomeTracker()
        summary = tracker.get_summary()
        self.assertEqual(summary['total_trades'], 0)

    def test_get_summary_with_trades(self):
        tracker = self.tot.TradeOutcomeTracker()
        for i in range(3):
            tracker.process_closed_trade({
                'trade_id': f'sum_{i}',
                'instrument': 'MES',
                'direction': 'LONG',
                'pnl': 50.0 if i < 2 else -25.0,
                'strategy': 'TEST',
            })
        summary = tracker.get_summary()
        self.assertEqual(summary['total_trades'], 3)
        self.assertEqual(summary['wins'], 2)
        self.assertEqual(summary['losses'], 1)

    def test_poll_once_no_crash(self):
        tracker = self.tot.TradeOutcomeTracker()
        count = tracker.poll_once()
        self.assertIsInstance(count, int)


class TestRagUpdater(unittest.TestCase):
    """Tests for rag_updater.py"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.learning_db = os.path.join(self.tmpdir, 'test_learning.db')
        self.rag_db = os.path.join(self.tmpdir, 'test_rag.db')

        import rag_updater as ru
        ru.LEARNING_DB = self.learning_db
        ru.RAG_DB_PATH = self.rag_db
        self.ru = ru

        self._setup_learning_data()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _setup_learning_data(self):
        from realtime_strategy_learner import RealtimeStrategyLearner
        learner = RealtimeStrategyLearner(self.learning_db)
        strategies = ['TREND_PULLBACK', 'BREAKOUT', 'MOMENTUM_CONT']
        for i in range(15):
            strat = strategies[i % 3]
            learner.record_trade({
                'trade_id': f'rag_test_{i}',
                'instrument': 'MES',
                'direction': 'LONG',
                'pnl': 50.0 if i % 2 == 0 else -25.0,
                'entry_hour': 10,
                'strategy': strat,
                'quality_score': 80,
            })

    def test_update_strategy_context(self):
        count = self.ru.update_strategy_context()
        self.assertGreater(count, 0)

        conn = sqlite3.connect(self.rag_db)
        c = conn.cursor()
        c.execute("SELECT * FROM strategy_context WHERE strategy = 'TREND_PULLBACK'")
        row = c.fetchone()
        conn.close()
        self.assertIsNotNone(row)

    def test_update_pattern_context(self):
        count = self.ru.update_pattern_context()
        self.assertGreater(count, 0)

        conn = sqlite3.connect(self.rag_db)
        c = conn.cursor()
        c.execute("SELECT * FROM pattern_context")
        rows = c.fetchall()
        conn.close()
        self.assertGreater(len(rows), 0)

    def test_update_all(self):
        total = self.ru.update_all()
        self.assertGreater(total, 0)

    def test_update_with_empty_db(self):
        self.ru.LEARNING_DB = os.path.join(self.tmpdir, 'nonexistent.db')
        count = self.ru.update_strategy_context()
        self.assertEqual(count, 0)


class TestAdaptiveParams(unittest.TestCase):
    """Tests for adaptive_params.py"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.learning_db = os.path.join(self.tmpdir, 'test_learning.db')
        self.thresholds_path = os.path.join(self.tmpdir, 'learned_thresholds.json')

        import adaptive_params as ap
        ap.LEARNING_DB = self.learning_db
        ap.THRESHOLDS_PATH = self.thresholds_path
        self.ap = ap

        self._setup_data()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _setup_data(self):
        from realtime_strategy_learner import RealtimeStrategyLearner
        learner = RealtimeStrategyLearner(self.learning_db)
        for i in range(20):
            learner.record_trade({
                'trade_id': f'ap_{i}',
                'instrument': 'MES',
                'direction': 'LONG',
                'pnl': 75.0 if i % 3 != 0 else -30.0,
                'entry_hour': 10,
                'strategy': 'TREND_PULLBACK',
                'quality_score': 82 if i % 3 != 0 else 68,
                'adx': 32.0,
                'confidence': 0.78,
            })
        for i in range(12):
            learner.record_trade({
                'trade_id': f'ap_bad_{i}',
                'instrument': 'MYM',
                'direction': 'SHORT',
                'pnl': -40.0 if i % 5 != 0 else 20.0,
                'entry_hour': 14,
                'strategy': 'EMA_CROSSOVER',
                'quality_score': 55,
            })

    def test_compute_strategy_thresholds(self):
        result = self.ap.compute_strategy_thresholds()
        self.assertIn('strategy_thresholds', result)
        self.assertIn('disabled_strategies', result)
        self.assertIn('preferred_strategies', result)

    def test_preferred_strategy(self):
        from realtime_strategy_learner import RealtimeStrategyLearner
        learner = RealtimeStrategyLearner(self.learning_db)
        for i in range(10):
            learner.record_trade({
                'trade_id': f'pref_{i}',
                'instrument': 'MGC',
                'direction': 'LONG',
                'pnl': 60.0 if i < 8 else -20.0,
                'entry_hour': 10,
                'strategy': 'IMPULSE',
                'quality_score': 90,
            })
        result = self.ap.compute_strategy_thresholds()
        self.assertIn('IMPULSE', result.get('preferred_strategies', []))

    def test_disabled_strategy(self):
        result = self.ap.compute_strategy_thresholds()
        self.assertIn('EMA_CROSSOVER', result.get('disabled_strategies', []))

    def test_compute_sl_tp_adjustments(self):
        result = self.ap.compute_sl_tp_adjustments()
        self.assertIsInstance(result, dict)
        if result:
            for key, adj in result.items():
                self.assertIn('sl_multiplier', adj)
                self.assertIn('tp_multiplier', adj)
                self.assertIn('size_multiplier', adj)

    def test_write_learned_thresholds(self):
        self.ap.write_learned_thresholds()
        self.assertTrue(os.path.exists(self.thresholds_path))

        with open(self.thresholds_path) as f:
            data = json.load(f)
        self.assertIn('updated_at', data)
        self.assertIn('strategy_thresholds', data)
        self.assertIn('sl_tp_adjustments', data)

    def test_get_adaptive_param_default(self):
        val = self.ap.get_adaptive_param('MES', 'LONG', 'sl_multiplier', 1.0)
        self.assertIsInstance(val, float)

    def test_get_adaptive_param_after_compute(self):
        self.ap.compute_sl_tp_adjustments()
        val = self.ap.get_adaptive_param('MES', 'LONG', 'sl_multiplier', 1.0)
        self.assertIsInstance(val, float)

    def test_adaptive_params_table_populated(self):
        self.ap.compute_strategy_thresholds()
        conn = sqlite3.connect(self.learning_db)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM adaptive_params")
        count = c.fetchone()[0]
        conn.close()
        self.assertGreater(count, 0)


class TestLearningAgentOrchestrator(unittest.TestCase):
    """Tests for learning_agent.py orchestrator."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        os.environ['TESTING'] = '1'

        import trade_outcome_tracker as tot
        import adaptive_params as ap
        import rag_updater as ru
        import learning_agent as la
        self._orig_tot_db = tot.LEARNING_DB
        self._orig_tot_vdb = tot.VALIDATOR_DB
        self._orig_ap_db = ap.LEARNING_DB
        self._orig_ap_th = ap.THRESHOLDS_PATH
        self._orig_ru_db = ru.LEARNING_DB
        self._orig_la_db = la.LEARNING_DB
        self._orig_la_th = la.THRESHOLDS_PATH

        tot.LEARNING_DB = os.path.join(self.tmpdir, 'learning.db')
        tot.VALIDATOR_DB = os.path.join(self.tmpdir, 'validator.db')
        ap.LEARNING_DB = os.path.join(self.tmpdir, 'learning.db')
        ap.THRESHOLDS_PATH = os.path.join(self.tmpdir, 'thresholds.json')
        ru.LEARNING_DB = os.path.join(self.tmpdir, 'learning.db')
        la.LEARNING_DB = os.path.join(self.tmpdir, 'learning.db')
        la.THRESHOLDS_PATH = os.path.join(self.tmpdir, 'thresholds.json')

    def tearDown(self):
        import trade_outcome_tracker as tot
        import adaptive_params as ap
        import rag_updater as ru
        import learning_agent as la
        tot.LEARNING_DB = self._orig_tot_db
        tot.VALIDATOR_DB = self._orig_tot_vdb
        ap.LEARNING_DB = self._orig_ap_db
        ap.THRESHOLDS_PATH = self._orig_ap_th
        ru.LEARNING_DB = self._orig_ru_db
        la.LEARNING_DB = self._orig_la_db
        la.THRESHOLDS_PATH = self._orig_la_th
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_learning_agent_imports(self):
        import learning_agent
        self.assertTrue(hasattr(learning_agent, 'LearningAgent'))
        self.assertTrue(hasattr(learning_agent, 'main'))

    def test_learning_agent_init(self):
        import learning_agent
        agent = learning_agent.LearningAgent()
        self.assertIsNotNone(agent.tracker)
        self.assertIsNotNone(agent.learner)
        self.assertEqual(agent._cycle, 0)
        self.assertEqual(agent._total_outcomes, 0)

    def test_run_once_no_crash(self):
        import learning_agent
        agent = learning_agent.LearningAgent()
        result = agent.run_once()
        self.assertIsInstance(result, int)

    def test_run_status_no_crash(self):
        import learning_agent
        agent = learning_agent.LearningAgent()
        agent.run_status()

    def test_run_report_no_crash(self):
        import learning_agent
        agent = learning_agent.LearningAgent()
        agent.run_report()


class TestValidatorOutcomeEndpoint(unittest.TestCase):
    """Tests for the new /trade_outcome and /learning_status endpoints."""

    @classmethod
    def setUpClass(cls):
        os.environ['TESTING'] = '1'
        import validator
        validator.init_database()
        cls.app = validator.app.test_client()

    def test_trade_outcome_win(self):
        resp = self.app.post('/trade_outcome', json={
            'symbol': 'MES', 'result': 'win', 'pnl': 50.0, 'strategy': 'TEST'
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data['status'], 'recorded')
        self.assertEqual(data['result'], 'win')

    def test_trade_outcome_loss(self):
        resp = self.app.post('/trade_outcome', json={
            'symbol': 'MNQ', 'result': 'loss', 'pnl': -25.0, 'strategy': 'TEST'
        })
        self.assertEqual(resp.status_code, 200)

    def test_learning_status(self):
        resp = self.app.get('/learning_status')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn('daily_stats', data)
        self.assertIn('open_positions', data)
        self.assertIn('strategy_performance', data)


class TestEndToEndLearningLoop(unittest.TestCase):
    """Integration test: trade → outcome → learn → thresholds → RAG."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.learning_db = os.path.join(self.tmpdir, 'test_learning.db')
        self.thresholds_path = os.path.join(self.tmpdir, 'learned_thresholds.json')
        self.rag_db = os.path.join(self.tmpdir, 'test_rag.db')

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_full_loop(self):
        import trade_outcome_tracker as tot
        import adaptive_params as ap
        import rag_updater as ru

        tot.LEARNING_DB = self.learning_db
        ap.LEARNING_DB = self.learning_db
        ap.THRESHOLDS_PATH = self.thresholds_path
        ru.LEARNING_DB = self.learning_db
        ru.RAG_DB_PATH = self.rag_db

        tracker = tot.TradeOutcomeTracker()

        for i in range(8):
            tracker.process_closed_trade({
                'trade_id': f'e2e_{i}',
                'instrument': 'MGC',
                'direction': 'LONG',
                'pnl': 80.0 if i < 6 else -40.0,
                'entry_hour': 10,
                'strategy': 'TREND_PULLBACK',
                'quality_score': 85,
                'adx': 30.0,
            })

        thresholds = ap.write_learned_thresholds()
        self.assertIn('strategy_thresholds', thresholds)

        rag_count = ru.update_all()
        self.assertGreater(rag_count, 0)

        self.assertTrue(os.path.exists(self.thresholds_path))
        with open(self.thresholds_path) as f:
            data = json.load(f)
        self.assertIn('updated_at', data)

        conn = sqlite3.connect(self.rag_db)
        c = conn.cursor()
        c.execute("SELECT * FROM strategy_context WHERE strategy='TREND_PULLBACK'")
        row = c.fetchone()
        conn.close()
        self.assertIsNotNone(row)

        summary = tracker.get_summary()
        self.assertEqual(summary['total_trades'], 8)
        self.assertEqual(summary['wins'], 6)
        self.assertAlmostEqual(summary['win_rate'], 0.75)


if __name__ == '__main__':
    unittest.main()
