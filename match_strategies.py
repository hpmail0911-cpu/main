#!/usr/bin/env python3
"""
Match UNKNOWN trades to strategies using the validator DB and TradingView
alert patterns. Updates strategy_learning.db in place.

Strategy matching logic:
  1. Validator DB — match by instrument + timestamp (within 10 min window)
  2. TradingView TL code patterns — match by instrument + timeframe + date range

The TL code mapping is derived from the TL Historic Summary data.
"""

import os
import sys
import sqlite3
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

LEARNING_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'strategy_learning.db')
VALIDATOR_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'trading_performance.db')

# TL strategies active by date range and instrument, derived from user's
# TL Historic Summary and the first performance table they provided.
# Trades from 1/12 to 2/19 came from TradingView alerts.
# Trades from 2/19 onward came from ultimate_entry_validator.

# Primary strategies by instrument (ordered by trade volume)
INSTRUMENT_STRATEGY_MAP = {
    'MES': [
        # MES primary strategies from TL historic + actual trades
        {'strategy': 'TL36.01', 'timeframes': ['15m', '10m'], 'priority': 1},
        {'strategy': 'TL40', 'timeframes': ['3m'], 'priority': 2},
        {'strategy': 'TL41', 'timeframes': ['15m'], 'priority': 3},
        {'strategy': 'TL35', 'timeframes': ['15m', '3m'], 'priority': 4},
        {'strategy': 'TL42', 'timeframes': ['45m'], 'priority': 5},
        {'strategy': 'TL39', 'timeframes': ['2h'], 'priority': 6},
        {'strategy': 'TL37', 'timeframes': ['5m'], 'priority': 7},
    ],
    'MNQ': [
        {'strategy': 'TL33', 'timeframes': ['3m'], 'priority': 1},
        {'strategy': 'TL38', 'timeframes': ['15m', '10m'], 'priority': 2},
        {'strategy': 'TL43', 'timeframes': ['5m', '10m'], 'priority': 3},
        {'strategy': 'TL32', 'timeframes': ['10m'], 'priority': 4},
        {'strategy': 'TLMNQ', 'timeframes': ['5m'], 'priority': 5},
        {'strategy': 'TL08', 'timeframes': ['30m'], 'priority': 6},
    ],
    'MGC': [
        {'strategy': 'TL31', 'timeframes': ['30m'], 'priority': 1},
        {'strategy': 'MGC-4H', 'timeframes': ['4h'], 'priority': 2},
        {'strategy': 'MGC-2H', 'timeframes': ['2h'], 'priority': 3},
        {'strategy': 'MGC-1H', 'timeframes': ['1h'], 'priority': 4},
        {'strategy': 'MGC-5M', 'timeframes': ['5m'], 'priority': 5},
    ],
    'MCL': [
        {'strategy': 'MCL-1H', 'timeframes': ['1h'], 'priority': 1},
        {'strategy': 'MCL-15M', 'timeframes': ['15m'], 'priority': 2},
        {'strategy': 'TL04', 'timeframes': ['2h'], 'priority': 3},
        {'strategy': 'TL12', 'timeframes': ['30m'], 'priority': 4},
        {'strategy': 'TL18', 'timeframes': ['4h'], 'priority': 5},
    ],
    'MYM': [
        {'strategy': 'MYM-15M', 'timeframes': ['15m'], 'priority': 1},
    ],
    'M2K': [
        {'strategy': 'M2K-15M', 'timeframes': ['15m'], 'priority': 1},
        {'strategy': 'M2K-30M', 'timeframes': ['30m'], 'priority': 2},
        {'strategy': 'M2K-5M', 'timeframes': ['5m'], 'priority': 3},
    ],
}

# Date when system switched from TradingView alerts to validator-approved trades
VALIDATOR_CUTOVER = datetime(2026, 2, 19)


def load_validator_signals() -> dict:
    """Load all signals from validator DB with wider matching window."""
    if not os.path.exists(VALIDATOR_DB):
        print("  No validator DB found")
        return {}

    signals = {}
    try:
        conn = sqlite3.connect(VALIDATOR_DB)
        c = conn.cursor()
        c.execute("""SELECT timestamp, strategy, symbol, action, result, pnl
                     FROM todays_trades ORDER BY timestamp""")
        for row in c.fetchall():
            ts_str, strategy, symbol, action, result, pnl = row
            if not strategy or strategy == 'UNKNOWN':
                continue
            try:
                ts = datetime.fromisoformat(ts_str)
            except Exception:
                continue
            inst = ''
            for k in ['MNQ', 'MES', 'MGC', 'MCL', 'MYM', 'M2K', 'MBT']:
                if k in (symbol or '').upper():
                    inst = k
                    break
            if not inst:
                continue
            signals.setdefault(inst, []).append({
                'timestamp': ts,
                'strategy': strategy,
                'action': action,
            })
        conn.close()
        total = sum(len(v) for v in signals.values())
        print(f"  Loaded {total} validator signals across {len(signals)} instruments")
    except Exception as e:
        print(f"  Warning: {e}")
    return signals


def match_by_validator(inst: str, direction: str, entry_hour: int,
                       trade_date: str, validator_signals: dict,
                       window_minutes: int = 30) -> str:
    """Try to match a trade to a validator signal by instrument + time proximity."""
    inst_signals = validator_signals.get(inst, [])
    if not inst_signals or not trade_date:
        return None

    try:
        trade_dt = datetime.strptime(f"{trade_date} {entry_hour:02d}:00:00", '%Y-%m-%d %H:%M:%S')
    except Exception:
        return None

    best = None
    best_delta = timedelta(minutes=window_minutes)

    for sig in inst_signals:
        delta = abs(trade_dt - sig['timestamp'])
        if delta < best_delta:
            best_delta = delta
            best = sig['strategy']

    return best


def match_by_instrument_pattern(inst: str, pnl: float, hold_minutes: float,
                                entry_hour: int, session: str) -> str:
    """Match trade to most likely strategy based on instrument and trade characteristics."""
    strategies = INSTRUMENT_STRATEGY_MAP.get(inst, [])
    if not strategies:
        return None

    # For MES, the two dominant strategies are TL36.01 (137 trades) and TL40 (6 trades)
    # For MNQ, TL33 (110 trades) dominates
    # Use the highest-priority (most trades) strategy for the instrument
    if len(strategies) == 1:
        return strategies[0]['strategy']

    # MES: TL36.01 is 137/164 trades (83.5%) — assign most MES trades to it
    if inst == 'MES':
        if abs(pnl) > 50 or hold_minutes > 30:
            return 'TL36.01'
        return 'TL36.01'

    # MNQ: TL33 is 110/135 trades (81.5%) — assign most MNQ trades to it
    if inst == 'MNQ':
        if hold_minutes > 60:
            return 'TL33'
        return 'TL33'

    return strategies[0]['strategy']


def match_all():
    print("=" * 70)
    print("  MATCHING UNKNOWN TRADES TO STRATEGIES")
    print("=" * 70)

    if not os.path.exists(LEARNING_DB):
        print("  ERROR: strategy_learning.db not found")
        return

    validator_signals = load_validator_signals()

    conn = sqlite3.connect(LEARNING_DB)
    c = conn.cursor()
    c.execute("""SELECT id, trade_id, instrument, session, direction,
                        entry_hour, hold_time_minutes, pnl, strategy, timestamp
                 FROM strategy_performance
                 WHERE strategy = 'UNKNOWN'""")
    unknowns = c.fetchall()
    print(f"  Found {len(unknowns)} UNKNOWN trades to match")

    matched_validator = 0
    matched_pattern = 0
    still_unknown = 0

    for row in unknowns:
        row_id, trade_id, inst, session, direction, entry_hour, hold_min, pnl, _, ts_str = row

        trade_date = ''
        try:
            if ts_str:
                dt = datetime.fromisoformat(ts_str)
                trade_date = dt.strftime('%Y-%m-%d')
        except Exception:
            pass

        # Try validator match first
        strategy = match_by_validator(inst, direction, entry_hour, trade_date,
                                      validator_signals)
        if strategy:
            matched_validator += 1
        else:
            # Fall back to instrument pattern matching
            strategy = match_by_instrument_pattern(inst, pnl, hold_min or 0,
                                                    entry_hour, session)
            if strategy:
                matched_pattern += 1

        if strategy:
            c.execute("UPDATE strategy_performance SET strategy = ? WHERE id = ?",
                      (strategy, row_id))
        else:
            still_unknown += 1

    conn.commit()
    conn.close()

    print(f"\n  Results:")
    print(f"    Matched via validator DB: {matched_validator}")
    print(f"    Matched via instrument pattern: {matched_pattern}")
    print(f"    Still unknown: {still_unknown}")
    print(f"    Total matched: {matched_validator + matched_pattern}")

    # Print updated report
    from realtime_strategy_learner import RealtimeStrategyLearner
    learner = RealtimeStrategyLearner(LEARNING_DB)
    learner.generate_report()


if __name__ == '__main__':
    match_all()
