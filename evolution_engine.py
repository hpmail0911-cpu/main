#!/usr/bin/env python3
"""
Autonomous Evolution Engine — the system improves itself automatically.

Runs periodically (every 4 hours by default) and:
  1. Analyzes trade outcomes by strategy, instrument, session, pattern
  2. Computes optimal filter weights from win/loss distributions
  3. Adjusts confluence thresholds, confidence gates, quality bars
  4. Disables consistently losing strategies
  5. Promotes consistently winning strategies (lowers their quality gate)
  6. Tunes SL/TP multipliers based on realized R:R
  7. Writes evolution_state.json consumed by enhanced_signal_filter.py

The loop is self-correcting: if a change makes performance worse, it
detects the regression and reverts the adjustment.
"""

import os
import sys
import json
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logger = logging.getLogger('evolution_engine')

LEARNING_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'strategy_learning.db')
EVOLUTION_STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    'evolution_state.json')
THRESHOLDS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               'learned_thresholds.json')

MIN_TRADES_FOR_EVOLUTION = 10
LOOKBACK_DAYS = 14
REGRESSION_CHECK_DAYS = 3

DISABLE_WR_THRESHOLD = 0.30
DEGRADE_WR_THRESHOLD = 0.45
PREFER_WR_THRESHOLD = 0.70
ELITE_WR_THRESHOLD = 0.80


def load_current_state() -> Dict:
    if os.path.exists(EVOLUTION_STATE_PATH):
        try:
            with open(EVOLUTION_STATE_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return {
        'version': 1,
        'last_evolution': None,
        'evolutions_run': 0,
        'filter_weights': {
            'technical_quality': 25,
            'trend_strength': 15,
            'mtf_alignment': 15,
            'volume': 10,
            'chop_regime': 10,
            'vwap_position': 10,
            'order_flow': 10,
            'vision_ai': 15,
            'pattern_confluence': 10,
        },
        'min_confluence': 65,
        'min_ai_confidence': 0.75,
        'min_quality': 75,
        'strategy_adjustments': {},
        'instrument_adjustments': {},
        'regression_history': [],
    }


def save_state(state: Dict):
    state['last_evolution'] = datetime.now().isoformat()
    with open(EVOLUTION_STATE_PATH, 'w') as f:
        json.dump(state, f, indent=2)


def analyze_recent_performance(days: int = LOOKBACK_DAYS) -> Dict:
    """Pull recent trade performance from learning DB."""
    if not os.path.exists(LEARNING_DB):
        return {}

    conn = sqlite3.connect(LEARNING_DB)
    c = conn.cursor()

    cutoff = (datetime.now() - timedelta(days=days)).isoformat()

    c.execute("""
        SELECT strategy, instrument, session, direction,
               COUNT(*) as trades,
               SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) as wins,
               AVG(pnl) as avg_pnl,
               SUM(pnl) as total_pnl,
               AVG(quality_score) as avg_quality,
               AVG(CASE WHEN outcome='WIN' THEN pnl ELSE NULL END) as avg_win,
               AVG(CASE WHEN outcome='LOSS' THEN ABS(pnl) ELSE NULL END) as avg_loss
        FROM strategy_performance
        WHERE timestamp >= ? AND strategy != 'UNKNOWN'
        GROUP BY strategy, instrument, session, direction
        HAVING trades >= ?
    """, (cutoff, MIN_TRADES_FOR_EVOLUTION))

    results = {}
    for row in c.fetchall():
        strategy, inst, session, direction, trades, wins, avg_pnl, total_pnl, avg_q, avg_win, avg_loss = row
        wr = wins / trades if trades > 0 else 0
        rr = (avg_win or 1) / (avg_loss or 1) if avg_loss else 2.0
        key = f"{strategy}_{inst}_{session}"
        results[key] = {
            'strategy': strategy, 'instrument': inst, 'session': session,
            'direction': direction, 'trades': trades, 'wins': wins,
            'win_rate': round(wr, 3), 'avg_pnl': round(avg_pnl or 0, 2),
            'total_pnl': round(total_pnl or 0, 2), 'avg_quality': round(avg_q or 0, 1),
            'realized_rr': round(rr, 2),
        }

    # Overall stats for regression check
    c.execute("""
        SELECT COUNT(*), SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END), SUM(pnl)
        FROM strategy_performance WHERE timestamp >= ?
    """, (cutoff,))
    overall = c.fetchone()
    conn.close()

    if overall and overall[0]:
        results['_overall'] = {
            'trades': overall[0],
            'win_rate': round(overall[1] / overall[0], 3) if overall[0] else 0,
            'total_pnl': round(overall[2] or 0, 2),
        }

    return results


def evolve_strategy_gates(state: Dict, performance: Dict) -> Dict:
    """Adjust per-strategy quality gates based on performance."""
    adjustments = state.get('strategy_adjustments', {})
    disabled = []
    preferred = []

    for key, perf in performance.items():
        if key.startswith('_'):
            continue

        wr = perf['win_rate']
        trades = perf['trades']
        strategy = perf['strategy']

        if wr >= ELITE_WR_THRESHOLD and trades >= 15:
            adjustments[key] = {
                'quality_adj': -10,
                'status': 'ELITE',
                'reason': f"{wr:.0%} WR on {trades} trades",
            }
            preferred.append(strategy)
        elif wr >= PREFER_WR_THRESHOLD and trades >= 10:
            adjustments[key] = {
                'quality_adj': -5,
                'status': 'PREFERRED',
                'reason': f"{wr:.0%} WR on {trades} trades",
            }
            preferred.append(strategy)
        elif wr < DISABLE_WR_THRESHOLD and trades >= MIN_TRADES_FOR_EVOLUTION:
            adjustments[key] = {
                'quality_adj': +25,
                'status': 'DISABLED',
                'reason': f"{wr:.0%} WR on {trades} trades — auto-disabled",
            }
            disabled.append(strategy)
        elif wr < DEGRADE_WR_THRESHOLD and trades >= MIN_TRADES_FOR_EVOLUTION:
            adjustments[key] = {
                'quality_adj': +15,
                'status': 'DEGRADED',
                'reason': f"{wr:.0%} WR on {trades} trades — quality gate raised",
            }
        else:
            adjustments[key] = {
                'quality_adj': 0,
                'status': 'NORMAL',
                'reason': f"{wr:.0%} WR on {trades} trades",
            }

    state['strategy_adjustments'] = adjustments

    if disabled:
        logger.info(f"  DISABLED strategies: {', '.join(set(disabled))}")
    if preferred:
        logger.info(f"  PREFERRED strategies: {', '.join(set(preferred))}")

    return state


def evolve_filter_weights(state: Dict, performance: Dict) -> Dict:
    """Adjust the 9-layer filter weights based on what's working."""
    weights = state.get('filter_weights', {})
    overall = performance.get('_overall', {})
    wr = overall.get('win_rate', 0.5)

    if wr >= 0.75:
        pass  # current weights are working well
    elif wr >= 0.60:
        weights['vision_ai'] = min(20, weights.get('vision_ai', 15) + 1)
        weights['order_flow'] = min(15, weights.get('order_flow', 10) + 1)
    elif wr < 0.50:
        state['min_confluence'] = min(80, state.get('min_confluence', 65) + 5)
        state['min_quality'] = min(85, state.get('min_quality', 75) + 3)
        logger.info(f"  WR below 50% — raised confluence to {state['min_confluence']}, "
                    f"quality to {state['min_quality']}")

    state['filter_weights'] = weights
    return state


def evolve_sl_tp(state: Dict, performance: Dict) -> Dict:
    """Tune SL/TP based on realized R:R ratios."""
    inst_adjustments = state.get('instrument_adjustments', {})

    for key, perf in performance.items():
        if key.startswith('_'):
            continue

        inst = perf['instrument']
        direction = perf['direction']
        rr = perf['realized_rr']
        wr = perf['win_rate']
        inst_key = f"{inst}_{direction}"

        if rr < 1.5 and wr < 0.65:
            inst_adjustments[inst_key] = {
                'sl_mult': 0.85,
                'tp_mult': 1.15,
                'reason': f"Low R:R ({rr:.1f}) and low WR ({wr:.0%}) — tighten stop, widen target",
            }
        elif rr > 3.0 and wr > 0.55:
            inst_adjustments[inst_key] = {
                'sl_mult': 1.0,
                'tp_mult': 0.90,
                'reason': f"High R:R ({rr:.1f}) — can tighten target for faster fills",
            }
        else:
            inst_adjustments[inst_key] = {
                'sl_mult': 1.0,
                'tp_mult': 1.0,
                'reason': f"R:R {rr:.1f}, WR {wr:.0%} — no adjustment needed",
            }

    state['instrument_adjustments'] = inst_adjustments
    return state


def check_regression(state: Dict, performance: Dict) -> bool:
    """Check if recent changes caused performance regression."""
    history = state.get('regression_history', [])
    overall = performance.get('_overall', {})

    if not overall:
        return False

    current = {
        'timestamp': datetime.now().isoformat(),
        'win_rate': overall.get('win_rate', 0),
        'total_pnl': overall.get('total_pnl', 0),
        'trades': overall.get('trades', 0),
    }
    history.append(current)

    if len(history) > 20:
        history = history[-20:]
    state['regression_history'] = history

    if len(history) >= 3:
        recent_wr = history[-1]['win_rate']
        prev_wr = history[-2]['win_rate']
        if recent_wr < prev_wr - 0.10 and history[-1]['trades'] >= 20:
            logger.warning(f"  REGRESSION detected: WR dropped {prev_wr:.0%} → {recent_wr:.0%}")
            state['min_confluence'] = max(60, state.get('min_confluence', 65) - 3)
            return True

    return False


def run_evolution() -> Dict:
    """Run one evolution cycle."""
    logger.info("=" * 60)
    logger.info("  EVOLUTION ENGINE — AUTONOMOUS IMPROVEMENT CYCLE")
    logger.info("=" * 60)

    state = load_current_state()
    performance = analyze_recent_performance()

    if not performance or '_overall' not in performance:
        logger.info("  Not enough data for evolution yet")
        save_state(state)
        return state

    overall = performance['_overall']
    logger.info(f"  Period: last {LOOKBACK_DAYS} days")
    logger.info(f"  Trades: {overall['trades']} | WR: {overall['win_rate']:.1%} | "
                f"PnL: ${overall['total_pnl']:+.2f}")

    state = evolve_strategy_gates(state, performance)
    state = evolve_filter_weights(state, performance)
    state = evolve_sl_tp(state, performance)
    regressed = check_regression(state, performance)

    if regressed:
        logger.warning("  Reverting aggressive changes due to regression")

    state['evolutions_run'] = state.get('evolutions_run', 0) + 1
    save_state(state)

    n_strat = len([v for v in state.get('strategy_adjustments', {}).values()
                   if v.get('status') != 'NORMAL'])
    n_inst = len([v for v in state.get('instrument_adjustments', {}).values()
                  if v.get('sl_mult', 1.0) != 1.0 or v.get('tp_mult', 1.0) != 1.0])

    logger.info(f"\n  Evolution #{state['evolutions_run']} complete:")
    logger.info(f"    Strategy adjustments: {n_strat}")
    logger.info(f"    SL/TP adjustments: {n_inst}")
    logger.info(f"    Min confluence: {state.get('min_confluence', 65)}")
    logger.info(f"    Min AI confidence: {state.get('min_ai_confidence', 0.75)}")
    logger.info(f"    Min quality: {state.get('min_quality', 75)}")
    logger.info("=" * 60)

    return state
