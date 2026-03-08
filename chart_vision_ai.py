#!/usr/bin/env python3
"""
Chart Vision AI — GPT-4V analyzes actual chart images for trade setups.

Generates candlestick chart images with indicators (EMA, VWAP, S/R, volume)
using matplotlib, then sends to GPT-4o (vision) for pattern recognition.

Detects: liquidity sweeps, trend channels, wedges, S/R bounces, fake breakouts,
         divergences, and consolidation breakouts that pure numeric analysis misses.

Usage:
    from chart_vision_ai import ChartVisionAI
    vision = ChartVisionAI()
    result = vision.analyze(instrument, df_1m, df_5m, df_15m)
    # result = {'signal': 'LONG', 'confidence': 0.82, 'patterns': [...], 'reason': '...'}
"""

import os
import io
import sys
import base64
import json
import logging
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from matplotlib.patches import Rectangle
    MPL_AVAILABLE = True
except ImportError:
    MPL_AVAILABLE = False

try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logger = logging.getLogger('chart_vision_ai')

_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
if os.path.exists(_ENV_PATH):
    with open(_ENV_PATH) as _f:
        for _ln in _f:
            _ln = _ln.strip()
            if not _ln or _ln.startswith('#') or '=' not in _ln:
                continue
            _k, _, _v = _ln.partition('=')
            _k = _k.strip(); _v = _v.strip().strip('"').strip("'")
            if _k and _k not in os.environ:
                os.environ[_k] = _v

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '')
VISION_MODEL = 'gpt-4o'
MAX_RETRIES = 2
VISION_TIMEOUT = 15


def compute_vwap(df: pd.DataFrame) -> pd.Series:
    """Volume-Weighted Average Price."""
    typical = (df['High'] + df['Low'] + df['Close']) / 3
    cum_vol = df['Volume'].cumsum()
    cum_tp_vol = (typical * df['Volume']).cumsum()
    vwap = cum_tp_vol / cum_vol
    vwap = vwap.replace([np.inf, -np.inf], np.nan).ffill()
    return vwap


def compute_support_resistance(df: pd.DataFrame, lookback: int = 50,
                                num_levels: int = 4) -> Tuple[List[float], List[float]]:
    """Find key S/R levels using swing highs/lows and volume clusters."""
    if len(df) < lookback:
        return [], []

    window = min(5, len(df) // 4)
    if window < 2:
        return [], []

    highs = df['High'].rolling(window, center=True).max()
    lows = df['Low'].rolling(window, center=True).min()

    swing_highs = df['High'][df['High'] == highs].dropna().unique()
    swing_lows = df['Low'][df['Low'] == lows].dropna().unique()

    price_range = df['High'].max() - df['Low'].min()
    cluster_dist = price_range * 0.005

    def cluster_levels(levels, max_n):
        if len(levels) == 0:
            return []
        levels = sorted(levels)
        clustered = []
        current = [levels[0]]
        for lv in levels[1:]:
            if abs(lv - current[-1]) < cluster_dist:
                current.append(lv)
            else:
                clustered.append(np.mean(current))
                current = [lv]
        clustered.append(np.mean(current))
        return sorted(clustered)[-max_n:]

    resistance = cluster_levels(swing_highs[-lookback:], num_levels)
    support = cluster_levels(swing_lows[-lookback:], num_levels)
    return support, resistance


def detect_liquidity_sweep(df: pd.DataFrame, lookback: int = 20) -> Optional[Dict]:
    """Detect liquidity sweeps — price spikes through S/R then reverses."""
    if len(df) < lookback + 5:
        return None

    recent = df.iloc[-lookback:]
    prev_high = recent['High'].iloc[:-3].max()
    prev_low = recent['Low'].iloc[:-3].min()

    last_3 = df.iloc[-3:]

    for i in range(len(last_3)):
        bar = last_3.iloc[i]
        if bar['High'] > prev_high and bar['Close'] < prev_high:
            return {
                'type': 'SWEEP_HIGH',
                'level': prev_high,
                'wick': bar['High'],
                'close': bar['Close'],
                'signal': 'SHORT',
                'confidence': 0.75,
            }
        if bar['Low'] < prev_low and bar['Close'] > prev_low:
            return {
                'type': 'SWEEP_LOW',
                'level': prev_low,
                'wick': bar['Low'],
                'close': bar['Close'],
                'signal': 'LONG',
                'confidence': 0.75,
            }
    return None


def render_chart(df: pd.DataFrame, instrument: str, timeframe: str,
                 vwap: pd.Series = None, support: List[float] = None,
                 resistance: List[float] = None, ema_8: pd.Series = None,
                 ema_21: pd.Series = None, ema_50: pd.Series = None,
                 sweep: Dict = None) -> bytes:
    """Render a candlestick chart with indicators as a PNG image."""
    if not MPL_AVAILABLE:
        return b''

    fig, (ax_price, ax_vol) = plt.subplots(2, 1, figsize=(12, 7),
                                            gridspec_kw={'height_ratios': [3, 1]},
                                            sharex=True)
    fig.patch.set_facecolor('#1a1a2e')
    ax_price.set_facecolor('#1a1a2e')
    ax_vol.set_facecolor('#1a1a2e')

    n = len(df)
    x = np.arange(n)

    for i in range(n):
        o, h, l, c = df['Open'].iloc[i], df['High'].iloc[i], df['Low'].iloc[i], df['Close'].iloc[i]
        color = '#00d4aa' if c >= o else '#ff4757'
        ax_price.plot([x[i], x[i]], [l, h], color=color, linewidth=0.8)
        body_bottom = min(o, c)
        body_height = abs(c - o) or (h - l) * 0.01
        ax_price.add_patch(Rectangle((x[i] - 0.35, body_bottom), 0.7, body_height,
                                      facecolor=color, edgecolor=color))

    if ema_8 is not None and len(ema_8) == n:
        ax_price.plot(x, ema_8.values, color='#ffd700', linewidth=1, alpha=0.8, label='EMA 8')
    if ema_21 is not None and len(ema_21) == n:
        ax_price.plot(x, ema_21.values, color='#00bfff', linewidth=1, alpha=0.8, label='EMA 21')
    if ema_50 is not None and len(ema_50) == n:
        ax_price.plot(x, ema_50.values, color='#ff69b4', linewidth=1, alpha=0.7, label='EMA 50')

    if vwap is not None and len(vwap) == n:
        ax_price.plot(x, vwap.values, color='#ffffff', linewidth=1.5, alpha=0.9,
                      linestyle='--', label='VWAP')

    if support:
        for lv in support:
            ax_price.axhline(y=lv, color='#00ff88', linewidth=0.8, alpha=0.5, linestyle=':')
    if resistance:
        for lv in resistance:
            ax_price.axhline(y=lv, color='#ff4444', linewidth=0.8, alpha=0.5, linestyle=':')

    if sweep:
        ax_price.axhline(y=sweep['level'], color='#ffaa00', linewidth=2, alpha=0.8)
        ax_price.annotate(f"SWEEP {sweep['type']}", xy=(n - 2, sweep['level']),
                          color='#ffaa00', fontsize=9, fontweight='bold')

    vol_colors = ['#00d4aa' if df['Close'].iloc[i] >= df['Open'].iloc[i] else '#ff4757'
                  for i in range(n)]
    ax_vol.bar(x, df['Volume'].values, color=vol_colors, alpha=0.7, width=0.7)

    ax_price.set_title(f'{instrument} {timeframe}', color='white', fontsize=14, fontweight='bold')
    ax_price.legend(loc='upper left', fontsize=8, facecolor='#2a2a4a', edgecolor='#444',
                    labelcolor='white')
    ax_price.tick_params(colors='white')
    ax_vol.tick_params(colors='white')
    ax_price.grid(True, alpha=0.1, color='white')
    ax_vol.grid(True, alpha=0.1, color='white')

    plt.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=100, bbox_inches='tight', facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf.read()


class ChartVisionAI:
    """GPT-4V chart analysis — reads actual chart images for pattern recognition."""

    def __init__(self, api_key: str = ''):
        self.api_key = api_key or OPENAI_API_KEY
        self.client = None
        self.calls = 0
        self.errors = 0

        if OPENAI_AVAILABLE and self.api_key and len(self.api_key) > 10:
            try:
                self.client = OpenAI(api_key=self.api_key)
                logger.info("Chart Vision AI initialized (GPT-4o vision)")
            except Exception as e:
                logger.warning(f"Chart Vision AI init failed: {e}")
        else:
            if not OPENAI_AVAILABLE:
                logger.info("Chart Vision AI disabled (openai package not installed)")
            elif not self.api_key:
                logger.info("Chart Vision AI disabled (no OPENAI_API_KEY)")

    def is_available(self) -> bool:
        return self.client is not None and MPL_AVAILABLE

    def analyze(self, instrument: str, df_1m: pd.DataFrame,
                df_5m: pd.DataFrame, df_15m: pd.DataFrame,
                current_signal: str = '') -> Optional[Dict]:
        """Analyze 3 timeframe charts with GPT-4V vision.

        Args:
            instrument: e.g. 'MES', 'MNQ'
            df_1m, df_5m, df_15m: OHLCV DataFrames
            current_signal: 'LONG' or 'SHORT' if scanner already has a signal

        Returns:
            {'signal': 'LONG'|'SHORT'|'NO_TRADE', 'confidence': 0.0-1.0,
             'patterns': [...], 'reason': '...', 'tier': 'vision'}
        """
        if not self.is_available():
            return None

        charts = []
        for df, tf in [(df_15m, '15m'), (df_5m, '5m'), (df_1m, '1m')]:
            if df is None or df.empty or len(df) < 20:
                continue
            chart_df = df.iloc[-80:] if len(df) > 80 else df
            vwap = compute_vwap(chart_df)
            support, resistance = compute_support_resistance(chart_df)
            ema_8 = chart_df['Close'].ewm(span=8).mean()
            ema_21 = chart_df['Close'].ewm(span=21).mean()
            ema_50 = chart_df['Close'].ewm(span=50).mean() if len(chart_df) >= 50 else None
            sweep = detect_liquidity_sweep(chart_df)

            img_bytes = render_chart(chart_df, instrument, tf, vwap,
                                      support, resistance, ema_8, ema_21, ema_50, sweep)
            if img_bytes:
                charts.append({
                    'timeframe': tf,
                    'image_b64': base64.b64encode(img_bytes).decode(),
                    'sweep': sweep,
                    'support': support,
                    'resistance': resistance,
                })

        if not charts:
            return None

        return self._call_vision(instrument, charts, current_signal)

    def _call_vision(self, instrument: str, charts: List[Dict],
                     current_signal: str) -> Optional[Dict]:
        """Send chart images to GPT-4V for analysis."""
        signal_context = f"\nThe scanner has a preliminary {current_signal} signal. Validate or reject it." if current_signal else ""

        prompt = f"""You are an expert futures day-trader analyzing {instrument} charts across 3 timeframes.

CHARTS PROVIDED: {', '.join(c['timeframe'] for c in charts)} (candlesticks + EMA 8/21/50 + VWAP + S/R levels + volume)

YOUR TASK — Identify the single best trade setup (or NO_TRADE):

LOOK FOR (in order of reliability):
1. LIQUIDITY SWEEPS — price spikes through S/R, wicks reject, then reverses (highest edge)
2. TREND CONTINUATION — pullback to EMA 21 or VWAP in a trending market, then bounce
3. BREAKOUT — consolidation squeeze followed by volume expansion
4. WEDGE/CHANNEL — price compressing in a wedge or channel, approaching breakout point
5. SUPPORT/RESISTANCE BOUNCE — clean rejection off a key level with volume confirmation
6. FAKE BREAKOUT — price breaks level but immediately reverses with strong candle

DO NOT TRADE:
- Sideways/ranging markets with no clear direction
- Low volume, no momentum conditions
- Price in the middle of a range with no setup

MULTI-TIMEFRAME RULES:
- 15m defines the trend direction (EMA alignment, structure)
- 5m confirms the setup (pullback, pattern formation)
- 1m triggers the entry (reversal candle, volume spike)
- All 3 must agree on direction for LONG/SHORT
- If timeframes conflict → NO_TRADE{signal_context}

Respond ONLY with valid JSON:
{{"signal": "LONG", "confidence": 0.82, "patterns": ["TREND_PULLBACK", "VWAP_BOUNCE"], "reason": "15m uptrend, 5m pullback to VWAP, 1m bullish engulfing with volume spike", "key_levels": {{"support": 5100, "resistance": 5150}}, "stop_suggestion": 5095, "target_suggestion": 5160}}"""

        content = [{"type": "text", "text": prompt}]
        for chart in charts:
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{chart['image_b64']}",
                    "detail": "high"
                }
            })

        for attempt in range(MAX_RETRIES):
            try:
                t0 = time.time()
                resp = self.client.chat.completions.create(
                    model=VISION_MODEL,
                    messages=[{
                        "role": "user",
                        "content": content
                    }],
                    max_tokens=300,
                    temperature=0.1,
                    timeout=VISION_TIMEOUT,
                )
                elapsed = time.time() - t0
                self.calls += 1

                raw = resp.choices[0].message.content.strip()
                return self._parse_response(raw, elapsed)

            except Exception as e:
                self.errors += 1
                logger.debug(f"Vision API error (attempt {attempt + 1}): {e}")
                if attempt < MAX_RETRIES - 1:
                    time.sleep(1)

        return None

    def _parse_response(self, raw: str, elapsed: float) -> Optional[Dict]:
        """Parse GPT-4V JSON response."""
        import re
        try:
            clean = re.sub(r'```(?:json)?', '', raw).strip()
            data = json.loads(clean)
            signal = data.get('signal', '').upper()
            confidence = float(data.get('confidence', 0))
            patterns = data.get('patterns', [])
            reason = data.get('reason', '')

            if signal not in ('LONG', 'SHORT', 'NO_TRADE'):
                return None
            if not (0.0 <= confidence <= 1.0):
                confidence = min(max(confidence, 0), 1.0)

            result = {
                'signal': signal,
                'confidence': confidence,
                'patterns': patterns,
                'reason': reason,
                'tier': 'vision',
                'elapsed': round(elapsed, 2),
                'stop_suggestion': data.get('stop_suggestion'),
                'target_suggestion': data.get('target_suggestion'),
                'key_levels': data.get('key_levels', {}),
            }

            logger.info(f"  Vision: {signal} {confidence:.0%} | "
                        f"{', '.join(patterns)} | {elapsed:.1f}s")
            return result

        except Exception as e:
            logger.debug(f"Vision parse error: {e} | raw={raw[:100]}")
            import re as _re
            sig_m = _re.search(r'\b(LONG|SHORT|NO_TRADE)\b', raw, _re.I)
            conf_m = _re.search(r'\b(0\.\d+)\b', raw)
            if sig_m:
                return {
                    'signal': sig_m.group(1).upper(),
                    'confidence': float(conf_m.group(1)) if conf_m else 0.65,
                    'patterns': [],
                    'reason': 'parsed from text',
                    'tier': 'vision',
                    'elapsed': round(elapsed, 2),
                }
            return None

    def get_stats(self) -> Dict:
        return {
            'available': self.is_available(),
            'calls': self.calls,
            'errors': self.errors,
            'success_rate': f"{(self.calls - self.errors) / max(self.calls, 1):.0%}",
        }
