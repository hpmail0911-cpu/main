"""
Realtime Strategy Learner — records closed trades and generates
performance reports per strategy/instrument/session.

Used by learning_monitor_fixed.py via:
    learner = RealtimeStrategyLearner()
    learner.record_trade(trade_dict)
    learner.generate_report()
    learner.learning_db   # path to the SQLite database
"""

import sqlite3
import logging
from datetime import datetime
from typing import Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_DB = "strategy_learning.db"


class RealtimeStrategyLearner:
    def __init__(self, db_path: str = DEFAULT_DB):
        self.learning_db = db_path
        self._init_db()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _init_db(self):
        conn = sqlite3.connect(self.learning_db)
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS strategy_performance (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id      TEXT UNIQUE,
                timestamp     TEXT,
                strategy      TEXT,
                instrument    TEXT,
                direction     TEXT,
                entry_price   REAL,
                exit_price    REAL,
                pnl           REAL,
                outcome       TEXT,
                session       TEXT,
                entry_hour    INTEGER,
                hold_time_min REAL,
                quality_score INTEGER DEFAULT 0,
                adx           REAL    DEFAULT 0,
                confidence    REAL    DEFAULT 0
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS strategy_summary (
                strategy   TEXT,
                instrument TEXT,
                session    TEXT,
                trades     INTEGER DEFAULT 0,
                wins       INTEGER DEFAULT 0,
                losses     INTEGER DEFAULT 0,
                avg_pnl    REAL    DEFAULT 0,
                win_rate   REAL    DEFAULT 0,
                updated_at TEXT,
                PRIMARY KEY (strategy, instrument, session)
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_sp_trade_id "
                  "ON strategy_performance (trade_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_sp_strategy "
                  "ON strategy_performance (strategy)")
        conn.commit()
        conn.close()

    # ------------------------------------------------------------------
    # Record a single closed trade
    # ------------------------------------------------------------------

    def record_trade(self, trade: Dict):
        """Persist one trade to the learning DB.

        Expected keys in *trade*:
            trade_id, instrument, direction, entry_price, exit_price,
            pnl, entry_hour, hold_time_minutes, strategy,
            confidence, quality_score, adx
        """
        trade_id = str(trade.get("trade_id", ""))
        if not trade_id:
            return

        pnl = float(trade.get("pnl", 0))
        outcome = "WIN" if pnl > 0 else "LOSS"

        session = self._classify_session(int(trade.get("entry_hour", 12)))

        conn = sqlite3.connect(self.learning_db)
        c = conn.cursor()
        try:
            c.execute("""
                INSERT OR IGNORE INTO strategy_performance
                    (trade_id, timestamp, strategy, instrument, direction,
                     entry_price, exit_price, pnl, outcome, session,
                     entry_hour, hold_time_min, quality_score, adx, confidence)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade_id,
                datetime.now().isoformat(),
                trade.get("strategy", "UNKNOWN"),
                trade.get("instrument", "UNKNOWN"),
                trade.get("direction", "UNKNOWN"),
                float(trade.get("entry_price", 0)),
                float(trade.get("exit_price", 0)),
                pnl,
                outcome,
                session,
                int(trade.get("entry_hour", 12)),
                float(trade.get("hold_time_minutes", 0)),
                int(trade.get("quality_score", 0)),
                float(trade.get("adx", 0)),
                float(trade.get("confidence", 0)),
            ))
            conn.commit()
        except sqlite3.IntegrityError:
            pass
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Rebuild strategy_summary from raw trades
    # ------------------------------------------------------------------

    def generate_report(self):
        """Regenerate the strategy_summary table and print highlights."""
        conn = sqlite3.connect(self.learning_db)
        c = conn.cursor()

        c.execute("DELETE FROM strategy_summary")

        c.execute("""
            INSERT INTO strategy_summary
                (strategy, instrument, session, trades, wins, losses,
                 avg_pnl, win_rate, updated_at)
            SELECT
                strategy, instrument, session,
                COUNT(*)                                        AS trades,
                SUM(CASE WHEN outcome='WIN'  THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN outcome='LOSS' THEN 1 ELSE 0 END) AS losses,
                AVG(pnl)                                        AS avg_pnl,
                ROUND(CAST(SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) AS REAL)
                      / COUNT(*), 3)                            AS win_rate,
                ?
            FROM strategy_performance
            GROUP BY strategy, instrument, session
        """, (datetime.now().isoformat(),))

        conn.commit()

        c.execute("""
            SELECT strategy, instrument, session, trades, wins, losses,
                   avg_pnl, win_rate
            FROM strategy_summary
            ORDER BY trades DESC
            LIMIT 15
        """)
        rows = c.fetchall()
        conn.close()

        if rows:
            print("\n  === STRATEGY PERFORMANCE SUMMARY ===")
            for row in rows:
                strat, inst, sess, trades, wins, losses, avg_pnl, wr = row
                wr_pct = wr * 100 if wr else 0
                print(f"  {strat:20s} {inst:5s} {sess:8s} "
                      f"{trades:3d} trades  {wr_pct:5.1f}% WR  "
                      f"avg ${avg_pnl:+.2f}")
        else:
            print("  No strategy data to report yet.")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_session(hour: int) -> str:
        if 9 <= hour < 16:
            return "NY"
        if 3 <= hour < 9:
            return "London"
        return "Asian"
