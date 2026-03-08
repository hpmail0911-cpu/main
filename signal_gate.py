#!/usr/bin/env python3
"""
Signal Gate — shared integration point for Chart Vision AI, Advanced Patterns,
and Enhanced Signal Filter. All 3 scanners call gate_signal() before sending.

This is the single function that sits between "scanner found a signal" and
"signal gets sent to the validator". It runs Vision AI + pattern scan +
confluence scoring and returns a GO/NO-GO decision.

Usage (in any scanner):
    from signal_gate import gate_signal, init_gate
    init_gate()  # once at startup

    # In send_signal():
    decision = gate_signal(instrument, action, signal_data, df_1m, df_5m, df_15m)
    if not decision['approved']:
        return False  # blocked
    # Apply adjustments from decision
    stop_loss = decision.get('stop_loss', stop_loss)
    take_profit = decision.get('take_profit', take_profit)
    position_size *= decision.get('size_multiplier', 1.0)
"""

import os
import sys
import logging
import time
from typing import Dict, Optional

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logger = logging.getLogger('signal_gate')

_vision_ai = None
_signal_filter = None
_initialized = False

_VISION_COOLDOWN = {}
VISION_COOLDOWN_SECONDS = 30


def init_gate():
    """Initialize Vision AI and Signal Filter. Call once at scanner startup."""
    global _vision_ai, _signal_filter, _initialized

    if _initialized:
        return

    try:
        from chart_vision_ai import ChartVisionAI
        _vision_ai = ChartVisionAI()
        if _vision_ai.is_available():
            logger.info("Signal Gate: Chart Vision AI ACTIVE (GPT-4o)")
        else:
            logger.info("Signal Gate: Chart Vision AI disabled (no API key or matplotlib)")
    except Exception as e:
        logger.warning(f"Signal Gate: Vision AI init error: {e}")
        _vision_ai = None

    try:
        from enhanced_signal_filter import EnhancedSignalFilter
        _signal_filter = EnhancedSignalFilter()
        logger.info("Signal Gate: Enhanced Signal Filter ACTIVE "
                     f"(min_confluence={_signal_filter.min_confluence}, "
                     f"min_ai_conf={_signal_filter.min_ai_confidence})")
    except Exception as e:
        logger.warning(f"Signal Gate: Filter init error: {e}")
        _signal_filter = None

    _initialized = True


def gate_signal(instrument: str, action: str, signal_data: Dict,
                df_1m: pd.DataFrame = None, df_5m: pd.DataFrame = None,
                df_15m: pd.DataFrame = None) -> Dict:
    """Run a signal through Vision AI + Advanced Patterns + Enhanced Filter.

    Returns:
        {
            'approved': bool,
            'confluence_score': int,
            'ai_confidence': float,
            'reasons': [...],
            'size_multiplier': float,
            'stop_loss': float or None (vision-suggested),
            'take_profit': float or None (vision-suggested),
            'vision_patterns': [...],
            'advanced_patterns': [...],
        }
    """
    if not _initialized:
        init_gate()

    result = {
        'approved': True,
        'confluence_score': 0,
        'ai_confidence': 0.0,
        'reasons': [],
        'size_multiplier': 1.0,
        'stop_loss': None,
        'take_profit': None,
        'vision_patterns': [],
        'advanced_patterns': [],
    }

    if not _signal_filter:
        return result

    vision_result = None
    pattern_signals = None
    df_entry = df_5m if df_5m is not None and len(df_5m) >= 20 else df_1m

    # ── Advanced Pattern Scan ────────────────────────────────────────────
    if df_1m is not None and df_5m is not None and df_15m is not None:
        try:
            from advanced_patterns import scan_advanced_patterns
            pattern_signals = scan_advanced_patterns(
                instrument, df_1m, df_5m, df_15m, min_confidence=65
            )
            if pattern_signals:
                result['advanced_patterns'] = [p.pattern for p in pattern_signals]
                logger.info(f"  Patterns: {', '.join(result['advanced_patterns'])}")
        except Exception as e:
            logger.debug(f"Pattern scan error: {e}")

    # ── Chart Vision AI (rate-limited per instrument) ────────────────────
    if (_vision_ai and _vision_ai.is_available() and
            df_1m is not None and df_5m is not None and df_15m is not None):

        now = time.time()
        last_call = _VISION_COOLDOWN.get(instrument, 0)
        if now - last_call >= VISION_COOLDOWN_SECONDS:
            try:
                vision_result = _vision_ai.analyze(
                    instrument, df_1m, df_5m, df_15m,
                    current_signal=action
                )
                _VISION_COOLDOWN[instrument] = now

                if vision_result:
                    result['vision_patterns'] = vision_result.get('patterns', [])
                    if vision_result.get('signal') == 'NO_TRADE':
                        logger.info(f"  Vision AI: NO_TRADE — {vision_result.get('reason', '')}")
            except Exception as e:
                logger.debug(f"Vision AI error: {e}")
        else:
            logger.debug(f"Vision AI cooldown for {instrument} "
                         f"({VISION_COOLDOWN_SECONDS - (now - last_call):.0f}s remaining)")

    # ── Enhanced Signal Filter ───────────────────────────────────────────
    decision = _signal_filter.evaluate(
        signal=signal_data,
        df_entry=df_entry,
        vision_result=vision_result,
        pattern_signals=pattern_signals,
    )

    result['approved'] = decision.approved
    result['confluence_score'] = decision.confluence_score
    result['ai_confidence'] = decision.ai_confidence
    result['reasons'] = decision.reasons
    result['size_multiplier'] = decision.adjustments.get('size_multiplier', 1.0)

    if decision.adjustments.get('vision_stop'):
        result['stop_loss'] = decision.adjustments['vision_stop']
    if decision.adjustments.get('vision_target'):
        result['take_profit'] = decision.adjustments['vision_target']

    status = "APPROVED" if decision.approved else "BLOCKED"
    logger.info(f"  Gate: {status} | Confluence: {decision.confluence_score}/100 | "
                f"AI: {decision.ai_confidence:.0%} | "
                f"{'; '.join(decision.reasons[-3:])}")

    return result


def get_gate_stats() -> Dict:
    """Return combined stats from Vision AI + Filter."""
    stats = {}
    if _vision_ai:
        stats['vision'] = _vision_ai.get_stats()
    if _signal_filter:
        stats['filter'] = _signal_filter.get_stats()
    return stats
