#!/usr/bin/env python3
"""
Tests for prop-firm stop-loss capping and market-condition validation.

Reproduces the exact scenarios from production logs where MGC signals were
blocked with "STOP TOO WIDE: 20.00 pts ($200) — MAX: 1.8 pts ($18)".
"""

import json
import os
import sys
import sqlite3
import unittest
from unittest.mock import patch
from datetime import datetime

os.environ.setdefault('TESTING', '1')

sys.path.insert(0, os.path.dirname(__file__))

from validator_market_conditions import (
    PROP_FIRM_LIMITS,
    round_to_tick,
    validate_stop_loss,
    validate_market_conditions,
)
from ultimate_entry_validator import (
    app,
    validate_risk_management,
    init_database,
    DB_PATH,
)


class TestRoundToTick(unittest.TestCase):

    def test_mgc_tick(self):
        self.assertAlmostEqual(round_to_tick(2890.53, 0.10), 2890.5)
        self.assertAlmostEqual(round_to_tick(2890.55, 0.10), 2890.6)

    def test_mcl_tick(self):
        self.assertAlmostEqual(round_to_tick(68.123, 0.01), 68.12)
        self.assertAlmostEqual(round_to_tick(68.126, 0.01), 68.13)

    def test_mym_tick(self):
        self.assertAlmostEqual(round_to_tick(39421.3, 1.0), 39421.0)
        self.assertAlmostEqual(round_to_tick(39421.6, 1.0), 39422.0)

    def test_mes_tick(self):
        self.assertAlmostEqual(round_to_tick(5432.10, 0.25), 5432.0)
        self.assertAlmostEqual(round_to_tick(5432.13, 0.25), 5432.25)


class TestPropFirmLimitsConfig(unittest.TestCase):

    def test_all_instruments_present(self):
        for sym in ('MES', 'MNQ', 'MGC', 'MCL', 'MYM', 'M2K'):
            self.assertIn(sym, PROP_FIRM_LIMITS)

    def test_max_risk_dollars(self):
        """Max risk per instrument should be $15-$20."""
        for sym, cfg in PROP_FIRM_LIMITS.items():
            max_risk = cfg['max_stop_pts'] * cfg['point_value']
            self.assertGreaterEqual(max_risk, 15.0, f"{sym} max risk ${max_risk} < $15")
            self.assertLessEqual(max_risk, 20.0, f"{sym} max risk ${max_risk} > $20")

    def test_target_rr_is_2(self):
        for sym, cfg in PROP_FIRM_LIMITS.items():
            self.assertEqual(cfg['target_rr'], 2.0, f"{sym} target R:R != 2.0")


class TestValidateStopLoss(unittest.TestCase):

    def test_mgc_stop_too_wide(self):
        """Reproduces exact production failure: MGC 20pt stop should be blocked."""
        alert = {
            'ticker': 'MGC', 'action': 'SHORT',
            'entry': 2890.0, 'stop_loss': 2910.0,
        }
        ok, msg = validate_stop_loss(alert)
        self.assertFalse(ok)
        self.assertIn('STOP TOO WIDE', msg)
        self.assertIn('20.00 pts', msg)
        self.assertIn('1.8 pts', msg)

    def test_mgc_stop_within_limits(self):
        """MGC with 1.8pt stop should pass."""
        alert = {
            'ticker': 'MGC', 'action': 'SHORT',
            'entry': 2890.0, 'stop_loss': 2891.8,
        }
        ok, msg = validate_stop_loss(alert)
        self.assertTrue(ok)

    def test_mcl_stop_too_wide(self):
        alert = {
            'ticker': 'MCL', 'action': 'LONG',
            'entry': 68.50, 'stop_loss': 68.00,
        }
        ok, msg = validate_stop_loss(alert)
        self.assertFalse(ok)
        self.assertIn('STOP TOO WIDE', msg)

    def test_mcl_stop_within_limits(self):
        alert = {
            'ticker': 'MCL', 'action': 'LONG',
            'entry': 68.50, 'stop_loss': 68.35,
        }
        ok, msg = validate_stop_loss(alert)
        self.assertTrue(ok)

    def test_mes_stop_within_limits(self):
        alert = {
            'ticker': 'MES', 'action': 'LONG',
            'entry': 5400.00, 'stop_loss': 5396.00,
        }
        ok, msg = validate_stop_loss(alert)
        self.assertTrue(ok)

    def test_no_entry_passes(self):
        alert = {'ticker': 'MGC', 'action': 'SHORT'}
        ok, msg = validate_stop_loss(alert)
        self.assertTrue(ok)


class TestValidateRiskManagementCapsStops(unittest.TestCase):
    """Core fix: validate_risk_management must cap stops at prop firm limits."""

    def test_mgc_short_atr20_caps_to_1_8(self):
        """
        Exact reproduction of production bug:
        MGC SHORT, ATR=20 → raw SL would be 30pts.
        After capping, SL should be 1.8pts, TP should be 3.6pts.
        """
        alert = {
            'strategy': 'MGC-10M', 'ticker': 'MGC', 'action': 'SHORT',
            'entry': 2890.0, 'atr': 20.0,
        }
        ok, updated, reason = validate_risk_management(alert)
        self.assertTrue(ok, f"Should pass after capping, got: {reason}")

        stop_dist = abs(updated['stop_loss'] - 2890.0)
        self.assertAlmostEqual(stop_dist, 1.8, places=1,
                               msg=f"Stop should be capped to 1.8pts, got {stop_dist:.2f}")

        tp_dist = abs(2890.0 - updated['take_profit'])
        self.assertAlmostEqual(tp_dist, 3.6, places=1,
                               msg=f"TP should be 3.6pts (2x1.8), got {tp_dist:.2f}")

        ok2, msg2 = validate_stop_loss(updated)
        self.assertTrue(ok2, f"Capped stop should pass validate_stop_loss: {msg2}")

    def test_mcl_long_atr_caps_to_0_15(self):
        alert = {
            'strategy': 'MCL-5M', 'ticker': 'MCL', 'action': 'LONG',
            'entry': 68.50, 'atr': 0.50,
        }
        ok, updated, reason = validate_risk_management(alert)
        self.assertTrue(ok, f"Should pass after capping, got: {reason}")

        stop_dist = abs(updated['entry'] - updated['stop_loss'])
        self.assertAlmostEqual(stop_dist, 0.15, places=2,
                               msg=f"MCL stop should be 0.15pts, got {stop_dist}")

        tp_dist = abs(updated['take_profit'] - updated['entry'])
        self.assertAlmostEqual(tp_dist, 0.30, places=2,
                               msg=f"MCL TP should be 0.30pts (2x0.15), got {tp_dist}")

        ok2, msg2 = validate_stop_loss(updated)
        self.assertTrue(ok2, f"Capped MCL stop should pass: {msg2}")

    def test_mnq_within_limits_not_capped(self):
        """MNQ with 5pt ATR → 7.5pt raw stop, under 9pt max → no capping."""
        alert = {
            'strategy': 'PT-TL5', 'ticker': 'MNQ', 'action': 'LONG',
            'entry': 20000.0, 'atr': 5.0,
        }
        ok, updated, reason = validate_risk_management(alert)
        self.assertTrue(ok)

        stop_dist = abs(updated['entry'] - updated['stop_loss'])
        self.assertAlmostEqual(stop_dist, 7.5, places=1,
                               msg="MNQ 7.5pt stop should NOT be capped (under 9pt max)")

    def test_mym_large_atr_caps(self):
        alert = {
            'strategy': 'MYM-5M', 'ticker': 'MYM', 'action': 'SHORT',
            'entry': 39000.0, 'atr': 50.0,
        }
        ok, updated, reason = validate_risk_management(alert)
        self.assertTrue(ok)

        stop_dist = abs(updated['stop_loss'] - 39000.0)
        self.assertAlmostEqual(stop_dist, 40.0, places=0,
                               msg=f"MYM stop should be capped to 40pts, got {stop_dist}")

    def test_mes_moderate_atr_caps(self):
        """MES with ATR=10 → raw SL=15pts, capped to 4pts."""
        alert = {
            'strategy': 'TL40', 'ticker': 'MES', 'action': 'LONG',
            'entry': 5400.0, 'atr': 10.0,
        }
        ok, updated, reason = validate_risk_management(alert)
        self.assertTrue(ok)

        stop_dist = abs(updated['entry'] - updated['stop_loss'])
        self.assertAlmostEqual(stop_dist, 4.0, places=1,
                               msg=f"MES stop should be capped to 4pts, got {stop_dist}")


class TestValidateMarketConditions(unittest.TestCase):

    def test_trending_market_passes(self):
        alert = {'ticker': 'MGC', 'strategy': 'MGC-10M', 'adx': 44.7, 'chop_index': 50.0}
        ok, msg = validate_market_conditions(alert)
        self.assertTrue(ok, msg)

    def test_low_adx_blocks(self):
        alert = {'ticker': 'MGC', 'strategy': 'MGC-10M', 'adx': 15.0, 'chop_index': 50.0}
        ok, msg = validate_market_conditions(alert)
        self.assertFalse(ok)
        self.assertIn('RANGING', msg)

    def test_high_chop_blocks(self):
        alert = {'ticker': 'MES', 'strategy': 'TL40', 'adx': 30.0, 'chop_index': 70.0}
        ok, msg = validate_market_conditions(alert)
        self.assertFalse(ok)
        self.assertIn('CHOPPY', msg)

    def test_breakout_exception(self):
        alert = {'ticker': 'MGC', 'strategy': 'ORB-MGC', 'adx': 15.0, 'chop_index': 50.0}
        ok, msg = validate_market_conditions(alert)
        self.assertTrue(ok, "ORB strategy should bypass ranging block")

    def test_no_adx_passes(self):
        alert = {'ticker': 'MGC', 'strategy': 'MGC-10M'}
        ok, msg = validate_market_conditions(alert)
        self.assertTrue(ok)


class TestEndToEndWebhook(unittest.TestCase):
    """Integration test: send the exact payload that was failing in production."""

    @classmethod
    def setUpClass(cls):
        test_db = 'test_trading_performance.db'
        import ultimate_entry_validator as uev
        uev.DB_PATH = test_db
        cls._orig_db = test_db
        init_database()

    @classmethod
    def tearDownClass(cls):
        try:
            os.remove(cls._orig_db)
        except FileNotFoundError:
            pass

    def setUp(self):
        conn = sqlite3.connect(self._orig_db)
        c = conn.cursor()
        c.execute("DELETE FROM open_positions")
        c.execute("DELETE FROM daily_performance")
        c.execute("DELETE FROM todays_trades")
        conn.commit()
        conn.close()

    @patch('ultimate_entry_validator.validate_time_filters', return_value=(True, "Time OK"))
    @patch('requests.post')
    def test_mgc_short_now_passes(self, mock_post, mock_time):
        """The exact signal that was blocked should now pass with capped stops."""
        mock_post.return_value = type('Resp', (), {'status_code': 200, 'text': '{"ok":true}'})()
        payload = {
            'strategy': 'MGC-10M',
            'ticker': 'MGC',
            'action': 'SHORT',
            'entry': 2890.0,
            'atr': 20.0,
            'adx': 44.7,
            'chop_index': 50.0,
            'candle_color': 'red',
            'mtf_alignment': 3,
        }
        with app.test_client() as client:
            resp = client.post('/webhook/tradingview',
                               data=json.dumps(payload),
                               content_type='application/json')
            data = resp.get_json()
            self.assertEqual(data['status'], 'approved',
                             f"Expected approved, got: {data}")
            self.assertAlmostEqual(data['stop_loss'], 2891.8, places=1)
            self.assertAlmostEqual(data['take_profit'], 2886.4, places=1)

    @patch('ultimate_entry_validator.validate_time_filters', return_value=(True, "Time OK"))
    @patch('requests.post')
    def test_mcl_long_capped_and_passes(self, mock_post, mock_time):
        mock_post.return_value = type('Resp', (), {'status_code': 200, 'text': '{"ok":true}'})()
        payload = {
            'strategy': 'MCL-5M',
            'ticker': 'MCL',
            'action': 'LONG',
            'entry': 68.50,
            'atr': 0.50,
            'adx': 35.0,
            'chop_index': 45.0,
            'candle_color': 'green',
            'mtf_alignment': 3,
        }
        with app.test_client() as client:
            resp = client.post('/webhook/tradingview',
                               data=json.dumps(payload),
                               content_type='application/json')
            data = resp.get_json()
            self.assertEqual(data['status'], 'approved',
                             f"Expected approved, got: {data}")
            stop_dist = abs(68.50 - data['stop_loss'])
            self.assertLessEqual(stop_dist, 0.16)


if __name__ == '__main__':
    unittest.main(verbosity=2)
