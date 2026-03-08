#!/usr/bin/env python3
"""Tests for Phase 2: Chart Vision AI, Advanced Patterns, Enhanced Signal Filter."""

import os
import sys
import unittest
import numpy as np
import pandas as pd

os.environ.setdefault('TESTING', '1')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _make_trending_df(n=80, start=5000, direction='up', volatility=2.0):
    """Generate a trending OHLCV DataFrame for testing."""
    np.random.seed(42)
    prices = [start]
    for _ in range(n - 1):
        delta = volatility * (0.6 if direction == 'up' else -0.6) + np.random.randn() * volatility
        prices.append(prices[-1] + delta)
    closes = np.array(prices)
    highs = closes + np.abs(np.random.randn(n)) * volatility
    lows = closes - np.abs(np.random.randn(n)) * volatility
    opens = closes + np.random.randn(n) * volatility * 0.3
    volumes = np.random.randint(100, 1000, n).astype(float)
    return pd.DataFrame({'Open': opens, 'High': highs, 'Low': lows,
                          'Close': closes, 'Volume': volumes})


def _make_ranging_df(n=80, center=5000, range_size=10):
    np.random.seed(42)
    closes = center + np.random.randn(n) * range_size * 0.3
    highs = closes + np.abs(np.random.randn(n)) * 2
    lows = closes - np.abs(np.random.randn(n)) * 2
    opens = closes + np.random.randn(n) * 1
    volumes = np.random.randint(50, 500, n).astype(float)
    return pd.DataFrame({'Open': opens, 'High': highs, 'Low': lows,
                          'Close': closes, 'Volume': volumes})


class TestAdvancedPatterns(unittest.TestCase):

    def test_compute_vwap(self):
        from advanced_patterns import compute_vwap
        df = _make_trending_df()
        vwap = compute_vwap(df)
        self.assertEqual(len(vwap), len(df))
        self.assertFalse(vwap.isna().all())

    def test_compute_vwap_bands(self):
        from advanced_patterns import compute_vwap_bands
        df = _make_trending_df()
        vwap, upper, lower = compute_vwap_bands(df)
        self.assertTrue((upper >= vwap).all())
        self.assertTrue((lower <= vwap).all())

    def test_compute_volume_delta(self):
        from advanced_patterns import compute_volume_delta
        df = _make_trending_df()
        delta = compute_volume_delta(df)
        self.assertEqual(len(delta), len(df))

    def test_compute_order_flow_imbalance(self):
        from advanced_patterns import compute_order_flow_imbalance
        df = _make_trending_df()
        ofi = compute_order_flow_imbalance(df)
        valid = ofi.dropna()
        self.assertTrue((valid >= 0).all() and (valid <= 1).all())

    def test_find_sr_levels(self):
        from advanced_patterns import find_sr_levels
        df = _make_trending_df(n=100)
        support, resistance = find_sr_levels(df)
        self.assertIsInstance(support, list)
        self.assertIsInstance(resistance, list)

    def test_detect_liquidity_sweep_returns_none_or_signal(self):
        from advanced_patterns import detect_liquidity_sweep
        df = _make_trending_df()
        result = detect_liquidity_sweep(df, 5.0)
        if result:
            self.assertIn(result.signal, ('LONG', 'SHORT'))
            self.assertGreater(result.confidence, 0)

    def test_detect_vwap_reclaim(self):
        from advanced_patterns import detect_vwap_reclaim
        df = _make_trending_df()
        result = detect_vwap_reclaim(df, 5.0)
        if result:
            self.assertIn(result.signal, ('LONG', 'SHORT'))

    def test_detect_fake_breakout(self):
        from advanced_patterns import detect_fake_breakout
        df = _make_trending_df(n=50)
        result = detect_fake_breakout(df, 5.0)
        if result:
            self.assertEqual(result.pattern, 'FAKE_BREAKOUT')

    def test_detect_wedge(self):
        from advanced_patterns import detect_wedge
        df = _make_ranging_df(n=40, range_size=5)
        result = detect_wedge(df, 3.0)
        if result:
            self.assertEqual(result.pattern, 'WEDGE_BREAKOUT')

    def test_detect_sr_bounce(self):
        from advanced_patterns import detect_sr_bounce
        df = _make_trending_df(n=50)
        result = detect_sr_bounce(df, 5.0)
        if result:
            self.assertIn(result.signal, ('LONG', 'SHORT'))

    def test_detect_volume_divergence(self):
        from advanced_patterns import detect_volume_divergence
        df = _make_trending_df()
        result = detect_volume_divergence(df, 5.0)
        if result:
            self.assertEqual(result.pattern, 'VOLUME_DIVERGENCE')

    def test_scan_advanced_patterns(self):
        from advanced_patterns import scan_advanced_patterns
        df_1m = _make_trending_df(n=80, direction='up')
        df_5m = _make_trending_df(n=80, direction='up')
        df_15m = _make_trending_df(n=80, direction='up')
        results = scan_advanced_patterns('MES', df_1m, df_5m, df_15m)
        self.assertIsInstance(results, list)

    def test_pattern_signal_dataclass(self):
        from advanced_patterns import PatternSignal
        sig = PatternSignal(
            pattern='TEST', signal='LONG', confidence=80,
            entry_price=5100, stop_loss=5090, take_profit=5120,
            reason='test signal'
        )
        self.assertEqual(sig.pattern, 'TEST')
        self.assertEqual(sig.signal, 'LONG')


class TestChartVisionAI(unittest.TestCase):

    def test_import(self):
        from chart_vision_ai import ChartVisionAI, compute_vwap, compute_support_resistance
        self.assertTrue(callable(ChartVisionAI))

    def test_compute_vwap(self):
        from chart_vision_ai import compute_vwap
        df = _make_trending_df()
        vwap = compute_vwap(df)
        self.assertEqual(len(vwap), len(df))

    def test_compute_sr(self):
        from chart_vision_ai import compute_support_resistance
        df = _make_trending_df(n=80)
        support, resistance = compute_support_resistance(df)
        self.assertIsInstance(support, list)
        self.assertIsInstance(resistance, list)

    def test_detect_liquidity_sweep(self):
        from chart_vision_ai import detect_liquidity_sweep
        df = _make_trending_df()
        result = detect_liquidity_sweep(df)
        # May or may not find a sweep in synthetic data

    def test_render_chart(self):
        from chart_vision_ai import render_chart, MPL_AVAILABLE
        if not MPL_AVAILABLE:
            self.skipTest("matplotlib not installed")
        df = _make_trending_df(n=40)
        vwap = df['Close'].rolling(10).mean()
        img = render_chart(df, 'MES', '5m', vwap=vwap)
        self.assertIsInstance(img, bytes)
        self.assertGreater(len(img), 1000)

    def test_vision_init_no_key(self):
        from chart_vision_ai import ChartVisionAI
        vision = ChartVisionAI(api_key='fake')
        self.assertFalse(vision.is_available())  # 'fake' is < 10 chars

    def test_get_stats(self):
        from chart_vision_ai import ChartVisionAI
        vision = ChartVisionAI(api_key='')
        stats = vision.get_stats()
        self.assertIn('available', stats)
        self.assertIn('calls', stats)


class TestEnhancedSignalFilter(unittest.TestCase):

    def test_import(self):
        from enhanced_signal_filter import EnhancedSignalFilter, FilterDecision
        esf = EnhancedSignalFilter()
        self.assertIsNotNone(esf)

    def test_high_quality_signal_approved(self):
        from enhanced_signal_filter import EnhancedSignalFilter
        esf = EnhancedSignalFilter()
        signal = {
            'action': 'LONG', 'quality_score': 90, 'pattern': 'TREND_PULLBACK',
            'adx': 35, 'volume_ratio': 1.8, 'mtf_alignment': 3, 'chop_index': 35,
        }
        decision = esf.evaluate(signal)
        self.assertTrue(decision.approved)
        self.assertGreater(decision.confluence_score, 60)

    def test_low_quality_signal_rejected(self):
        from enhanced_signal_filter import EnhancedSignalFilter
        esf = EnhancedSignalFilter()
        signal = {
            'action': 'LONG', 'quality_score': 50, 'pattern': 'ema_crossover',
            'adx': 15, 'volume_ratio': 0.8, 'mtf_alignment': 1, 'chop_index': 65,
        }
        decision = esf.evaluate(signal)
        self.assertFalse(decision.approved)

    def test_breakout_in_choppy_allowed(self):
        from enhanced_signal_filter import EnhancedSignalFilter
        esf = EnhancedSignalFilter()
        signal = {
            'action': 'LONG', 'quality_score': 85, 'pattern': 'BREAKOUT',
            'adx': 20, 'volume_ratio': 2.0, 'mtf_alignment': 3, 'chop_index': 65,
        }
        decision = esf.evaluate(signal)
        self.assertTrue(decision.approved)

    def test_vision_contradiction_rejects(self):
        from enhanced_signal_filter import EnhancedSignalFilter
        esf = EnhancedSignalFilter()
        signal = {
            'action': 'LONG', 'quality_score': 85, 'pattern': 'TREND_PULLBACK',
            'adx': 30, 'volume_ratio': 1.5, 'mtf_alignment': 3, 'chop_index': 40,
        }
        vision = {'signal': 'SHORT', 'confidence': 0.85, 'patterns': ['BEARISH']}
        decision = esf.evaluate(signal, vision_result=vision)
        self.assertLess(decision.confluence_score, 70)

    def test_vision_confirmation_boosts(self):
        from enhanced_signal_filter import EnhancedSignalFilter
        esf = EnhancedSignalFilter()
        signal = {
            'action': 'LONG', 'quality_score': 82, 'pattern': 'TREND_PULLBACK',
            'adx': 30, 'volume_ratio': 1.5, 'mtf_alignment': 3, 'chop_index': 40,
        }
        no_vision = esf.evaluate(signal)
        vision = {'signal': 'LONG', 'confidence': 0.85, 'patterns': ['TREND_CONTINUATION']}
        with_vision = esf.evaluate(signal, vision_result=vision)
        self.assertGreater(with_vision.confluence_score, no_vision.confluence_score)

    def test_size_multiplier_scales_with_score(self):
        from enhanced_signal_filter import EnhancedSignalFilter
        esf = EnhancedSignalFilter()
        strong = {
            'action': 'LONG', 'quality_score': 95, 'adx': 40,
            'volume_ratio': 2.5, 'mtf_alignment': 3, 'chop_index': 30, 'pattern': 'MOMENTUM',
        }
        decision = esf.evaluate(strong)
        self.assertGreaterEqual(decision.adjustments.get('size_multiplier', 1), 1.25)

    def test_df_entry_vwap_scoring(self):
        from enhanced_signal_filter import EnhancedSignalFilter
        esf = EnhancedSignalFilter()
        df = _make_trending_df(n=30, direction='up')
        signal = {
            'action': 'LONG', 'quality_score': 85, 'adx': 30,
            'volume_ratio': 1.5, 'mtf_alignment': 3, 'chop_index': 40, 'pattern': 'TREND',
        }
        decision = esf.evaluate(signal, df_entry=df)
        self.assertIn('vwap', decision.adjustments)

    def test_get_stats(self):
        from enhanced_signal_filter import EnhancedSignalFilter
        esf = EnhancedSignalFilter()
        esf.evaluate({'action': 'LONG', 'quality_score': 90, 'adx': 35,
                      'volume_ratio': 2.0, 'mtf_alignment': 3, 'chop_index': 35, 'pattern': 'X'})
        stats = esf.get_stats()
        self.assertEqual(stats['evaluated'], 1)


if __name__ == '__main__':
    unittest.main()
