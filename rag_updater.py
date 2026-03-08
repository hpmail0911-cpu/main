#!/usr/bin/env python3
"""
RAG Updater — backfills pattern_context and strategy_context in the RAG
database from actual trade outcomes stored in the learning DB.

Called periodically by the learning agent to keep the AI analyzer's
historical context accurate. The prompts sent to GPT-4o-mini and Llama
include pattern win rates and strategy reliability from these tables.

Reads:  strategy_learning.db  (trade outcomes)
Writes: rag_signals.db        (pattern_context, strategy_context)
"""

import os
import sys
import sqlite3
import logging
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logger = logging.getLogger('rag_updater')

LEARNING_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'strategy_learning.db')

try:
    from setup_rag_database import RAG_DB_PATH, init_rag_database
    RAG_AVAILABLE = True
except ImportError:
    RAG_AVAILABLE = False
    RAG_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               'rag_signals.db')


def update_strategy_context():
    """Rebuild strategy_context table from learning DB trade outcomes."""
    if not os.path.exists(LEARNING_DB):
        logger.info("No learning DB found — skipping strategy context update")
        return 0

    if RAG_AVAILABLE:
        init_rag_database()

    try:
        learn_conn = sqlite3.connect(LEARNING_DB)
        lc = learn_conn.cursor()
        lc.execute("""
            SELECT strategy, instrument,
                   COUNT(*) as total,
                   SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) as wins,
                   SUM(CASE WHEN outcome='LOSS' THEN 1 ELSE 0 END) as losses,
                   AVG(quality_score) as avg_q,
                   AVG(confidence) as avg_c
            FROM strategy_performance
            WHERE strategy != 'UNKNOWN'
            GROUP BY strategy, instrument
            HAVING total >= 3
        """)
        rows = lc.fetchall()
        learn_conn.close()

        if not rows:
            return 0

        rag_conn = sqlite3.connect(RAG_DB_PATH)
        rc = rag_conn.cursor()

        rc.execute("""CREATE TABLE IF NOT EXISTS strategy_context (
            strategy TEXT, instrument TEXT,
            total_signals INTEGER DEFAULT 0, approved_signals INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0, losses INTEGER DEFAULT 0,
            avg_quality REAL DEFAULT 0, avg_confidence REAL DEFAULT 0,
            win_rate REAL DEFAULT 0, last_updated DATETIME,
            PRIMARY KEY (strategy, instrument)
        )""")

        updated = 0
        for row in rows:
            strategy, inst, total, wins, losses, avg_q, avg_c = row
            wr = wins / total if total > 0 else 0
            rc.execute("""INSERT INTO strategy_context
                          (strategy, instrument, total_signals, approved_signals,
                           wins, losses, avg_quality, avg_confidence, win_rate, last_updated)
                          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                          ON CONFLICT(strategy, instrument) DO UPDATE SET
                              total_signals = ?,
                              approved_signals = ?,
                              wins = ?,
                              losses = ?,
                              avg_quality = ?,
                              avg_confidence = ?,
                              win_rate = ?,
                              last_updated = ?""",
                       (strategy, inst, total, total, wins, losses,
                        avg_q or 0, avg_c or 0, wr, datetime.now().isoformat(),
                        total, total, wins, losses, avg_q or 0, avg_c or 0, wr,
                        datetime.now().isoformat()))
            updated += 1

        rag_conn.commit()
        rag_conn.close()
        logger.info(f"Updated {updated} strategy context entries in RAG DB")
        return updated

    except Exception as e:
        logger.error(f"Strategy context update error: {e}")
        return 0


def update_pattern_context():
    """Rebuild pattern_context table from learning DB, mapping strategies to patterns."""
    if not os.path.exists(LEARNING_DB):
        return 0

    if RAG_AVAILABLE:
        init_rag_database()

    STRATEGY_TO_PATTERN = {
        'TREND_PULLBACK': 'TREND_PULLBACK',
        'MOMENTUM_CONT': 'MOMENTUM_CONT',
        'BREAKOUT': 'BREAKOUT',
        'IMPULSE': 'IMPULSE',
        'TREND_RESUMPTION': 'TREND_RESUMPTION',
        'EMA_CROSSOVER': 'EMA_CROSSOVER',
    }

    try:
        learn_conn = sqlite3.connect(LEARNING_DB)
        lc = learn_conn.cursor()
        lc.execute("""
            SELECT strategy, instrument, session,
                   COUNT(*) as total,
                   SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) as wins,
                   SUM(CASE WHEN outcome='LOSS' THEN 1 ELSE 0 END) as losses
            FROM strategy_performance
            WHERE strategy != 'UNKNOWN'
            GROUP BY strategy, instrument, session
            HAVING total >= 3
        """)
        rows = lc.fetchall()
        learn_conn.close()

        if not rows:
            return 0

        rag_conn = sqlite3.connect(RAG_DB_PATH)
        rc = rag_conn.cursor()

        rc.execute("""CREATE TABLE IF NOT EXISTS pattern_context (
            pattern TEXT, instrument TEXT, session TEXT,
            total_signals INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0, losses INTEGER DEFAULT 0,
            win_rate REAL DEFAULT 0, reliability INTEGER DEFAULT 65,
            last_updated DATETIME,
            PRIMARY KEY (pattern, instrument, session)
        )""")

        updated = 0
        for row in rows:
            strategy, inst, session, total, wins, losses = row
            pattern = strategy
            for prefix, pat in STRATEGY_TO_PATTERN.items():
                if prefix in strategy.upper():
                    pattern = pat
                    break

            wr = wins / total if total > 0 else 0
            reliability = min(int(wr * 100), 99) if total >= 5 else 65

            rc.execute("""INSERT INTO pattern_context
                          (pattern, instrument, session, total_signals,
                           wins, losses, win_rate, reliability, last_updated)
                          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                          ON CONFLICT(pattern, instrument, session) DO UPDATE SET
                              total_signals = ?,
                              wins = ?,
                              losses = ?,
                              win_rate = ?,
                              reliability = ?,
                              last_updated = ?""",
                       (pattern, inst, session, total, wins, losses, wr, reliability,
                        datetime.now().isoformat(),
                        total, wins, losses, wr, reliability, datetime.now().isoformat()))
            updated += 1

        rag_conn.commit()
        rag_conn.close()
        logger.info(f"Updated {updated} pattern context entries in RAG DB")
        return updated

    except Exception as e:
        logger.error(f"Pattern context update error: {e}")
        return 0


def update_all():
    """Run all RAG context updates."""
    s = update_strategy_context()
    p = update_pattern_context()
    return s + p
