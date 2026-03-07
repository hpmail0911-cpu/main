#!/usr/bin/env python3
"""
Tests for the stop-loss capping fix.

Verifies that:
1. Wide stops are CAPPED (not rejected) at the instrument maximum.
2. TP is recalculated to maintain target R:R after capping.
3. Stops within limits are left untouched.
4. R:R check passes on capped values.
5. Market condition validation works correctly.
"""

import json
import os
import sys
import unittest

os.environ.setdefault('TESTING', '1')

from validator_market_conditions import (
    validate_stop_loss,
    validate_market_conditions,
    get_max_stop_distance,
    INSTRUMENT_CONFIG,
    _snap_to_tick,
)


class TestStopLossCapping(unittest.TestCase):
    """validate_stop_loss should cap wide stops, never reject."""

    def test_mgc_short_20pt_stop_capped_to_1_8(self):
        """The exact scenario from the logs: MGC SHORT with 20pt ATR-based stop."""
        alert = {
            'ticker': 'MGC',
            'action': 'SHORT',
            'entry': 2900.0,
            'stop_loss': 2920.0,
            'take_profit': 2860.0,
            'atr': 20.0,
            'adx': 44.7,
            'chop_index': 50.0,
            'mtf_alignment': 3,
            'candle_color': 'red',
        }
        ok, msg = validate_stop_loss(alert)
        self.assertTrue(ok, f"Should NOT reject — got: {msg}")
        self.assertIn('capped', msg.lower(), f"Should indicate capping — got: {msg}")

        sl = alert['stop_loss']
        tp = alert['take_profit']
        stop_dist = abs(2900.0 - sl)
        self.assertAlmostEqual(stop_dist, 1.8, places=1,
                               msg=f"Stop should be capped to 1.8 pts, got {stop_dist:.2f}")

        tp_dist = abs(2900.0 - tp)
        expected_tp_dist = 1.8 * 2.0
        self.assertAlmostEqual(tp_dist, expected_tp_dist, places=1,
                               msg=f"TP should be {expected_tp_dist} pts, got {tp_dist:.2f}")

    def test_mgc_long_wide_stop_capped(self):
        """MGC LONG with a wide stop gets capped."""
        alert = {
            'ticker': 'MGC',
            'action': 'LONG',
            'entry': 2900.0,
            'stop_loss': 2880.0,
            'take_profit': 2940.0,
        }
        ok, msg = validate_stop_loss(alert)
        self.assertTrue(ok)

        sl = alert['stop_loss']
        stop_dist = abs(2900.0 - sl)
        self.assertAlmostEqual(stop_dist, 1.8, places=1)
        self.assertGreater(sl, 2898.0, "SL should be just below entry for LONG")

    def test_mes_stop_within_limit_unchanged(self):
        """MES with a 3pt stop (under 4pt max) should be untouched."""
        alert = {
            'ticker': 'MES',
            'action': 'LONG',
            'entry': 5000.0,
            'stop_loss': 4997.0,
            'take_profit': 5006.0,
        }
        ok, msg = validate_stop_loss(alert)
        self.assertTrue(ok)
        self.assertNotIn('capped', msg.lower(), f"Should NOT cap — stop is within limit: {msg}")
        self.assertEqual(alert['stop_loss'], 4997.0, "SL should be unchanged")
        self.assertEqual(alert['take_profit'], 5006.0, "TP should be unchanged")

    def test_mnq_wide_stop_capped_to_9(self):
        """MNQ with 15pt stop gets capped to 9pt max."""
        alert = {
            'ticker': 'MNQ',
            'action': 'SHORT',
            'entry': 20000.0,
            'stop_loss': 20015.0,
            'take_profit': 19970.0,
        }
        ok, msg = validate_stop_loss(alert)
        self.assertTrue(ok)

        sl = alert['stop_loss']
        stop_dist = abs(20000.0 - sl)
        self.assertAlmostEqual(stop_dist, 9.0, places=1)

    def test_mcl_wide_stop_capped_to_0_15(self):
        """MCL with 0.50pt stop capped to 0.15pt max."""
        alert = {
            'ticker': 'MCL',
            'action': 'LONG',
            'entry': 70.00,
            'stop_loss': 69.50,
            'take_profit': 71.00,
        }
        ok, msg = validate_stop_loss(alert)
        self.assertTrue(ok)

        sl = alert['stop_loss']
        stop_dist = abs(70.00 - sl)
        self.assertAlmostEqual(stop_dist, 0.15, places=2)

    def test_mym_40pt_stop_at_limit_passes(self):
        """MYM with exactly 40pt stop (the max) should pass without capping."""
        alert = {
            'ticker': 'MYM',
            'action': 'LONG',
            'entry': 40000.0,
            'stop_loss': 39960.0,
            'take_profit': 40080.0,
        }
        ok, msg = validate_stop_loss(alert)
        self.assertTrue(ok)
        self.assertNotIn('capped', msg.lower())

    def test_tick_snapping(self):
        """Capped SL/TP should be snapped to valid tick increments."""
        alert = {
            'ticker': 'MGC',
            'action': 'LONG',
            'entry': 2900.05,
            'stop_loss': 2880.05,
            'take_profit': 2940.05,
        }
        ok, _ = validate_stop_loss(alert)
        self.assertTrue(ok)

        sl = alert['stop_loss']
        tp = alert['take_profit']
        sl_remainder = round(sl % 0.10, 6)
        tp_remainder = round(tp % 0.10, 6)
        self.assertTrue(sl_remainder < 1e-4 or abs(sl_remainder - 0.10) < 1e-4,
                        msg=f"SL {sl} not on 0.10 tick grid (remainder {sl_remainder})")
        self.assertTrue(tp_remainder < 1e-4 or abs(tp_remainder - 0.10) < 1e-4,
                        msg=f"TP {tp} not on 0.10 tick grid (remainder {tp_remainder})")

    def test_no_entry_passes_through(self):
        """Missing entry/stop data should not cause an error."""
        alert = {'ticker': 'MGC', 'action': 'SHORT'}
        ok, msg = validate_stop_loss(alert)
        self.assertTrue(ok)

    def test_unknown_symbol_passes_through(self):
        """Unknown instruments pass without validation."""
        alert = {'ticker': 'XYZ', 'action': 'LONG', 'entry': 100, 'stop_loss': 90}
        ok, msg = validate_stop_loss(alert)
        self.assertTrue(ok)

    def test_get_max_stop_distance(self):
        self.assertEqual(get_max_stop_distance('MGC'), 1.8)
        self.assertEqual(get_max_stop_distance('MNQ'), 9.0)
        self.assertEqual(get_max_stop_distance('MES'), 4.0)
        self.assertEqual(get_max_stop_distance('UNKNOWN'), 5.0)


class TestMarketConditions(unittest.TestCase):
    """validate_market_conditions should allow trending/momentum, block choppy."""

    def _base_alert(self, **overrides):
        d = {'ticker': 'MES', 'action': 'LONG', 'strategy': 'TL33'}
        d.update(overrides)
        return d

    def test_trending_market_passes(self):
        alert = self._base_alert(adx=30, chop_index=45)
        ok, msg = validate_market_conditions(alert)
        self.assertTrue(ok, msg)

    def test_momentum_market_passes(self):
        alert = self._base_alert(adx=40, chop_index=40)
        ok, msg = validate_market_conditions(alert)
        self.assertTrue(ok, msg)

    def test_choppy_market_blocked(self):
        alert = self._base_alert(adx=30, chop_index=65)
        ok, msg = validate_market_conditions(alert)
        self.assertFalse(ok, msg)
        self.assertIn('CHOPPY', msg)

    def test_ranging_market_blocked(self):
        alert = self._base_alert(adx=12, chop_index=50)
        ok, msg = validate_market_conditions(alert)
        self.assertFalse(ok, msg)

    def test_breakout_strategy_bypasses_block(self):
        alert = self._base_alert(strategy='ORB-MGC', adx=12, chop_index=65)
        ok, msg = validate_market_conditions(alert)
        self.assertTrue(ok, msg)
        self.assertIn('BREAKOUT', msg)

    def test_mym_higher_adx_threshold(self):
        """MYM requires ADX >= 35; ADX 30 should be CONSOLIDATING."""
        alert = self._base_alert(ticker='MYM', adx=30, chop_index=50)
        ok, msg = validate_market_conditions(alert)
        self.assertFalse(ok, msg)

    def test_mcl_higher_adx_threshold(self):
        """MCL requires ADX >= 30; ADX 25 should be CONSOLIDATING."""
        alert = self._base_alert(ticker='MCL', adx=25, chop_index=50)
        ok, msg = validate_market_conditions(alert)
        self.assertFalse(ok, msg)


class TestRiskManagementIntegration(unittest.TestCase):
    """End-to-end: validate_risk_management should cap stop and pass R:R."""

    def test_mgc_signal_no_longer_rejected(self):
        """Reproduce the exact failure from the logs — should now pass."""
        from validator import validate_risk_management

        alert = {
            'strategy': 'MGC-10M',
            'ticker': 'MGC',
            'action': 'SHORT',
            'entry': 2900.0,
            'atr': 20.0,
            'adx': 44.7,
            'chop_index': 50.0,
            'mtf_alignment': 3,
            'candle_color': 'red',
        }
        ok, updated_data, reason = validate_risk_management(alert)
        self.assertTrue(ok, f"Should pass risk management — got: {reason}")

        sl = updated_data['stop_loss']
        stop_dist = abs(2900.0 - sl)
        self.assertLessEqual(stop_dist, 1.8 + 0.01,
                             f"Stop should be ≤ 1.8 pts, got {stop_dist:.2f}")

    def test_mnq_signal_caps_at_9pts(self):
        from validator import validate_risk_management

        alert = {
            'strategy': 'TL43',
            'ticker': 'MNQ',
            'action': 'LONG',
            'entry': 20000.0,
            'atr': 50.0,
        }
        ok, updated_data, reason = validate_risk_management(alert)
        self.assertTrue(ok, f"Should pass — got: {reason}")

        sl = updated_data['stop_loss']
        stop_dist = abs(20000.0 - sl)
        self.assertLessEqual(stop_dist, 9.0 + 0.01,
                             f"Stop should be ≤ 9 pts, got {stop_dist:.2f}")


if __name__ == '__main__':
    unittest.main(verbosity=2)
