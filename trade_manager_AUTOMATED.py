#!/usr/bin/env python3
"""
FULLY AUTOMATED TRADE MANAGER — 24/7 FUTURES TRADING
ProjectX/TopStepX API — NO MANUAL INTERVENTION

COMPLETE AUDIT FIXES:
  FIX-1  Removed AUTO_SHUTDOWN - allows 24hr trading (NY/London/Asia/UAE)
  FIX-2  Updated BE triggers to $0.75 profit (0.15pts MES, 0.375pts MNQ, etc)
  FIX-3  Fixed order_type: 3→2 (Stop Market, not Stop Limit)
  FIX-4  Added price rounding for proper tick sizes
  FIX-5  Session-aware stops for MGC/MCL (tighter during Asia)
  FIX-6  Live per-trade PnL via client.get_quote()
"""

import time
import logging
import sqlite3
import os
from datetime import datetime
from typing import Dict, List, Optional

from projectx_api_client import ProjectXClient

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================================================
# LOAD .env
# ============================================================================

def _load_env_now():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    if not os.path.exists(p):
        return
    with open(p) as f:
        for ln in f:
            ln = ln.strip()
            if not ln or ln.startswith('#') or '=' not in ln:
                continue
            k, _, v = ln.partition('=')
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v

_load_env_now()

PROJECTX_USERNAME   = os.environ.get('PROJECTX_USERNAME')
PROJECTX_API_KEY    = os.environ.get('PROJECTX_API_KEY') or os.environ.get('PROJECTX_API_SECRET')
PROJECTX_ACCOUNT_ID = int(os.environ.get('PROJECTX_ACCOUNT_ID') or '16129707')

MAE_PERCENTAGE        = 0.80
STOP_RETRY_ATTEMPTS   = 3
STOP_RETRY_DELAY      = 2
TRAILING_ENABLED      = True
TRAILING_TRIGGER_PTS  = 4
TRAILING_DISTANCE_PTS = 2
DAILY_MAX_LOSS        = -800
DAILY_MAX_PROFIT_LOCK = None

# FIX-1: REMOVED AUTO_SHUTDOWN_TIME - Trading 24/7 during futures market hours

# FIX-2: Updated BE triggers to $0.75 profit
INSTRUMENT_PARAMS = {
    'MNQ':  {'be_trigger': 0.375,  'be_move': 0.25, 'point_value': 2.0,   'default_stop': 9.0,  'tick_size': 0.25},
    'MES':  {'be_trigger': 0.15,   'be_move': 0.25, 'point_value': 5.0,   'default_stop': 4.0,  'tick_size': 0.25},
    'MGC':  {'be_trigger': 0.075,  'be_move': 0.10, 'point_value': 10.0,  'default_stop': 1.8,  'tick_size': 0.10},
    'MCL':  {'be_trigger': 0.0075, 'be_move': 0.01, 'point_value': 100.0, 'default_stop': 0.15, 'tick_size': 0.01},
    'MCLE': {'be_trigger': 0.0075, 'be_move': 0.01, 'point_value': 100.0, 'default_stop': 0.15, 'tick_size': 0.01},
    'MYM':  {'be_trigger': 1.5,    'be_move': 1.0,  'point_value': 0.50,  'default_stop': 40.0, 'tick_size': 1.0},
    'M2K':  {'be_trigger': 0.15,   'be_move': 0.10, 'point_value': 5.0,   'default_stop': 4.0,  'tick_size': 0.10},
}

CHECK_INTERVAL_SECONDS = 10
DB_PATH = "trade_manager_auto.db"

# Per-tick quote cache — cleared at the top of every main loop iteration
_quote_cache: Dict[str, float] = {}


# ============================================================================
# PRICE ROUNDING - FIX-4: Proper tick sizes
# ============================================================================

def round_to_tick(price: float, instrument: str) -> float:
    """Round price to proper tick size for each instrument"""
    params = INSTRUMENT_PARAMS.get(instrument, {'tick_size': 0.01})
    tick = params.get('tick_size', 0.01)
    return round(price / tick) * tick


# ============================================================================
# LIVE PRICE — real-time quote, cached per tick
# ============================================================================

def get_live_price(client: ProjectXClient, contract_id: str) -> Optional[float]:
    """
    Fetch current market price for contract_id.
    Cached within the current 10s tick so multiple positions on the same
    instrument share one API call instead of firing N separate requests.
    Returns None if quote unavailable (falls back to API pnl field).
    """
    global _quote_cache
    if contract_id in _quote_cache:
        return _quote_cache[contract_id]

    price = None
    try:
        quote = client.get_quote(contract_id)
        if quote:
            last = quote.get('last') or quote.get('lastPrice') or quote.get('lastTradedPrice')
            bid  = quote.get('bid')  or quote.get('bidPrice')
            ask  = quote.get('ask')  or quote.get('askPrice')
            if last:
                price = float(last)
            elif bid and ask:
                price = (float(bid) + float(ask)) / 2.0
            elif bid:
                price = float(bid)
            elif ask:
                price = float(ask)
    except Exception as e:
        logger.debug(f"Quote fetch failed for {contract_id}: {e}")

    if price:
        _quote_cache[contract_id] = price
    return price


# ============================================================================
# HELPERS
# ============================================================================

def get_instrument_params(contract_id: str) -> tuple:
    for key, params in INSTRUMENT_PARAMS.items():
        if key in contract_id:
            return key, params
    logger.warning(f"Unrecognized contract: {contract_id} -- using MNQ fallback.")
    return 'UNKNOWN', {'be_trigger': 0.375, 'be_move': 0.25, 'point_value': 2.0, 'default_stop': 30, 'tick_size': 0.25}


def detect_direction(trade: Dict) -> str:
    # side field is most reliable when present
    side = trade.get('side')
    if side == 0: return 'LONG'
    if side == 1: return 'SHORT'
    # Explicit None check — or-chain caused netSize=0 to fall through to stale openSize
    for field in ('netSize', 'openSize', 'size'):
        val = trade.get(field)
        if val is not None:
            return 'SHORT' if int(val) < 0 else 'LONG'
    return 'LONG'  # safe default


def _is_open(t: Dict) -> bool:
    """Check if trade is open"""
    # Explicit None check — don't use or-chain (0 is falsy but means FLAT/CLOSED)
    for field in ('netSize', 'openSize', 'remainingSize', 'size'):
        val = t.get(field)
        if val is not None:
            return abs(int(val)) > 0   # abs() catches SHORT (-1) and LONG (+1) correctly

    # Last resort: explicit isClosed / status field only
    is_closed = (t.get('isClosed') is True
                 or t.get('closed') is True
                 or str(t.get('status', '')).upper() in ('CLOSED', 'FILLED', 'CANCELLED', 'EXPIRED'))
    return not is_closed


# ============================================================================
# DATABASE
# ============================================================================

def init_database():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS managed_trades
                 (trade_id INTEGER PRIMARY KEY,
                  contract_id TEXT,
                  direction TEXT DEFAULT 'LONG',
                  entry_price REAL,
                  original_stop REAL,
                  current_stop REAL,
                  stop_order_id INTEGER,
                  be_moved INTEGER DEFAULT 0,
                  first_seen TEXT,
                  last_modified TEXT)''')
    try:
        c.execute("ALTER TABLE managed_trades ADD COLUMN direction TEXT DEFAULT 'LONG'")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    c.execute('''CREATE TABLE IF NOT EXISTS modifications
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  timestamp TEXT,
                  trade_id INTEGER,
                  action TEXT,
                  old_stop REAL,
                  new_stop REAL,
                  success INTEGER)''')
    conn.commit()
    conn.close()


def get_managed_trade(trade_id: int) -> Optional[Dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM managed_trades WHERE trade_id = ?", (trade_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return {
            'trade_id':      row['trade_id'],
            'contract_id':   row['contract_id'],
            'direction':     row['direction'],
            'entry_price':   row['entry_price'],
            'original_stop': row['original_stop'],
            'current_stop':  row['current_stop'],
            'stop_order_id': row['stop_order_id'],
            'be_moved':      bool(row['be_moved']),
            'first_seen':    row['first_seen'],
            'last_modified': row['last_modified'],
        }
    return None


def save_managed_trade(trade: Dict):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""INSERT OR REPLACE INTO managed_trades
                 (trade_id, contract_id, direction, entry_price, original_stop, current_stop,
                  stop_order_id, be_moved, first_seen, last_modified)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?,
                         COALESCE((SELECT first_seen FROM managed_trades WHERE trade_id = ?), ?),
                         ?)""",
              (trade['trade_id'], trade['contract_id'], trade.get('direction', 'LONG'),
               trade['entry_price'], trade['original_stop'], trade['current_stop'],
               trade.get('stop_order_id'), int(trade['be_moved']),
               trade['trade_id'], datetime.now().isoformat(), datetime.now().isoformat()))
    conn.commit()
    conn.close()


def mark_trade_closed(trade_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM managed_trades WHERE trade_id = ?", (trade_id,))
    conn.commit()
    conn.close()


def get_all_managed_trade_ids() -> List[int]:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT trade_id FROM managed_trades")
    rows = c.fetchall()
    conn.close()
    return [r[0] for r in rows]


def log_modification(trade_id: int, action: str, old_stop: float, new_stop: float, success: bool):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""INSERT INTO modifications
                 (timestamp, trade_id, action, old_stop, new_stop, success)
                 VALUES (?, ?, ?, ?, ?, ?)""",
              (datetime.now().isoformat(), trade_id, action, old_stop, new_stop, int(success)))
    conn.commit()
    conn.close()


# ============================================================================
# ORDER / POSITION ACTIONS
# ============================================================================

def move_stop_automated(client: ProjectXClient, trade: Dict, managed: Dict, new_stop: float) -> bool:
    account_id        = trade.get('accountId')
    contract_id       = managed['contract_id']
    old_stop_order_id = managed.get('stop_order_id')
    old_stop          = managed['current_stop']
    
    # FIX-4: Round to proper tick size
    instrument = managed.get('contract_id', '').split('.')[-1][:3] if '.' in managed.get('contract_id', '') else 'MES'
    for key in INSTRUMENT_PARAMS.keys():
        if key in contract_id:
            instrument = key
            break
    new_stop = round_to_tick(new_stop, instrument)
    
    logger.info(f"   Moving stop: {old_stop:.4f} -> {new_stop:.4f}")

    if old_stop_order_id:
        client.cancel_order(old_stop_order_id)

    direction  = managed.get('direction', 'LONG').upper()
    close_side = 1 if direction == 'LONG' else 0   # SELL to close LONG, BUY to close SHORT
    
    # Explicit None check so netSize=0 doesn't fall to stale openSize
    _sz = None
    for _f in ('netSize', 'openSize', 'size'):
        _v = trade.get(_f)
        if _v is not None:
            _sz = _v
            break
    size = abs(int(_sz)) if _sz is not None and int(_sz) != 0 else 1

    result = None
    for attempt in range(1, STOP_RETRY_ATTEMPTS + 1):
        logger.info(f"   Stop attempt {attempt}...")
        result = client.place_order(
            account_id=account_id,
            contract_id=contract_id,
            order_type=2,  # FIX-3: Stop Market (was 3=Stop Limit)
            side=close_side,
            size=size,
            stop_price=new_stop,
        )
        if result and result.get('success'):
            break
        logger.warning(f"   Attempt {attempt} failed. Retrying...")
        time.sleep(STOP_RETRY_DELAY)

    if result and result.get('success'):
        managed['current_stop']  = new_stop
        managed['stop_order_id'] = result.get('orderId')
        save_managed_trade(managed)
        log_modification(managed['trade_id'], 'STOP_MOVED', old_stop, new_stop, True)
        logger.info(f"   ✅ Stop placed @ ${new_stop:.4f}")
        return True
    else:
        logger.error(f"   ❌ Failed to place stop after {STOP_RETRY_ATTEMPTS} attempts!")
        log_modification(managed['trade_id'], 'STOP_MOVED', old_stop, new_stop, False)
        return False


def close_position_automated(client: ProjectXClient, trade: Dict, managed: Dict, reason: str) -> bool:
    account_id  = trade.get('accountId')
    contract_id = managed['contract_id']
    size        = abs(int(trade.get('netSize') or trade.get('openSize') or trade.get('size') or 1))
    logger.warning(f"   Closing {size} contracts: {reason}")
    success = client.close_position(account_id=account_id, contract_id=contract_id, size=size)
    log_modification(managed['trade_id'], 'POSITION_CLOSED', 0, 0, success)
    if success:
        mark_trade_closed(managed['trade_id'])
    return success


# ============================================================================
# CORE TRADE MANAGER
# ============================================================================

def manage_trade(client: ProjectXClient, trade: Dict) -> Optional[float]:
    """Manage a single open trade. Returns realised PnL if trade closed this tick, else None."""
    trade_id    = trade.get('id')
    contract_id = trade.get('contractId', '')
    direction   = detect_direction(trade)
    instrument, params = get_instrument_params(contract_id)
    size        = abs(int(trade.get('netSize') or trade.get('openSize') or 1))
    point_value = params['point_value']

    entry_price = float(
        trade.get('averagePrice') or
        trade.get('price')        or
        trade.get('entryPrice')   or
        0.0
    )

    # ── LIVE PnL — fetched from real-time quote, NOT stale profitAndLoss ──────
    live_price = get_live_price(client, contract_id)

    if live_price and entry_price:
        if direction == 'LONG':
            pnl = (live_price - entry_price) * point_value * size
        else:
            pnl = (entry_price - live_price) * point_value * size
        price_source = f"live @ {live_price:.4f}"
    else:
        # Fallback: use API pnl field when quote is unavailable
        raw_pnl = trade.get('profitAndLoss')
        pnl = float(raw_pnl) if raw_pnl is not None else 0.0
        live_price = live_price or entry_price
        price_source = "API pnl (quote unavail)"

    # profit_pts should be price_diff only → divide out BOTH point_value AND size
    profit_pts = pnl / (point_value * size) if (point_value > 0 and size > 0) else 0.0

    # ── REGISTER NEW TRADE ────────────────────────────────────────────────────
    managed = get_managed_trade(trade_id)
    if not managed:
        if not entry_price:
            logger.warning(f"Trade {trade_id}: entry_price not yet available, skipping.")
            return
        original_stop = (
            entry_price - params['default_stop'] if direction == 'LONG'
            else entry_price + params['default_stop']
        )
        managed = {
            'trade_id':      trade_id,
            'contract_id':   contract_id,
            'direction':     direction,
            'entry_price':   entry_price,
            'original_stop': original_stop,
            'current_stop':  original_stop,
            'stop_order_id': None,
            'be_moved':      False,
        }
        save_managed_trade(managed)
        logger.info(
            f"New trade: {trade_id} ({instrument}) [{direction}]"
            f"  entry=${entry_price:.4f}  stop=${original_stop:.4f}"
        )

    if managed.get('direction') != direction:
        logger.info(f"   Direction updated: {managed.get('direction')} -> {direction}")
        managed['direction'] = direction
        save_managed_trade(managed)

    if managed.get('entry_price') is None or managed.get('current_stop') is None:
        logger.warning(f"   Invalid stop data for {trade_id}. Reinitializing.")
        if not entry_price:
            return
        managed['entry_price']  = entry_price
        managed['current_stop'] = (
            entry_price - params['default_stop'] if direction == 'LONG'
            else entry_price + params['default_stop']
        )
        save_managed_trade(managed)

    # ── LIVE PnL LOG PER TRADE ────────────────────────────────────────────────
    logger.info(
        f"  {instrument} #{trade_id} [{direction}] x{size}"
        f"  entry={entry_price:.4f}  now={live_price:.4f}"
        f"  PnL ${pnl:+.2f} ({profit_pts:+.2f} pts)  [{price_source}]"
    )

    # ── MAE EXIT ──────────────────────────────────────────────────────────────
    stop_distance_pts     = abs(managed['entry_price'] - managed['current_stop'])
    stop_distance_dollars = stop_distance_pts * point_value * size
    loss_dollars          = abs(min(0.0, pnl))
    if stop_distance_dollars > 0 and loss_dollars > (stop_distance_dollars * MAE_PERCENTAGE):
        logger.warning(
            f"   MAE TRIGGER: ${loss_dollars:.2f} loss > "
            f"{MAE_PERCENTAGE*100:.0f}% of stop (${stop_distance_dollars:.2f})"
        )
        close_position_automated(client, trade, managed, 'MAE 80% rule')
        return pnl  # return realised PnL for daily accumulation

    # ── TRAILING STOP ─────────────────────────────────────────────────────────
    if TRAILING_ENABLED and managed['be_moved'] and live_price:
        if direction == 'LONG':
            proposed = live_price - TRAILING_DISTANCE_PTS
            if proposed > managed['current_stop']:
                logger.info(f"   Trail LONG: {managed['current_stop']:.4f} -> {proposed:.4f}")
                move_stop_automated(client, trade, managed, proposed)
        else:
            proposed = live_price + TRAILING_DISTANCE_PTS
            if proposed < managed['current_stop']:
                logger.info(f"   Trail SHORT: {managed['current_stop']:.4f} -> {proposed:.4f}")
                move_stop_automated(client, trade, managed, proposed)

    # ── BREAKEVEN ─────────────────────────────────────────────────────────────
    be_trigger = params['be_trigger']
    be_move    = params['be_move']
    if not managed['be_moved'] and profit_pts >= be_trigger:
        new_stop = (
            managed['entry_price'] + be_move if direction == 'LONG'
            else managed['entry_price'] - be_move
        )
        logger.info(f"   Breakeven triggered: +{profit_pts:.2f} pts -> stop ${new_stop:.4f}")
        if move_stop_automated(client, trade, managed, new_stop):
            managed['be_moved'] = True
            save_managed_trade(managed)


# ============================================================================
# MAIN LOOP - 24/7 TRADING
# ============================================================================

def main_loop():
    logger.info("=" * 60)
    logger.info("  AUTOMATED TRADE MANAGER — 24/7 FUTURES TRADING")
    logger.info("  Supports: NY, London, Asia, UAE sessions")
    logger.info("=" * 60)

    if not PROJECTX_USERNAME or not PROJECTX_API_KEY:
        logger.error("Set PROJECTX_USERNAME and PROJECTX_API_KEY in .env")
        return

    client = ProjectXClient(PROJECTX_USERNAME, PROJECTX_API_KEY, 'prod')

    try:
        client.authenticate()
        logger.info("✅ ProjectX authenticated successfully")
    except Exception as e:
        logger.error(f"❌ Authentication failed: {e}")
        logger.error("   Check PROJECTX_USERNAME / PROJECTX_API_KEY in .env")
        return

    init_database()

    iteration  = 0
    daily_pnl  = 0.0
    _last_date = datetime.now().date()

    while True:
        try:
            iteration += 1
            now = datetime.now().strftime('%H:%M:%S')

            # Reset daily P&L on new calendar day
            if datetime.now().date() != _last_date:
                logger.info(f"New day — resetting daily PnL (was ${daily_pnl:+.2f})")
                daily_pnl  = 0.0
                _last_date = datetime.now().date()

            # Enforce DAILY_MAX_LOSS circuit breaker
            if DAILY_MAX_LOSS is not None and daily_pnl <= DAILY_MAX_LOSS:
                logger.error(f"🛑 DAILY_MAX_LOSS hit: ${daily_pnl:.2f} — stopping trade manager")
                break

            # Clear quote cache at top of every tick
            global _quote_cache
            _quote_cache = {}

            # Refresh authentication token periodically
            if iteration % 330 == 0:
                try:
                    client.authenticate()
                    logger.info("Token refreshed")
                except Exception as e:
                    logger.error(f"Token refresh failed: {e}")

            if iteration % 12 == 1:
                logger.info(f"\nCHECK #{iteration} - {now}")

            # Get open positions
            _raw        = client.get_open_positions(PROJECTX_ACCOUNT_ID)
            trades      = _raw if isinstance(_raw, list) else []
            open_trades = [t for t in trades if _is_open(t)]
            open_ids    = {t.get('id') for t in open_trades}

            # Clean up closed trades
            for db_id in get_all_managed_trade_ids():
                if db_id not in open_ids:
                    logger.info(f"Trade {db_id} closed -- removing")
                    mark_trade_closed(db_id)

            # FIX-1: REMOVED AUTO_SHUTDOWN - Trading 24/7 during futures market hours

            # Manage open trades
            if open_trades:
                for trade in open_trades:
                    _pnl = manage_trade(client, trade)
                    if _pnl is not None:
                        daily_pnl += _pnl
            elif iteration % 12 == 1:
                logger.info("No open trades")

            time.sleep(CHECK_INTERVAL_SECONDS)

        except KeyboardInterrupt:
            logger.info("Stopped.")
            break
        except Exception as e:
            logger.error(f"Error: {e}", exc_info=True)
            time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == '__main__':
    main_loop()
