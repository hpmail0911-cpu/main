#!/usr/bin/env python3
"""
ULTIMATE ENTRY VALIDATOR
Maximum filtering - only perfect setups get through!

FIX-1  expire_stale_positions() double-count bug fixed — overlapping DELETE
        rowcounts were being summed, making expired count wrong/misleading.
FIX-2  POSITION_EXPIRY_MINUTES reduced from 480→90 min. 8-hour expiry meant
        stale DB rows from morning blocked afternoon signals permanently.
FIX-3  Instrument-aware expiry: equity index = 90 min, commodities = 180 min.
        Matches actual trade durations and prevents phantom blocking.
FIX-4  Added GET /reset_positions endpoint for fast manual clearing.
FIX-5  expire_stale_positions() now returns accurate count (no double-count).
FIX-6  Stop loss clamped to prop firm max BEFORE validation — ATR-based stops
        no longer block every trade when ATR > max_stop (e.g. MGC ATR=20 > 1.8).
FIX-7  Take profit computed from clamped stop × R:R, not raw ATR × 3.
FIX-8  Removed triple-duplicated TESTING_MODE blocks and validation calls.
FIX-9  Fixed /positions endpoint referencing undefined 'signal' variable.
"""

from flask import Flask, request, jsonify
import requests
import json
import logging
import sqlite3
from datetime import datetime, time, timedelta
from typing import Dict, Optional, List, Tuple
try:
    from market_condition_engine import evaluate_conditions, get_news_status, ConditionState
    _MCE_AVAILABLE = True
except ImportError:
    _MCE_AVAILABLE = False
import os
import threading
from validator_market_conditions import (
    validate_stop_loss, validate_market_conditions,
    clamp_stop_to_prop_firm, get_prop_firm_config, round_to_tick,
    PROP_FIRM_STOPS,
)


# ==============================================================================
# TRADERSPOST CONFIGURATION
# ==============================================================================
TRADERSPOST_WEBHOOK = (
    "https://webhooks.traderspost.io/trading/webhook/"
    "40eea1cf-bea2-4c17-91c5-c420ac82fe4d/"
    "c76209cc75f83179acacac7c16ffc8f4"
)

try:
    from llama_analyzer_openai import HybridMarketAnalyzer
    LLAMA_AVAILABLE = True
except ImportError:
    HybridMarketAnalyzer = None
    LLAMA_AVAILABLE = False
    logging.warning("⚠️ llama_analyzer_openai not found — AI layer disabled (all other layers active)")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ==========================================
# TESTING MODE - 1 CONTRACT ONLY
# ==========================================
TESTING_MODE = True
LOCKED_POSITION_SIZE = 1
MAX_POSITION_SIZE = 1


app = Flask(__name__)

# ============================================================================
# SIGNAL DEDUPLICATION + THREAD LOCK
# ============================================================================
_signal_lock = threading.Lock()
_recent_signals: dict = {}
DEDUP_WINDOW_SECONDS = 60

def is_duplicate_signal(symbol: str, action: str) -> bool:
    key = f"{symbol.upper()}_{action.upper()}"
    last_time = _recent_signals.get(key)
    if last_time and (datetime.now() - last_time).total_seconds() < DEDUP_WINDOW_SECONDS:
        return True
    return False

def record_signal(symbol: str, action: str):
    key = f"{symbol.upper()}_{action.upper()}"
    _recent_signals[key] = datetime.now()

try:
    if LLAMA_AVAILABLE:
        analyzer = HybridMarketAnalyzer(use_openai=True)
        logger.info("✅ AI Analyzer initialized")
    else:
        analyzer = None
        logger.info("ℹ️ AI Analyzer skipped (llama_analyzer_openai not installed)")
except Exception as e:
    logger.error(f"❌ Failed to initialize analyzer: {e}")
    analyzer = None


# ============================================================================
# CONFIGURATION
# ============================================================================

DB_PATH = "trading_performance.db"

MAX_DAILY_LOSSES = 3
DAILY_PROFIT_TARGET = 500
MAX_DAILY_DRAWDOWN = -300

MAX_CONCURRENT_POSITIONS = 6
MAX_POSITION_SIZE_TOTAL = 1

AVOID_FIRST_MINUTES = 5
AVOID_LAST_MINUTES = 10
LUNCH_START = time(11, 30)
LUNCH_END = time(13, 0)

ATR_RANGES = {
    'MNQ': (10,  100),
    'MES': (5,    50),
    'MYM': (20,  200),
    'M2K': (0.5,  20),
    'MGC': (2,    30),
    'MCL': (0.1,   3),
}
ATR_MIN_MNQ = 10
ATR_MAX_MNQ = 100
ATR_MIN_MES = 5
ATR_MAX_MES = 50

VOLUME_MULTIPLIER = 1.2
GAP_COOLDOWN_MINUTES = 10
MIN_SPY_CORRELATION = 0.7
MAX_CHOP_INDEX = 60

POSITION_EXPIRY_BY_INSTRUMENT = {
    'MNQ': 30,
    'MES': 30,
    'MYM': 30,
    'M2K': 30,
    'MGC': 60,
    'MCL': 60,
}
POSITION_EXPIRY_DEFAULT_MINUTES = 30

STRATEGY_RISK_PARAMS = {
    'PT-TL5': {
        'MNQ': {'stop_loss_points': 10, 'take_profit_points': 40, 'default_position_size': 1, 'max_position_size': 2, 'min_rr_ratio': 2.0},
        'MES': {'stop_loss_points': 5, 'take_profit_points': 20, 'default_position_size': 1, 'max_position_size': 2, 'min_rr_ratio': 2.0}
    },
    'TL33': {'MNQ': {'stop_loss_points': 8, 'take_profit_points': 20, 'default_position_size': 1, 'max_position_size': 2, 'min_rr_ratio': 2.0}},
    'PT-TL33': {'MNQ': {'stop_loss_points': 8, 'take_profit_points': 25, 'default_position_size': 1, 'max_position_size': 2, 'min_rr_ratio': 2.0}},
    'PT-TL38': {'MNQ': {'stop_loss_points': 12, 'take_profit_points': 30, 'default_position_size': 1, 'max_position_size': 1, 'min_rr_ratio': 2.0}},
    'TL36.01-v2': {'MES': {'stop_loss_points': 8, 'take_profit_points': 20, 'default_position_size': 1, 'max_position_size': 2, 'min_rr_ratio': 2.0}},
    'TL40': {'MES': {'stop_loss_points': 6, 'take_profit_points': 15, 'default_position_size': 1, 'max_position_size': 1, 'min_rr_ratio': 2.0}},
    'TL43': {'MNQ': {'stop_loss_points': 15, 'take_profit_points': 60, 'default_position_size': 1, 'max_position_size': 1, 'min_rr_ratio': 2.0}},
    'MYM-1H':  {'MYM': {'stop_loss_points': 20, 'take_profit_points': 40, 'default_position_size': 1, 'max_position_size': 2, 'min_rr_ratio': 2.0}},
    'MYM-15M': {'MYM': {'stop_loss_points': 20, 'take_profit_points': 40, 'default_position_size': 1, 'max_position_size': 2, 'min_rr_ratio': 2.0}},
    'MYM-30M': {'MYM': {'stop_loss_points': 20, 'take_profit_points': 40, 'default_position_size': 1, 'max_position_size': 2, 'min_rr_ratio': 2.0}},
    'MYM-5M':  {'MYM': {'stop_loss_points': 15, 'take_profit_points': 30, 'default_position_size': 1, 'max_position_size': 2, 'min_rr_ratio': 2.0}},
    'MYM-10M': {'MYM': {'stop_loss_points': 20, 'take_profit_points': 40, 'default_position_size': 1, 'max_position_size': 2, 'min_rr_ratio': 2.0}},
    'MYM-1D':  {'MYM': {'stop_loss_points': 30, 'take_profit_points': 60, 'default_position_size': 1, 'max_position_size': 1, 'min_rr_ratio': 2.0}},
}


# ============================================================================
# DATABASE SETUP
# ============================================================================

def init_database():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS daily_performance
                 (date TEXT PRIMARY KEY,
                  trades INTEGER DEFAULT 0,
                  wins INTEGER DEFAULT 0,
                  losses INTEGER DEFAULT 0,
                  total_pnl REAL DEFAULT 0,
                  max_drawdown REAL DEFAULT 0,
                  stopped_out INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS todays_trades
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  timestamp TEXT,
                  strategy TEXT,
                  symbol TEXT,
                  action TEXT,
                  result TEXT,
                  pnl REAL DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS open_positions
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  strategy TEXT,
                  symbol TEXT,
                  action TEXT,
                  quantity INTEGER,
                  entry_time TEXT,
                  entry_price REAL,
                  stop_loss REAL,
                  take_profit REAL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS gaps
                 (symbol TEXT,
                  gap_time TEXT,
                  gap_size REAL)''')
    conn.commit()
    conn.close()


def get_daily_stats() -> Dict:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    today = datetime.now().strftime('%Y-%m-%d')
    c.execute("SELECT * FROM daily_performance WHERE date = ?", (today,))
    row = c.fetchone()
    conn.close()
    if row:
        return {'trades': row[1], 'wins': row[2], 'losses': row[3],
                'total_pnl': row[4], 'max_drawdown': row[5], 'stopped_out': row[6]}
    return {'trades': 0, 'wins': 0, 'losses': 0, 'total_pnl': 0.0,
            'max_drawdown': 0.0, 'stopped_out': 0}


def record_trade(strategy: str, symbol: str, action: str, result: str = 'pending', pnl: float = 0.0):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""INSERT INTO todays_trades (timestamp, strategy, symbol, action, result, pnl)
                 VALUES (?, ?, ?, ?, ?, ?)""",
              (datetime.now().isoformat(), strategy, symbol, action, result, pnl))
    today = datetime.now().strftime('%Y-%m-%d')
    c.execute("INSERT OR IGNORE INTO daily_performance (date) VALUES (?)", (today,))
    c.execute("UPDATE daily_performance SET trades = trades + 1 WHERE date = ?", (today,))
    conn.commit()
    conn.close()


# ============================================================================
# POSITION EXPIRY — instrument-aware, no double-count
# ============================================================================

def expire_stale_positions() -> int:
    """
    Remove stale positions by two rules (no double-counting):
      Rule A: older than per-instrument expiry window
      Rule B: from a previous calendar day

    Returns the number of rows actually deleted.
    """
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    c.execute("SELECT id, symbol, entry_time FROM open_positions")
    rows = c.fetchall()

    ids_to_delete = []
    for row_id, symbol, entry_time_str in rows:
        try:
            entry_dt = datetime.fromisoformat(entry_time_str)
        except Exception:
            ids_to_delete.append(row_id)
            continue

        if entry_dt < today_start:
            ids_to_delete.append(row_id)
            continue

        sym_key = (symbol or '')[:3].upper()
        expiry_min = POSITION_EXPIRY_BY_INSTRUMENT.get(sym_key, POSITION_EXPIRY_DEFAULT_MINUTES)
        if now - entry_dt > timedelta(minutes=expiry_min):
            ids_to_delete.append(row_id)

    if ids_to_delete:
        placeholders = ','.join('?' * len(ids_to_delete))
        c.execute(f"DELETE FROM open_positions WHERE id IN ({placeholders})", ids_to_delete)

    total = c.rowcount if ids_to_delete else 0
    conn.commit()
    conn.close()

    if total:
        logger.info(f"🕐 Auto-expired {total} stale position(s)")
    return total


def get_open_positions_count() -> int:
    """Get number of currently open positions (auto-expires stale ones first)."""
    expire_stale_positions()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM open_positions")
    count = c.fetchone()[0]
    conn.close()
    return count


def get_open_symbols() -> list:
    """Return list of currently open position symbols e.g. ['MNQ', 'MGC']."""
    expire_stale_positions()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT symbol FROM open_positions")
    symbols = [row[0].upper()[:3] for row in c.fetchall()]
    conn.close()
    return symbols


# ============================================================================
# CORRELATION GROUPS
# ============================================================================
CORRELATION_GROUPS = {
    'US_EQUITY': ['MNQ', 'MES', 'MYM'],
    'COMMODITY': ['MGC', 'MCL'],
    'SMALL_CAP': ['M2K'],
}
SYMBOL_TO_GROUP = {}
for _group_name, _symbols in CORRELATION_GROUPS.items():
    for _sym in _symbols:
        SYMBOL_TO_GROUP[_sym] = _group_name

def get_correlation_group(symbol: str) -> str:
    return SYMBOL_TO_GROUP.get(symbol.upper()[:3], 'UNKNOWN')


def add_open_position(strategy: str, symbol: str, action: str, quantity: int,
                      entry_price: float, stop_loss: float, take_profit: float):
    expire_stale_positions()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM open_positions WHERE symbol = ?", (symbol.upper(),))
    if c.fetchone()[0] > 0:
        logger.warning(f"⚠️  Skipping duplicate position for {symbol} (already tracked)")
        conn.close()
        return
    c.execute("""INSERT INTO open_positions
                 (strategy, symbol, action, quantity, entry_time, entry_price, stop_loss, take_profit)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
              (strategy, symbol.upper(), action, quantity, datetime.now().isoformat(),
               entry_price, stop_loss, take_profit))
    conn.commit()
    conn.close()
    logger.info(f"📌 Position tracked: {symbol} {action} @ {entry_price}")


# ============================================================================
# VALIDATION FUNCTIONS
# ============================================================================

def validate_candlestick_color(alert_data: Dict) -> Tuple[bool, str]:
    action = alert_data.get('action', '').upper()
    candle_color_str = alert_data.get('candle_color', '').lower()
    if candle_color_str in ('green', 'red'):
        is_green = candle_color_str == 'green'
        is_red   = candle_color_str == 'red'
        if action == 'LONG' and not is_green:
            return False, f"BLOCKED: LONG requires green candle (got: {candle_color_str})"
        if action == 'SHORT' and not is_red:
            return False, f"BLOCKED: SHORT requires red candle (got: {candle_color_str})"
        logger.info(f"✅ Candlestick: {candle_color_str} candle for {action}")
        return True, f"Candlestick color confirmed: {candle_color_str}"

    open_price  = alert_data.get('open_1m')  or alert_data.get('open')
    close_price = alert_data.get('close_1m') or alert_data.get('close')
    if open_price is None or close_price is None:
        logger.warning("⚠️ No candle data to validate color")
        return True, "No candle data available"

    is_green = close_price > open_price
    is_red   = close_price < open_price
    if action == 'LONG' and not is_green:
        return False, f"BLOCKED: LONG requires green candle (close={close_price} <= open={open_price})"
    if action == 'SHORT' and not is_red:
        return False, f"BLOCKED: SHORT requires red candle (close={close_price} >= open={open_price})"

    candle_type = "green" if is_green else "red"
    logger.info(f"✅ Candlestick: {candle_type} candle for {action} trade")
    return True, f"Candlestick color confirmed: {candle_type}"


def validate_mtf_alignment(alert_data: Dict) -> Tuple[bool, str]:
    action = alert_data.get('action', '').upper()
    mtf_int = alert_data.get('mtf_alignment')
    if mtf_int is not None:
        try:
            aligned = int(mtf_int)
            if aligned >= 2:
                logger.info(f"✅ MTF Alignment: {aligned}/3 aligned for {action}")
                return True, f"MTF aligned: {aligned}/3"
            return False, f"BLOCKED: {action} needs 2+ aligned timeframes (only {aligned}/3)"
        except Exception:
            pass

    ema_1m  = alert_data.get('ema_aligned_1m', alert_data.get('ema_aligned'))
    ema_5m  = alert_data.get('ema_aligned_5m')
    ema_15m = alert_data.get('ema_aligned_15m')
    timeframes_checked = 0
    bullish_count      = 0
    for val in [ema_1m, ema_5m, ema_15m]:
        if val is not None:
            timeframes_checked += 1
            if val:
                bullish_count += 1

    if timeframes_checked == 0:
        logger.warning("⚠️ No MTF data — passing by default")
        return True, "No MTF data to validate"

    bearish_count = timeframes_checked - bullish_count
    if action in ('LONG', 'BUY'):
        if bullish_count >= 2:
            logger.info(f"✅ MTF LONG: {bullish_count}/{timeframes_checked} bullish")
            return True, f"MTF bullish: {bullish_count}/{timeframes_checked}"
        return False, f"BLOCKED: LONG needs 2+ bullish timeframes (only {bullish_count}/{timeframes_checked})"
    elif action in ('SHORT', 'SELL'):
        if bearish_count >= 2:
            logger.info(f"✅ MTF SHORT: {bearish_count}/{timeframes_checked} bearish")
            return True, f"MTF bearish: {bearish_count}/{timeframes_checked}"
        return False, f"BLOCKED: SHORT needs 2+ bearish timeframes (only {bearish_count}/{timeframes_checked})"
    return True, f"MTF: unknown action '{action}', skipping"


INSTRUMENT_SESSIONS = {
    'MGC': [(0, 17), (18, 24)],
    'MCL': [(0, 17), (18, 24)],
    'MES': [(9, 16)],
    'MNQ': [(9, 16)],
    'MYM': [(9, 16)],
    'M2K': [(9, 16)],
}

def _is_instrument_session(instrument: str, hour: int) -> bool:
    windows = INSTRUMENT_SESSIONS.get(instrument[:3].upper())
    if not windows:
        return True
    return any(start <= hour < end for start, end in windows)


def validate_time_filters(alert_data: dict = None) -> Tuple[bool, str]:
    from datetime import timezone, timedelta as td
    utc_now  = datetime.now(timezone.utc)
    year     = utc_now.year
    mar1     = datetime(year, 3, 1, tzinfo=timezone.utc)
    dst_start = (mar1 + td(days=(6 - mar1.weekday()) % 7) + td(weeks=1)).replace(hour=7)
    nov1     = datetime(year, 11, 1, tzinfo=timezone.utc)
    dst_end  = (nov1 + td(days=(6 - nov1.weekday()) % 7)).replace(hour=6)
    is_edt   = dst_start <= utc_now < dst_end
    et       = timezone(td(hours=-4) if is_edt else td(hours=-5))
    now_et   = utc_now.astimezone(et)
    hour     = now_et.hour
    weekday  = now_et.weekday()
    tz_lbl   = 'EDT' if is_edt else 'EST'

    if weekday == 5:
        return False, "BLOCKED: Weekend (Saturday)"
    if weekday == 4 and hour >= 17:
        return False, "BLOCKED: Weekend (Friday after 5 PM ET)"
    if weekday == 6 and hour < 18:
        return False, "BLOCKED: Weekend (Sunday before 6 PM ET)"
    if weekday in (0, 1, 2, 3) and hour == 17:
        return False, f"BLOCKED: CME maintenance 5-6 PM ET ({now_et.strftime('%H:%M')} {tz_lbl})"

    instrument = ''
    if alert_data:
        instrument = (alert_data.get('ticker') or alert_data.get('instrument') or '')[:3].upper()
    if instrument and not _is_instrument_session(instrument, hour):
        if instrument in ('MES', 'MNQ', 'MYM', 'M2K'):
            return False, (f"BLOCKED: {instrument} is NY-only (9AM-4PM ET). "
                           f"Current time {now_et.strftime('%H:%M')} {tz_lbl}")
        return False, (f"BLOCKED: {instrument} outside valid session "
                       f"({now_et.strftime('%H:%M')} {tz_lbl})")

    if _MCE_AVAILABLE:
        news_blocked, event_name = get_news_status()
        if news_blocked:
            return False, f"BLOCKED: {event_name} — news blackout active"

    logger.info(f"✅ Time filter: {now_et.strftime('%H:%M')} {tz_lbl} — {instrument or 'instrument'} in session")
    return True, f"Time OK: {now_et.strftime('%H:%M')} {tz_lbl}"


def validate_daily_limits() -> Tuple[bool, str]:
    stats = get_daily_stats()
    if stats['stopped_out']:
        return False, "BLOCKED: Daily trading stopped (limit hit earlier)"
    if stats['losses'] >= MAX_DAILY_LOSSES:
        logger.error(f"🚫 Max daily losses reached: {stats['losses']}")
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        today = datetime.now().strftime('%Y-%m-%d')
        c.execute("UPDATE daily_performance SET stopped_out = 1 WHERE date = ?", (today,))
        conn.commit()
        conn.close()
        return False, f"BLOCKED: Max {MAX_DAILY_LOSSES} losses reached ({stats['losses']} losses today)"
    if stats['total_pnl'] >= DAILY_PROFIT_TARGET:
        logger.info(f"🎯 Daily profit target reached: ${stats['total_pnl']}")
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        today = datetime.now().strftime('%Y-%m-%d')
        c.execute("UPDATE daily_performance SET stopped_out = 1 WHERE date = ?", (today,))
        conn.commit()
        conn.close()
        return False, f"BLOCKED: Daily profit target reached (${stats['total_pnl']:.2f} >= ${DAILY_PROFIT_TARGET})"
    if stats['total_pnl'] <= MAX_DAILY_DRAWDOWN:
        logger.error(f"🚫 Max daily drawdown reached: ${stats['total_pnl']}")
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        today = datetime.now().strftime('%Y-%m-%d')
        c.execute("UPDATE daily_performance SET stopped_out = 1 WHERE date = ?", (today,))
        conn.commit()
        conn.close()
        return False, f"BLOCKED: Max drawdown reached (${stats['total_pnl']:.2f} <= ${MAX_DAILY_DRAWDOWN})"
    logger.info(f"✅ Daily limits: {stats['losses']}/{MAX_DAILY_LOSSES} losses, ${stats['total_pnl']:.2f} P&L")
    return True, f"Daily limits OK: {stats['losses']} losses, ${stats['total_pnl']:.2f} P&L"


def validate_exposure(alert_data: Dict = None) -> Tuple[bool, str]:
    """
    Validate exposure limits with correlation checking.
    Uses fresh expiry check — stale positions removed before counting.
    """
    open_count   = get_open_positions_count()
    open_symbols = get_open_symbols()

    if open_count >= MAX_CONCURRENT_POSITIONS:
        return False, f"BLOCKED: Max {MAX_CONCURRENT_POSITIONS} concurrent positions (have {open_count})"

    if alert_data:
        incoming = (alert_data.get('ticker') or alert_data.get('instrument') or '').upper()[:3]
        incoming_group = get_correlation_group(incoming)

        if incoming in open_symbols:
            return False, f"BLOCKED: Already in a {incoming} position"

        if incoming_group != 'UNKNOWN':
            for open_sym in open_symbols:
                open_group = get_correlation_group(open_sym)
                if open_group == incoming_group:
                    group_members = ', '.join(CORRELATION_GROUPS.get(incoming_group, []))
                    return False, (
                        f"BLOCKED: Correlation conflict — already have {open_sym} open "
                        f"({incoming_group} group: {group_members} are r>0.80 correlated). "
                        f"Cannot add {incoming}."
                    )

        logger.info(
            f"✅ Exposure OK: {open_count}/{MAX_CONCURRENT_POSITIONS} positions | "
            f"{incoming} → group:{incoming_group} | "
            f"Open: {open_symbols if open_symbols else 'none'}"
        )
    else:
        logger.info(f"✅ Exposure: {open_count}/{MAX_CONCURRENT_POSITIONS} positions open")

    return True, f"Exposure OK: {open_count} positions, no correlation conflict"


def validate_volatility(alert_data: Dict) -> Tuple[bool, str]:
    symbol = (alert_data.get('ticker') or alert_data.get('instrument') or '')[:3].upper()
    atr = alert_data.get('atr_1m') or alert_data.get('atr')
    if atr is None:
        logger.warning("⚠️ No ATR data — skipping volatility check")
        return True, "No ATR data to validate"
    atr = float(atr)
    limits = ATR_RANGES.get(symbol)
    if not limits:
        logger.warning(f"⚠️ No ATR limits for {symbol} — skipping check")
        return True, f"No ATR limits for {symbol}"
    min_atr, max_atr = limits
    if atr < min_atr:
        return False, f"BLOCKED: ATR too low ({atr:.2f} < {min_atr}) — not enough movement"
    if atr > max_atr:
        return False, f"BLOCKED: ATR too high ({atr:.2f} > {max_atr}) — too volatile"
    logger.info(f"✅ Volatility: ATR={atr:.2f} in range ({min_atr}–{max_atr}) for {symbol}")
    return True, f"ATR in range: {atr:.2f}"


def validate_whipsaw_protection(alert_data: Dict) -> Tuple[bool, str]:
    adx        = alert_data.get('adx_1m') or alert_data.get('adx')
    chop_index = alert_data.get('chop_index')
    symbol     = (alert_data.get('ticker') or alert_data.get('instrument') or '')[:3].upper()
    adx_min    = 35 if symbol == 'MYM' else 25
    if adx is not None and float(adx) > 0 and float(adx) < adx_min:
        return False, (f"BLOCKED: ADX too low ({adx:.1f} < {adx_min}) — "
                       f"{'MYM requires 35+ (choppy Dow)' if symbol == 'MYM' else 'weak trend, likely ranging'}")
    if chop_index is not None and chop_index > MAX_CHOP_INDEX:
        return False, f"BLOCKED: Chop Index too high ({chop_index:.1f} > {MAX_CHOP_INDEX}) - market is choppy"
    logger.info(f"✅ Whipsaw protection: ADX={adx} (min={adx_min}), Chop={chop_index}")
    return True, "Not choppy/ranging"


def calculate_setup_quality_score(alert_data: Dict) -> int:
    score = 0
    adx = alert_data.get('adx_1m') or alert_data.get('adx', 0)
    if adx >= 35:   score += 20
    elif adx >= 28: score += 15
    elif adx >= 22: score += 10
    elif adx >= 18: score += 5

    rsi = alert_data.get('rsi_1m') or alert_data.get('rsi', 50)
    if 45 <= rsi <= 55:   score += 15
    elif 40 <= rsi <= 60: score += 10

    action  = alert_data.get('action', '').upper()
    ema_1m  = alert_data.get('ema_aligned_1m', alert_data.get('ema_aligned'))
    ema_5m  = alert_data.get('ema_aligned_5m')
    ema_15m = alert_data.get('ema_aligned_15m')
    mtf_int = alert_data.get('mtf_alignment')
    ema_aligned = False
    if mtf_int is not None:
        try:
            ema_aligned = int(mtf_int) >= 2
        except Exception:
            pass
    elif action in ('SHORT', 'SELL'):
        short_tfs = [v for v in [ema_1m, ema_5m, ema_15m] if v is not None]
        ema_aligned = sum(1 for v in short_tfs if not v) >= 2 or len(short_tfs) == 0
    else:
        long_tfs = [v for v in [ema_1m, ema_5m, ema_15m] if v is not None]
        ema_aligned = sum(1 for v in long_tfs if v) >= 2 or len(long_tfs) == 0
    if ema_aligned:
        score += 20

    volume_ratio = alert_data.get('volume_ratio')
    volume       = alert_data.get('volume')
    avg_volume   = alert_data.get('avg_volume')
    if volume_ratio:
        if volume_ratio >= 1.5: score += 15
        elif volume_ratio >= 1.0: score += 10
        elif volume_ratio >= 0.8: score += 5
    elif volume and avg_volume:
        if volume > avg_volume * 1.5: score += 15
        elif volume > avg_volume: score += 10
        elif volume > avg_volume * 0.8: score += 5
    else:
        score += 8

    atr = alert_data.get('atr')
    if atr:
        symbol    = (alert_data.get('ticker') or alert_data.get('instrument') or '')[:3].upper()
        atr_range = ATR_RANGES.get(symbol)
        if atr_range and atr_range[0] <= float(atr) <= atr_range[1]:
            score += 15
        elif not atr_range:
            score += 10

    mtf_valid, _ = validate_mtf_alignment(alert_data)
    if mtf_valid:
        score += 15

    logger.info(f"📊 Setup Quality Score: {score}/100")
    return score


def validate_risk_management(alert_data: Dict) -> Tuple[bool, Dict, str]:
    """
    Compute / validate stop-loss, take-profit, and position size.

    FIX-6: Stop loss is clamped to the prop firm maximum for the instrument
    BEFORE the R:R check.  This prevents ATR-based stops (which can be much
    wider than the prop firm allows) from blocking every trade.

    FIX-7: Take profit is derived from the *clamped* stop distance × target R:R,
    not from raw ATR × 3.  This keeps the reward proportional to actual risk.
    """
    strategy   = alert_data.get('strategy', 'UNKNOWN')
    ticker     = alert_data.get('ticker') or alert_data.get('instrument', 'UNKNOWN')
    action     = alert_data.get('action', 'UNKNOWN')
    instrument = ticker[:3].upper() if len(ticker) >= 3 else ticker.upper()
    strategy_params = STRATEGY_RISK_PARAMS.get(strategy, {}).get(instrument, {
        'stop_loss_points': 10, 'take_profit_points': 20,
        'default_position_size': 1, 'max_position_size': 1, 'min_rr_ratio': 2.0
    })

    stop_loss = alert_data.get('stop_loss')
    entry     = alert_data.get('entry') or alert_data.get('entry_price') or alert_data.get('close_1m')

    pf_config    = get_prop_firm_config(instrument)
    max_stop_pts = pf_config.get('max_stop_points') if pf_config else None
    tick_size    = pf_config.get('tick_size', 0.01) if pf_config else 0.01
    target_rr    = strategy_params.get('min_rr_ratio', 2.0)

    # ── Compute or clamp stop loss ──────────────────────────────────────
    if stop_loss is None and entry is not None:
        atr = alert_data.get('atr', 0)
        atr_based = atr * 1.5 if atr and atr > 0 else strategy_params['stop_loss_points']

        if max_stop_pts is not None:
            sl_dist = min(atr_based, max_stop_pts)
        else:
            sl_dist = atr_based

        sl_dist = max(sl_dist, tick_size)
        if action == 'LONG':
            stop_loss = round_to_tick(float(entry) - sl_dist, tick_size)
        else:
            stop_loss = round_to_tick(float(entry) + sl_dist, tick_size)
        alert_data['stop_loss'] = stop_loss

        if max_stop_pts is not None and atr_based > max_stop_pts:
            logger.info(f"➕ Stop loss: {stop_loss} (ATR-based {atr_based:.2f} clamped → {max_stop_pts} pts prop firm max)")
        else:
            logger.info(f"➕ Added stop loss: {stop_loss}")

    elif stop_loss is not None and entry is not None:
        stop_loss, was_clamped = clamp_stop_to_prop_firm(float(entry), float(stop_loss), action, instrument)
        alert_data['stop_loss'] = stop_loss
        if was_clamped:
            logger.info(f"➕ Stop loss clamped to prop firm max: {stop_loss}")

    elif stop_loss is None:
        logger.warning("⚠️ No entry price — using default stop distance")
        sl_dist = strategy_params['stop_loss_points']
        if max_stop_pts is not None:
            sl_dist = min(sl_dist, max_stop_pts)
        alert_data['stop_loss'] = sl_dist
        stop_loss = sl_dist

    # ── Compute take profit from clamped stop distance × R:R ────────────
    take_profit = alert_data.get('take_profit')
    if take_profit is None and entry is not None and stop_loss is not None:
        stop_dist = abs(float(entry) - float(stop_loss))
        tp_dist = stop_dist * target_rr
        if action == 'LONG':
            take_profit = round_to_tick(float(entry) + tp_dist, tick_size)
        else:
            take_profit = round_to_tick(float(entry) - tp_dist, tick_size)
        alert_data['take_profit'] = take_profit
        logger.info(f"➕ Added take profit: {take_profit} ({target_rr}:1 R:R from {stop_dist:.2f} pt stop)")
    elif take_profit is None:
        _tp_pts = strategy_params['take_profit_points']
        if entry is not None:
            take_profit = round(float(entry) + _tp_pts, 2) if action == 'LONG' else round(float(entry) - _tp_pts, 2)
        else:
            take_profit = _tp_pts
        alert_data['take_profit'] = take_profit

    # ── Validate R:R ────────────────────────────────────────────────────
    if entry and stop_loss and take_profit:
        if action == 'LONG':
            risk   = abs(float(entry) - float(stop_loss))
            reward = abs(float(take_profit) - float(entry))
        else:
            risk   = abs(float(stop_loss) - float(entry))
            reward = abs(float(entry) - float(take_profit))
        if risk > 0:
            rr_ratio = reward / risk
            min_rr   = strategy_params.get('min_rr_ratio', 1.5)
            if rr_ratio < (min_rr - 0.001):
                return False, alert_data, f"BLOCKED: R:R {rr_ratio:.2f} < {min_rr}"
            logger.info(f"✅ R:R: {rr_ratio:.2f}:1")

    # ── Position size: HARD CAP 1 contract ──────────────────────────────
    MAX_CONTRACTS_PER_TRADE = 1
    alert_data['position_size'] = MAX_CONTRACTS_PER_TRADE
    return True, alert_data, "Risk management OK"


# ============================================================================
# WEBHOOK ENDPOINT
# ============================================================================

@app.route('/webhook/tradingview', methods=['POST'])
def tradingview_webhook():
    with _signal_lock:
        return _process_signal()


def _process_signal():
    try:
        alert_data = request.get_json()
        strategy   = alert_data.get('strategy', 'UNKNOWN')
        symbol     = alert_data.get('ticker', 'UNKNOWN')
        action_raw = alert_data.get('action', 'UNKNOWN')
        _amap      = {'buy': 'LONG', 'sell': 'SHORT', 'long': 'LONG', 'short': 'SHORT'}
        action     = _amap.get(action_raw.lower(), action_raw.upper())
        alert_data['action'] = action

        logger.info("="*80)
        logger.info(f"📨 RECEIVED: {strategy} {action} on {symbol}")
        logger.info("="*80)

        if is_duplicate_signal(symbol, action):
            logger.warning(f"⚠️  DEDUP: {symbol} {action} already approved within {DEDUP_WINDOW_SECONDS}s")
            return jsonify({'status': 'duplicate', 'reason': f'Same signal already processed within {DEDUP_WINDOW_SECONDS}s'}), 200

        validation_results = []

        # 1. Risk Management (computes & clamps SL/TP to prop firm limits)
        is_valid, alert_data, reason = validate_risk_management(alert_data)
        validation_results.append(("Risk Management", is_valid, reason))
        if not is_valid:
            logger.error(f"🚫 BLOCKED: {reason}")
            return jsonify({'status': 'blocked', 'reason': reason}), 200

        # 2. Time Filters
        is_valid, reason = validate_time_filters(alert_data)
        validation_results.append(("Time Window", is_valid, reason))
        if not is_valid:
            logger.error(f"🚫 BLOCKED: {reason}")
            return jsonify({'status': 'blocked', 'reason': reason}), 200

        # 2b. Market Condition Engine (optional)
        if _MCE_AVAILABLE:
            _mce_ev = evaluate_conditions(
                float(alert_data.get('vix') or 0),
                float(alert_data.get('adx') or alert_data.get('adx_1m') or 25),
                symbol[:3].upper(), action, alert_data
            )
            if _mce_ev.blocked:
                logger.error(f"🚫 MCE BLOCK: {_mce_ev.reason}")
                return jsonify({'status': 'blocked', 'reason': _mce_ev.reason}), 200
            _mce_q_adj = _mce_ev.quality_bonus
            if _mce_q_adj != 0:
                _sign = '+' if _mce_q_adj > 0 else ''
                logger.info(f"  📊 MCE [{_mce_ev.condition}]: quality threshold {_sign}{_mce_q_adj}")
            validation_results.append(("MCE Condition", True, f"{_mce_ev.condition} Q{'+' if _mce_q_adj >= 0 else ''}{_mce_q_adj}"))
        else:
            _mce_q_adj = 0

        # 3. Daily Limits
        is_valid, reason = validate_daily_limits()
        validation_results.append(("Daily Limits", is_valid, reason))
        if not is_valid:
            logger.error(f"🚫 BLOCKED: {reason}")
            return jsonify({'status': 'blocked', 'reason': reason}), 200

        # 4. Exposure
        is_valid, reason = validate_exposure(alert_data)
        validation_results.append(("Exposure", is_valid, reason))
        if not is_valid:
            logger.error(f"🚫 BLOCKED: {reason}")
            return jsonify({'status': 'blocked', 'reason': reason}), 200

        # 4b. Atomic duplicate guard (race-condition prevention)
        _live_count = get_open_positions_count()
        if _live_count >= MAX_CONCURRENT_POSITIONS:
            logger.error(f"🚫 ATOMIC GUARD: {_live_count}/{MAX_CONCURRENT_POSITIONS} positions — "
                         f"blocking {symbol} {action} (race condition caught)")
            return jsonify({'status': 'blocked',
                            'reason': f'Atomic guard: {_live_count}/{MAX_CONCURRENT_POSITIONS} concurrent positions'}), 200

        # 5. Candlestick Color
        is_valid, reason = validate_candlestick_color(alert_data)
        validation_results.append(("Candlestick", is_valid, reason))
        if not is_valid:
            logger.error(f"🚫 BLOCKED: {reason}")
            return jsonify({'status': 'blocked', 'reason': reason}), 200

        # 6. MTF Alignment
        is_valid, reason = validate_mtf_alignment(alert_data)
        validation_results.append(("MTF Alignment", is_valid, reason))
        if not is_valid:
            logger.error(f"🚫 BLOCKED: {reason}")
            return jsonify({'status': 'blocked', 'reason': reason}), 200

        # 7. Volatility
        is_valid, reason = validate_volatility(alert_data)
        validation_results.append(("Volatility", is_valid, reason))
        if not is_valid:
            logger.error(f"🚫 BLOCKED: {reason}")
            return jsonify({'status': 'blocked', 'reason': reason}), 200

        # 8. Whipsaw Protection
        is_valid, reason = validate_whipsaw_protection(alert_data)
        validation_results.append(("Whipsaw Protection", is_valid, reason))
        if not is_valid:
            logger.error(f"🚫 BLOCKED: {reason}")
            return jsonify({'status': 'blocked', 'reason': reason}), 200

        # 9. Setup Quality Score
        quality_score = calculate_setup_quality_score(alert_data)
        _sym_check    = (alert_data.get('ticker') or alert_data.get('instrument') or '')[:3].upper()
        min_score     = (70 if _sym_check == 'MYM' else 60)
        quality_score_adjusted = quality_score + _mce_q_adj
        is_valid      = quality_score_adjusted >= min_score
        validation_results.append(("Quality Score", is_valid, f"{quality_score_adjusted}/100 (min:{min_score})"))
        if not is_valid:
            logger.error(f"🚫 BLOCKED: Quality score {quality_score_adjusted} < {min_score}")
            return jsonify({'status': 'blocked', 'reason': f'Setup quality too low: {quality_score_adjusted}/100 (min: {min_score})'}), 200

        # 10. Prop Firm Stop Loss (final safety check — should pass after clamping)
        stop_ok, stop_msg = validate_stop_loss(alert_data)
        if not stop_ok:
            logger.error(stop_msg)
            return jsonify({'status': 'blocked', 'reason': stop_msg}), 200

        # 11. Market Condition (trending/momentum only)
        market_ok, market_msg = validate_market_conditions(alert_data)
        if not market_ok:
            logger.error(market_msg)
            return jsonify({'status': 'blocked', 'reason': market_msg}), 200
        logger.info(market_msg)

        # 12. AI Analysis (optional)
        if analyzer:
            market_data = {
                'symbol':        symbol,
                'action':        action,
                'strategy':      strategy,
                'quality_score': quality_score_adjusted,
            }
            for key, value in alert_data.items():
                if key not in ['strategy', 'ticker', 'action']:
                    market_data[key] = value
            result = analyzer.analyze_market(symbol, market_data, strategy)
            validation_results.append(("AI Analysis", True, f"{result['signal']} @ {result['confidence']:.0%}"))

            _is_orb = any(x in strategy.upper() for x in ('ORB', 'BREAKOUT', 'BREAK', 'RANGE'))
            _strongly_opposes = (result['signal'] != action and result['confidence'] >= 0.70)

            if not _is_orb and _strongly_opposes:
                logger.error(
                    f"🚫 BLOCKED: AI strongly opposes {action} "
                    f"— confident {result['signal']} @ {result['confidence']:.0%} | {result.get('reason','')}"
                )
                return jsonify({'status': 'blocked', 'reason': 'AI strongly opposes signal direction',
                                'ai_signal': result['signal'], 'ai_confidence': result['confidence']}), 200
            elif _is_orb:
                logger.info(
                    f"  🤖 AI [{result['signal']} @ {result['confidence']:.0%}] "
                    f"— ORB strategy bypass (direction-agnostic by design)"
                )
            elif result['signal'] != action:
                logger.info(
                    f"  🤖 AI [{result['signal']} @ {result['confidence']:.0%}] "
                    f"— opposes but low confidence ({result['confidence']:.0%} < 70%), not blocking"
                )
            else:
                logger.info(
                    f"  🤖 AI [{result['signal']} @ {result['confidence']:.0%}] ✅ agrees"
                )

        # ── ALL PASSED ──────────────────────────────────────────────────
        logger.info("="*80)
        logger.info("✅ ALL VALIDATIONS PASSED!")
        logger.info("="*80)
        for check, passed, msg in validation_results:
            logger.info(f"  ✅ {check}: {msg}")
        logger.info("="*80)

        entry_px = alert_data.get('entry') or alert_data.get('entry_price') or alert_data.get('close_1m', 'N/A')
        sl_px    = alert_data.get('stop_loss', 'N/A')
        tp_px    = alert_data.get('take_profit', 'N/A')
        pos_sz   = alert_data.get('position_size', 1)
        pattern  = alert_data.get('pattern', 'N/A')
        atr_val  = alert_data.get('atr_1m') or alert_data.get('atr', 'N/A')
        adx_val  = alert_data.get('adx_1m') or alert_data.get('adx', 'N/A')
        rsi_val  = alert_data.get('rsi', 'N/A')
        try:
            risk_pts   = abs(float(entry_px) - float(sl_px))
            reward_pts = abs(float(tp_px) - float(entry_px))
            rr_actual  = reward_pts / risk_pts if risk_pts > 0 else 0
        except Exception:
            risk_pts = reward_pts = rr_actual = 0

        logger.info(f"📋 TRADE DETAILS:")
        logger.info(f"   Strategy   : {strategy}")
        logger.info(f"   Symbol     : {symbol}")
        logger.info(f"   Action     : {action}")
        logger.info(f"   Pattern    : {pattern}")
        logger.info(f"   Entry      : {entry_px}")
        logger.info(f"   Stop Loss  : {sl_px}  (risk {risk_pts:.1f} pts)")
        logger.info(f"   Take Profit: {tp_px}  (reward {reward_pts:.1f} pts)")
        logger.info(f"   R:R Ratio  : {rr_actual:.2f}:1")
        logger.info(f"   Size       : {pos_sz} contracts")
        logger.info(f"   ATR        : {atr_val}  ADX: {adx_val}  RSI: {rsi_val}")
        logger.info("="*80)

        record_trade(strategy, symbol, action, 'approved')
        record_signal(symbol, action)
        add_open_position(strategy, symbol, action, alert_data['position_size'],
                          alert_data.get('entry', 0), alert_data['stop_loss'], alert_data['take_profit'])

        _action_map = {'LONG': 'buy', 'SHORT': 'sell', 'buy': 'buy', 'sell': 'sell'}
        _sl_price   = alert_data.get('stop_loss')
        _tp_price   = alert_data.get('take_profit')
        tp_payload  = {
            'ticker':     symbol,
            'action':     _action_map.get(action, action.lower()),
            'price':      alert_data.get('entry') or alert_data.get('entry_price') or alert_data.get('close_1m'),
            'quantity':   alert_data.get('position_size', 1),
            'stopLoss':   {'type': 'stop',  'stopPrice':  round(float(_sl_price), 2)} if _sl_price else None,
            'takeProfit': {'type': 'limit', 'limitPrice': round(float(_tp_price), 2)} if _tp_price else None,
            'strategy':   strategy,
            'pattern':    alert_data.get('pattern', 'ema_crossover'),
            'quality':    quality_score,
            'rr_ratio':   round(rr_actual, 2) if rr_actual else None,
        }
        tp_payload = {k: v for k, v in tp_payload.items() if v is not None}
        try:
            response = requests.post(TRADERSPOST_WEBHOOK, json=tp_payload, timeout=5)
            logger.info(f"📤 TradersPost response: {response.status_code} — {response.text[:120]}")
            if response.status_code not in (200, 201):
                logger.error(f"❌ TradersPost rejected: {response.text}")
        except Exception as e:
            logger.error(f"❌ TradersPost error: {e}")
            return jsonify({'status': 'error', 'reason': str(e)}), 500

        return jsonify({
            'status': 'approved',
            'quality_score': quality_score,
            'validations': len(validation_results),
            'stop_loss': alert_data['stop_loss'],
            'take_profit': alert_data['take_profit'],
            'position_size': alert_data['position_size']
        }), 200

    except Exception as e:
        logger.error(f"❌ Error: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ============================================================================
# ADMIN ENDPOINTS
# ============================================================================

@app.route('/health', methods=['GET'])
def health():
    stats = get_daily_stats()
    return jsonify({
        'status': 'healthy',
        'today_trades': stats['trades'],
        'today_wins': stats['wins'],
        'today_losses': stats['losses'],
        'today_pnl': stats['total_pnl'],
        'stopped_out': bool(stats['stopped_out']),
        'open_positions': get_open_positions_count()
    }), 200


@app.route('/positions', methods=['GET'])
def list_positions():
    expire_stale_positions()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, strategy, symbol, action, entry_time, entry_price FROM open_positions ORDER BY entry_time DESC")
    rows = c.fetchall()
    conn.close()
    positions = [{'id': r[0], 'strategy': r[1], 'symbol': r[2], 'action': r[3],
                  'entry_time': r[4], 'entry_price': r[5]} for r in rows]
    return jsonify({'count': len(positions), 'positions': positions}), 200


@app.route('/close_position', methods=['POST'])
def close_position():
    data = request.get_json(force=True) or {}
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if data.get('clear_all'):
        c.execute("DELETE FROM open_positions")
        removed = c.rowcount
        conn.commit()
        conn.close()
        logger.info(f"🗑️  Cleared all {removed} open position(s)")
        return jsonify({'status': 'cleared', 'removed': removed}), 200
    elif data.get('id'):
        c.execute("DELETE FROM open_positions WHERE id = ?", (data['id'],))
    elif data.get('symbol'):
        c.execute("DELETE FROM open_positions WHERE symbol = ?", (data['symbol'].upper(),))
    else:
        conn.close()
        return jsonify({'status': 'error', 'reason': 'Provide id, symbol, or clear_all=true'}), 400
    removed = c.rowcount
    conn.commit()
    conn.close()
    logger.info(f"🗑️  Closed {removed} position(s)")
    return jsonify({'status': 'closed', 'removed': removed}), 200


@app.route('/reset_positions', methods=['GET', 'POST'])
def reset_positions():
    """Quick reset — clears all tracked positions instantly.
       curl http://localhost:8765/reset_positions
    """
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM open_positions")
    removed = c.rowcount
    conn.commit()
    conn.close()
    logger.warning(f"🔄 RESET: Cleared {removed} position(s) via /reset_positions")
    return jsonify({'status': 'reset', 'removed': removed,
                    'message': 'All tracked positions cleared. Validator unblocked.'}), 200


if __name__ == '__main__':
    logger.info("="*80)
    logger.info("🚀 ULTIMATE ENTRY VALIDATOR")
    logger.info("="*80)
    logger.info("🛡️ VALIDATION LAYERS:")
    logger.info("   1. Risk Management (SL/TP/position size + prop firm clamping)")
    logger.info("   2. Time Filters (instrument-aware sessions)")
    logger.info("   3. Daily Limits (max losses/profit target/drawdown)")
    logger.info("   4. Exposure (max positions/size/correlation)")
    logger.info("   5. Candlestick Color (green=LONG, red=SHORT)")
    logger.info("   6. MTF Alignment (2/3 timeframes)")
    logger.info("   7. Volatility (ATR range)")
    logger.info("   8. Whipsaw Protection (ADX/Chop)")
    logger.info("   9. Setup Quality (0-100 score)")
    logger.info("  10. Prop Firm Stop Validation (final safety check)")
    logger.info("  11. Market Conditions (trending/momentum only)")
    logger.info("  12. AI Market Analysis (60%+ confidence)")
    logger.info("="*80)

    for sym, cfg in PROP_FIRM_STOPS.items():
        max_risk = cfg['max_stop_points'] * cfg['point_value']
        logger.info(f"⚡ {sym}: max stop {cfg['max_stop_points']} pts (${max_risk:.0f})")

    logger.info("="*80)
    logger.info("⚡ Position expiry: MNQ/MES/MYM/M2K=30min  MGC/MCL=60min")
    logger.info("⚡ Quick unblock:   curl http://localhost:8765/reset_positions")
    logger.info("="*80)

    init_database()

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM open_positions")
    cleared = c.rowcount
    conn.commit()
    conn.close()
    if cleared:
        logger.warning(f"🗑️  STARTUP: Cleared {cleared} leftover position(s) from previous session")
    else:
        logger.info("✅ STARTUP: No stale positions found")

    app.run(host='0.0.0.0', port=8765, debug=False)
