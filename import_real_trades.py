#!/usr/bin/env python3
"""
Import real trade data from TopStepX CSV export into the learning database.

Reads: trades_export (5).csv (TopStepX trade history)
Matches: trades to strategies using the validator's trading_performance.db
Writes: strategy_learning.db with real P&L, instruments, sessions, strategies

Usage:
  python3 import_real_trades.py                          # uses default CSV path
  python3 import_real_trades.py "trades_export (5).csv"  # specify CSV
"""

import csv
import os
import sys
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from realtime_strategy_learner import RealtimeStrategyLearner

LEARNING_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'strategy_learning.db')
VALIDATOR_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'trading_performance.db')

INSTRUMENT_MAP = {
    'MNQ': 'MNQ', 'MES': 'MES', 'MGC': 'MGC', 'MCL': 'MCL',
    'MYM': 'MYM', 'M2K': 'M2K', 'MBT': 'MBT',
}


def get_instrument(contract_name: str) -> str:
    for key in INSTRUMENT_MAP:
        if key in contract_name.upper():
            return key
    return 'UNKNOWN'


def classify_session(hour: int) -> str:
    if 9 <= hour < 16:
        return 'NY'
    elif 3 <= hour < 9:
        return 'London'
    elif 18 <= hour or hour < 3:
        return 'Asian'
    return 'UAE'


def parse_duration(dur_str: str) -> float:
    try:
        parts = dur_str.split(':')
        h = int(parts[0])
        m = int(parts[1])
        s = float(parts[2].split('.')[0])
        return h * 60 + m + s / 60
    except Exception:
        return 0.0


def load_validator_strategies() -> dict:
    """Load approved trades from validator DB to match strategies."""
    if not os.path.exists(VALIDATOR_DB):
        print(f"  Note: {VALIDATOR_DB} not found — strategies will be matched by timestamp")
        return {}

    signals = {}
    try:
        conn = sqlite3.connect(VALIDATOR_DB)
        c = conn.cursor()
        c.execute("""SELECT timestamp, strategy, symbol, action, result, pnl
                     FROM todays_trades ORDER BY timestamp""")
        for row in c.fetchall():
            ts_str, strategy, symbol, action, result, pnl = row
            try:
                ts = datetime.fromisoformat(ts_str)
            except Exception:
                continue
            inst = get_instrument(symbol or '')
            key = (inst, ts.strftime('%Y-%m-%d %H'))
            signals[key] = strategy or 'UNKNOWN'
        conn.close()
        print(f"  Loaded {len(signals)} validator signals for strategy matching")
    except Exception as e:
        print(f"  Warning: Could not read validator DB: {e}")
    return signals


def import_trades(csv_path: str):
    print("=" * 70)
    print("  IMPORTING REAL TRADES INTO LEARNING DATABASE")
    print("=" * 70)

    if not os.path.exists(csv_path):
        print(f"  ERROR: CSV not found: {csv_path}")
        return

    learner = RealtimeStrategyLearner(LEARNING_DB)

    conn = sqlite3.connect(LEARNING_DB)
    c = conn.cursor()
    c.execute("SELECT trade_id FROM strategy_performance")
    existing = {row[0] for row in c.fetchall()}
    conn.close()
    print(f"  Existing trades in learning DB: {len(existing)}")

    validator_strategies = load_validator_strategies()

    imported = 0
    skipped = 0
    errors = 0

    with open(csv_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            trade_id = str(row.get('Id', ''))
            if not trade_id or trade_id in existing:
                skipped += 1
                continue

            contract = row.get('ContractName', '')
            instrument = get_instrument(contract)
            pnl = float(row.get('PnL', 0))
            direction = row.get('Type', 'Long')
            entry_price = float(row.get('EntryPrice', 0))
            exit_price = float(row.get('ExitPrice', 0))
            size = int(row.get('Size', 1))
            duration = parse_duration(row.get('TradeDuration', '00:00:00'))

            entry_time_str = row.get('EnteredAt', '')
            entry_hour = 12
            trade_date = ''
            try:
                parts = entry_time_str.rsplit(' ', 1)
                if len(parts) == 2 and ('+' in parts[1] or '-' in parts[1]):
                    fixed = parts[0] + parts[1].replace(':', '')
                    dt = datetime.strptime(fixed, '%m/%d/%Y %H:%M:%S%z')
                else:
                    dt = datetime.strptime(entry_time_str.split('.')[0], '%m/%d/%Y %H:%M:%S')
                entry_hour = dt.hour
                trade_date = dt.strftime('%Y-%m-%d')
            except Exception:
                try:
                    dt_str = entry_time_str.split(' -')[0].split(' +')[0]
                    dt = datetime.strptime(dt_str, '%m/%d/%Y %H:%M:%S')
                    entry_hour = dt.hour
                    trade_date = dt.strftime('%Y-%m-%d')
                except Exception:
                    pass

            strategy = 'UNKNOWN'
            key = (instrument, f"{trade_date} {entry_hour:02d}" if trade_date else '')
            if key in validator_strategies:
                strategy = validator_strategies[key]

            trade = {
                'trade_id': trade_id,
                'instrument': instrument,
                'direction': direction,
                'entry_price': entry_price,
                'exit_price': exit_price,
                'pnl': pnl,
                'entry_hour': entry_hour,
                'hold_time_minutes': duration,
                'strategy': strategy,
                'quality_score': 0,
                'adx': 0,
                'confidence': 0,
            }

            try:
                learner.record_trade(trade)
                imported += 1
                existing.add(trade_id)
            except Exception as e:
                errors += 1

    print(f"\n  Results:")
    print(f"    Imported: {imported}")
    print(f"    Skipped (already in DB): {skipped}")
    print(f"    Errors: {errors}")

    learner.generate_report()

    print("\n  Per-instrument breakdown:")
    conn = sqlite3.connect(LEARNING_DB)
    c = conn.cursor()
    c.execute("""SELECT instrument,
                        COUNT(*) as trades,
                        SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) as wins,
                        SUM(pnl) as total_pnl,
                        AVG(pnl) as avg_pnl
                 FROM strategy_performance
                 GROUP BY instrument
                 ORDER BY total_pnl DESC""")
    for row in c.fetchall():
        inst, trades, wins, total, avg = row
        wr = wins / trades * 100 if trades > 0 else 0
        print(f"    {inst}: {trades} trades, {wr:.1f}% WR, ${total:+.2f} total, ${avg:+.2f} avg")

    c.execute("""SELECT COUNT(*),
                        SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END),
                        SUM(pnl)
                 FROM strategy_performance""")
    total_row = c.fetchone()
    if total_row and total_row[0]:
        trades, wins, total_pnl = total_row
        print(f"\n    TOTAL: {trades} trades, {wins / trades * 100:.1f}% WR, ${total_pnl:+.2f}")
    conn.close()
    print("=" * 70)


if __name__ == '__main__':
    csv_path = sys.argv[1] if len(sys.argv) > 1 else 'trades_export (5).csv'
    import_trades(csv_path)
