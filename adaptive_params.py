#!/usr/bin/env python3
"""
Adaptive Parameter Tuner — adjusts SL/TP multipliers, position sizing,
and quality gates based on actual trade outcome data.

Reads:   strategy_learning.db
Writes:  learned_thresholds.json  (consumed by threshold_tuner.py)
         adaptive_params table in strategy_learning.db
         risk_config.json updates (optional)

Called by the learning agent after new outcomes are processed.
"""

import os
import sys
import json
import sqlite3
import logging
from datetime import datetime
from typing import Dict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logger = logging.getLogger('adaptive_params')

LEARNING_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'strategy_learning.db')
THRESHOLDS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               'learned_thresholds.json')
RISK_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                'risk_config.json')

MIN_TRADES_FOR_ADJUSTMENT = 5
MIN_TRADES_FOR_DISABLE = 10
DISABLE_WIN_RATE = 0.30
PREFER_WIN_RATE = 0.70
DEGRADE_WIN_RATE = 0.45


def compute_strategy_thresholds() -> Dict:
    """Compute per-strategy quality adjustments from trade history."""
    if not os.path.exists(LEARNING_DB):
        return {}

    conn = sqlite3.connect(LEARNING_DB)
    c = conn.cursor()

    c.execute("""
        SELECT strategy, instrument, session,
               COUNT(*) as trades,
               SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) as wins,
               AVG(pnl) as avg_pnl,
               AVG(quality_score) as avg_q,
               AVG(CASE WHEN outcome='WIN' THEN pnl ELSE NULL END) as avg_win_pnl,
               AVG(CASE WHEN outcome='LOSS' THEN pnl ELSE NULL END) as avg_loss_pnl
        FROM strategy_performance
        WHERE strategy != 'UNKNOWN'
          AND timestamp >= datetime('now', '-30 days')
        GROUP BY strategy, instrument, session
        HAVING trades >= ?
    """, (MIN_TRADES_FOR_ADJUSTMENT,))

    thresholds = {}
    disabled = []
    preferred = []

    for row in c.fetchall():
        strategy, inst, session, trades, wins, avg_pnl, avg_q, avg_win, avg_loss = row
        wr = wins / trades if trades > 0 else 0
        key = f"{strategy}_{inst}_{session}"

        if wr < DISABLE_WIN_RATE and trades >= MIN_TRADES_FOR_DISABLE:
            disabled.append(strategy)
            quality_adj = +20
            thresholds[key] = {
                'quality_adj': quality_adj, 'disabled': True,
                'wr': round(wr, 3), 'trades': trades,
                'avg_pnl': round(avg_pnl or 0, 2),
            }
        elif wr < DEGRADE_WIN_RATE:
            quality_adj = +10
            thresholds[key] = {
                'quality_adj': quality_adj, 'disabled': False,
                'wr': round(wr, 3), 'trades': trades,
                'avg_pnl': round(avg_pnl or 0, 2),
            }
        elif wr >= PREFER_WIN_RATE:
            preferred.append(strategy)
            quality_adj = -5
            thresholds[key] = {
                'quality_adj': quality_adj, 'disabled': False,
                'wr': round(wr, 3), 'trades': trades,
                'avg_pnl': round(avg_pnl or 0, 2),
            }
        else:
            quality_adj = 0
            thresholds[key] = {
                'quality_adj': quality_adj, 'disabled': False,
                'wr': round(wr, 3), 'trades': trades,
                'avg_pnl': round(avg_pnl or 0, 2),
            }

        _write_adaptive_param(c, strategy, inst, session, 'quality_adj', quality_adj)
        _write_adaptive_param(c, strategy, inst, session, 'win_rate', wr)
        _write_adaptive_param(c, strategy, inst, session, 'avg_pnl', avg_pnl or 0)

    conn.commit()

    c.execute("""
        SELECT instrument, session,
               COUNT(*) as trades,
               SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) as wins,
               AVG(CASE WHEN outcome='WIN' THEN quality_score ELSE NULL END) as avg_win_q,
               AVG(CASE WHEN outcome='LOSS' THEN quality_score ELSE NULL END) as avg_lose_q
        FROM strategy_performance
        WHERE timestamp >= datetime('now', '-30 days')
          AND quality_score > 0
        GROUP BY instrument, session
        HAVING trades >= 10
    """)

    instrument_thresholds = {}
    for row in c.fetchall():
        inst, session, trades, wins, avg_win_q, avg_lose_q = row
        if avg_win_q and avg_lose_q:
            suggested_min = (avg_win_q + avg_lose_q) / 2
            instrument_thresholds[f"{inst}_{session}"] = {
                'suggested_min_quality': round(suggested_min, 1),
                'avg_winner_quality': round(avg_win_q, 1),
                'avg_loser_quality': round(avg_lose_q, 1),
                'win_rate': round(wins / trades, 3),
                'trades': trades,
            }

    conn.close()

    return {
        'strategy_thresholds': thresholds,
        'instrument_thresholds': instrument_thresholds,
        'disabled_strategies': list(set(disabled)),
        'preferred_strategies': list(set(preferred)),
    }


def compute_sl_tp_adjustments() -> Dict:
    """Compute SL/TP multiplier suggestions from outcome data."""
    if not os.path.exists(LEARNING_DB):
        return {}

    conn = sqlite3.connect(LEARNING_DB)
    c = conn.cursor()

    c.execute("""
        SELECT instrument, direction,
               COUNT(*) as trades,
               SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) as wins,
               AVG(CASE WHEN outcome='WIN' THEN pnl ELSE NULL END) as avg_win,
               AVG(CASE WHEN outcome='LOSS' THEN ABS(pnl) ELSE NULL END) as avg_loss,
               AVG(hold_time_minutes) as avg_hold
        FROM strategy_performance
        WHERE timestamp >= datetime('now', '-14 days')
        GROUP BY instrument, direction
        HAVING trades >= 5
    """)

    adjustments = {}
    for row in c.fetchall():
        inst, direction, trades, wins, avg_win, avg_loss, avg_hold = row
        wr = wins / trades if trades > 0 else 0.5
        rr = (avg_win or 1) / (avg_loss or 1) if avg_loss else 2.0

        if rr < 1.5 and wr < 0.65:
            sl_adj = 0.85
            tp_adj = 1.15
        elif rr > 3.0 and wr > 0.55:
            sl_adj = 1.0
            tp_adj = 0.90
        else:
            sl_adj = 1.0
            tp_adj = 1.0

        if wr > 0.75 and trades >= 10:
            size_adj = 1.25
        elif wr < 0.40 and trades >= 10:
            size_adj = 0.75
        else:
            size_adj = 1.0

        key = f"{inst}_{direction}"
        adjustments[key] = {
            'sl_multiplier': round(sl_adj, 2),
            'tp_multiplier': round(tp_adj, 2),
            'size_multiplier': round(size_adj, 2),
            'win_rate': round(wr, 3),
            'realized_rr': round(rr, 2),
            'avg_hold_minutes': round(avg_hold or 0, 1),
            'trades': trades,
        }

        _write_adaptive_param(c, '*', inst, direction, 'sl_multiplier', sl_adj)
        _write_adaptive_param(c, '*', inst, direction, 'tp_multiplier', tp_adj)
        _write_adaptive_param(c, '*', inst, direction, 'size_multiplier', size_adj)

    conn.commit()
    conn.close()
    return adjustments


def _write_adaptive_param(cursor, strategy: str, instrument: str,
                          session: str, param_name: str, param_value: float):
    cursor.execute("""INSERT INTO adaptive_params
                      (strategy, instrument, session, param_name, param_value, updated_at)
                      VALUES (?, ?, ?, ?, ?, ?)
                      ON CONFLICT(strategy, instrument, session, param_name)
                      DO UPDATE SET param_value = ?, updated_at = ?""",
                   (strategy, instrument, session, param_name, param_value,
                    datetime.now().isoformat(), param_value, datetime.now().isoformat()))


def write_learned_thresholds():
    """Compute all adaptive parameters and write learned_thresholds.json."""
    thresholds = compute_strategy_thresholds()
    sl_tp = compute_sl_tp_adjustments()

    thresholds['sl_tp_adjustments'] = sl_tp
    thresholds['updated_at'] = datetime.now().isoformat()

    with open(THRESHOLDS_PATH, 'w') as f:
        json.dump(thresholds, f, indent=2)

    n_strat = len(thresholds.get('strategy_thresholds', {}))
    n_inst = len(thresholds.get('instrument_thresholds', {}))
    n_sltp = len(sl_tp)
    n_dis = len(thresholds.get('disabled_strategies', []))
    n_pref = len(thresholds.get('preferred_strategies', []))

    logger.info(f"Wrote learned_thresholds.json: "
                f"{n_strat} strategies, {n_inst} instruments, "
                f"{n_sltp} SL/TP adjustments, "
                f"{n_dis} disabled, {n_pref} preferred")

    return thresholds


def get_adaptive_param(instrument: str, direction: str, param: str,
                       default: float = 1.0) -> float:
    """Read a single adaptive parameter. Used by scanner/validator at runtime."""
    if not os.path.exists(LEARNING_DB):
        return default
    try:
        conn = sqlite3.connect(LEARNING_DB)
        c = conn.cursor()
        c.execute("""SELECT param_value FROM adaptive_params
                     WHERE instrument = ? AND session = ? AND param_name = ?
                     ORDER BY updated_at DESC LIMIT 1""",
                  (instrument, direction, param))
        row = c.fetchone()
        conn.close()
        return row[0] if row else default
    except Exception:
        return default
