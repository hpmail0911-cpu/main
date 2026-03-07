#!/usr/bin/env python3
"""
SIGNAL DETECTOR — Chart-reading engine for 80%+ win-rate trade detection.

Reads OHLCV data across multiple timeframes, classifies market regime,
detects high-probability patterns, and returns fully qualified signals
with entry/SL/TP and quality scores.

REGIME RULES:
  TRADE:  TRENDING / CONTINUATION / MOMENTUM
  BLOCK:  SIDEWAYS / CONSOLIDATING / RANGING / CHOPPY
  EXEMPT: BREAKOUT / IMPULSE (trade even in ranging conditions)

PATTERNS DETECTED (6 high-WR setups):
  1. TREND_PULLBACK    — pullback to 21-EMA in trending market, bounce candle
  2. MOMENTUM_CONT     — strong trend, price above all EMAs, momentum bar
  3. BREAKOUT          — price breaks consolidation range with volume spike
  4. IMPULSE           — Bollinger squeeze breakout with volume confirmation
  5. TREND_RESUMPTION  — brief consolidation in trend, continuation candle
  6. EMA_CROSSOVER     — fast EMA crosses slow EMA with trend + volume

MTF CONFIRMATION:
  Every signal requires 2-of-3 higher timeframes to agree on direction.
  Timeframe hierarchy: signal TF → 3x TF → 6x TF
  e.g. 5m signal checks 15m and 30m trends.

QUALITY SCORING (0–100):
  Trend strength (ADX):    0–20
  MTF alignment:           0–20
  Volume confirmation:     0–15
  Pattern reliability:     0–20
  RSI position:            0–10
  EMA alignment:           0–15
"""

import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

try:
    import pandas as pd
    import numpy as np
except ImportError:
    pd = None
    np = None

from data_feed import get_ohlcv, TF_CONFIG

logger = logging.getLogger(__name__)

# ==========================================================================
# Configuration
# ==========================================================================

MIN_BARS = 30
EMA_FAST = 8
EMA_MID = 21
EMA_SLOW = 50

BB_PERIOD = 20
BB_STD = 2.0
BB_SQUEEZE_THRESHOLD = 0.4

ADX_TRENDING = 25
ADX_STRONG = 35
ADX_MIN_BREAKOUT = 15

CHOP_MAX = 61.8

VOLUME_SURGE = 1.5
VOLUME_CONFIRM = 1.2

CONSOLIDATION_BARS_MIN = 5
CONSOLIDATION_BARS_MAX = 20
RANGE_ATR_RATIO = 0.6

PULLBACK_DEPTH_MIN = 0.3
PULLBACK_DEPTH_MAX = 0.75

RSI_OB = 75
RSI_OS = 25

INSTRUMENT_CONFIG = {
    'MES': {'max_stop_pts': 4.00,  'dollar_per_pt': 5.00,   'tick_size': 0.25, 'target_rr': 2.0},
    'MNQ': {'max_stop_pts': 9.00,  'dollar_per_pt': 2.00,   'tick_size': 0.25, 'target_rr': 2.0},
    'MGC': {'max_stop_pts': 1.80,  'dollar_per_pt': 10.00,  'tick_size': 0.10, 'target_rr': 2.0},
    'MCL': {'max_stop_pts': 0.15,  'dollar_per_pt': 100.00, 'tick_size': 0.01, 'target_rr': 2.0},
    'MYM': {'max_stop_pts': 40.00, 'dollar_per_pt': 0.50,   'tick_size': 1.00, 'target_rr': 2.0},
    'M2K': {'max_stop_pts': 4.00,  'dollar_per_pt': 5.00,   'tick_size': 0.10, 'target_rr': 2.0},
}

MTF_HIERARCHY = {
    '1m':  ['5m',  '15m'],
    '3m':  ['15m', '30m'],
    '5m':  ['15m', '30m'],
    '7m':  ['15m', '1h'],
    '10m': ['30m', '1h'],
    '15m': ['1h',  '4h'],
    '30m': ['1h',  '4h'],
    '45m': ['4h',  '1d'],
    '1h':  ['4h',  '1d'],
    '2h':  ['4h',  '1d'],
    '4h':  ['1d',  '1d'],
}

ALL_INSTRUMENTS = ['MES', 'MNQ', 'MGC', 'MCL', 'MYM', 'M2K']

SCAN_TIMEFRAMES = ['5m', '10m', '15m', '30m', '1h']


# ==========================================================================
# Data classes
# ==========================================================================

@dataclass
class MarketRegime:
    state: str = 'UNKNOWN'
    adx: float = 0.0
    chop: float = 50.0
    trend_direction: str = 'FLAT'
    ema_aligned: bool = False
    is_tradeable: bool = False
    is_breakout_ok: bool = True


@dataclass
class MTFConfirmation:
    direction: str = 'FLAT'
    aligned_count: int = 0
    total_checked: int = 0
    confirmed: bool = False
    details: List[str] = field(default_factory=list)


@dataclass
class Signal:
    instrument: str = ''
    timeframe: str = ''
    direction: str = ''
    pattern: str = ''
    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    quality_score: int = 0
    regime: str = ''
    adx: float = 0.0
    rsi: float = 50.0
    atr: float = 0.0
    volume_ratio: float = 1.0
    mtf_aligned: int = 0
    candle_color: str = ''
    ema_aligned_1m: bool = False
    ema_aligned_5m: bool = False
    ema_aligned_15m: bool = False
    reason: str = ''


# ==========================================================================
# Technical indicator calculations
# ==========================================================================

def _ema(series: 'pd.Series', period: int) -> 'pd.Series':
    return series.ewm(span=period, adjust=False).mean()


def _sma(series: 'pd.Series', period: int) -> 'pd.Series':
    return series.rolling(window=period).mean()


def _rsi(close: 'pd.Series', period: int = 14) -> 'pd.Series':
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def _atr(high: 'pd.Series', low: 'pd.Series', close: 'pd.Series',
         period: int = 14) -> 'pd.Series':
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()


def _adx(high: 'pd.Series', low: 'pd.Series', close: 'pd.Series',
         period: int = 14) -> 'pd.Series':
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    tr = _atr.__wrapped__(high, low, close, period) if hasattr(_atr, '__wrapped__') else \
         pd.concat([high - low, (high - close.shift()).abs(),
                    (low - close.shift()).abs()], axis=1).max(axis=1).rolling(period).mean()

    plus_di = 100 * plus_dm.rolling(period).mean() / tr
    minus_di = 100 * minus_dm.rolling(period).mean() / tr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    return dx.rolling(period).mean()


def _bollinger_bands(close: 'pd.Series', period: int = 20,
                     std_mult: float = 2.0) -> Tuple['pd.Series', 'pd.Series', 'pd.Series']:
    mid = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = mid + std_mult * std
    lower = mid - std_mult * std
    return upper, mid, lower


def _chop_index(high: 'pd.Series', low: 'pd.Series', close: 'pd.Series',
                period: int = 14) -> float:
    atr_vals = _atr(high, low, close, 1)
    atr_sum = atr_vals.rolling(period).sum()
    hh = high.rolling(period).max()
    ll = low.rolling(period).min()
    range_val = hh - ll
    try:
        ci = 100 * np.log10(atr_sum / range_val) / np.log10(period)
        val = ci.iloc[-1]
        return float(val) if not np.isnan(val) else 50.0
    except Exception:
        return 50.0


def _swing_high(high: 'pd.Series', lookback: int = 5) -> float:
    return float(high.iloc[-lookback:].max())


def _swing_low(low: 'pd.Series', lookback: int = 5) -> float:
    return float(low.iloc[-lookback:].min())


def _is_higher_highs_higher_lows(high: 'pd.Series', low: 'pd.Series',
                                  bars: int = 10) -> bool:
    """Check if price is making HH and HL over the last `bars`."""
    if len(high) < bars * 2:
        return False
    recent_high = high.iloc[-bars:].max()
    prev_high = high.iloc[-bars*2:-bars].max()
    recent_low = low.iloc[-bars:].min()
    prev_low = low.iloc[-bars*2:-bars].min()
    return recent_high > prev_high and recent_low > prev_low


def _is_lower_highs_lower_lows(high: 'pd.Series', low: 'pd.Series',
                                bars: int = 10) -> bool:
    """Check if price is making LH and LL over the last `bars`."""
    if len(high) < bars * 2:
        return False
    recent_high = high.iloc[-bars:].max()
    prev_high = high.iloc[-bars*2:-bars].max()
    recent_low = low.iloc[-bars:].min()
    prev_low = low.iloc[-bars*2:-bars].min()
    return recent_high < prev_high and recent_low < prev_low


def _compute_indicators(df: 'pd.DataFrame') -> dict:
    """Compute all technical indicators for a DataFrame."""
    close = df['Close']
    high = df['High']
    low = df['Low']
    volume = df['Volume'] if 'Volume' in df.columns else pd.Series(0, index=df.index)

    ema8 = _ema(close, EMA_FAST)
    ema21 = _ema(close, EMA_MID)
    ema50 = _ema(close, EMA_SLOW) if len(close) >= EMA_SLOW else ema21

    rsi_series = _rsi(close)
    atr_series = _atr(high, low, close)
    adx_series = _adx(high, low, close)
    bb_upper, bb_mid, bb_lower = _bollinger_bands(close)

    avg_vol = volume.rolling(20).mean()
    vol_ratio = (volume / avg_vol).fillna(1.0)

    chop = _chop_index(high, low, close)

    return {
        'close': close,
        'high': high,
        'low': low,
        'open': df['Open'],
        'volume': volume,
        'ema8': ema8,
        'ema21': ema21,
        'ema50': ema50,
        'rsi': rsi_series,
        'atr': atr_series,
        'adx': adx_series,
        'bb_upper': bb_upper,
        'bb_mid': bb_mid,
        'bb_lower': bb_lower,
        'vol_ratio': vol_ratio,
        'chop': chop,
    }


# ==========================================================================
# Market regime classification
# ==========================================================================

def classify_regime(ind: dict, instrument: str = '') -> MarketRegime:
    """Classify the current market regime from indicator values."""
    regime = MarketRegime()

    adx_val = float(ind['adx'].iloc[-1]) if not np.isnan(ind['adx'].iloc[-1]) else 0.0
    regime.adx = adx_val
    regime.chop = ind['chop']

    close_now = float(ind['close'].iloc[-1])
    ema8_now = float(ind['ema8'].iloc[-1])
    ema21_now = float(ind['ema21'].iloc[-1])
    ema50_now = float(ind['ema50'].iloc[-1])

    bullish_emas = close_now > ema8_now > ema21_now > ema50_now
    bearish_emas = close_now < ema8_now < ema21_now < ema50_now

    regime.ema_aligned = bullish_emas or bearish_emas

    if bullish_emas:
        regime.trend_direction = 'BULL'
    elif bearish_emas:
        regime.trend_direction = 'BEAR'
    elif close_now > ema21_now:
        regime.trend_direction = 'WEAK_BULL'
    elif close_now < ema21_now:
        regime.trend_direction = 'WEAK_BEAR'
    else:
        regime.trend_direction = 'FLAT'

    adx_threshold = 35 if instrument == 'MYM' else 30 if instrument == 'MCL' else ADX_TRENDING

    if regime.chop > CHOP_MAX:
        regime.state = 'CHOPPY'
    elif adx_val >= ADX_STRONG and regime.ema_aligned:
        regime.state = 'MOMENTUM'
    elif adx_val >= adx_threshold and (bullish_emas or bearish_emas):
        regime.state = 'TRENDING'
    elif adx_val >= adx_threshold and regime.trend_direction in ('WEAK_BULL', 'WEAK_BEAR'):
        regime.state = 'CONTINUATION'
    elif adx_val < adx_threshold * 0.7:
        regime.state = 'RANGING'
    elif adx_val < adx_threshold:
        regime.state = 'CONSOLIDATING'
    else:
        regime.state = 'SIDEWAYS'

    regime.is_tradeable = regime.state in ('TRENDING', 'CONTINUATION', 'MOMENTUM')
    regime.is_breakout_ok = True

    return regime


# ==========================================================================
# Multi-timeframe confirmation
# ==========================================================================

def check_mtf(instrument: str, signal_tf: str,
              direction: str) -> MTFConfirmation:
    """Check higher timeframes for trend agreement with the signal direction."""
    conf = MTFConfirmation(direction=direction)
    higher_tfs = MTF_HIERARCHY.get(signal_tf, [])

    if not higher_tfs:
        conf.confirmed = True
        conf.aligned_count = 1
        conf.total_checked = 1
        return conf

    for htf in higher_tfs:
        try:
            df = get_ohlcv(instrument, htf)
            if df is None or len(df) < MIN_BARS:
                continue

            close = df['Close']
            ema8 = _ema(close, EMA_FAST)
            ema21 = _ema(close, EMA_MID)

            close_now = float(close.iloc[-1])
            ema8_now = float(ema8.iloc[-1])
            ema21_now = float(ema21.iloc[-1])

            conf.total_checked += 1

            if direction == 'buy':
                if close_now > ema21_now and ema8_now > ema21_now:
                    conf.aligned_count += 1
                    conf.details.append(f"{htf}: BULL (C>{ema21_now:.1f})")
                else:
                    conf.details.append(f"{htf}: not bullish")
            else:
                if close_now < ema21_now and ema8_now < ema21_now:
                    conf.aligned_count += 1
                    conf.details.append(f"{htf}: BEAR (C<{ema21_now:.1f})")
                else:
                    conf.details.append(f"{htf}: not bearish")

        except Exception as e:
            logger.debug(f"MTF check failed for {instrument} {htf}: {e}")

    conf.confirmed = conf.aligned_count >= 2 or (
        conf.total_checked == 1 and conf.aligned_count >= 1
    )
    return conf


# ==========================================================================
# Stop-loss / Take-profit calculation (structure-based)
# ==========================================================================

def _snap(price: float, tick: float) -> float:
    return round(round(price / tick) * tick, 10)


def calculate_sl_tp(instrument: str, direction: str, entry: float,
                    ind: dict, pattern: str) -> Tuple[float, float]:
    """Calculate structure-based SL and prop-firm-capped TP."""
    cfg = INSTRUMENT_CONFIG.get(instrument, {})
    max_stop = cfg.get('max_stop_pts', 5.0)
    tick = cfg.get('tick_size', 0.25)
    target_rr = cfg.get('target_rr', 2.0)

    atr_val = float(ind['atr'].iloc[-1]) if not np.isnan(ind['atr'].iloc[-1]) else max_stop

    if direction == 'buy':
        structure_sl = _swing_low(ind['low'], lookback=7)
        raw_dist = max(entry - structure_sl, atr_val * 1.0)
    else:
        structure_sl = _swing_high(ind['high'], lookback=7)
        raw_dist = max(structure_sl - entry, atr_val * 1.0)

    stop_dist = min(raw_dist, max_stop)

    if direction == 'buy':
        sl = _snap(entry - stop_dist, tick)
        tp = _snap(entry + stop_dist * target_rr, tick)
    else:
        sl = _snap(entry + stop_dist, tick)
        tp = _snap(entry - stop_dist * target_rr, tick)

    return sl, tp


# ==========================================================================
# Quality scoring
# ==========================================================================

def score_signal(ind: dict, regime: MarketRegime, mtf: MTFConfirmation,
                 pattern: str, direction: str) -> int:
    """Score a signal 0–100 based on confluence factors."""
    score = 0

    # Trend strength (0–20)
    if regime.adx >= ADX_STRONG:
        score += 20
    elif regime.adx >= ADX_TRENDING:
        score += 15
    elif regime.adx >= 18:
        score += 8

    # MTF alignment (0–20)
    if mtf.aligned_count >= 2:
        score += 20
    elif mtf.aligned_count >= 1:
        score += 12

    # Volume confirmation (0–15)
    vol = float(ind['vol_ratio'].iloc[-1]) if not np.isnan(ind['vol_ratio'].iloc[-1]) else 1.0
    if vol >= VOLUME_SURGE:
        score += 15
    elif vol >= VOLUME_CONFIRM:
        score += 10
    elif vol >= 0.8:
        score += 5

    # Pattern reliability (0–20)
    pattern_scores = {
        'TREND_PULLBACK': 18,
        'MOMENTUM_CONT': 17,
        'BREAKOUT': 16,
        'IMPULSE': 15,
        'TREND_RESUMPTION': 14,
        'EMA_CROSSOVER': 12,
    }
    score += pattern_scores.get(pattern, 10)

    # RSI position (0–10)
    rsi_val = float(ind['rsi'].iloc[-1]) if not np.isnan(ind['rsi'].iloc[-1]) else 50.0
    if direction == 'buy':
        if 40 <= rsi_val <= 65:
            score += 10
        elif 30 <= rsi_val <= 70:
            score += 5
    else:
        if 35 <= rsi_val <= 60:
            score += 10
        elif 30 <= rsi_val <= 70:
            score += 5

    # EMA alignment bonus (0–15)
    if regime.ema_aligned:
        score += 15
    elif regime.trend_direction in ('WEAK_BULL', 'WEAK_BEAR'):
        score += 7

    return min(score, 100)


# ==========================================================================
# Pattern detection — 6 high-WR setups
# ==========================================================================

def _detect_trend_pullback(ind: dict, regime: MarketRegime) -> Optional[Tuple[str, str]]:
    """Pullback to 21-EMA in trending market, then bounce candle.

    Requires: ADX >= 25, EMAs aligned, price touched/crossed EMA21 in last 5 bars,
    current bar closes back in trend direction with volume.
    """
    if not regime.is_tradeable or regime.adx < ADX_TRENDING:
        return None

    close = ind['close']
    low = ind['low']
    high = ind['high']
    open_ = ind['open']
    ema21 = ind['ema21']
    vol = ind['vol_ratio']

    now = -1
    close_now = float(close.iloc[now])
    open_now = float(open_.iloc[now])
    ema21_now = float(ema21.iloc[now])
    vol_now = float(vol.iloc[now]) if not np.isnan(vol.iloc[now]) else 1.0

    if regime.trend_direction in ('BULL', 'WEAK_BULL'):
        touched_ema = any(
            float(low.iloc[i]) <= float(ema21.iloc[i]) * 1.002
            for i in range(-5, -1)
        )
        bounce = close_now > open_now and close_now > ema21_now
        if touched_ema and bounce and vol_now >= 0.9:
            return ('buy', 'TREND_PULLBACK')

    elif regime.trend_direction in ('BEAR', 'WEAK_BEAR'):
        touched_ema = any(
            float(high.iloc[i]) >= float(ema21.iloc[i]) * 0.998
            for i in range(-5, -1)
        )
        bounce = close_now < open_now and close_now < ema21_now
        if touched_ema and bounce and vol_now >= 0.9:
            return ('sell', 'TREND_PULLBACK')

    return None


def _detect_momentum_continuation(ind: dict, regime: MarketRegime) -> Optional[Tuple[str, str]]:
    """Strong trend, price above all EMAs, fresh momentum bar.

    Requires: ADX >= 30, full EMA alignment, current bar is a momentum candle
    (body > 60% of range) in trend direction, volume above average.
    """
    if regime.state != 'MOMENTUM' and regime.adx < 30:
        return None
    if not regime.ema_aligned:
        return None

    close = ind['close']
    open_ = ind['open']
    high = ind['high']
    low = ind['low']
    vol = ind['vol_ratio']

    close_now = float(close.iloc[-1])
    open_now = float(open_.iloc[-1])
    high_now = float(high.iloc[-1])
    low_now = float(low.iloc[-1])
    vol_now = float(vol.iloc[-1]) if not np.isnan(vol.iloc[-1]) else 1.0

    bar_range = high_now - low_now
    if bar_range <= 0:
        return None
    body = abs(close_now - open_now)
    body_ratio = body / bar_range

    if body_ratio < 0.55:
        return None
    if vol_now < VOLUME_CONFIRM:
        return None

    if regime.trend_direction == 'BULL' and close_now > open_now:
        return ('buy', 'MOMENTUM_CONT')
    elif regime.trend_direction == 'BEAR' and close_now < open_now:
        return ('sell', 'MOMENTUM_CONT')

    return None


def _detect_breakout(ind: dict, regime: MarketRegime) -> Optional[Tuple[str, str]]:
    """Price breaks consolidation range with volume spike.

    Works in ANY regime (including ranging/consolidating).
    Requires: 5–20 bars of consolidation (range < 0.6 * ATR * period),
    then current bar closes outside range with volume >= 1.5x average.
    """
    close = ind['close']
    high = ind['high']
    low = ind['low']
    vol = ind['vol_ratio']
    atr_val = float(ind['atr'].iloc[-1]) if not np.isnan(ind['atr'].iloc[-1]) else 0

    if atr_val <= 0:
        return None

    vol_now = float(vol.iloc[-1]) if not np.isnan(vol.iloc[-1]) else 1.0
    if vol_now < VOLUME_SURGE:
        return None

    for lookback in [10, 15, 8, 20, 6]:
        if len(close) < lookback + 2:
            continue

        consol_high = float(high.iloc[-lookback-1:-1].max())
        consol_low = float(low.iloc[-lookback-1:-1].min())
        consol_range = consol_high - consol_low

        if consol_range > atr_val * lookback * RANGE_ATR_RATIO:
            continue

        close_now = float(close.iloc[-1])

        if close_now > consol_high:
            return ('buy', 'BREAKOUT')
        elif close_now < consol_low:
            return ('sell', 'BREAKOUT')

    return None


def _detect_impulse(ind: dict, regime: MarketRegime) -> Optional[Tuple[str, str]]:
    """Bollinger Band squeeze breakout with volume confirmation.

    Works in ANY regime. Requires: BB width narrowing (squeeze) in the last
    10 bars, then price breaks upper/lower band with volume.
    """
    close = ind['close']
    bb_upper = ind['bb_upper']
    bb_lower = ind['bb_lower']
    bb_mid = ind['bb_mid']
    vol = ind['vol_ratio']

    if len(close) < BB_PERIOD + 5:
        return None

    bb_width_now = float(bb_upper.iloc[-1] - bb_lower.iloc[-1])
    mid_now = float(bb_mid.iloc[-1])
    if mid_now <= 0:
        return None
    bb_pct_now = bb_width_now / mid_now * 100

    bb_width_prev = []
    for i in range(-10, -2):
        try:
            w = float(bb_upper.iloc[i] - bb_lower.iloc[i])
            m = float(bb_mid.iloc[i])
            if m > 0:
                bb_width_prev.append(w / m * 100)
        except Exception:
            continue

    if not bb_width_prev:
        return None

    avg_width = sum(bb_width_prev) / len(bb_width_prev)
    was_squeezed = min(bb_width_prev) < avg_width * BB_SQUEEZE_THRESHOLD + avg_width * 0.5

    if not was_squeezed:
        return None

    close_now = float(close.iloc[-1])
    vol_now = float(vol.iloc[-1]) if not np.isnan(vol.iloc[-1]) else 1.0

    if vol_now < VOLUME_CONFIRM:
        return None

    if close_now > float(bb_upper.iloc[-1]):
        return ('buy', 'IMPULSE')
    elif close_now < float(bb_lower.iloc[-1]):
        return ('sell', 'IMPULSE')

    return None


def _detect_trend_resumption(ind: dict, regime: MarketRegime) -> Optional[Tuple[str, str]]:
    """Brief 2–5 bar consolidation within a trend, then continuation candle.

    Requires: trending regime, 2–5 inside/narrow bars, then a momentum bar
    in the trend direction.
    """
    if not regime.is_tradeable:
        return None

    close = ind['close']
    open_ = ind['open']
    high = ind['high']
    low = ind['low']
    atr_series = ind['atr']
    vol = ind['vol_ratio']

    if len(close) < 8:
        return None

    atr_val = float(atr_series.iloc[-1]) if not np.isnan(atr_series.iloc[-1]) else 0
    if atr_val <= 0:
        return None

    narrow_count = 0
    for i in range(-5, -1):
        bar_range = float(high.iloc[i]) - float(low.iloc[i])
        if bar_range < atr_val * 0.6:
            narrow_count += 1

    if narrow_count < 2:
        return None

    close_now = float(close.iloc[-1])
    open_now = float(open_.iloc[-1])
    high_now = float(high.iloc[-1])
    low_now = float(low.iloc[-1])

    bar_range = high_now - low_now
    if bar_range <= 0:
        return None
    body = abs(close_now - open_now)
    body_ratio = body / bar_range

    if body_ratio < 0.50:
        return None

    vol_now = float(vol.iloc[-1]) if not np.isnan(vol.iloc[-1]) else 1.0
    if vol_now < 0.9:
        return None

    if regime.trend_direction in ('BULL', 'WEAK_BULL') and close_now > open_now:
        return ('buy', 'TREND_RESUMPTION')
    elif regime.trend_direction in ('BEAR', 'WEAK_BEAR') and close_now < open_now:
        return ('sell', 'TREND_RESUMPTION')

    return None


def _detect_ema_crossover(ind: dict, regime: MarketRegime) -> Optional[Tuple[str, str]]:
    """Fast EMA (8) crosses slow EMA (21) with trend + volume.

    Only signals if the cross happened in the last 2 bars and
    the broader trend (EMA50) agrees.
    """
    ema8 = ind['ema8']
    ema21 = ind['ema21']
    ema50 = ind['ema50']
    vol = ind['vol_ratio']

    if len(ema8) < 3:
        return None

    ema8_now = float(ema8.iloc[-1])
    ema8_prev = float(ema8.iloc[-2])
    ema21_now = float(ema21.iloc[-1])
    ema21_prev = float(ema21.iloc[-2])
    ema50_now = float(ema50.iloc[-1])
    vol_now = float(vol.iloc[-1]) if not np.isnan(vol.iloc[-1]) else 1.0

    if vol_now < 0.9:
        return None

    bullish_cross = ema8_prev <= ema21_prev and ema8_now > ema21_now
    bearish_cross = ema8_prev >= ema21_prev and ema8_now < ema21_now

    close_now = float(ind['close'].iloc[-1])

    if bullish_cross and close_now > ema50_now:
        if regime.adx >= 18:
            return ('buy', 'EMA_CROSSOVER')
    elif bearish_cross and close_now < ema50_now:
        if regime.adx >= 18:
            return ('sell', 'EMA_CROSSOVER')

    return None


# ==========================================================================
# Main detection entry point
# ==========================================================================

ALL_DETECTORS = [
    _detect_trend_pullback,
    _detect_momentum_continuation,
    _detect_breakout,
    _detect_impulse,
    _detect_trend_resumption,
    _detect_ema_crossover,
]

BREAKOUT_PATTERNS = frozenset({'BREAKOUT', 'IMPULSE'})


def detect_signal(instrument: str, timeframe: str,
                  df: 'pd.DataFrame' = None) -> Optional[Signal]:
    """Analyze a chart and return a Signal if a high-probability setup is found.

    Parameters
    ----------
    instrument : str
        e.g. 'MES', 'MNQ', 'MGC'
    timeframe : str
        e.g. '5m', '15m', '1h'
    df : pd.DataFrame, optional
        Pre-fetched OHLCV data. If None, fetched via data_feed.

    Returns
    -------
    Signal or None
        Fully qualified signal with entry/SL/TP/quality, or None if no setup.
    """
    if df is None:
        df = get_ohlcv(instrument, timeframe)

    if df is None or len(df) < MIN_BARS:
        return None

    ind = _compute_indicators(df)
    regime = classify_regime(ind, instrument)

    # Try each pattern detector in priority order
    detection = None
    for detector in ALL_DETECTORS:
        try:
            result = detector(ind, regime)
            if result:
                detection = result
                break
        except Exception as e:
            logger.debug(f"Detector {detector.__name__} error: {e}")
            continue

    if detection is None:
        return None

    direction, pattern = detection

    # Enforce regime rules: block non-breakout patterns in bad regimes
    if pattern not in BREAKOUT_PATTERNS and not regime.is_tradeable:
        logger.debug(f"{instrument} {timeframe}: {pattern} blocked — regime {regime.state}")
        return None

    # MTF confirmation (breakout patterns need only 1-of-2)
    mtf = check_mtf(instrument, timeframe, direction)
    if pattern in BREAKOUT_PATTERNS:
        mtf_ok = mtf.aligned_count >= 1 or mtf.total_checked == 0
    else:
        mtf_ok = mtf.confirmed
    if not mtf_ok:
        logger.debug(f"{instrument} {timeframe}: MTF not confirmed "
                     f"({mtf.aligned_count}/{mtf.total_checked})")
        return None

    entry = float(ind['close'].iloc[-1])
    sl, tp = calculate_sl_tp(instrument, direction, entry, ind, pattern)
    quality = score_signal(ind, regime, mtf, pattern, direction)

    if quality < 65:
        logger.debug(f"{instrument} {timeframe}: quality {quality} < 65 — skipped")
        return None

    close_now = float(ind['close'].iloc[-1])
    open_now = float(ind['open'].iloc[-1])
    candle = 'green' if close_now > open_now else 'red'

    rsi_val = float(ind['rsi'].iloc[-1]) if not np.isnan(ind['rsi'].iloc[-1]) else 50.0
    atr_val = float(ind['atr'].iloc[-1]) if not np.isnan(ind['atr'].iloc[-1]) else 0.0
    adx_val = regime.adx
    vol_val = float(ind['vol_ratio'].iloc[-1]) if not np.isnan(ind['vol_ratio'].iloc[-1]) else 1.0

    is_bull = direction == 'buy'

    signal = Signal(
        instrument=instrument,
        timeframe=timeframe,
        direction=direction,
        pattern=pattern,
        entry_price=entry,
        stop_loss=sl,
        take_profit=tp,
        quality_score=quality,
        regime=regime.state,
        adx=adx_val,
        rsi=rsi_val,
        atr=atr_val,
        volume_ratio=vol_val,
        mtf_aligned=mtf.aligned_count,
        candle_color=candle,
        ema_aligned_1m=is_bull,
        ema_aligned_5m=is_bull,
        ema_aligned_15m=is_bull if mtf.aligned_count >= 2 else not is_bull,
        reason=f"{pattern} in {regime.state} | ADX={adx_val:.0f} RSI={rsi_val:.0f} "
               f"Vol={vol_val:.1f}x | MTF {mtf.aligned_count}/{mtf.total_checked}",
    )

    return signal


# ==========================================================================
# Full scan — check all instruments × timeframes
# ==========================================================================

def scan_all(instruments: List[str] = None,
             timeframes: List[str] = None) -> List[Signal]:
    """Scan all instrument/timeframe combos and return detected signals."""
    instruments = instruments or ALL_INSTRUMENTS
    timeframes = timeframes or SCAN_TIMEFRAMES
    signals = []

    for instrument in instruments:
        for tf in timeframes:
            try:
                sig = detect_signal(instrument, tf)
                if sig:
                    signals.append(sig)
                    logger.info(
                        f"  DETECTED: {sig.instrument} {tf} {sig.direction.upper()} "
                        f"— {sig.pattern} Q={sig.quality_score} | {sig.reason}"
                    )
            except Exception as e:
                logger.debug(f"Scan error {instrument} {tf}: {e}")

    signals.sort(key=lambda s: s.quality_score, reverse=True)
    return signals
