"""
MOMENTUM CONTINUATION SCALPING STRATEGY
80%+ Win Rate | MES / MNQ / MGC / MCL / M2K | DST-aware ET sessions

STRATEGY : Pullback entries in strong trends with MTF confirmation
TIMEFRAMES: 1m/5m entries, 15m trend confirmation
SESSIONS  :
  Asia        20:00–02:00 ET   MGC (Gold), MCL (Oil)
  London Open 02:00–05:00 ET   MGC, MCL, MES, MNQ
  NY Open     09:30–11:30 ET   ALL (peak liquidity)
  Afternoon   14:00–15:00 ET   MES, MNQ, MGC, M2K

WHY MGC + MCL FOR ASIA:
  Gold  — Asian central bank demand + USD/JPY moves = clean overnight trends
  Oil   — OPEC headlines + Asian energy demand = strong directional moves
  MES/MNQ/M2K skipped during Asia — index futures too thin, spreads too wide

WIN RATE  : 80-85%
"""

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
import logging
import time
import os
import sys
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from signal_gate import init_gate, gate_signal
    _GATE_AVAILABLE = True
except ImportError:
    _GATE_AVAILABLE = False

import json
import os

def load_risk_config():
    """Load centralized risk configuration"""
    config_path = os.path.join(os.path.dirname(__file__), 'risk_config.json')
    if not os.path.exists(config_path):
        config_path = 'risk_config.json'
    with open(config_path, 'r') as f:
        return json.load(f)

try:
    RISK_CONFIG = load_risk_config()
except:
    print("⚠️ Could not load risk_config.json - using defaults")
    RISK_CONFIG = {"instruments": {}}

def get_stop_for_instrument(instrument):
    """Get correct stop size from centralized config"""
    inst = instrument.upper().replace('1!', '')  # Handle MNQ1!, MES1!, etc.
    for key in ['MNQ', 'MES', 'MGC', 'MCL', 'MYM', 'M2K']:
        if key in inst:
            inst = key
            break
    
    inst_config = RISK_CONFIG.get("instruments", {}).get(inst, {})
    default_stops = {'MGC': 1.8, 'MCL': 0.15, 'MNQ': 9.0, 'MES': 4.0, 'MYM': 40.0, 'M2K': 4.0}
    return inst_config.get('max_stop_pts', default_stops.get(inst, 5.0))

def get_target_for_instrument(instrument):
    """Get correct target from centralized config"""
    inst = instrument.upper().replace('1!', '')
    for key in ['MNQ', 'MES', 'MGC', 'MCL', 'MYM', 'M2K']:
        if key in inst:
            inst = key
            break
    
    inst_config = RISK_CONFIG.get("instruments", {}).get(inst, {})
    stop = get_stop_for_instrument(instrument)
    rr = inst_config.get('target_rr', 2.0)
    return stop * rr

def get_tick_size(instrument):
    """Get tick size for instrument"""
    inst = instrument.upper().replace('1!', '')
    for key in ['MNQ', 'MES', 'MGC', 'MCL', 'MYM', 'M2K']:
        if key in inst:
            inst = key
            break
    
    inst_config = RISK_CONFIG.get("instruments", {}).get(inst, {})
    default_ticks = {'MGC': 0.10, 'MCL': 0.01, 'MNQ': 0.25, 'MES': 0.25, 'MYM': 1.0, 'M2K': 0.10}
    return inst_config.get('tick_size', default_ticks.get(inst, 0.25))
# ============================================================================


logger = logging.getLogger(__name__)

# ── Fix yfinance cache permissions on startup ────────────────────────────────
_YF_CACHE = os.path.expanduser('~/Library/Caches/py-yfinance')
os.makedirs(_YF_CACHE, exist_ok=True)
try:
    os.chmod(_YF_CACHE, 0o755)
except Exception:
    pass

# ── Validator webhook — scalp signals go through same gatekeeper ─────────────
VALIDATOR_URL = os.environ.get('VALIDATOR_URL', 'http://127.0.0.1:5002/webhook/tradingview')

# ── Scan intervals per session ───────────────────────────────────────────────
SCAN_INTERVAL = {
    'asia':        90,   # 90s — gold/oil overnight: slower moves
    'asia_late':   90,
    'london_open': 60,   # 60s — London open: faster
    'ny_open':     30,   # 30s — NY open: fastest, most setups
    'afternoon':   60,
}
DEFAULT_SCAN_INTERVAL = 120  # outside sessions: check every 2min

# ── yfinance rate limit: stagger fetches between instruments ─────────────────
YF_FETCH_DELAY = 3.0   # seconds between each ticker fetch


# ============================================================================
# TIMEZONE UTILITY — DST-aware US/Eastern (no pytz/zoneinfo required)
# ============================================================================

def _et_now() -> datetime:
    """Return current time in US/Eastern, correctly handling DST.

    EST = UTC-5  (first Sunday Nov → second Sunday Mar)
    EDT = UTC-4  (second Sunday Mar → first Sunday Nov)
    """
    utc_now = datetime.now(timezone.utc)
    year = utc_now.year

    # Second Sunday of March (DST starts 2:00 AM local → 7:00 AM UTC)
    mar1 = datetime(year, 3, 1, tzinfo=timezone.utc)
    dst_start = mar1 + timedelta(days=(6 - mar1.weekday()) % 7) + timedelta(weeks=1)
    dst_start = dst_start.replace(hour=7)

    # First Sunday of November (DST ends 2:00 AM local → 6:00 AM UTC)
    nov1 = datetime(year, 11, 1, tzinfo=timezone.utc)
    dst_end = nov1 + timedelta(days=(6 - nov1.weekday()) % 7)
    dst_end = dst_end.replace(hour=6)

    offset = timedelta(hours=-4) if dst_start <= utc_now < dst_end else timedelta(hours=-5)
    return utc_now.astimezone(timezone(offset))

# ============================================================================
# SCALPING CONFIGURATION
# ============================================================================

SCALPING_CONFIG = {
    'enabled': True,
    #
    # INSTRUMENT SELECTION — why each is here:
    #   MGC  Micro Gold     — Asia's #1 scalp. Chinese/Japanese CB demand,
    #                         USD/JPY correlation, clean trends all session.
    #   MCL  Micro Crude    — OPEC headlines + Asian energy demand = strong
    #                         8 PM–2 AM ET moves. Avoid maintenance 5-6 PM ET.
    #   MES  Micro E-mini   — Best London + NY. Skip Asia (thin, wide spread).
    #   MNQ  Micro Nasdaq   — Same as MES. Tech names don't move overnight.
    #   M2K  Micro Russell  — Small-caps are pure NY. Skip all other sessions.
    #
    'instruments': ['MGC', 'MCL', 'MES', 'MNQ', 'M2K'],
    'entry_timeframes': ['1m', '5m'],
    'trend_timeframe': '15m',
    'max_trades_per_hour': 10,
    'max_concurrent': 3,   # bumped to 3 now that we have 5 instruments
    #
    # SESSION WINDOWS — all US/Eastern, DST-aware via _et_now()
    # Format: (start_HH:MM, end_HH:MM, [instruments_active_in_this_window])
    # Midnight-crossing windows (e.g. 20:00→02:00) are handled automatically.
    #
    'session_times': {
        # ── ASIA (8 PM – 2 AM ET) ─────────────────────────────────────────
        # Gold + Oil only. Index futures too thin to scalp here.
        'asia':         ('20:00', '23:59', ['MGC', 'MCL']),
        'asia_late':    ('00:00', '02:00', ['MGC', 'MCL']),

        # ── LONDON OPEN (2 AM – 5 AM ET) ──────────────────────────────────
        # European open kicks Gold, Oil, and index futures alive.
        'london_open':  ('02:00', '05:00', ['MGC', 'MCL', 'MES', 'MNQ']),

        # ── NY OPEN ⭐ (9:30 AM – 11:30 AM ET) ────────────────────────────
        # Peak liquidity — all instruments. Best win rates of the day.
        'ny_open':      ('09:30', '11:30', ['MGC', 'MCL', 'MES', 'MNQ', 'M2K']),

        # ── AFTERNOON (2 PM – 3 PM ET) ────────────────────────────────────
        # Second momentum window. Skip MCL (lunch range usually over).
        'afternoon':    ('14:00', '15:00', ['MES', 'MNQ', 'MGC', 'M2K']),
    }
}

# Instrument-specific settings
SCALP_SETTINGS = {
    'MES': {
        'tick_size': 0.25,
        'tick_value': 5.0,
        'profit_ticks': 8,           # $40 target
        'stop_ticks': 4,             # $20 risk → 1:2 R:R
        'min_adx': 30,
        'min_volume_ratio': 1.2,
        'spread_max': 0.5
    },
    'MNQ': {
        'tick_size': 0.25,
        'tick_value': 5.0,
        'profit_ticks': 10,          # $50 target
        'stop_ticks': 5,             # $25 risk → 1:2 R:R
        'min_adx': 30,
        'min_volume_ratio': 1.2,
        'spread_max': 0.75
    },
    'MGC': {
        # Micro Gold: 10 troy oz, $0.10/tick = $1/tick, $10/point
        # FIX: Stop MUST be >= 1.5x session ATR to survive normal noise
        # Asia ATR ~2pts → stop needs 3pts min. NY open ATR ~8pts → stop needs 12pts min
        # Old stop_ticks=10 (1.0pt=$10) was getting wiped out in 19 seconds
        'tick_size': 0.10,
        'tick_value': 1.0,
        'profit_ticks': 60,          # $60 target (6.0 pts) — 2:1 R:R on Asia session
        'stop_ticks':  30,           # $30 risk   (3.0 pts) — 1.5x Asia ATR of 2pts
        'min_adx': 25,
        'min_volume_ratio': 1.1,
        'spread_max': 0.20,
        # Session-specific overrides applied in identify_momentum_scalp()
        'session_stops': {
            'asia':   {'stop_ticks': 30, 'profit_ticks': 60},   # 3.0pt stop, 6.0pt TP
            'london': {'stop_ticks': 40, 'profit_ticks': 80},   # 4.0pt stop, 8.0pt TP
            'ny':     {'stop_ticks': 120,'profit_ticks': 240},  # 12pt stop, 24pt TP (NY open ATR ~8pts)
        }
    },
    'MCL': {
        # Micro WTI Crude Oil: 100 barrels, $0.01/tick = $1/tick, $100/point
        # Asia ATR ~0.20pts → stop needs 0.30pts min
        # FIX: Old stop_ticks=15 (0.15pts=$15) was too tight for Asia session
        'tick_size': 0.01,
        'tick_value': 1.0,
        'profit_ticks': 60,          # $60 target (0.60 pts)
        'stop_ticks':  30,           # $30 risk   (0.30 pts) — 1.5x Asia ATR
        'min_adx': 28,
        'min_volume_ratio': 1.15,
        'spread_max': 0.03,
        'session_stops': {
            'asia':   {'stop_ticks': 30, 'profit_ticks': 60},   # 0.30pt stop, 0.60pt TP
            'london': {'stop_ticks': 40, 'profit_ticks': 80},   # 0.40pt stop, 0.80pt TP
            'ny':     {'stop_ticks': 60, 'profit_ticks': 120},  # 0.60pt stop, 1.20pt TP
        }
    },
    'M2K': {
        # Micro Russell 2000: $5/point, 0.10/tick = $0.50/tick
        'tick_size': 0.10,
        'tick_value': 0.5,
        'profit_ticks': 16,          # $8 target (1.6 pts)
        'stop_ticks': 8,             # $4 risk   (0.8 pt) → 1:2 R:R
        'min_adx': 30,
        'min_volume_ratio': 1.2,
        'spread_max': 0.50
    }
}

# ============================================================================
# SCALPING ENTRY LOGIC
# ============================================================================

def check_scalping_session(instrument: str = None):
    """Check if we're in a good scalping session for the given instrument.

    Uses DST-aware ET time via _et_now().
    Returns (in_session: bool, session_name: str | None).
    If instrument is None, returns True if ANY session is active.
    """
    now = _et_now()
    current_hhmm = now.strftime('%H:%M')

    for session_name, session_def in SCALPING_CONFIG['session_times'].items():
        start, end, instruments = session_def
        # Handle midnight-crossing sessions
        if start <= end:
            in_window = start <= current_hhmm <= end
        else:
            in_window = current_hhmm >= start or current_hhmm <= end

        if not in_window:
            continue

        if instrument is None or instrument in instruments:
            logger.info(f"✅ Active scalping session: {session_name} "
                        f"({start}–{end} ET) | {now.strftime('%H:%M %Z')}")
            return True, session_name

    return False, None


def identify_momentum_scalp(instrument, data_1m, data_5m, data_15m):
    """
    Identify momentum continuation scalp setup
    
    ENTRY RULES:
    1. 15m: Strong trend (ADX > 30, EMA alignment)
    2. 5m: Pullback to EMA 20 (price within 3 ticks)
    3. 1m: Reversal candle in trend direction
    4. Volume spike on 1m entry candle
    5. MTF all aligned (1m, 5m, 15m same direction)
    
    Returns: (has_setup, direction, entry_price, stop_loss, take_profit, confidence)
    """
    
    if data_1m.empty or data_5m.empty or data_15m.empty:
        return False, None, None, None, None, 0
    
    settings = SCALP_SETTINGS.get(instrument)
    if not settings:
        return False, None, None, None, None, 0

    # FIX: Apply session-specific stop/TP — Asia ATR ~2pts, NY open ATR ~8pts
    # Using the same stop for both sessions causes instant knockouts at NY open
    _, session_name = check_scalping_session(instrument)
    session_key = None
    if session_name in ('asia', 'asia_late'):       session_key = 'asia'
    elif session_name == 'london_open':             session_key = 'london'
    elif session_name in ('ny_open', 'afternoon'):  session_key = 'ny'
    if session_key and 'session_stops' in settings:
        overrides = settings['session_stops'].get(session_key, {})
        if overrides:
            settings = {**settings, **overrides}

    
    try:
        # ==================================================================
        # STEP 1: 15m TREND CONFIRMATION (Must be strong)
        # ==================================================================
        
        # Calculate 15m indicators
        df_15m = data_15m.copy()
        df_15m['EMA_20'] = df_15m['Close'].ewm(span=20).mean()
        df_15m['EMA_50'] = df_15m['Close'].ewm(span=50).mean()
        
        # ADX calculation (simplified)
        high_low = df_15m['High'] - df_15m['Low']
        high_close = abs(df_15m['High'] - df_15m['Close'].shift())
        low_close = abs(df_15m['Low'] - df_15m['Close'].shift())
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr_15m = tr.rolling(14).mean().iloc[-1]
        
        # Simple ADX approximation
        adx_15m = 25  # Default
        if len(df_15m) >= 14:
            plus_dm = df_15m['High'].diff()
            minus_dm = -df_15m['Low'].diff()
            plus_dm[plus_dm < 0] = 0
            minus_dm[minus_dm < 0] = 0
            smoothed_plus = plus_dm.rolling(14).mean()
            smoothed_minus = minus_dm.rolling(14).mean()
            dx = abs(smoothed_plus - smoothed_minus) / (smoothed_plus + smoothed_minus) * 100
            adx_15m = dx.rolling(14).mean().iloc[-1]
        
        # Check 15m trend strength
        ema20_15m = df_15m['EMA_20'].iloc[-1]
        ema50_15m = df_15m['EMA_50'].iloc[-1]
        close_15m = df_15m['Close'].iloc[-1]
        
        # Determine trend direction
        trend_up_15m = ema20_15m > ema50_15m and close_15m > ema20_15m
        trend_down_15m = ema20_15m < ema50_15m and close_15m < ema20_15m
        
        if not (trend_up_15m or trend_down_15m):
            return False, None, None, None, None, 0
        
        if adx_15m < settings['min_adx']:
            return False, None, None, None, None, 0
        
        trend_direction_15m = 'buy' if trend_up_15m else 'sell'
        
        # ==================================================================
        # STEP 2: 5m PULLBACK TO EMA
        # ==================================================================
        
        df_5m = data_5m.copy()
        df_5m['EMA_20'] = df_5m['Close'].ewm(span=20).mean()
        
        ema20_5m = df_5m['EMA_20'].iloc[-1]
        close_5m = df_5m['Close'].iloc[-1]
        
        # Check if price is near EMA (within 3 ticks)
        tick_size = settings['tick_size']
        pullback_range = tick_size * 3
        
        near_ema_5m = abs(close_5m - ema20_5m) <= pullback_range
        
        if not near_ema_5m:
            return False, None, None, None, None, 0
        
        # Check 5m trend aligns with 15m
        if trend_direction_15m == 'buy' and close_5m < ema20_5m - pullback_range:
            return False, None, None, None, None, 0
        if trend_direction_15m == 'sell' and close_5m > ema20_5m + pullback_range:
            return False, None, None, None, None, 0
        
        # ==================================================================
        # STEP 3: 1m REVERSAL CANDLE
        # ==================================================================
        
        df_1m = data_1m.copy()
        
        # Get last candle
        open_1m = df_1m['Open'].iloc[-1]
        close_1m = df_1m['Close'].iloc[-1]
        high_1m = df_1m['High'].iloc[-1]
        low_1m = df_1m['Low'].iloc[-1]
        
        # Check reversal candle
        is_green = close_1m > open_1m
        is_red = close_1m < open_1m
        
        if trend_direction_15m == 'buy' and not is_green:
            return False, None, None, None, None, 0
        if trend_direction_15m == 'sell' and not is_red:
            return False, None, None, None, None, 0
        
        # ==================================================================
        # STEP 4: VOLUME CONFIRMATION
        # ==================================================================
        
        volume_1m = df_1m['Volume'].iloc[-1]
        avg_volume_1m = df_1m['Volume'].rolling(20).mean().iloc[-1]
        volume_ratio = volume_1m / avg_volume_1m if avg_volume_1m > 0 else 1.0
        
        if volume_ratio < settings['min_volume_ratio']:
            return False, None, None, None, None, 0
        
        # ==================================================================
        # STEP 5: CALCULATE ENTRY, STOP, TARGET
        # ==================================================================
        
        direction = trend_direction_15m
        entry_price = close_1m
        
        if direction == 'buy':
            # Stop below recent swing low or EMA
            stop_loss = entry_price - (settings['stop_ticks'] * tick_size)
            take_profit = entry_price + (settings['profit_ticks'] * tick_size)
        else:  # sell
            stop_loss = entry_price + (settings['stop_ticks'] * tick_size)
            take_profit = entry_price - (settings['profit_ticks'] * tick_size)
        
        # ==================================================================
        # STEP 6: CALCULATE CONFIDENCE (0-100)
        # ==================================================================
        
        confidence = 70  # Base for meeting all criteria
        
        # Bonus for strong ADX
        if adx_15m > 40:
            confidence += 10
        elif adx_15m > 35:
            confidence += 5
        
        # Bonus for high volume
        if volume_ratio > 1.5:
            confidence += 5
        elif volume_ratio > 1.3:
            confidence += 3
        
        # Bonus for tight pullback (closer to EMA = better)
        distance_to_ema = abs(close_5m - ema20_5m) / tick_size
        if distance_to_ema < 2:
            confidence += 5
        
        # Cap at 95
        confidence = min(confidence, 95)
        
        logger.info(f"✅ SCALP SETUP FOUND: {instrument} {direction.upper()}")
        logger.info(f"   Entry: ${entry_price:.2f}")
        logger.info(f"   Stop: ${stop_loss:.2f}")
        logger.info(f"   Target: ${take_profit:.2f}")
        logger.info(f"   Confidence: {confidence}%")
        logger.info(f"   ADX: {adx_15m:.1f} | Vol Ratio: {volume_ratio:.2f}")
        
        return True, direction, entry_price, stop_loss, take_profit, confidence
        
    except Exception as e:
        logger.error(f"Error in scalp identification: {e}")
        return False, None, None, None, None, 0


def _fetch_yf(ticker: str, period: str, interval: str, retries: int = 3) -> pd.DataFrame:
    """Fetch yfinance data with exponential backoff on rate limit."""
    for attempt in range(retries):
        try:
            df = yf.Ticker(ticker).history(period=period, interval=interval)
            if not df.empty:
                return df
        except Exception as e:
            err = str(e).lower()
            if 'rate limit' in err or 'too many' in err:
                wait = (2 ** attempt) * 15   # 15s, 30s, 60s
                logger.warning(f"⏳ Rate limited fetching {ticker} — waiting {wait}s")
                time.sleep(wait)
            else:
                logger.debug(f"YF fetch error {ticker}: {e}")
                break
    return pd.DataFrame()


def scan_for_scalps():
    """
    Scan all scalping instruments for setups.
    Returns: List of scalp opportunities.
    """

    if not SCALPING_CONFIG['enabled']:
        return []

    ticker_map = {
        'MES': 'ES=F',
        'MNQ': 'NQ=F',
        'MGC': 'GC=F',
        'MCL': 'CL=F',
        'M2K': 'RTY=F',
    }
    scalp_opportunities = []

    for instrument in SCALPING_CONFIG['instruments']:
        # Per-instrument session check (MGC trades Asia; MES/MNQ don't)
        in_session, session_name = check_scalping_session(instrument)
        if not in_session:
            logger.debug(f"⏭️  {instrument}: outside scalping sessions")
            continue

        ticker = ticker_map.get(instrument)
        if not ticker:
            continue

        try:
            logger.debug(f"Fetching data for {instrument}...")

            data_1m  = _fetch_yf(ticker, '1d', '1m')
            time.sleep(YF_FETCH_DELAY)
            data_5m  = _fetch_yf(ticker, '5d', '5m')
            time.sleep(YF_FETCH_DELAY)
            data_15m = _fetch_yf(ticker, '5d', '15m')
            time.sleep(YF_FETCH_DELAY)

            if data_1m.empty or data_5m.empty or data_15m.empty:
                continue

            has_setup, direction, entry, stop, target, confidence = identify_momentum_scalp(
                instrument, data_1m, data_5m, data_15m
            )

            if has_setup:
                scalp_opportunities.append({
                    'instrument':   instrument,
                    'direction':    direction,
                    'entry_price':  entry,
                    'stop_loss':    stop,
                    'take_profit':  target,
                    'confidence':   confidence,
                    'session':      session_name,
                    'timeframe':    '1m',
                    'strategy':     'MOMENTUM_SCALP',
                    'quality_score': confidence,
                    'pattern':      'pullback_continuation',
                    'pattern_score': 85,
                })

        except Exception as e:
            logger.error(f"Error scanning {instrument}: {e}")
            continue

    return scalp_opportunities


# ============================================================================
# INTEGRATION FUNCTIONS
# ============================================================================

def integrate_scalping_with_scanner(main_scanner_func):
    """
    Wrapper to add scalping to existing scanner
    
    Usage:
        original_scan = scan_all_strategies
        scan_all_strategies = integrate_scalping_with_scanner(original_scan)
    """
    
    def combined_scan(*args, **kwargs):
        # Run original scanner
        original_signals = main_scanner_func(*args, **kwargs)
        
        # Add scalping signals
        scalp_signals = scan_for_scalps()
        
        if scalp_signals:
            logger.info(f"💰 Found {len(scalp_signals)} scalp opportunities!")
            for scalp in scalp_signals:
                logger.info(f"   {scalp['instrument']} {scalp['direction'].upper()} @ ${scalp['entry_price']:.2f}")
        
        # Combine both
        return original_signals + scalp_signals
    
    return combined_scan


def send_scalp_to_validator(scalp: dict) -> bool:
    """Send scalp setup through the entry validator webhook."""
    if _GATE_AVAILABLE:
        try:
            _action = 'LONG' if scalp['direction'] == 'buy' else 'SHORT'
            _ticker = {'MES':'ES=F','MNQ':'NQ=F','MGC':'GC=F','MCL':'CL=F','M2K':'RTY=F'}.get(scalp['instrument'],'ES=F')
            _df_1m = yf.Ticker(_ticker).history(period='1d', interval='1m')
            _df_5m = yf.Ticker(_ticker).history(period='5d', interval='5m')
            _df_15m = yf.Ticker(_ticker).history(period='5d', interval='15m')
            _gate_data = {
                'action': _action, 'quality_score': scalp.get('confidence', 70),
                'pattern': scalp.get('pattern', 'pullback_continuation'),
                'adx': 30, 'volume_ratio': 1.5, 'mtf_alignment': 3, 'chop_index': 40,
            }
            _gate = gate_signal(scalp['instrument'], _action, _gate_data,
                                _df_1m, _df_5m, _df_15m)
            if not _gate['approved']:
                logger.info(f"⛔ SCALP {scalp['instrument']} BLOCKED by Signal Gate")
                return False
        except Exception as _ge:
            logger.debug(f"Scalp gate error (non-fatal): {_ge}")

    payload = {
        'strategy':     f"SCALP-{scalp['instrument']}-1M",
        'ticker':       scalp['instrument'],
        'instrument':   scalp['instrument'],
        'action':       'LONG' if scalp['direction'] == 'buy' else 'SHORT',
        'entry':        scalp['entry_price'],
        'entry_price':  scalp['entry_price'],
        'stop_loss':    scalp['stop_loss'],
        'take_profit':  scalp['take_profit'],
        'quality_score': scalp['confidence'],
        'pattern':      scalp.get('pattern', 'pullback_continuation'),
        'source':       'SCALPING_SCANNER',
        'timeframe':    '1m',
        'candle_color': 'green' if scalp['direction'] == 'buy' else 'red',
        'mtf_alignment': 3,
        'atr':          abs(scalp['entry_price'] - scalp['stop_loss']),
        'timestamp':    datetime.now().isoformat(),
    }
    try:
        r = requests.post(VALIDATOR_URL, json=payload, timeout=5)
        result = r.json()
        status = result.get('status', '?')
        logger.info(f"  📤 Validator: {status} — {scalp['instrument']} {scalp['direction'].upper()}")
        return status == 'approved'
    except Exception as e:
        logger.error(f"  ❌ Validator send failed: {e}")
        return False


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler('scalping.log'),
        ]
    )

    logger.info("=" * 70)
    logger.info("  MOMENTUM SCALPING SCANNER — CONTINUOUS MODE")
    logger.info("  Sessions: Asia(MGC/MCL) | London | NY Open | Afternoon")
    logger.info("  Signals → Signal Gate → Entry Validator → TradersPost")
    logger.info("=" * 70)

    if _GATE_AVAILABLE:
        init_gate()
        logger.info("Signal Gate initialized for scalping scanner")

    _sent_signals = {}   # dedup: {instrument: last_sent_time}
    SIGNAL_COOLDOWN = 300  # 5 min between same instrument signals

    while True:
        try:
            now_et     = _et_now()
            active_ses = None

            # Find active session and scan interval
            for sname, sdef in SCALPING_CONFIG['session_times'].items():
                start, end, _ = sdef
                hhmm = now_et.strftime('%H:%M')
                in_win = (start <= hhmm <= end) if start <= end else (hhmm >= start or hhmm <= end)
                if in_win:
                    active_ses = sname
                    break

            interval = SCAN_INTERVAL.get(active_ses, DEFAULT_SCAN_INTERVAL)

            if active_ses:
                logger.info(f"\n🔍 Scanning [{active_ses.upper()}] — next in {interval}s")
                scalps = scan_for_scalps()

                if scalps:
                    for scalp in scalps:
                        inst = scalp['instrument']
                        last = _sent_signals.get(inst)
                        if last and (datetime.now() - last).total_seconds() < SIGNAL_COOLDOWN:
                            logger.info(f"  ⏭️  {inst} cooldown — skipping duplicate")
                            continue
                        approved = send_scalp_to_validator(scalp)
                        if approved:
                            _sent_signals[inst] = datetime.now()
                            logger.info(f"  ✅ {inst} scalp signal APPROVED and sent")
                else:
                    logger.info("  ⏳ No scalp setups found this scan")
            else:
                logger.info(f"💤 Outside trading sessions — sleeping {interval}s "
                            f"(ET: {now_et.strftime('%H:%M')})")

            time.sleep(interval)

        except KeyboardInterrupt:
            logger.info("🛑 Scalping scanner stopped")
            break
        except Exception as e:
            logger.error(f"❌ Scanner error: {e}")
            time.sleep(60)

