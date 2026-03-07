#!/usr/bin/env python3
"""
Realtime Strategy Learner — records trade outcomes and generates adaptive
performance reports used by the threshold tuner and learning monitor.

DB schema (strategy_performance):
  trade_id, strategy, instrument, session, direction, entry_hour,
  hold_time_minutes, outcome, pnl, quality_score, adx, confidence,
  timestamp
"""

import sqlite3
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

LEARNING_DB = 'strategy_learning.db'


def _current_session() -> str:
    et = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-5)))
    h = et.hour
    if 9 <= h < 16:
        return 'NY'
    elif 3 <= h < 9:
        return 'London'
    return 'Asian'


class RealtimeStrategyLearner:
    def __init__(self, db_path: str = LEARNING_DB):
        self.learning_db = db_path
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.learning_db)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS strategy_performance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id TEXT UNIQUE,
            strategy TEXT,
            instrument TEXT,
            session TEXT,
            direction TEXT,
            entry_hour INTEGER,
            hold_time_minutes REAL,
            outcome TEXT,
            pnl REAL,
            quality_score INTEGER DEFAULT 0,
            adx REAL DEFAULT 0,
            confidence REAL DEFAULT 0,
            timestamp DATETIME
        )''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_sp_strategy ON strategy_performance (strategy)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_sp_instrument ON strategy_performance (instrument)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_sp_session ON strategy_performance (session)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_sp_outcome ON strategy_performance (outcome)')
        c.execute('''CREATE TABLE IF NOT EXISTS adaptive_params (
            strategy TEXT,
            instrument TEXT,
            session TEXT,
            param_name TEXT,
            param_value REAL,
            updated_at DATETIME,
            PRIMARY KEY (strategy, instrument, session, param_name)
        )''')
        conn.commit()
        conn.close()

    def record_trade(self, trade: dict):
        """Record a completed trade into the learning database."""
        trade_id = str(trade.get('trade_id', ''))
        if not trade_id:
            return

        pnl = float(trade.get('pnl', 0))
        outcome = 'WIN' if pnl > 0 else 'LOSS'
        instrument = trade.get('instrument', 'UNKNOWN')
        direction = trade.get('direction', 'Long')
        entry_hour = int(trade.get('entry_hour', 12))
        session = self._classify_session(entry_hour)

        conn = sqlite3.connect(self.learning_db)
        c = conn.cursor()
        try:
            c.execute('''INSERT OR IGNORE INTO strategy_performance
                (trade_id, strategy, instrument, session, direction,
                 entry_hour, hold_time_minutes, outcome, pnl,
                 quality_score, adx, confidence, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (trade_id,
                 trade.get('strategy', 'UNKNOWN'),
                 instrument,
                 session,
                 direction,
                 entry_hour,
                 float(trade.get('hold_time_minutes', 0)),
                 outcome,
                 pnl,
                 int(trade.get('quality_score', 0)),
                 float(trade.get('adx', 0)),
                 float(trade.get('confidence', 0)),
                 datetime.now().isoformat()))
            conn.commit()
        except sqlite3.IntegrityError:
            pass
        finally:
            conn.close()

    def _classify_session(self, entry_hour: int) -> str:
        if 9 <= entry_hour < 16:
            return 'NY'
        elif 3 <= entry_hour < 9:
            return 'London'
        return 'Asian'

    def generate_report(self):
        """Print a summary of strategy performance from the learning DB."""
        conn = sqlite3.connect(self.learning_db)
        c = conn.cursor()

        c.execute("""
            SELECT strategy, instrument, session,
                   COUNT(*) as trades,
                   SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) as wins,
                   SUM(pnl) as total_pnl,
                   AVG(pnl) as avg_pnl
            FROM strategy_performance
            GROUP BY strategy, instrument, session
            ORDER BY total_pnl DESC
        """)
        rows = c.fetchall()

        if not rows:
            print("\n  No trades recorded yet.")
            conn.close()
            return

        print("\n" + "=" * 80)
        print("  STRATEGY PERFORMANCE REPORT")
        print("=" * 80)
        print(f"  {'Strategy':<20} {'Inst':<6} {'Session':<8} {'Trades':>6} {'WR':>8} {'Total PnL':>12} {'Avg PnL':>10}")
        print("  " + "-" * 74)

        for row in rows:
            strategy, inst, session, trades, wins, total_pnl, avg_pnl = row
            wr = wins / trades * 100 if trades > 0 else 0
            print(f"  {strategy:<20} {inst:<6} {session:<8} {trades:>6} {wr:>7.1f}% ${total_pnl:>10.2f} ${avg_pnl:>8.2f}")

        # Overall stats
        c.execute("""
            SELECT COUNT(*) as trades,
                   SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) as wins,
                   SUM(pnl) as total_pnl
            FROM strategy_performance
        """)
        total = c.fetchone()
        if total and total[0] > 0:
            trades, wins, total_pnl = total
            wr = wins / trades * 100
            print("  " + "-" * 74)
            print(f"  {'TOTAL':<20} {'':6} {'':8} {trades:>6} {wr:>7.1f}% ${total_pnl:>10.2f}")

        # Per-session summary
        c.execute("""
            SELECT session,
                   COUNT(*) as trades,
                   SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) as wins,
                   SUM(pnl) as total_pnl
            FROM strategy_performance
            GROUP BY session
        """)
        sessions = c.fetchall()
        if sessions:
            print("\n  SESSION BREAKDOWN:")
            for session, trades, wins, total_pnl in sessions:
                wr = wins / trades * 100 if trades > 0 else 0
                print(f"    {session}: {trades} trades, {wr:.1f}% WR, ${total_pnl:.2f}")

        print("=" * 80 + "\n")
        conn.close()

    def get_strategy_stats(self, strategy: str, instrument: str = None,
                           session: str = None) -> dict:
        """Return performance stats for a specific strategy."""
        conn = sqlite3.connect(self.learning_db)
        c = conn.cursor()

        query = "SELECT COUNT(*), SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END), AVG(pnl), SUM(pnl) FROM strategy_performance WHERE strategy = ?"
        params = [strategy]

        if instrument:
            query += " AND instrument = ?"
            params.append(instrument)
        if session:
            query += " AND session = ?"
            params.append(session)

        c.execute(query, params)
        row = c.fetchone()
        conn.close()

        if row and row[0] > 0:
            trades, wins, avg_pnl, total_pnl = row
            return {
                'trades': trades,
                'wins': wins,
                'losses': trades - wins,
                'win_rate': wins / trades,
                'avg_pnl': avg_pnl or 0,
                'total_pnl': total_pnl or 0,
            }
        return {'trades': 0, 'wins': 0, 'losses': 0, 'win_rate': 0, 'avg_pnl': 0, 'total_pnl': 0}
