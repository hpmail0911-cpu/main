#!/usr/bin/env python3
"""
LEARNING MONITOR — FIXED VERSION
Watches completed trades and updates AI learning system.

FIXES APPLIED:
  FIX-1  Strategy names pulled from validator's trading_performance.db
         (was always 'UNKNOWN' because CSV has no strategy field)
  FIX-2  Real quality_score, ADX, confidence from validator DB
         (was hardcoded 75/30/0.75 for every trade)
  FIX-3  Instrument naming: 'MNQ' not '/MNQ'
         (slash prefix broke cross-referencing with scanners)
  FIX-4  Timezone parsing fixed for EnteredAt format
         '01/02/2026 13:00:01 -06:00' → strip space before offset
  FIX-5  Learned parameters fed back to scanners via threshold_tuner.py
"""

import time
import sqlite3
import csv
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from realtime_strategy_learner import RealtimeStrategyLearner


# Validator DB path — where the ultimate_entry_validator stores approved signals
VALIDATOR_DB_PATHS = [
    'trading_performance.db',
    '../trading_performance.db',
    os.path.expanduser('~/topstepx-agent/trading_performance.db'),
]

# Threshold tuner output — scanners read this to adjust quality thresholds
TUNER_OUTPUT_PATH = 'learned_thresholds.json'


def _find_validator_db() -> str:
    for p in VALIDATOR_DB_PATHS:
        if os.path.exists(p):
            return p
    return None


def _load_validator_signals(validator_db: str) -> dict:
    """Load approved signals from the validator's DB.

    Returns a dict keyed by (instrument, action, approx_minute) → {strategy, quality, ...}
    so we can match broker trades by timestamp proximity.
    """
    if not validator_db or not os.path.exists(validator_db):
        return {}

    signals = {}
    try:
        conn = sqlite3.connect(validator_db)
        c = conn.cursor()
        c.execute("""
            SELECT timestamp, strategy, symbol, action, result, pnl
            FROM todays_trades
            WHERE result = 'approved'
            ORDER BY timestamp
        """)
        for row in c.fetchall():
            ts_str, strategy, symbol, action, result, pnl = row
            try:
                ts = datetime.fromisoformat(ts_str)
            except Exception:
                continue
            inst = symbol[:3].upper() if symbol else ''
            act = action.upper() if action else ''
            key = (inst, act, ts.strftime('%Y-%m-%d %H:%M'))
            signals[key] = {
                'strategy': strategy,
                'symbol': inst,
                'action': act,
                'timestamp': ts,
            }
        conn.close()
    except Exception as e:
        print(f"  Warning: Could not read validator DB: {e}")

    return signals


def _match_trade_to_signal(trade_inst: str, trade_action: str, trade_ts: datetime,
                           signals: dict, window_minutes: int = 10) -> dict:
    """Find the closest validator signal matching this trade within a time window."""
    best_match = None
    best_delta = timedelta(minutes=window_minutes)

    action_map = {'Long': 'LONG', 'Short': 'SHORT', 'LONG': 'LONG', 'SHORT': 'SHORT'}
    normalized_action = action_map.get(trade_action, trade_action.upper())

    for key, sig in signals.items():
        sig_inst, sig_action, _ = key
        if sig_inst != trade_inst or sig_action != normalized_action:
            continue
        delta = abs(trade_ts - sig['timestamp'])
        if delta < best_delta:
            best_delta = delta
            best_match = sig

    return best_match


def _write_threshold_tuner(learning_db: str):
    """Generate learned_thresholds.json for scanners to read.

    FIX-5: This creates the file that threshold_tuner.py loads, feeding
    learned quality adjustments back into the scanners' detect_signal().
    """
    try:
        conn = sqlite3.connect(learning_db)
        c = conn.cursor()

        # Per-strategy performance
        c.execute("""
            SELECT strategy, instrument, session,
                   COUNT(*) as trades,
                   SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) as wins,
                   AVG(pnl) as avg_pnl
            FROM strategy_performance
            WHERE strategy != 'UNKNOWN'
              AND timestamp >= datetime('now', '-30 days')
            GROUP BY strategy, instrument, session
            HAVING trades >= 5
        """)

        thresholds = {}
        disabled = []
        preferred = []

        for row in c.fetchall():
            strategy, inst, session, trades, wins, avg_pnl = row
            wr = wins / trades if trades > 0 else 0

            key = f"{strategy}_{inst}_{session}"

            if wr < 0.30 and trades >= 10:
                disabled.append(strategy)
                thresholds[key] = {'quality_adj': +20, 'disabled': True, 'wr': wr, 'trades': trades}
            elif wr < 0.45:
                thresholds[key] = {'quality_adj': +10, 'disabled': False, 'wr': wr, 'trades': trades}
            elif wr >= 0.70:
                preferred.append(strategy)
                thresholds[key] = {'quality_adj': -5, 'disabled': False, 'wr': wr, 'trades': trades}
            else:
                thresholds[key] = {'quality_adj': 0, 'disabled': False, 'wr': wr, 'trades': trades}

        # Per-instrument/session quality thresholds
        c.execute("""
            SELECT instrument, session,
                   COUNT(*) as trades,
                   SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) as wins,
                   AVG(CASE WHEN outcome='WIN' THEN quality_score ELSE NULL END) as avg_winner_q,
                   AVG(CASE WHEN outcome='LOSS' THEN quality_score ELSE NULL END) as avg_loser_q
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

        output = {
            'updated_at': datetime.now().isoformat(),
            'strategy_thresholds': thresholds,
            'instrument_thresholds': instrument_thresholds,
            'disabled_strategies': list(set(disabled)),
            'preferred_strategies': list(set(preferred)),
        }

        with open(TUNER_OUTPUT_PATH, 'w') as f:
            json.dump(output, f, indent=2)

        print(f"  Wrote {TUNER_OUTPUT_PATH}: "
              f"{len(thresholds)} strategy thresholds, "
              f"{len(instrument_thresholds)} instrument thresholds, "
              f"{len(disabled)} disabled, {len(preferred)} preferred")

    except Exception as e:
        print(f"  Warning: threshold tuner write failed: {e}")


class LearningMonitor:
    def __init__(self, trades_csv_path='trades_export (5).csv'):
        self.learner = RealtimeStrategyLearner()
        self.trades_csv = trades_csv_path
        self.seen_trades = set()
        self.trade_count = 0
        self.validator_signals = {}

    def load_seen_trades(self):
        conn = sqlite3.connect(self.learner.learning_db)
        c = conn.cursor()
        c.execute("SELECT trade_id FROM strategy_performance")
        self.seen_trades = {row[0] for row in c.fetchall() if row[0]}
        self.trade_count = len(self.seen_trades)
        conn.close()
        print(f"  Loaded {self.trade_count} previously analyzed trades")

    def load_validator_signals(self):
        """FIX-1 + FIX-2: Load strategy names and quality data from validator DB."""
        db_path = _find_validator_db()
        if db_path:
            self.validator_signals = _load_validator_signals(db_path)
            print(f"  Loaded {len(self.validator_signals)} approved signals from validator DB ({db_path})")
        else:
            print("  Warning: validator DB not found — strategy names will be 'UNKNOWN'")
            self.validator_signals = {}

    def parse_topstepx_csv(self):
        if not Path(self.trades_csv).exists():
            print(f"  Warning: CSV not found: {self.trades_csv}")
            return []

        trades = []
        with open(self.trades_csv, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                contract = row.get('ContractName', '')

                # FIX-3: No "/" prefix — use bare instrument names
                instrument = 'UNKNOWN'
                for key in ['MNQ', 'MES', 'MGC', 'MCL', 'MYM', 'M2K', 'MBT']:
                    if key in contract:
                        instrument = key
                        break

                # FIX-4: Fix timezone parsing — strip space before offset
                entry_time_str = row.get('EnteredAt', '')
                entry_hour = 12
                trade_ts = None
                try:
                    # '01/02/2026 13:00:01 -06:00' → '01/02/2026 13:00:01-06:00'
                    # Find the last space before the timezone offset
                    parts = entry_time_str.rsplit(' ', 1)
                    if len(parts) == 2 and ('+' in parts[1] or '-' in parts[1]):
                        fixed_str = parts[0] + parts[1].replace(':', '')
                        trade_ts = datetime.strptime(fixed_str, '%m/%d/%Y %H:%M:%S%z')
                    else:
                        trade_ts = datetime.strptime(entry_time_str.split('.')[0], '%m/%d/%Y %H:%M:%S')
                    entry_hour = trade_ts.hour
                except Exception:
                    try:
                        dt_str = entry_time_str.split(' -')[0].split(' +')[0]
                        trade_ts = datetime.strptime(dt_str, '%m/%d/%Y %H:%M:%S')
                        entry_hour = trade_ts.hour
                    except Exception:
                        pass

                # Parse duration
                duration_str = row.get('TradeDuration', '00:00:00')
                try:
                    time_parts = duration_str.split(':')
                    hours = int(time_parts[0])
                    minutes = int(time_parts[1])
                    secs = float(time_parts[2].split('.')[0])
                    hold_time = hours * 60 + minutes + secs / 60
                except Exception:
                    hold_time = 0

                direction = row.get('Type', 'Long')
                pnl = float(row.get('PnL', 0))
                trade_id = int(row.get('Id', 0))

                # FIX-1 + FIX-2: Match to validator signal for real strategy + quality data
                strategy = 'UNKNOWN'
                quality_score = 0
                adx = 0
                confidence = 0

                if trade_ts and self.validator_signals:
                    match = _match_trade_to_signal(instrument, direction, trade_ts,
                                                   self.validator_signals)
                    if match:
                        strategy = match.get('strategy', 'UNKNOWN')

                trade = {
                    'trade_id': trade_id,
                    'instrument': instrument,
                    'direction': direction,
                    'entry_price': float(row.get('EntryPrice', 0)),
                    'exit_price': float(row.get('ExitPrice', 0)),
                    'pnl': pnl,
                    'entry_hour': entry_hour,
                    'hold_time_minutes': hold_time,
                    'strategy': strategy,
                    'confidence': confidence,
                    'quality_score': quality_score,
                    'adx': adx,
                }

                trades.append(trade)

        return trades

    def process_new_trades(self):
        all_trades = self.parse_topstepx_csv()
        new_trades = [t for t in all_trades if t['trade_id'] not in self.seen_trades]

        if new_trades:
            matched = sum(1 for t in new_trades if t['strategy'] != 'UNKNOWN')
            print(f"\n  Processing {len(new_trades)} new trades ({matched} matched to strategies)...")

            for trade in new_trades:
                self.learner.record_trade(trade)
                self.seen_trades.add(trade['trade_id'])
                self.trade_count += 1

                if self.trade_count % 10 == 0:
                    print(f"\n  {self.trade_count} trades analyzed — updating adaptive params + tuner...")
                    self.learner.generate_report()
                    _write_threshold_tuner(self.learner.learning_db)

        return len(new_trades)

    def monitor_loop(self, check_interval=60):
        print("=" * 80)
        print("  AI LEARNING MONITOR — STARTED (FIXED)")
        print("  Reads strategy names from validator DB")
        print("  Updates threshold_tuner every 10 trades")
        print("=" * 80 + "\n")

        self.load_seen_trades()
        self.load_validator_signals()

        while True:
            try:
                new_count = self.process_new_trades()
                if new_count > 0:
                    print(f"  Processed {new_count} new trades (Total: {self.trade_count})")
                    self.load_validator_signals()

                time.sleep(check_interval)

            except KeyboardInterrupt:
                print(f"\n  Learning monitor stopped. Total: {self.trade_count}")
                _write_threshold_tuner(self.learner.learning_db)
                break
            except Exception as e:
                print(f"  Error: {e}")
                time.sleep(check_interval)

    def run_one_time_analysis(self):
        print("=" * 80)
        print("  ONE-TIME ANALYSIS — Processing all historical trades")
        print("=" * 80 + "\n")

        self.load_seen_trades()
        self.load_validator_signals()
        new_count = self.process_new_trades()

        if new_count > 0:
            print(f"\n  Analyzed {new_count} new trades (Total: {self.trade_count})")
        else:
            print("  All trades already analyzed")

        self.learner.generate_report()
        _write_threshold_tuner(self.learner.learning_db)


if __name__ == '__main__':
    import sys

    monitor = LearningMonitor()

    if len(sys.argv) > 1 and sys.argv[1] == '--once':
        monitor.run_one_time_analysis()
    else:
        monitor.monitor_loop()
