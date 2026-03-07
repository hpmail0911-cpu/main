#!/usr/bin/env python3
"""
Tests for instrument_config.py — session detection, instrument rules,
quality gates, scan plans, and trading system configurations.
"""

import os
import unittest

os.environ.setdefault('TESTING', '1')

from instrument_config import (
    INSTRUMENTS, SESSIONS,
    get_current_session, get_active_instruments,
    get_instrument_session_config, get_scan_plan,
    is_pattern_allowed, get_quality_gate, get_adx_threshold,
    log_config_summary,
)


class TestInstrumentDefinitions(unittest.TestCase):
    """Verify all 6 instruments are properly configured."""

    REQUIRED_INSTRUMENTS = ['MCL', 'MGC', 'MES', 'MNQ', 'MYM', 'M2K']

    def test_all_instruments_defined(self):
        for inst in self.REQUIRED_INSTRUMENTS:
            self.assertIn(inst, INSTRUMENTS, f"{inst} missing from INSTRUMENTS")

    def test_all_have_risk_params(self):
        for inst in self.REQUIRED_INSTRUMENTS:
            cfg = INSTRUMENTS[inst]
            self.assertIn('max_stop_pts', cfg)
            self.assertIn('dollar_per_pt', cfg)
            self.assertIn('tick_size', cfg)
            self.assertIn('target_rr', cfg)
            self.assertGreater(cfg['max_stop_pts'], 0)
            self.assertGreater(cfg['dollar_per_pt'], 0)

    def test_all_have_sessions(self):
        for inst in self.REQUIRED_INSTRUMENTS:
            cfg = INSTRUMENTS[inst]
            self.assertIn('sessions', cfg)
            self.assertIn('NY', cfg['sessions'])

    def test_all_have_tiers(self):
        tiers = set()
        for inst in self.REQUIRED_INSTRUMENTS:
            tier = INSTRUMENTS[inst]['tier']
            tiers.add(tier)
        self.assertIn('COMMODITY_PRIMARY', tiers)
        self.assertIn('EQUITY_PRIMARY', tiers)
        self.assertIn('EQUITY_SECONDARY', tiers)

    def test_risk_per_contract_reasonable(self):
        """Max risk per trade should be $15-$20 for prop firm."""
        for inst in self.REQUIRED_INSTRUMENTS:
            cfg = INSTRUMENTS[inst]
            risk = cfg['max_stop_pts'] * cfg['dollar_per_pt']
            self.assertLessEqual(risk, 25.0,
                f"{inst}: ${risk:.0f} risk per contract exceeds $25 limit")
            self.assertGreaterEqual(risk, 10.0,
                f"{inst}: ${risk:.0f} risk per contract too low")


class TestCommodityPrimary(unittest.TestCase):
    """MCL and MGC should trade all 4 sessions."""

    def test_mcl_all_sessions_enabled(self):
        for session in ['NY', 'LONDON', 'ASIA', 'UAE']:
            cfg = INSTRUMENTS['MCL']['sessions'][session]
            self.assertTrue(cfg['enabled'],
                f"MCL should be enabled in {session}")

    def test_mgc_all_sessions_enabled(self):
        for session in ['NY', 'LONDON', 'ASIA', 'UAE']:
            cfg = INSTRUMENTS['MGC']['sessions'][session]
            self.assertTrue(cfg['enabled'],
                f"MGC should be enabled in {session}")

    def test_mcl_ny_has_most_timeframes(self):
        ny_tfs = INSTRUMENTS['MCL']['sessions']['NY']['timeframes']
        asia_tfs = INSTRUMENTS['MCL']['sessions']['ASIA']['timeframes']
        self.assertGreaterEqual(len(ny_tfs), len(asia_tfs))

    def test_mcl_higher_adx_than_mgc(self):
        """MCL is choppier — needs higher ADX threshold."""
        mcl_ny = INSTRUMENTS['MCL']['sessions']['NY']['adx_min']
        mgc_ny = INSTRUMENTS['MGC']['sessions']['NY']['adx_min']
        self.assertGreaterEqual(mcl_ny, mgc_ny)

    def test_mgc_london_excellent(self):
        """London is THE gold session — quality gate should be low."""
        london = INSTRUMENTS['MGC']['sessions']['LONDON']
        ny = INSTRUMENTS['MGC']['sessions']['NY']
        self.assertLessEqual(london['quality_min'], ny['quality_min'])

    def test_mcl_asia_tighter_quality(self):
        """Asia session MCL has lower volume — quality should be higher."""
        asia = INSTRUMENTS['MCL']['sessions']['ASIA']
        ny = INSTRUMENTS['MCL']['sessions']['NY']
        self.assertGreaterEqual(asia['quality_min'], ny['quality_min'])


class TestEquityPrimary(unittest.TestCase):
    """MES and MNQ should trade NY + London, not Asia/UAE."""

    def test_mes_ny_enabled(self):
        self.assertTrue(INSTRUMENTS['MES']['sessions']['NY']['enabled'])

    def test_mes_london_enabled(self):
        self.assertTrue(INSTRUMENTS['MES']['sessions']['LONDON']['enabled'])

    def test_mes_asia_disabled(self):
        self.assertFalse(INSTRUMENTS['MES']['sessions']['ASIA'].get('enabled', False))

    def test_mes_uae_disabled(self):
        self.assertFalse(INSTRUMENTS['MES']['sessions']['UAE'].get('enabled', False))

    def test_mnq_ny_enabled(self):
        self.assertTrue(INSTRUMENTS['MNQ']['sessions']['NY']['enabled'])

    def test_mnq_asia_disabled(self):
        self.assertFalse(INSTRUMENTS['MNQ']['sessions']['ASIA'].get('enabled', False))

    def test_mnq_ny_has_fast_timeframes(self):
        """MNQ NY should include 3m and 5m for momentum trading."""
        tfs = INSTRUMENTS['MNQ']['sessions']['NY']['timeframes']
        self.assertIn('5m', tfs)

    def test_equity_ny_fast_expiry(self):
        """Equity positions in NY should have short expiry (30 min)."""
        for inst in ['MES', 'MNQ']:
            expiry = INSTRUMENTS[inst]['sessions']['NY']['position_expiry_min']
            self.assertLessEqual(expiry, 45)


class TestEquitySecondary(unittest.TestCase):
    """MYM and M2K should only trade NY with strict gates."""

    def test_mym_only_ny(self):
        for session in ['LONDON', 'ASIA', 'UAE']:
            enabled = INSTRUMENTS['MYM']['sessions'][session].get('enabled', False)
            self.assertFalse(enabled, f"MYM should be disabled in {session}")
        self.assertTrue(INSTRUMENTS['MYM']['sessions']['NY']['enabled'])

    def test_m2k_only_ny(self):
        for session in ['LONDON', 'ASIA', 'UAE']:
            enabled = INSTRUMENTS['M2K']['sessions'][session].get('enabled', False)
            self.assertFalse(enabled, f"M2K should be disabled in {session}")
        self.assertTrue(INSTRUMENTS['M2K']['sessions']['NY']['enabled'])

    def test_mym_high_adx_threshold(self):
        """MYM (choppy Dow) needs ADX >= 35."""
        adx_min = INSTRUMENTS['MYM']['sessions']['NY']['adx_min']
        self.assertGreaterEqual(adx_min, 35)

    def test_mym_high_quality_gate(self):
        """MYM should have quality gate >= 75."""
        q_min = INSTRUMENTS['MYM']['sessions']['NY']['quality_min']
        self.assertGreaterEqual(q_min, 75)

    def test_m2k_stricter_than_mes(self):
        """M2K should have stricter quality gate than MES."""
        m2k_q = INSTRUMENTS['M2K']['sessions']['NY']['quality_min']
        mes_q = INSTRUMENTS['MES']['sessions']['NY']['quality_min']
        self.assertGreaterEqual(m2k_q, mes_q)

    def test_secondary_max_1_position(self):
        """Secondary equities should have max 1 position."""
        for inst in ['MYM', 'M2K']:
            max_pos = INSTRUMENTS[inst]['sessions']['NY']['max_positions']
            self.assertEqual(max_pos, 1)

    def test_mym_limited_patterns(self):
        """MYM should NOT include EMA_CROSSOVER (too many whipsaws)."""
        patterns = INSTRUMENTS['MYM']['sessions']['NY']['patterns']
        self.assertNotIn('EMA_CROSSOVER', patterns)


class TestSessionDetection(unittest.TestCase):
    def test_get_current_session_returns_string(self):
        session = get_current_session()
        self.assertIn(session, ['NY', 'LONDON', 'ASIA', 'UAE'])

    def test_all_sessions_defined(self):
        for s in ['NY', 'LONDON', 'ASIA', 'UAE']:
            self.assertIn(s, SESSIONS)


class TestActiveInstruments(unittest.TestCase):
    def test_ny_all_6_active(self):
        active = get_active_instruments('NY')
        self.assertEqual(len(active), 6)
        for inst in ['MCL', 'MGC', 'MES', 'MNQ', 'MYM', 'M2K']:
            self.assertIn(inst, active)

    def test_london_4_active(self):
        active = get_active_instruments('LONDON')
        self.assertIn('MCL', active)
        self.assertIn('MGC', active)
        self.assertIn('MES', active)
        self.assertIn('MNQ', active)
        self.assertNotIn('MYM', active)
        self.assertNotIn('M2K', active)

    def test_asia_2_active(self):
        active = get_active_instruments('ASIA')
        self.assertIn('MCL', active)
        self.assertIn('MGC', active)
        self.assertNotIn('MES', active)
        self.assertNotIn('MNQ', active)
        self.assertNotIn('MYM', active)

    def test_uae_2_active(self):
        active = get_active_instruments('UAE')
        self.assertIn('MCL', active)
        self.assertIn('MGC', active)
        self.assertNotIn('MES', active)

    def test_commodities_first_in_order(self):
        """Commodities should be scanned before equities."""
        active = get_active_instruments('NY')
        mcl_idx = active.index('MCL')
        mgc_idx = active.index('MGC')
        mes_idx = active.index('MES')
        mym_idx = active.index('MYM')
        self.assertLess(mcl_idx, mes_idx)
        self.assertLess(mgc_idx, mes_idx)
        self.assertLess(mes_idx, mym_idx)


class TestInstrumentSessionConfig(unittest.TestCase):
    def test_mcl_ny_returns_config(self):
        cfg = get_instrument_session_config('MCL', 'NY')
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg['instrument'], 'MCL')
        self.assertEqual(cfg['session'], 'NY')
        self.assertIn('timeframes', cfg)
        self.assertIn('adx_min', cfg)
        self.assertIn('quality_min', cfg)
        self.assertIn('patterns', cfg)

    def test_mes_asia_returns_none(self):
        cfg = get_instrument_session_config('MES', 'ASIA')
        self.assertIsNone(cfg)

    def test_unknown_instrument_returns_none(self):
        cfg = get_instrument_session_config('XYZ', 'NY')
        self.assertIsNone(cfg)

    def test_config_includes_risk_params(self):
        cfg = get_instrument_session_config('MGC', 'LONDON')
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg['max_stop_pts'], 1.80)
        self.assertEqual(cfg['tick_size'], 0.10)


class TestScanPlan(unittest.TestCase):
    def test_ny_plan_covers_all_instruments(self):
        plan = get_scan_plan('NY')
        instruments = set(p['instrument'] for p in plan)
        self.assertEqual(instruments, {'MCL', 'MGC', 'MES', 'MNQ', 'MYM', 'M2K'})

    def test_asia_plan_only_commodities(self):
        plan = get_scan_plan('ASIA')
        instruments = set(p['instrument'] for p in plan)
        self.assertEqual(instruments, {'MCL', 'MGC'})

    def test_plan_items_have_config(self):
        plan = get_scan_plan('NY')
        for item in plan:
            self.assertIn('instrument', item)
            self.assertIn('timeframe', item)
            self.assertIn('session', item)
            self.assertIn('config', item)

    def test_plan_respects_timeframes(self):
        plan = get_scan_plan('ASIA')
        for item in plan:
            if item['instrument'] == 'MCL':
                asia_tfs = INSTRUMENTS['MCL']['sessions']['ASIA']['timeframes']
                self.assertIn(item['timeframe'], asia_tfs)


class TestPatternAllowed(unittest.TestCase):
    def test_mcl_breakout_allowed_ny(self):
        self.assertTrue(is_pattern_allowed('MCL', 'BREAKOUT', 'NY'))

    def test_mcl_ema_crossover_not_allowed_ny(self):
        """MCL NY does not include EMA_CROSSOVER."""
        patterns = INSTRUMENTS['MCL']['sessions']['NY']['patterns']
        if 'EMA_CROSSOVER' not in patterns:
            self.assertFalse(is_pattern_allowed('MCL', 'EMA_CROSSOVER', 'NY'))

    def test_mgc_all_patterns_ny(self):
        """MGC NY should allow all 6 patterns."""
        for pattern in ['TREND_PULLBACK', 'MOMENTUM_CONT', 'BREAKOUT',
                        'IMPULSE', 'TREND_RESUMPTION', 'EMA_CROSSOVER']:
            self.assertTrue(is_pattern_allowed('MGC', pattern, 'NY'),
                            f"MGC NY should allow {pattern}")

    def test_disabled_instrument_returns_false(self):
        self.assertFalse(is_pattern_allowed('MES', 'BREAKOUT', 'ASIA'))


class TestQualityAndADXGates(unittest.TestCase):
    def test_quality_gate_mcl_asia_high(self):
        q = get_quality_gate('MCL', 'ASIA')
        self.assertGreaterEqual(q, 75)

    def test_quality_gate_mgc_london_lower(self):
        q = get_quality_gate('MGC', 'LONDON')
        self.assertLessEqual(q, 72)

    def test_quality_gate_mym_ny_strict(self):
        q = get_quality_gate('MYM', 'NY')
        self.assertGreaterEqual(q, 75)

    def test_adx_threshold_mym_high(self):
        adx = get_adx_threshold('MYM', 'NY')
        self.assertGreaterEqual(adx, 35)

    def test_adx_threshold_mcl_moderate(self):
        adx = get_adx_threshold('MCL', 'NY')
        self.assertGreaterEqual(adx, 25)

    def test_disabled_instrument_defaults(self):
        q = get_quality_gate('MES', 'ASIA')
        self.assertEqual(q, 80)


class TestConfigSummary(unittest.TestCase):
    def test_log_config_returns_string(self):
        summary = log_config_summary()
        self.assertIsInstance(summary, str)
        self.assertIn('NY', summary)
        self.assertIn('MCL', summary)
        self.assertIn('MGC', summary)


if __name__ == '__main__':
    unittest.main(verbosity=2)
