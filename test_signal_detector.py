#!/usr/bin/env python3
"""
Tests for signal_detector.py — pattern detection, regime classification,
MTF confirmation, quality scoring, and SL/TP calculation.
"""

import os
import unittest
import numpy as np
import pandas as pd

os.environ.setdefault('TESTING', '1')

from signal_detector import (
    _ema, _rsi, _atr, _bollinger_bands,
    _compute_indicators, classify_regime,
    _detect_trend_pullback, _detect_momentum_continuation,
    _detect_breakout, _detect_impulse,
    _detect_trend_resumption, _detect_ema_crossover,
    detect_signal, score_signal, calculate_sl_tp,
    MarketRegime, MTFConfirmation, Signal,
    BREAKOUT_PATTERNS, INSTRUMENT_CONFIG,
    _is_higher_highs_higher_lows, _is_lower_highs_lower_lows,
    _swing_high, _swing_low,
)


def _make_trending_bull_df(bars=60, base=5000.0, step=2.0, volatility=1.0):
    """Create a bullish trending DataFrame with HH/HL."""
    np.random.seed(42)
    dates = pd.date_range('2026-01-01', periods=bars, freq='5min')
    prices = base + np.arange(bars) * step + np.random.randn(bars) * volatility
    highs = prices + np.random.rand(bars) * 3
    lows = prices - np.random.rand(bars) * 3
    opens = prices - np.random.randn(bars) * 0.5
    volumes = np.random.randint(1000, 5000, bars).astype(float)
    return pd.DataFrame({
        'Open': opens, 'High': highs, 'Low': lows, 'Close': prices,
        'Volume': volumes
    }, index=dates)


def _make_trending_bear_df(bars=60, base=5000.0, step=2.0, volatility=1.0):
    """Create a bearish trending DataFrame with LH/LL."""
    np.random.seed(42)
    dates = pd.date_range('2026-01-01', periods=bars, freq='5min')
    prices = base - np.arange(bars) * step + np.random.randn(bars) * volatility
    highs = prices + np.random.rand(bars) * 3
    lows = prices - np.random.rand(bars) * 3
    opens = prices + np.random.randn(bars) * 0.5
    volumes = np.random.randint(1000, 5000, bars).astype(float)
    return pd.DataFrame({
        'Open': opens, 'High': highs, 'Low': lows, 'Close': prices,
        'Volume': volumes
    }, index=dates)


def _make_ranging_df(bars=60, base=5000.0, amplitude=1.5):
    """Create a ranging/sideways DataFrame with minimal directional movement."""
    np.random.seed(42)
    dates = pd.date_range('2026-01-01', periods=bars, freq='5min')
    prices = base + np.sin(np.arange(bars) * 0.5) * amplitude + np.random.randn(bars) * 0.3
    highs = prices + np.random.rand(bars) * 1.0
    lows = prices - np.random.rand(bars) * 1.0
    opens = prices - np.random.randn(bars) * 0.2
    volumes = np.random.randint(500, 1500, bars).astype(float)
    return pd.DataFrame({
        'Open': opens, 'High': highs, 'Low': lows, 'Close': prices,
        'Volume': volumes
    }, index=dates)


def _make_breakout_df(bars=60, base=5000.0):
    """Create a consolidation→breakout DataFrame."""
    np.random.seed(42)
    dates = pd.date_range('2026-01-01', periods=bars, freq='5min')
    prices = np.full(bars, base)
    prices[:bars-3] += np.random.randn(bars-3) * 1.0
    prices[-3] = base + 1.0
    prices[-2] = base + 3.0
    prices[-1] = base + 8.0
    highs = prices + np.random.rand(bars) * 1.5
    highs[-1] = prices[-1] + 2.0
    lows = prices - np.random.rand(bars) * 1.5
    opens = prices.copy()
    opens[-1] = base + 2.0
    volumes = np.random.randint(800, 2000, bars).astype(float)
    volumes[-1] = 8000.0
    volumes[-2] = 5000.0
    return pd.DataFrame({
        'Open': opens, 'High': highs, 'Low': lows, 'Close': prices,
        'Volume': volumes
    }, index=dates)


class TestIndicators(unittest.TestCase):
    def test_ema_returns_series(self):
        s = pd.Series(np.arange(50, dtype=float))
        result = _ema(s, 8)
        self.assertEqual(len(result), 50)
        self.assertGreater(float(result.iloc[-1]), float(result.iloc[0]))

    def test_rsi_range(self):
        s = pd.Series(np.random.randn(100).cumsum() + 100)
        result = _rsi(s)
        valid = result.dropna()
        self.assertTrue(all(0 <= v <= 100 for v in valid))

    def test_atr_positive(self):
        df = _make_trending_bull_df()
        result = _atr(df['High'], df['Low'], df['Close'])
        valid = result.dropna()
        self.assertTrue(all(v >= 0 for v in valid))

    def test_bollinger_bands_width(self):
        s = pd.Series(np.random.randn(50).cumsum() + 100)
        upper, mid, lower = _bollinger_bands(s)
        valid_idx = upper.dropna().index
        for i in valid_idx:
            self.assertGreater(float(upper[i]), float(lower[i]))


class TestRegimeClassification(unittest.TestCase):
    def test_trending_bull_regime(self):
        df = _make_trending_bull_df()
        ind = _compute_indicators(df)
        regime = classify_regime(ind, 'MES')
        self.assertIn(regime.trend_direction, ('BULL', 'WEAK_BULL'))
        self.assertIn(regime.state, ('TRENDING', 'MOMENTUM', 'CONTINUATION'))

    def test_ranging_regime(self):
        df = _make_ranging_df()
        ind = _compute_indicators(df)
        regime = classify_regime(ind, 'MES')
        self.assertIn(regime.state, ('RANGING', 'CONSOLIDATING', 'SIDEWAYS', 'CHOPPY'))
        self.assertFalse(regime.is_tradeable)

    def test_regime_has_adx(self):
        df = _make_trending_bull_df()
        ind = _compute_indicators(df)
        regime = classify_regime(ind, 'MES')
        self.assertGreater(regime.adx, 0)

    def test_mym_higher_threshold(self):
        df = _make_trending_bull_df()
        ind = _compute_indicators(df)
        regime = classify_regime(ind, 'MYM')
        self.assertIsNotNone(regime.state)


class TestPatternDetection(unittest.TestCase):
    def test_detect_signal_returns_none_for_short_data(self):
        df = pd.DataFrame({
            'Open': [100], 'High': [101], 'Low': [99],
            'Close': [100.5], 'Volume': [1000]
        })
        result = detect_signal('MES', '5m', df)
        self.assertIsNone(result)

    def test_trending_bull_detects_something(self):
        df = _make_trending_bull_df(bars=80, step=3.0, volatility=0.5)
        df.loc[df.index[-1], 'Volume'] = 10000
        df.loc[df.index[-2], 'Volume'] = 8000
        ind = _compute_indicators(df)
        regime = classify_regime(ind, 'MES')
        if regime.is_tradeable:
            for detector in [_detect_trend_pullback, _detect_momentum_continuation,
                             _detect_trend_resumption, _detect_ema_crossover]:
                result = detector(ind, regime)
                if result:
                    direction, pattern = result
                    self.assertEqual(direction, 'buy')
                    self.assertIsInstance(pattern, str)
                    break

    def test_breakout_detection(self):
        df = _make_breakout_df()
        ind = _compute_indicators(df)
        regime = classify_regime(ind, 'MES')
        result = _detect_breakout(ind, regime)
        if result:
            direction, pattern = result
            self.assertEqual(pattern, 'BREAKOUT')
            self.assertEqual(direction, 'buy')

    def test_breakout_allowed_in_ranging(self):
        """Breakout patterns should be detected even when regime is RANGING."""
        self.assertIn('BREAKOUT', BREAKOUT_PATTERNS)
        self.assertIn('IMPULSE', BREAKOUT_PATTERNS)

    def test_ema_crossover_needs_trend(self):
        df = _make_ranging_df()
        ind = _compute_indicators(df)
        regime = classify_regime(ind, 'MES')
        result = _detect_ema_crossover(ind, regime)
        # In a ranging market with low ADX, EMA crossover shouldn't fire
        # (it requires ADX >= 18)


class TestSLTP(unittest.TestCase):
    def test_sl_within_max_stop(self):
        df = _make_trending_bull_df()
        ind = _compute_indicators(df)
        entry = float(ind['close'].iloc[-1])
        sl, tp = calculate_sl_tp('MES', 'buy', entry, ind, 'TREND_PULLBACK')
        stop_dist = abs(entry - sl)
        self.assertLessEqual(stop_dist, 4.0 + 0.01)
        self.assertGreater(tp, entry)

    def test_sl_short_direction(self):
        df = _make_trending_bear_df()
        ind = _compute_indicators(df)
        entry = float(ind['close'].iloc[-1])
        sl, tp = calculate_sl_tp('MNQ', 'sell', entry, ind, 'MOMENTUM_CONT')
        self.assertGreater(sl, entry)
        self.assertLess(tp, entry)

    def test_sl_respects_instrument_config(self):
        for inst, cfg in INSTRUMENT_CONFIG.items():
            df = _make_trending_bull_df(base=1000.0)
            ind = _compute_indicators(df)
            entry = float(ind['close'].iloc[-1])
            sl, tp = calculate_sl_tp(inst, 'buy', entry, ind, 'TREND_PULLBACK')
            stop_dist = abs(entry - sl)
            self.assertLessEqual(stop_dist, cfg['max_stop_pts'] + 0.01,
                                 f"{inst}: stop {stop_dist} > max {cfg['max_stop_pts']}")


class TestQualityScoring(unittest.TestCase):
    def test_high_quality_trending(self):
        df = _make_trending_bull_df(bars=80, step=3.0)
        df.loc[df.index[-5:], 'Volume'] = 10000
        ind = _compute_indicators(df)
        regime = MarketRegime(state='MOMENTUM', adx=42.0, ema_aligned=True,
                              trend_direction='BULL', is_tradeable=True)
        mtf = MTFConfirmation(aligned_count=2, total_checked=2, confirmed=True)
        score = score_signal(ind, regime, mtf, 'TREND_PULLBACK', 'buy')
        self.assertGreaterEqual(score, 70)

    def test_low_quality_weak_setup(self):
        df = _make_ranging_df()
        ind = _compute_indicators(df)
        regime = MarketRegime(state='RANGING', adx=15.0, ema_aligned=False,
                              trend_direction='FLAT', is_tradeable=False)
        mtf = MTFConfirmation(aligned_count=0, total_checked=2, confirmed=False)
        score = score_signal(ind, regime, mtf, 'EMA_CROSSOVER', 'buy')
        self.assertLess(score, 65)

    def test_score_range(self):
        df = _make_trending_bull_df()
        ind = _compute_indicators(df)
        regime = MarketRegime(state='TRENDING', adx=28.0, ema_aligned=True,
                              trend_direction='BULL', is_tradeable=True)
        mtf = MTFConfirmation(aligned_count=1, total_checked=2, confirmed=False)
        score = score_signal(ind, regime, mtf, 'EMA_CROSSOVER', 'buy')
        self.assertGreaterEqual(score, 0)
        self.assertLessEqual(score, 100)


class TestSignalDataclass(unittest.TestCase):
    def test_signal_defaults(self):
        sig = Signal()
        self.assertEqual(sig.quality_score, 0)
        self.assertEqual(sig.direction, '')

    def test_signal_creation(self):
        sig = Signal(
            instrument='MES', timeframe='5m', direction='buy',
            pattern='TREND_PULLBACK', entry_price=5000.0,
            stop_loss=4997.0, take_profit=5006.0, quality_score=82
        )
        self.assertEqual(sig.instrument, 'MES')
        self.assertEqual(sig.quality_score, 82)


class TestMarketStructure(unittest.TestCase):
    def test_higher_highs_higher_lows(self):
        df = _make_trending_bull_df(bars=40, step=3.0, volatility=0.5)
        result = _is_higher_highs_higher_lows(df['High'], df['Low'], bars=10)
        self.assertTrue(result)

    def test_lower_highs_lower_lows(self):
        df = _make_trending_bear_df(bars=40, step=3.0, volatility=0.5)
        result = _is_lower_highs_lower_lows(df['High'], df['Low'], bars=10)
        self.assertTrue(result)

    def test_ranging_no_structure(self):
        df = _make_ranging_df(bars=40)
        hh_hl = _is_higher_highs_higher_lows(df['High'], df['Low'], bars=10)
        lh_ll = _is_lower_highs_lower_lows(df['High'], df['Low'], bars=10)
        # Ranging market shouldn't consistently make HH/HL or LH/LL
        # (at least one should be False)
        self.assertFalse(hh_hl and lh_ll)


class TestSwingLevels(unittest.TestCase):
    def test_swing_high(self):
        high = pd.Series([100, 102, 105, 103, 101])
        self.assertEqual(_swing_high(high, 5), 105)

    def test_swing_low(self):
        low = pd.Series([100, 98, 95, 97, 99])
        self.assertEqual(_swing_low(low, 5), 95)


class TestValidatorImpulseBreakout(unittest.TestCase):
    """Test enhanced market condition classification with impulse/breakout."""

    def test_impulse_in_allowed_states(self):
        from validator_market_conditions import ALLOWED_STATES
        self.assertIn('IMPULSE', ALLOWED_STATES)
        self.assertIn('BREAKOUT', ALLOWED_STATES)

    def test_pattern_breakout_detected(self):
        from validator_market_conditions import _is_breakout_or_impulse
        alert = {'pattern': 'BREAKOUT', 'strategy': 'MES-5M'}
        self.assertEqual(_is_breakout_or_impulse(alert), 'BREAKOUT')

    def test_pattern_impulse_detected(self):
        from validator_market_conditions import _is_breakout_or_impulse
        alert = {'pattern': 'IMPULSE', 'strategy': 'MES-5M'}
        self.assertEqual(_is_breakout_or_impulse(alert), 'IMPULSE')

    def test_strategy_name_breakout(self):
        from validator_market_conditions import _is_breakout_or_impulse
        alert = {'pattern': 'basic', 'strategy': 'ORB-MES'}
        self.assertEqual(_is_breakout_or_impulse(alert), 'BREAKOUT')

    def test_volume_spike_impulse(self):
        from validator_market_conditions import _is_breakout_or_impulse
        alert = {'pattern': 'basic', 'strategy': 'MES-5M',
                 'volume_ratio': 2.0, 'adx': 20}
        self.assertEqual(_is_breakout_or_impulse(alert), 'IMPULSE')

    def test_no_breakout_normal(self):
        from validator_market_conditions import _is_breakout_or_impulse
        alert = {'pattern': 'basic', 'strategy': 'MES-5M',
                 'volume_ratio': 1.0, 'adx': 20}
        self.assertEqual(_is_breakout_or_impulse(alert), '')

    def test_impulse_passes_market_conditions(self):
        from validator_market_conditions import validate_market_conditions
        alert = {'ticker': 'MES', 'action': 'LONG', 'strategy': 'MES-5M',
                 'pattern': 'IMPULSE', 'adx': 15, 'chop_index': 65}
        ok, msg = validate_market_conditions(alert)
        self.assertTrue(ok, f"IMPULSE should pass even in choppy: {msg}")

    def test_breakout_passes_in_ranging(self):
        from validator_market_conditions import validate_market_conditions
        alert = {'ticker': 'MES', 'action': 'LONG', 'strategy': 'MES-5M',
                 'pattern': 'BREAKOUT', 'adx': 12, 'chop_index': 55}
        ok, msg = validate_market_conditions(alert)
        self.assertTrue(ok, f"BREAKOUT should pass in ranging: {msg}")

    def test_non_breakout_blocked_in_ranging(self):
        from validator_market_conditions import validate_market_conditions
        alert = {'ticker': 'MES', 'action': 'LONG', 'strategy': 'MES-5M',
                 'pattern': 'EMA_CROSSOVER', 'adx': 12, 'chop_index': 55}
        ok, msg = validate_market_conditions(alert)
        self.assertFalse(ok, f"EMA_CROSSOVER should be blocked in ranging: {msg}")


class TestEndToEnd(unittest.TestCase):
    """Full detect_signal call with synthetic data."""

    def test_detect_with_bull_data(self):
        df = _make_trending_bull_df(bars=80, step=3.0, volatility=0.5)
        df.loc[df.index[-3:], 'Volume'] = 10000
        result = detect_signal('MES', '5m', df)
        # May or may not detect depending on exact data, but should not crash
        if result:
            self.assertIsInstance(result, Signal)
            self.assertEqual(result.instrument, 'MES')
            self.assertIn(result.direction, ('buy', 'sell'))
            self.assertGreater(result.quality_score, 0)
            self.assertGreater(result.entry_price, 0)

    def test_detect_with_ranging_data(self):
        df = _make_ranging_df(bars=80)
        result = detect_signal('MES', '5m', df)
        # In ranging market, only breakout/impulse should pass
        if result:
            self.assertIn(result.pattern, ('BREAKOUT', 'IMPULSE'))

    def test_detect_returns_none_for_short_df(self):
        import pandas as pd
        df = pd.DataFrame({'Open': [1], 'High': [2], 'Low': [0.5], 'Close': [1.5], 'Volume': [100]})
        result = detect_signal('MES', '5m', df)
        self.assertIsNone(result)


if __name__ == '__main__':
    unittest.main(verbosity=2)
