#!/usr/bin/env python3
"""
RAG Database — stores and retrieves strategy/pattern context for AI analysis.

Tracks historical signal performance so the AI analyzer can reference
win rates, reliability scores, and pattern history when validating signals.

API used by llama_analyzer_openai.py:
  init_rag_database()
  get_strategy_context(strategy, symbol) → dict
  get_pattern_context(pattern, symbol, session) → dict
  log_signal_to_rag(strategy, instrument, action, quality_score, pattern,
                    ai_approved, ai_confidence)
"""

import sqlite3
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)

RAG_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'rag_signals.db')

_initialized = False


def init_rag_database(db_path: str = None):
    """Create RAG tables if they don't exist."""
    global _initialized
    path = db_path or RAG_DB_PATH

    conn = sqlite3.connect(path)
    c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS signal_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        strategy TEXT,
        instrument TEXT,
        action TEXT,
        quality_score INTEGER,
        pattern TEXT,
        ai_approved INTEGER,
        ai_confidence REAL,
        outcome TEXT DEFAULT 'PENDING',
        pnl REAL DEFAULT 0,
        timestamp DATETIME
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_rag_strategy ON signal_log (strategy)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_rag_instrument ON signal_log (instrument)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_rag_pattern ON signal_log (pattern)')

    c.execute('''CREATE TABLE IF NOT EXISTS strategy_context (
        strategy TEXT,
        instrument TEXT,
        total_signals INTEGER DEFAULT 0,
        approved_signals INTEGER DEFAULT 0,
        wins INTEGER DEFAULT 0,
        losses INTEGER DEFAULT 0,
        avg_quality REAL DEFAULT 0,
        avg_confidence REAL DEFAULT 0,
        win_rate REAL DEFAULT 0,
        last_updated DATETIME,
        PRIMARY KEY (strategy, instrument)
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS pattern_context (
        pattern TEXT,
        instrument TEXT,
        session TEXT,
        total_signals INTEGER DEFAULT 0,
        wins INTEGER DEFAULT 0,
        losses INTEGER DEFAULT 0,
        win_rate REAL DEFAULT 0,
        reliability INTEGER DEFAULT 65,
        last_updated DATETIME,
        PRIMARY KEY (pattern, instrument, session)
    )''')

    conn.commit()
    conn.close()
    _initialized = True


def _ensure_initialized():
    if not _initialized:
        init_rag_database()


def get_strategy_context(strategy: str, symbol: str) -> dict:
    """Return historical performance context for a strategy on a symbol."""
    _ensure_initialized()
    instrument = symbol[:3].upper() if symbol else ''

    try:
        conn = sqlite3.connect(RAG_DB_PATH)
        c = conn.cursor()
        c.execute("""SELECT total_signals, approved_signals, wins, losses,
                            avg_quality, avg_confidence, win_rate
                     FROM strategy_context
                     WHERE strategy = ? AND instrument = ?""",
                  (strategy, instrument))
        row = c.fetchone()
        conn.close()

        if row:
            return {
                'total_signals': row[0],
                'approved_signals': row[1],
                'wins': row[2],
                'losses': row[3],
                'avg_quality': row[4],
                'avg_confidence': row[5],
                'win_rate': row[6],
            }
    except Exception as e:
        logger.debug(f"RAG strategy context error: {e}")

    return {}


def get_pattern_context(pattern: str, symbol: str, session: str) -> dict:
    """Return historical performance context for a pattern on a symbol/session."""
    _ensure_initialized()
    instrument = symbol[:3].upper() if symbol else ''

    try:
        conn = sqlite3.connect(RAG_DB_PATH)
        c = conn.cursor()
        c.execute("""SELECT total_signals, wins, losses, win_rate, reliability
                     FROM pattern_context
                     WHERE pattern = ? AND instrument = ? AND session = ?""",
                  (pattern, instrument, session))
        row = c.fetchone()
        conn.close()

        if row:
            return {
                'total_signals': row[0],
                'wins': row[1],
                'losses': row[2],
                'win_rate': row[3],
                'reliability': row[4],
            }
    except Exception as e:
        logger.debug(f"RAG pattern context error: {e}")

    return {}


def log_signal_to_rag(strategy: str = '', instrument: str = '',
                      action: str = '', quality_score: int = 0,
                      pattern: str = '', ai_approved: bool = True,
                      ai_confidence: float = 0.0):
    """Log a signal decision to the RAG database for future reference."""
    _ensure_initialized()
    instrument = instrument[:3].upper() if instrument else ''

    try:
        conn = sqlite3.connect(RAG_DB_PATH)
        c = conn.cursor()

        c.execute("""INSERT INTO signal_log
                     (strategy, instrument, action, quality_score, pattern,
                      ai_approved, ai_confidence, timestamp)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                  (strategy, instrument, action, quality_score, pattern,
                   1 if ai_approved else 0, ai_confidence,
                   datetime.now().isoformat()))

        c.execute("""INSERT INTO strategy_context
                     (strategy, instrument, total_signals, approved_signals,
                      avg_quality, avg_confidence, last_updated)
                     VALUES (?, ?, 1, ?, ?, ?, ?)
                     ON CONFLICT(strategy, instrument) DO UPDATE SET
                         total_signals = total_signals + 1,
                         approved_signals = approved_signals + ?,
                         avg_quality = ((avg_quality * total_signals) + ?) / (total_signals + 1),
                         avg_confidence = ((avg_confidence * total_signals) + ?) / (total_signals + 1),
                         last_updated = ?""",
                  (strategy, instrument,
                   1 if ai_approved else 0,
                   ai_confidence, quality_score,
                   datetime.now().isoformat(),
                   1 if ai_approved else 0,
                   quality_score,
                   ai_confidence,
                   datetime.now().isoformat()))

        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug(f"RAG log error: {e}")


def update_signal_outcome(signal_id: int, outcome: str, pnl: float = 0.0):
    """Update a previously logged signal with its trade outcome."""
    _ensure_initialized()
    try:
        conn = sqlite3.connect(RAG_DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE signal_log SET outcome = ?, pnl = ? WHERE id = ?",
                  (outcome, pnl, signal_id))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug(f"RAG outcome update error: {e}")
