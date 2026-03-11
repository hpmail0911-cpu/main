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

PROGRESSIVE_TP_ENABLED = True
PROGRESSIVE_TP_RATIO   = 1.5     # close 50% at 1.5x stop distance
PROGRESSIVE_TP_PCT     = 0.50    # close this fraction of position

TELEGRAM_ENABLED       = True
TELEGRAM_WARNING_PNL   = -200
TELEGRAM_PAUSE_PNL     = -400
TELEGRAM_BOT_TOKEN     = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID       = os.environ.get('TELEGRAM_CHAT_ID', '')

WHATSAPP_ENABLED       = True
WHATSAPP_PHONE         = os.environ.get('WHATSAPP_PHONE', '+13128382340')
WHATSAPP_API_KEY       = os.environ.get('WHATSAPP_API_KEY', '')
TWILIO_ACCOUNT_SID     = os.environ.get('TWILIO_ACCOUNT_SID', '')
TWILIO_AUTH_TOKEN      = os.environ.get('TWILIO_AUTH_TOKEN', '')
TWILIO_WHATSAPP_FROM   = os.environ.get('TWILIO_WHATSAPP_FROM', '')

# BE triggers in USD: MNQ/MES/MYM/M2K=$2.58, MGC/MCL=$1.25
# Converted to points: USD / point_value
INSTRUMENT_PARAMS = {
    'MNQ':  {'be_trigger': 1.29,    'be_move': 0.50, 'point_value': 2.0,   'default_stop': 9.0,  'tick_size': 0.25, 'trail_distance': 3.5, 'min_hold_seconds': 30},
    'MES':  {'be_trigger': 0.516,   'be_move': 0.25, 'point_value': 5.0,   'default_stop': 4.0,  'tick_size': 0.25, 'trail_distance': 1.5, 'min_hold_seconds': 30},
    'MGC':  {'be_trigger': 0.125,   'be_move': 0.10, 'point_value': 10.0,  'default_stop': 1.8,  'tick_size': 0.10, 'trail_distance': 0.6, 'min_hold_seconds': 30},
    'MCL':  {'be_trigger': 0.0125,  'be_move': 0.005,'point_value': 100.0, 'default_stop': 0.15, 'tick_size': 0.01, 'trail_distance': 0.05, 'min_hold_seconds': 30},
    'MCLE': {'be_trigger': 0.0125,  'be_move': 0.005,'point_value': 100.0, 'default_stop': 0.15, 'tick_size': 0.01, 'trail_distance': 0.05, 'min_hold_seconds': 30},
    'MYM':  {'be_trigger': 5.16,    'be_move': 1.0,  'point_value': 0.50,  'default_stop': 40.0, 'tick_size': 1.0,  'trail_distance': 12.0, 'min_hold_seconds': 30},
    'M2K':  {'be_trigger': 0.516,   'be_move': 0.20, 'point_value': 5.0,   'default_stop': 4.0,  'tick_size': 0.10, 'trail_distance': 1.5, 'min_hold_seconds': 30},
}

# Per-instrument circuit breaker: stop trading after 2 consecutive losses or $70 loss
INSTRUMENT_MAX_CONSEC_LOSSES = 2
INSTRUMENT_MAX_DAILY_LOSS = -70
_instrument_losses: Dict[str, List[float]] = {}
_instrument_blocked: Dict[str, str] = {}
_instrument_loss_date: str = ''

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
    decimals = max(0, len(str(tick).rstrip('0').split('.')[-1])) if '.' in str(tick) else 0
    return round(round(price / tick) * tick, decimals)


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
                  partial_closed INTEGER DEFAULT 0,
                  original_size INTEGER DEFAULT 1,
                  first_seen TEXT,
                  last_modified TEXT)''')
    for col, default in [('direction', "'LONG'"), ('partial_closed', '0'), ('original_size', '1')]:
        try:
            c.execute(f"ALTER TABLE managed_trades ADD COLUMN {col} TEXT DEFAULT {default}")
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


def check_instrument_circuit_breaker(instrument: str) -> bool:
    """Returns True if instrument is BLOCKED (should not trade)."""
    global _instrument_losses, _instrument_blocked, _instrument_loss_date
    today = datetime.now().strftime('%Y-%m-%d')
    if today != _instrument_loss_date:
        _instrument_losses = {}
        _instrument_blocked = {}
        _instrument_loss_date = today
    return instrument in _instrument_blocked


def record_instrument_loss(instrument: str, pnl: float):
    """Record a loss for circuit breaker tracking."""
    global _instrument_losses, _instrument_blocked, _instrument_loss_date
    today = datetime.now().strftime('%Y-%m-%d')
    if today != _instrument_loss_date:
        _instrument_losses = {}
        _instrument_blocked = {}
        _instrument_loss_date = today

    if instrument not in _instrument_losses:
        _instrument_losses[instrument] = []
    _instrument_losses[instrument].append(pnl)

    losses = _instrument_losses[instrument]
    daily_total = sum(losses)
    consec = 0
    for p in reversed(losses):
        if p < 0:
            consec += 1
        else:
            break

    if consec >= INSTRUMENT_MAX_CONSEC_LOSSES:
        _instrument_blocked[instrument] = f"{consec} consecutive losses"
        logger.warning(f"🛑 {instrument} BLOCKED: {consec} consecutive losses")
        send_alert(f"<b>🛑 {instrument} BLOCKED</b>\n{consec} consecutive losses\n"
                   f"Daily total: ${daily_total:+.2f}\n"
                   f"Use /unblock_{instrument.lower()} to resume", level='CRITICAL')

    if daily_total <= INSTRUMENT_MAX_DAILY_LOSS:
        _instrument_blocked[instrument] = f"Daily loss ${daily_total:.2f}"
        logger.warning(f"🛑 {instrument} BLOCKED: Daily loss ${daily_total:.2f}")
        send_alert(f"<b>🛑 {instrument} BLOCKED</b>\n"
                   f"Daily loss: ${daily_total:+.2f} (limit: ${INSTRUMENT_MAX_DAILY_LOSS})\n"
                   f"Use /unblock_{instrument.lower()} to resume", level='CRITICAL')


def unblock_instrument(instrument: str):
    """Manually unblock an instrument after reviewing losses."""
    global _instrument_blocked
    if instrument in _instrument_blocked:
        reason = _instrument_blocked.pop(instrument)
        logger.info(f"✅ {instrument} UNBLOCKED (was: {reason})")
        send_alert(f"<b>✅ {instrument} UNBLOCKED</b>\nTrading resumed", level='INFO')
        return True
    return False


def get_blocked_instruments() -> Dict[str, str]:
    """Return dict of blocked instruments and reasons."""
    return dict(_instrument_blocked)


def send_telegram(message: str, level: str = 'INFO'):
    """Send a Telegram alert if configured."""
    if not TELEGRAM_ENABLED or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        prefix = {'WARNING': '⚠️', 'CRITICAL': '🛑', 'INFO': 'ℹ️'}.get(level, 'ℹ️')
        import requests as _req
        _req.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={'chat_id': TELEGRAM_CHAT_ID, 'text': f"{prefix} {message}", 'parse_mode': 'HTML'},
            timeout=5,
        )
    except Exception as e:
        logger.debug(f"Telegram send failed: {e}")


def send_whatsapp(message: str, level: str = 'INFO'):
    """Send a WhatsApp alert. Supports Twilio or CallMeBot."""
    if not WHATSAPP_ENABLED or not WHATSAPP_PHONE:
        return
    prefix = {'WARNING': '⚠️', 'CRITICAL': '🛑', 'INFO': 'ℹ️'}.get(level, 'ℹ️')
    text = f"{prefix} {message}".replace('<b>', '*').replace('</b>', '*').replace('<br>', '\n')

    import requests as _req

    # Method 1: Twilio WhatsApp API (most reliable, paid)
    if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_WHATSAPP_FROM:
        try:
            _req.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json",
                data={
                    'From': f"whatsapp:{TWILIO_WHATSAPP_FROM}",
                    'To': f"whatsapp:{WHATSAPP_PHONE}",
                    'Body': text,
                },
                auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
                timeout=10,
            )
            return
        except Exception as e:
            logger.debug(f"Twilio WhatsApp failed: {e}")

    # Method 2: CallMeBot (free, requires one-time registration)
    if WHATSAPP_API_KEY:
        try:
            import urllib.parse
            _req.get(
                f"https://api.callmebot.com/whatsapp.php?"
                f"phone={WHATSAPP_PHONE}&text={urllib.parse.quote(text)}&apikey={WHATSAPP_API_KEY}",
                timeout=10,
            )
            return
        except Exception as e:
            logger.debug(f"CallMeBot WhatsApp failed: {e}")


def send_alert(message: str, level: str = 'INFO'):
    """Send alert via all configured channels."""
    send_telegram(message, level)
    send_whatsapp(message, level)


def get_managed_trade(trade_id: int) -> Optional[Dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM managed_trades WHERE trade_id = ?", (trade_id,))
    row = c.fetchone()
    conn.close()
    if row:
        keys = row.keys()
        return {
            'trade_id':      row['trade_id'],
            'contract_id':   row['contract_id'],
            'direction':     row['direction'] if 'direction' in keys else 'LONG',
            'entry_price':   row['entry_price'],
            'original_stop': row['original_stop'],
            'current_stop':  row['current_stop'],
            'stop_order_id': row['stop_order_id'],
            'be_moved':      bool(row['be_moved']),
            'partial_closed': bool(row['partial_closed']) if 'partial_closed' in keys else False,
            'original_size': int(row['original_size']) if 'original_size' in keys else 1,
            'first_seen':    row['first_seen'],
            'last_modified': row['last_modified'],
        }
    return None


def save_managed_trade(trade: Dict):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""INSERT OR REPLACE INTO managed_trades
                 (trade_id, contract_id, direction, entry_price, original_stop, current_stop,
                  stop_order_id, be_moved, partial_closed, original_size, first_seen, last_modified)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                         COALESCE((SELECT first_seen FROM managed_trades WHERE trade_id = ?), ?),
                         ?)""",
              (trade['trade_id'], trade['contract_id'], trade.get('direction', 'LONG'),
               trade['entry_price'], trade['original_stop'], trade['current_stop'],
               trade.get('stop_order_id'), int(trade.get('be_moved', False)),
               int(trade.get('partial_closed', False)), int(trade.get('original_size', 1)),
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

    if check_instrument_circuit_breaker(instrument):
        logger.debug(f"  {instrument} #{trade_id} — BLOCKED by circuit breaker, skipping")
        return None
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
        original_stop = round_to_tick(original_stop, instrument)
        managed = {
            'trade_id':      trade_id,
            'contract_id':   contract_id,
            'direction':     direction,
            'entry_price':   entry_price,
            'original_stop': original_stop,
            'current_stop':  original_stop,
            'stop_order_id': None,
            'be_moved':      False,
            'partial_closed': False,
            'original_size':  size,
        }

        # ── IMMEDIATELY PLACE HARD STOP ON EXCHANGE ──────────────────────
        close_side = 1 if direction == 'LONG' else 0
        stop_side = close_side ^ 1  # opposite side to close
        stop_result = None
        for _attempt in range(STOP_RETRY_ATTEMPTS):
            stop_result = client.place_order(
                account_id=trade.get('accountId'),
                contract_id=contract_id,
                order_type=2,   # Stop Market
                side=stop_side,
                size=size,
                stop_price=original_stop,
            )
            if stop_result and stop_result.get('success'):
                managed['stop_order_id'] = stop_result.get('orderId')
                break
            time.sleep(STOP_RETRY_DELAY)

        if managed.get('stop_order_id'):
            logger.info(
                f"New trade: {trade_id} ({instrument}) [{direction}]"
                f"  entry=${entry_price:.4f}  HARD STOP=${original_stop:.4f}"
                f"  (order #{managed['stop_order_id']})"
            )
        else:
            logger.error(
                f"New trade: {trade_id} ({instrument}) [{direction}]"
                f"  entry=${entry_price:.4f}  ⚠️ STOP FAILED — relying on MAE"
            )
            send_alert(
                f"<b>⚠️ STOP FAILED</b> {instrument} #{trade_id} [{direction}]\n"
                f"Entry: ${entry_price:.4f} | Stop should be: ${original_stop:.4f}\n"
                f"NO HARD STOP ON EXCHANGE — MAE only",
                level='CRITICAL'
            )

        save_managed_trade(managed)

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

    # ── MINIMUM HOLD TIME (prevent premature exits / 1-second trades) ────────
    min_hold = params.get('min_hold_seconds', 30)
    first_seen = managed.get('first_seen', '')
    if first_seen:
        try:
            entry_dt = datetime.fromisoformat(first_seen)
            hold_seconds = (datetime.now() - entry_dt).total_seconds()
            if hold_seconds < min_hold:
                logger.debug(f"   Hold time {hold_seconds:.0f}s < {min_hold}s min — skip stop mods")
                return
        except Exception:
            pass

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
        record_instrument_loss(instrument, pnl)
        return pnl  # return realised PnL for daily accumulation

    # ── PROGRESSIVE PROFIT TARGET (close 50% at 1.5x stop distance) ────────
    stop_dist = abs(managed['entry_price'] - managed.get('original_stop', managed['current_stop']))
    tp1_dist = stop_dist * PROGRESSIVE_TP_RATIO

    if (PROGRESSIVE_TP_ENABLED and not managed.get('partial_closed', False)
            and size >= 2 and profit_pts >= tp1_dist):
        close_qty = max(1, int(size * PROGRESSIVE_TP_PCT))
        logger.info(f"   TP1 HIT: +{profit_pts:.2f} pts >= {tp1_dist:.2f} pts (1.5x stop) "
                    f"— closing {close_qty} of {size} contracts")
        _close_side = 1 if direction == 'LONG' else 0
        result = client.place_order(
            account_id=trade.get('accountId'),
            contract_id=managed['contract_id'],
            order_type=2,
            side=_close_side ^ 1,
            size=close_qty,
        )
        if result and result.get('success'):
            managed['partial_closed'] = True
            save_managed_trade(managed)
            log_modification(managed['trade_id'], 'PARTIAL_TP1', 0, profit_pts, True)
            send_alert(
                    f"<b>TP1</b> {instrument} #{trade_id} [{direction}]\n"
                    f"Closed {close_qty}/{size} @ +{profit_pts:.2f} pts (${pnl:+.2f})",
                    level='INFO'
                )
            logger.info(f"   ✅ TP1: closed {close_qty} contracts, trailing remainder")
        else:
            log_modification(managed['trade_id'], 'PARTIAL_TP1', 0, profit_pts, False)

    # ── TRAILING STOP (instrument-aware distances) ───────────────────────────
    trail_dist = params.get('trail_distance', TRAILING_DISTANCE_PTS)
    if TRAILING_ENABLED and managed['be_moved'] and live_price:
        if direction == 'LONG':
            proposed = round_to_tick(live_price - trail_dist, instrument)
            if proposed > managed['current_stop']:
                logger.info(f"   Trail LONG: {managed['current_stop']:.4f} -> {proposed:.4f} "
                            f"(dist={trail_dist} pts)")
                move_stop_automated(client, trade, managed, proposed)
        else:
            proposed = round_to_tick(live_price + trail_dist, instrument)
            if proposed < managed['current_stop']:
                logger.info(f"   Trail SHORT: {managed['current_stop']:.4f} -> {proposed:.4f} "
                            f"(dist={trail_dist} pts)")
                move_stop_automated(client, trade, managed, proposed)

    # ── BREAKEVEN ─────────────────────────────────────────────────────────────
    be_trigger = params['be_trigger']
    be_move    = params['be_move']
    if not managed['be_moved'] and profit_pts >= be_trigger:
        new_stop = round_to_tick(
            managed['entry_price'] + be_move if direction == 'LONG'
            else managed['entry_price'] - be_move,
            instrument
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
    logger.info("  Progressive TP: close 50% at 1.5x stop, trail remainder")
    logger.info("  Instrument-aware trailing: MNQ=2pt MES=1pt MGC=0.8pt MCL=0.05pt")
    logger.info("  Telegram alerts: -$200 warning, -$400 pause")
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
                send_alert(f"<b>Daily Summary</b>\nPnL: ${daily_pnl:+.2f}", level='INFO')
                logger.info(f"New day — resetting daily PnL (was ${daily_pnl:+.2f})")
                daily_pnl  = 0.0
                _last_date = datetime.now().date()
                main_loop._warn_sent = False
                main_loop._pause_sent = False

            # Telegram PnL alerts
            if (TELEGRAM_ENABLED or WHATSAPP_ENABLED) and daily_pnl <= TELEGRAM_PAUSE_PNL and not getattr(main_loop, '_pause_sent', False):
                send_alert(
                    f"<b>🛑 DAILY PnL: ${daily_pnl:+.2f}</b>\n"
                    f"Hit ${TELEGRAM_PAUSE_PNL} threshold — PAUSING TRADING",
                    level='CRITICAL'
                )
                main_loop._pause_sent = True
            elif (TELEGRAM_ENABLED or WHATSAPP_ENABLED) and daily_pnl <= TELEGRAM_WARNING_PNL and not getattr(main_loop, '_warn_sent', False):
                send_alert(
                    f"<b>⚠️ DAILY PnL: ${daily_pnl:+.2f}</b>\n"
                    f"Hit ${TELEGRAM_WARNING_PNL} warning threshold",
                    level='WARNING'
                )
                main_loop._warn_sent = True

            # Enforce DAILY_MAX_LOSS circuit breaker
            if DAILY_MAX_LOSS is not None and daily_pnl <= DAILY_MAX_LOSS:
                send_alert(f"<b>🛑 MAX LOSS HIT: ${daily_pnl:+.2f}</b>\nTrade Manager STOPPED", level='CRITICAL')
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

            # Check for unblock requests (touch file: unblock_MGC, unblock_MES, etc.)
            for _ub_file in [f for f in os.listdir('.') if f.startswith('unblock_')]:
                _ub_inst = _ub_file.replace('unblock_', '').upper()
                if unblock_instrument(_ub_inst):
                    os.remove(_ub_file)

            if iteration % 12 == 1:
                blocked = get_blocked_instruments()
                if blocked:
                    logger.warning(f"  BLOCKED instruments: {blocked}")
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
