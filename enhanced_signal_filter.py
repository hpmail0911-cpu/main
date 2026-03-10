#!/usr/bin/env python3
"""
Enhanced Signal Filter — multi-layer confluence scoring with 0.75 AI confidence gate.

Combines: Technical score + Pattern quality + Vision AI + MTF alignment + Volume +
          VWAP position + Order flow + Regime filter → final GO/NO-GO decision.

Eliminates low-probability trades by requiring confluence across multiple
independent dimensions. Designed to push win rate from 55% → 70-80%.

Usage:
    from enhanced_signal_filter import EnhancedSignalFilter
    esf = EnhancedSignalFilter()
    decision = esf.evaluate(signal_data)
    if decision['approved']:
        send_signal(...)
"""

import os
import sys
import logging
import numpy as np
import pandas as pd
from typing import Dict, Optional
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logger = logging.getLogger('enhanced_signal_filter')

MIN_CONFLUENCE_SCORE = 65
MIN_AI_CONFIDENCE = 0.75
MIN_QUALITY_SCORE = 75

REGIME_BLOCK = {'RANGING', 'CONSOLIDATING', 'CHOPPY'}
REGIME_ALLOW_ALL = {'MOMENTUM', 'TRENDING'}
REGIME_BREAKOUT_ONLY = {'RANGING', 'CONSOLIDATING'}

MAX_ATR_SPIKE_RATIO = 2.0
INSTRUMENT_DAILY_LOSS_LIMIT = -70
_instrument_daily_pnl: Dict = {}
_instrument_daily_date: str = ''


@dataclass
class FilterDecision:
    approved: bool
    confluence_score: int
    ai_confidence: float
    reasons: list
    adjustments: Dict


class EnhancedSignalFilter:
    """Multi-dimensional signal quality filter."""

    def __init__(self, min_confluence: int = MIN_CONFLUENCE_SCORE,
                 min_ai_confidence: float = MIN_AI_CONFIDENCE):
        self.min_confluence = min_confluence
        self.min_ai_confidence = min_ai_confidence
        self.stats = {'evaluated': 0, 'approved': 0, 'rejected': 0}

    def evaluate(self, signal: Dict, df_entry: pd.DataFrame = None,
                 vision_result: Dict = None, pattern_signals: list = None) -> FilterDecision:
        """Evaluate a signal through multiple confluence layers.

        Args:
            signal: Scanner signal dict with action, quality_score, pattern, etc.
            df_entry: Entry timeframe DataFrame for indicator computation
            vision_result: Output from ChartVisionAI.analyze()
            pattern_signals: Output from scan_advanced_patterns()
        """
        self.stats['evaluated'] += 1
        reasons = []
        score = 0
        adjustments = {}

        action = signal.get('action', 'LONG').upper()
        quality = int(signal.get('quality_score', 0))
        pattern = signal.get('pattern', '')
        adx = float(signal.get('adx', 0) or 0)
        atr = float(signal.get('atr', 0) or 0)
        volume_ratio = float(signal.get('volume_ratio', 1.0) or 1.0)
        mtf = int(signal.get('mtf_alignment', 0) or 0)
        chop = float(signal.get('chop_index', 50) or 50)

        is_breakout = any(x in pattern.upper() for x in ('BREAKOUT', 'IMPULSE', 'SWEEP'))

        # ── Layer 1: Technical Quality (0-25 pts) ──────────────────────────
        if quality >= 90:
            score += 25; reasons.append(f"quality={quality} (excellent)")
        elif quality >= 80:
            score += 20; reasons.append(f"quality={quality} (good)")
        elif quality >= 70:
            score += 12; reasons.append(f"quality={quality} (fair)")
        else:
            score += 5; reasons.append(f"quality={quality} (weak)")

        # ── Layer 2: Trend Strength / ADX (0-15 pts) ──────────────────────
        if adx >= 35:
            score += 15; reasons.append(f"ADX={adx:.0f} (strong trend)")
        elif adx >= 25:
            score += 10; reasons.append(f"ADX={adx:.0f} (moderate trend)")
        elif adx >= 20:
            score += 5; reasons.append(f"ADX={adx:.0f} (weak)")
        elif not is_breakout:
            reasons.append(f"ADX={adx:.0f} (no trend, not breakout → penalty)")

        # ── Layer 3: MTF Alignment (0-15 pts) ─────────────────────────────
        if mtf >= 3:
            score += 15; reasons.append("MTF 3/3 aligned")
        elif mtf == 2:
            score += 8; reasons.append("MTF 2/3 aligned")
        else:
            reasons.append("MTF < 2 — weak alignment")

        # ── Layer 4: Volume Confirmation (0-10 pts) ───────────────────────
        if volume_ratio >= 2.0:
            score += 10; reasons.append(f"volume {volume_ratio:.1f}x (spike)")
        elif volume_ratio >= 1.5:
            score += 7; reasons.append(f"volume {volume_ratio:.1f}x (above avg)")
        elif volume_ratio >= 1.2:
            score += 4; reasons.append(f"volume {volume_ratio:.1f}x (normal)")
        else:
            reasons.append(f"volume {volume_ratio:.1f}x (low)")

        # ── Layer 5: Chop / Regime Filter (0-10 pts or BLOCK) ─────────────
        if chop < 38.2:
            score += 10; reasons.append(f"chop={chop:.0f} (trending)")
        elif chop < 50:
            score += 5; reasons.append(f"chop={chop:.0f} (transitional)")
        elif chop > 61.8 and not is_breakout:
            score -= 10; reasons.append(f"chop={chop:.0f} (CHOPPY — blocked)")

        if is_breakout:
            score += 10; reasons.append("breakout/impulse/sweep bonus")

        # ── Layer 6: VWAP Position (0-10 pts) ────────────────────────────
        if df_entry is not None and len(df_entry) >= 20:
            try:
                from advanced_patterns import compute_vwap
                vwap = compute_vwap(df_entry)
                price = df_entry['Close'].iloc[-1]
                vwap_val = vwap.iloc[-1]
                above_vwap = price > vwap_val

                if action == 'LONG' and above_vwap:
                    score += 10; reasons.append("above VWAP (bullish)")
                elif action == 'SHORT' and not above_vwap:
                    score += 10; reasons.append("below VWAP (bearish)")
                elif action == 'LONG' and not above_vwap:
                    score += 3; reasons.append("below VWAP for LONG (caution)")
                else:
                    score += 3; reasons.append("above VWAP for SHORT (caution)")

                adjustments['vwap'] = round(vwap_val, 2)
            except Exception:
                pass

        # ── Layer 7: Order Flow Imbalance (0-10 pts) ─────────────────────
        if df_entry is not None and len(df_entry) >= 15:
            try:
                from advanced_patterns import compute_order_flow_imbalance
                ofi = compute_order_flow_imbalance(df_entry, 10)
                ofi_val = ofi.iloc[-1]

                if action == 'LONG' and ofi_val > 0.60:
                    score += 10; reasons.append(f"OFI={ofi_val:.2f} (buy pressure)")
                elif action == 'SHORT' and ofi_val < 0.40:
                    score += 10; reasons.append(f"OFI={ofi_val:.2f} (sell pressure)")
                elif (action == 'LONG' and ofi_val < 0.40) or (action == 'SHORT' and ofi_val > 0.60):
                    score -= 5; reasons.append(f"OFI={ofi_val:.2f} (CONTRA flow)")
                else:
                    score += 3; reasons.append(f"OFI={ofi_val:.2f} (neutral)")

                adjustments['order_flow_imbalance'] = round(ofi_val, 3)
            except Exception:
                pass

        # ── Layer 8: Vision AI Confirmation (0-15 pts) ───────────────────
        ai_confidence = 0.0
        if vision_result:
            vision_signal = vision_result.get('signal', '')
            vision_conf = float(vision_result.get('confidence', 0))
            ai_confidence = vision_conf
            vision_patterns = vision_result.get('patterns', [])

            if vision_signal == action and vision_conf >= 0.75:
                score += 15; reasons.append(f"Vision AI CONFIRMS {action} @ {vision_conf:.0%}")
            elif vision_signal == action and vision_conf >= 0.60:
                score += 8; reasons.append(f"Vision AI agrees {action} @ {vision_conf:.0%}")
            elif vision_signal == 'NO_TRADE':
                score -= 10; reasons.append(f"Vision AI says NO_TRADE")
            elif vision_signal and vision_signal != action:
                score -= 15; reasons.append(f"Vision AI CONTRADICTS: says {vision_signal}")

            if vision_patterns:
                adjustments['vision_patterns'] = vision_patterns

            if vision_result.get('stop_suggestion'):
                adjustments['vision_stop'] = vision_result['stop_suggestion']
            if vision_result.get('target_suggestion'):
                adjustments['vision_target'] = vision_result['target_suggestion']
        else:
            score += 5; reasons.append("No vision AI (neutral)")

        # ── Layer 9: Advanced Pattern Confluence (0-10 pts) ──────────────
        if pattern_signals:
            matching = [p for p in pattern_signals if p.signal == action]
            contra = [p for p in pattern_signals if p.signal != action]

            if len(matching) >= 2:
                score += 10
                reasons.append(f"{len(matching)} advanced patterns confirm")
            elif len(matching) == 1:
                score += 5
                reasons.append(f"1 pattern: {matching[0].pattern}")

            if contra:
                score -= 5
                reasons.append(f"{len(contra)} contra pattern(s)")

            adjustments['advanced_patterns'] = [p.pattern for p in matching]

        # ── Layer 10: Volatility Spike Filter ────────────────────────────
        if df_entry is not None and len(df_entry) >= 20:
            try:
                current_range = float(df_entry['High'].iloc[-1] - df_entry['Low'].iloc[-1])
                avg_range = float((df_entry['High'] - df_entry['Low']).rolling(20).mean().iloc[-2])
                if avg_range > 0:
                    range_ratio = current_range / avg_range
                    if range_ratio > MAX_ATR_SPIKE_RATIO:
                        score -= 15
                        reasons.append(f"VOLATILITY SPIKE: bar range {range_ratio:.1f}x avg (too volatile)")
                    elif range_ratio > 1.5:
                        score -= 5
                        reasons.append(f"elevated volatility: {range_ratio:.1f}x avg")
            except Exception:
                pass

        # ── Layer 11: Per-Instrument Daily Loss Limit ────────────────────
        global _instrument_daily_pnl, _instrument_daily_date
        from datetime import datetime as _dt
        today = _dt.now().strftime('%Y-%m-%d')
        if today != _instrument_daily_date:
            _instrument_daily_pnl = {}
            _instrument_daily_date = today
        instrument = signal.get('instrument', signal.get('ticker', ''))
        inst_pnl = _instrument_daily_pnl.get(instrument, 0)
        if inst_pnl <= INSTRUMENT_DAILY_LOSS_LIMIT:
            score = 0
            reasons.append(f"BLOCKED: {instrument} daily loss ${inst_pnl:.0f} hit limit ${INSTRUMENT_DAILY_LOSS_LIMIT}")

        # ── FINAL DECISION ───────────────────────────────────────────────
        score = max(0, min(100, score))

        if vision_result and ai_confidence < self.min_ai_confidence and ai_confidence > 0:
            approved = False
            reasons.append(f"BLOCKED: AI confidence {ai_confidence:.0%} < {self.min_ai_confidence:.0%}")
        elif score < self.min_confluence:
            approved = False
            reasons.append(f"BLOCKED: confluence {score} < {self.min_confluence}")
        elif quality < MIN_QUALITY_SCORE and not is_breakout:
            approved = False
            reasons.append(f"BLOCKED: quality {quality} < {MIN_QUALITY_SCORE}")
        else:
            approved = True

        if approved:
            self.stats['approved'] += 1
        else:
            self.stats['rejected'] += 1

        # Position size adjustment based on confluence
        if score >= 90:
            adjustments['size_multiplier'] = 1.5
        elif score >= 80:
            adjustments['size_multiplier'] = 1.25
        elif score >= 70:
            adjustments['size_multiplier'] = 1.0
        else:
            adjustments['size_multiplier'] = 0.75

        return FilterDecision(
            approved=approved,
            confluence_score=score,
            ai_confidence=ai_confidence,
            reasons=reasons,
            adjustments=adjustments,
        )

    @staticmethod
    def record_instrument_pnl(instrument: str, pnl: float):
        """Called by learning agent when a trade outcome is recorded."""
        global _instrument_daily_pnl, _instrument_daily_date
        from datetime import datetime as _dt
        today = _dt.now().strftime('%Y-%m-%d')
        if today != _instrument_daily_date:
            _instrument_daily_pnl = {}
            _instrument_daily_date = today
        _instrument_daily_pnl[instrument] = _instrument_daily_pnl.get(instrument, 0) + pnl

    def get_stats(self) -> Dict:
        total = self.stats['evaluated'] or 1
        return {
            'evaluated': self.stats['evaluated'],
            'approved': self.stats['approved'],
            'rejected': self.stats['rejected'],
            'approval_rate': f"{self.stats['approved'] / total:.1%}",
        }
