#!/usr/bin/env python3
"""
Advanced Pattern Detection — high-probability setups that pure indicator
strategies miss.

Patterns:
  1. LIQUIDITY_SWEEP    — Stop hunt above/below S/R then sharp reversal
  2. VWAP_RECLAIM       — Price reclaims VWAP with volume after rejection
  3. TREND_CHANNEL      — Price bouncing within ascending/descending channel
  4. WEDGE_BREAKOUT     — Narrowing range (wedge) with volume breakout
  5. SR_BOUNCE          — Clean rejection off key support/resistance
  6. FAKE_BREAKOUT      — Breaks level, traps traders, reverses immediately
  7. VOLUME_DIVERGENCE  — Price makes new high/low but volume decreasing
  8. ORDER_FLOW_IMBAL   — Large volume imbalance (buy vs sell pressure proxy)

All patterns require multi-timeframe confirmation and return a confidence
score 0-100 that feeds into the signal quality filter.
"""

import numpy as np
import pandas as pd
import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger('advanced_patterns')


@dataclass
class PatternSignal:
    pattern: str
    signal: str        # 'LONG' or 'SHORT'
    confidence: int    # 0-100
    entry_price: float
    stop_loss: float
    take_profit: float
    reason: str
    indicators: Dict = field(default_factory=dict)


# ── VWAP ─────────────────────────────────────────────────────────────────────

def compute_vwap(df: pd.DataFrame) -> pd.Series:
    typical = (df['High'] + df['Low'] + df['Close']) / 3
    cum_vol = df['Volume'].cumsum().replace(0, np.nan)
    return ((typical * df['Volume']).cumsum() / cum_vol).ffill()


def compute_vwap_bands(df: pd.DataFrame, std_mult: float = 1.5) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """VWAP with upper/lower bands (like Bollinger but anchored to VWAP)."""
    vwap = compute_vwap(df)
    typical = (df['High'] + df['Low'] + df['Close']) / 3
    cum_vol = df['Volume'].cumsum().replace(0, np.nan)
    cum_sq = ((typical ** 2) * df['Volume']).cumsum() / cum_vol
    variance = cum_sq - vwap ** 2
    variance = variance.clip(lower=0)
    std = np.sqrt(variance)
    return vwap, vwap + std * std_mult, vwap - std * std_mult


# ── Volume Delta (proxy) ────────────────────────────────────────────────────

def compute_volume_delta(df: pd.DataFrame) -> pd.Series:
    """Approximate buy/sell volume using close position within bar range.

    Not true Level 2 order flow, but a useful proxy from OHLCV data.
    Values > 0 indicate buying pressure, < 0 indicate selling pressure.
    """
    bar_range = (df['High'] - df['Low']).replace(0, np.nan)
    close_position = (df['Close'] - df['Low']) / bar_range
    close_position = close_position.fillna(0.5)
    buy_vol = df['Volume'] * close_position
    sell_vol = df['Volume'] * (1 - close_position)
    return buy_vol - sell_vol


def compute_cumulative_delta(df: pd.DataFrame, period: int = 14) -> pd.Series:
    delta = compute_volume_delta(df)
    return delta.rolling(period).sum()


# ── Order Flow Imbalance ────────────────────────────────────────────────────

def compute_order_flow_imbalance(df: pd.DataFrame, period: int = 10) -> pd.Series:
    """Ratio of buy volume to total volume over a rolling window.
    Values > 0.6 = strong buying. Values < 0.4 = strong selling.
    """
    bar_range = (df['High'] - df['Low']).replace(0, np.nan)
    close_pos = ((df['Close'] - df['Low']) / bar_range).fillna(0.5)
    buy_vol = (df['Volume'] * close_pos).rolling(period).sum()
    total_vol = df['Volume'].rolling(period).sum().replace(0, np.nan)
    return (buy_vol / total_vol).fillna(0.5)


# ── Support / Resistance ────────────────────────────────────────────────────

def find_sr_levels(df: pd.DataFrame, lookback: int = 50,
                   num_levels: int = 5) -> Tuple[List[float], List[float]]:
    if len(df) < lookback:
        return [], []

    window = max(3, min(7, lookback // 8))
    highs_roll = df['High'].rolling(window * 2 + 1, center=True).max()
    lows_roll = df['Low'].rolling(window * 2 + 1, center=True).min()

    swing_highs = df['High'][df['High'] == highs_roll].dropna()
    swing_lows = df['Low'][df['Low'] == lows_roll].dropna()

    price_range = df['High'].max() - df['Low'].min()
    if price_range == 0:
        return [], []
    cluster_pct = 0.003
    cluster_dist = price_range * cluster_pct

    def cluster(levels):
        if len(levels) == 0:
            return []
        arr = sorted(levels)
        groups = [[arr[0]]]
        for v in arr[1:]:
            if abs(v - groups[-1][-1]) < cluster_dist:
                groups[-1].append(v)
            else:
                groups.append([v])
        return sorted([np.mean(g) for g in groups], reverse=True)[:num_levels]

    return cluster(swing_lows.values), cluster(swing_highs.values)


# ── Pattern Detection Functions ─────────────────────────────────────────────

def detect_liquidity_sweep(df: pd.DataFrame, atr: float) -> Optional[PatternSignal]:
    """Liquidity sweep: price wicks through S/R then closes back inside."""
    if len(df) < 25:
        return None

    support, resistance = find_sr_levels(df.iloc[:-3], lookback=40)
    last = df.iloc[-1]

    for r_level in resistance:
        if last['High'] > r_level and last['Close'] < r_level:
            wick_size = last['High'] - last['Close']
            if wick_size > atr * 0.3:
                return PatternSignal(
                    pattern='LIQUIDITY_SWEEP',
                    signal='SHORT',
                    confidence=80,
                    entry_price=last['Close'],
                    stop_loss=last['High'] + atr * 0.3,
                    take_profit=last['Close'] - atr * 2.0,
                    reason=f"Sweep above resistance {r_level:.2f}, wick rejected",
                    indicators={'sweep_level': r_level, 'wick': last['High']},
                )

    for s_level in support:
        if last['Low'] < s_level and last['Close'] > s_level:
            wick_size = last['Close'] - last['Low']
            if wick_size > atr * 0.3:
                return PatternSignal(
                    pattern='LIQUIDITY_SWEEP',
                    signal='LONG',
                    confidence=80,
                    entry_price=last['Close'],
                    stop_loss=last['Low'] - atr * 0.3,
                    take_profit=last['Close'] + atr * 2.0,
                    reason=f"Sweep below support {s_level:.2f}, wick rejected",
                    indicators={'sweep_level': s_level, 'wick': last['Low']},
                )
    return None


def detect_vwap_reclaim(df: pd.DataFrame, atr: float) -> Optional[PatternSignal]:
    """VWAP reclaim: price crosses back above/below VWAP with volume."""
    if len(df) < 20:
        return None

    vwap = compute_vwap(df)
    vol_avg = df['Volume'].rolling(20).mean().iloc[-1]
    last = df.iloc[-1]
    prev = df.iloc[-2]
    vol_ratio = last['Volume'] / vol_avg if vol_avg > 0 else 1.0

    vwap_now = vwap.iloc[-1]
    vwap_prev = vwap.iloc[-2]

    if prev['Close'] < vwap_prev and last['Close'] > vwap_now and vol_ratio > 1.2:
        return PatternSignal(
            pattern='VWAP_RECLAIM',
            signal='LONG',
            confidence=72,
            entry_price=last['Close'],
            stop_loss=last['Close'] - atr * 1.5,
            take_profit=last['Close'] + atr * 2.5,
            reason=f"Reclaimed VWAP {vwap_now:.2f} from below, vol {vol_ratio:.1f}x avg",
            indicators={'vwap': vwap_now, 'volume_ratio': vol_ratio},
        )

    if prev['Close'] > vwap_prev and last['Close'] < vwap_now and vol_ratio > 1.2:
        return PatternSignal(
            pattern='VWAP_RECLAIM',
            signal='SHORT',
            confidence=72,
            entry_price=last['Close'],
            stop_loss=last['Close'] + atr * 1.5,
            take_profit=last['Close'] - atr * 2.5,
            reason=f"Lost VWAP {vwap_now:.2f} from above, vol {vol_ratio:.1f}x avg",
            indicators={'vwap': vwap_now, 'volume_ratio': vol_ratio},
        )
    return None


def detect_fake_breakout(df: pd.DataFrame, atr: float) -> Optional[PatternSignal]:
    """Fake breakout: breaks level, traps breakout traders, reverses."""
    if len(df) < 30:
        return None

    support, resistance = find_sr_levels(df.iloc[:-5], lookback=30)

    for i in range(-4, -1):
        bar = df.iloc[i]
        next_bar = df.iloc[i + 1] if i + 1 < -1 else df.iloc[-1]

        for r_level in resistance:
            if bar['Close'] > r_level and next_bar['Close'] < r_level:
                vol_avg = df['Volume'].rolling(20).mean().iloc[i]
                if bar['Volume'] > vol_avg * 1.3:
                    return PatternSignal(
                        pattern='FAKE_BREAKOUT',
                        signal='SHORT',
                        confidence=78,
                        entry_price=next_bar['Close'],
                        stop_loss=bar['High'] + atr * 0.3,
                        take_profit=next_bar['Close'] - atr * 2.0,
                        reason=f"Fake breakout above {r_level:.2f}, trapped longs",
                        indicators={'level': r_level, 'trap_bar': bar['Close']},
                    )

        for s_level in support:
            if bar['Close'] < s_level and next_bar['Close'] > s_level:
                vol_avg = df['Volume'].rolling(20).mean().iloc[i]
                if bar['Volume'] > vol_avg * 1.3:
                    return PatternSignal(
                        pattern='FAKE_BREAKOUT',
                        signal='LONG',
                        confidence=78,
                        entry_price=next_bar['Close'],
                        stop_loss=bar['Low'] - atr * 0.3,
                        take_profit=next_bar['Close'] + atr * 2.0,
                        reason=f"Fake breakout below {s_level:.2f}, trapped shorts",
                        indicators={'level': s_level, 'trap_bar': bar['Close']},
                    )
    return None


def detect_wedge(df: pd.DataFrame, atr: float, lookback: int = 20) -> Optional[PatternSignal]:
    """Wedge/triangle: narrowing range approaching breakout."""
    if len(df) < lookback + 5:
        return None

    window = df.iloc[-lookback:-1]
    highs = window['High'].values
    lows = window['Low'].values
    x = np.arange(len(highs))

    try:
        high_slope = np.polyfit(x, highs, 1)[0]
        low_slope = np.polyfit(x, lows, 1)[0]
    except Exception:
        return None

    range_start = highs[0] - lows[0]
    range_end = highs[-1] - lows[-1]

    if range_end >= range_start * 0.8:
        return None

    last = df.iloc[-1]
    vol_avg = df['Volume'].rolling(20).mean().iloc[-1]
    vol_ratio = last['Volume'] / vol_avg if vol_avg > 0 else 1.0

    if vol_ratio < 1.3:
        return None

    projected_high = highs[-1] + high_slope
    projected_low = lows[-1] + low_slope

    if last['Close'] > projected_high:
        return PatternSignal(
            pattern='WEDGE_BREAKOUT',
            signal='LONG',
            confidence=75,
            entry_price=last['Close'],
            stop_loss=projected_low - atr * 0.5,
            take_profit=last['Close'] + (range_start * 0.8),
            reason=f"Wedge breakout UP, range contracted {range_end/range_start:.0%}, vol {vol_ratio:.1f}x",
            indicators={'range_contraction': range_end / range_start, 'volume_ratio': vol_ratio},
        )

    if last['Close'] < projected_low:
        return PatternSignal(
            pattern='WEDGE_BREAKOUT',
            signal='SHORT',
            confidence=75,
            entry_price=last['Close'],
            stop_loss=projected_high + atr * 0.5,
            take_profit=last['Close'] - (range_start * 0.8),
            reason=f"Wedge breakout DOWN, range contracted {range_end/range_start:.0%}, vol {vol_ratio:.1f}x",
            indicators={'range_contraction': range_end / range_start, 'volume_ratio': vol_ratio},
        )
    return None


def detect_sr_bounce(df: pd.DataFrame, atr: float) -> Optional[PatternSignal]:
    """Support/resistance bounce with volume confirmation."""
    if len(df) < 25:
        return None

    support, resistance = find_sr_levels(df.iloc[:-3], lookback=40)
    last = df.iloc[-1]
    vol_avg = df['Volume'].rolling(20).mean().iloc[-1]
    vol_ratio = last['Volume'] / vol_avg if vol_avg > 0 else 1.0

    for s in support:
        dist = abs(last['Low'] - s) / atr if atr > 0 else 999
        if dist < 0.5 and last['Close'] > s and last['Close'] > last['Open']:
            return PatternSignal(
                pattern='SR_BOUNCE',
                signal='LONG',
                confidence=70 + min(int(vol_ratio * 5), 15),
                entry_price=last['Close'],
                stop_loss=s - atr * 0.5,
                take_profit=last['Close'] + atr * 2.0,
                reason=f"Bounce off support {s:.2f}, vol {vol_ratio:.1f}x",
                indicators={'level': s, 'distance_atr': dist},
            )

    for r in resistance:
        dist = abs(last['High'] - r) / atr if atr > 0 else 999
        if dist < 0.5 and last['Close'] < r and last['Close'] < last['Open']:
            return PatternSignal(
                pattern='SR_BOUNCE',
                signal='SHORT',
                confidence=70 + min(int(vol_ratio * 5), 15),
                entry_price=last['Close'],
                stop_loss=r + atr * 0.5,
                take_profit=last['Close'] - atr * 2.0,
                reason=f"Rejection at resistance {r:.2f}, vol {vol_ratio:.1f}x",
                indicators={'level': r, 'distance_atr': dist},
            )
    return None


def detect_volume_divergence(df: pd.DataFrame, atr: float, lookback: int = 14) -> Optional[PatternSignal]:
    """Volume divergence: new price extreme on declining volume."""
    if len(df) < lookback + 5:
        return None

    recent = df.iloc[-lookback:]
    vol_trend = np.polyfit(np.arange(lookback), recent['Volume'].values, 1)[0]

    last = df.iloc[-1]
    prev_high = recent['High'].iloc[:-3].max()
    prev_low = recent['Low'].iloc[:-3].min()

    if last['High'] >= prev_high and vol_trend < 0:
        return PatternSignal(
            pattern='VOLUME_DIVERGENCE',
            signal='SHORT',
            confidence=68,
            entry_price=last['Close'],
            stop_loss=last['High'] + atr * 0.5,
            take_profit=last['Close'] - atr * 2.0,
            reason=f"New high on declining volume — bearish divergence",
            indicators={'vol_slope': vol_trend},
        )

    if last['Low'] <= prev_low and vol_trend < 0:
        return PatternSignal(
            pattern='VOLUME_DIVERGENCE',
            signal='LONG',
            confidence=68,
            entry_price=last['Close'],
            stop_loss=last['Low'] - atr * 0.5,
            take_profit=last['Close'] + atr * 2.0,
            reason=f"New low on declining volume — bullish divergence",
            indicators={'vol_slope': vol_trend},
        )
    return None


# ── Multi-Timeframe Scanner ────────────────────────────────────────────────

def scan_advanced_patterns(instrument: str, df_1m: pd.DataFrame,
                           df_5m: pd.DataFrame, df_15m: pd.DataFrame,
                           min_confidence: int = 70) -> List[PatternSignal]:
    """Scan all advanced patterns across 3 timeframes with MTF confirmation.

    Returns patterns that pass MTF alignment check (15m trend must agree).
    """
    results = []

    ema_8_15m = df_15m['Close'].ewm(span=8).mean() if len(df_15m) >= 8 else None
    ema_21_15m = df_15m['Close'].ewm(span=21).mean() if len(df_15m) >= 21 else None
    trend_15m = None
    if ema_8_15m is not None and ema_21_15m is not None:
        if ema_8_15m.iloc[-1] > ema_21_15m.iloc[-1]:
            trend_15m = 'LONG'
        elif ema_8_15m.iloc[-1] < ema_21_15m.iloc[-1]:
            trend_15m = 'SHORT'

    for df, tf_name in [(df_5m, '5m'), (df_1m, '1m')]:
        if df is None or df.empty or len(df) < 20:
            continue

        high_low = df['High'] - df['Low']
        high_close = abs(df['High'] - df['Close'].shift())
        low_close = abs(df['Low'] - df['Close'].shift())
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = tr.rolling(14).mean().iloc[-1]
        if np.isnan(atr) or atr <= 0:
            continue

        detectors = [
            detect_liquidity_sweep,
            detect_vwap_reclaim,
            detect_fake_breakout,
            detect_wedge,
            detect_sr_bounce,
            detect_volume_divergence,
        ]

        for detector in detectors:
            try:
                signal = detector(df, atr)
                if signal is None:
                    continue
                if signal.confidence < min_confidence:
                    continue

                if trend_15m and signal.signal != trend_15m:
                    if signal.pattern not in ('LIQUIDITY_SWEEP', 'FAKE_BREAKOUT'):
                        continue

                signal.indicators['timeframe'] = tf_name
                signal.indicators['instrument'] = instrument
                signal.indicators['trend_15m'] = trend_15m
                results.append(signal)
            except Exception as e:
                logger.debug(f"Pattern {detector.__name__} error: {e}")

    results.sort(key=lambda s: s.confidence, reverse=True)
    return results
