"""
Prop Firm Validation Rules
Stop loss limits and market condition filters for conservative prop firm trading.

Each instrument has a maximum stop distance (in points) derived from the prop
firm's per-trade risk cap ($15-$20).  Stop losses computed from ATR or sent by
the alert are clamped to these limits before the trade reaches the broker.
"""

import logging
from typing import Dict, Tuple

logger = logging.getLogger(__name__)

# ============================================================================
# PROP FIRM STOP LOSS LIMITS
# ============================================================================
# max_stop_points  – furthest the stop can be from entry (instrument points)
# point_value      – dollar P&L per 1-point move (for display / $ risk calc)
# tick_size        – minimum price increment on the exchange
# target_rr        – minimum reward-to-risk ratio enforced on every trade

PROP_FIRM_STOPS = {
    'MES': {'max_stop_points': 4.00,  'point_value': 5.00,   'tick_size': 0.25, 'target_rr': 2.0},
    'MNQ': {'max_stop_points': 9.00,  'point_value': 2.00,   'tick_size': 0.25, 'target_rr': 2.0},
    'MGC': {'max_stop_points': 1.80,  'point_value': 10.00,  'tick_size': 0.10, 'target_rr': 2.0},
    'MCL': {'max_stop_points': 0.15,  'point_value': 100.00, 'tick_size': 0.01, 'target_rr': 2.0},
    'MYM': {'max_stop_points': 40.00, 'point_value': 0.50,   'tick_size': 1.00, 'target_rr': 2.0},
    'M2K': {'max_stop_points': 4.00,  'point_value': 5.00,   'tick_size': 0.10, 'target_rr': 2.0},
}

# ============================================================================
# MARKET CONDITION FILTERS
# ============================================================================

ADX_MINIMUMS = {
    'MES': 25,
    'MNQ': 25,
    'MGC': 25,
    'MCL': 30,
    'MYM': 35,
    'M2K': 25,
}

MAX_CHOPPINESS = 61.8
MIN_MTF_AGREEMENT = 2

ALLOWED_STATES = {'TRENDING', 'CONTINUATION', 'MOMENTUM', 'BREAKOUT'}
BLOCKED_STATES = {'SIDEWAYS', 'CONSOLIDATING', 'RANGING', 'CHOPPY'}


# ============================================================================
# HELPERS
# ============================================================================

def round_to_tick(price: float, tick_size: float) -> float:
    """Round price to nearest valid tick increment."""
    return round(round(price / tick_size) * tick_size, 10)


def get_prop_firm_config(symbol: str) -> dict:
    """Get prop firm stop/risk config for an instrument (empty dict if unknown)."""
    sym = symbol[:3].upper() if len(symbol) >= 3 else symbol.upper()
    return PROP_FIRM_STOPS.get(sym, {})


def clamp_stop_to_prop_firm(
    entry: float, stop_loss: float, action: str, symbol: str
) -> Tuple[float, bool]:
    """
    If stop_loss exceeds the prop firm max for *symbol*, clamp it and return
    (new_stop, True).  Otherwise return (stop_loss, False).
    """
    config = get_prop_firm_config(symbol)
    if not config:
        return stop_loss, False

    max_stop  = config['max_stop_points']
    tick_size = config['tick_size']
    current_dist = abs(float(entry) - float(stop_loss))

    if current_dist <= max_stop:
        return stop_loss, False

    if action.upper() in ('LONG', 'BUY'):
        new_stop = round_to_tick(float(entry) - max_stop, tick_size)
    else:
        new_stop = round_to_tick(float(entry) + max_stop, tick_size)

    logger.warning(
        f"⚠️ Stop clamped: {current_dist:.2f} pts → {max_stop} pts "
        f"(prop firm max for {symbol[:3].upper()})"
    )
    return new_stop, True


# ============================================================================
# STOP LOSS VALIDATION
# ============================================================================

def validate_stop_loss(alert_data: Dict) -> Tuple[bool, str]:
    """
    Final safety check: reject the trade if the stop distance still exceeds
    the prop firm limit.  Normally the stop has already been clamped by
    validate_risk_management, so this should rarely fire.
    """
    symbol = (alert_data.get('ticker') or alert_data.get('instrument') or '')[:3].upper()
    config = PROP_FIRM_STOPS.get(symbol)
    if not config:
        return True, f"✅ No prop firm config for {symbol} — skipping stop validation"

    entry     = alert_data.get('entry') or alert_data.get('entry_price') or alert_data.get('close_1m')
    stop_loss = alert_data.get('stop_loss')

    if entry is None or stop_loss is None:
        return True, "✅ No entry/stop data to validate stop distance"

    try:
        entry     = float(entry)
        stop_loss = float(stop_loss)
    except (TypeError, ValueError):
        return True, "✅ Non-numeric entry/stop — skipping"

    stop_distance    = abs(entry - stop_loss)
    max_stop         = config['max_stop_points']
    point_value      = config['point_value']
    risk_dollars     = stop_distance * point_value
    max_risk_dollars = max_stop * point_value

    if stop_distance > max_stop * 1.01:  # 1% tolerance for float rounding
        return False, (
            f"🚫 STOP TOO WIDE: {stop_distance:.2f} pts (${risk_dollars:.0f}) "
            f"— MAX: {max_stop} pts (${max_risk_dollars:.0f}) for {symbol}"
        )

    logger.info(
        f"✅ Stop OK: {stop_distance:.2f} pts (${risk_dollars:.0f}) ≤ "
        f"{max_stop} pts (${max_risk_dollars:.0f}) for {symbol}"
    )
    return True, f"✅ Stop OK: {stop_distance:.2f} pts (${risk_dollars:.0f}) for {symbol}"


# ============================================================================
# MARKET CONDITION VALIDATION
# ============================================================================

def classify_market_state(adx: float, chop: float, symbol: str) -> str:
    """Classify market regime from ADX and choppiness index."""
    min_adx = ADX_MINIMUMS.get(symbol[:3].upper(), 25)

    if adx >= min_adx + 15 and chop < 40:
        return 'MOMENTUM'
    if adx >= min_adx and chop <= MAX_CHOPPINESS:
        return 'TRENDING'
    if adx >= min_adx - 5 and chop <= MAX_CHOPPINESS + 5:
        return 'CONTINUATION'
    if chop > MAX_CHOPPINESS:
        return 'CHOPPY'
    if adx < min_adx:
        return 'RANGING'
    return 'SIDEWAYS'


def validate_market_conditions(alert_data: Dict) -> Tuple[bool, str]:
    """
    Require trending / momentum conditions.  Block ranging / choppy markets
    unless the strategy is a breakout type (ORB, BREAKOUT, etc.).
    """
    symbol   = (alert_data.get('ticker') or alert_data.get('instrument') or '')[:3].upper()
    strategy = alert_data.get('strategy', '').upper()
    adx      = alert_data.get('adx_1m') or alert_data.get('adx')
    chop     = alert_data.get('chop_index')

    is_breakout = any(
        tag in strategy for tag in ('ORB', 'BREAKOUT', 'BREAK', 'RANGE')
    )

    if adx is None:
        if is_breakout:
            return True, "✅ Market conditions: breakout strategy — ADX not required"
        logger.warning("⚠️ No ADX data — skipping market condition check")
        return True, "✅ Market conditions: no ADX data available"

    try:
        adx = float(adx)
    except (TypeError, ValueError):
        return True, "✅ Market conditions: non-numeric ADX — skipping"

    chop_val = float(chop) if chop is not None else 50.0
    state    = classify_market_state(adx, chop_val, symbol)
    min_adx  = ADX_MINIMUMS.get(symbol, 25)

    if is_breakout and state in BLOCKED_STATES:
        logger.info(
            f"✅ Market conditions: {state} but breakout exception "
            f"(ADX={adx:.1f}, Chop={chop_val:.1f})"
        )
        return True, f"✅ Market: {state} — breakout exception applied"

    if state in BLOCKED_STATES:
        return False, (
            f"🚫 MARKET {state}: ADX={adx:.1f} (min={min_adx}), "
            f"Chop={chop_val:.1f} (max={MAX_CHOPPINESS}) — "
            f"only TRENDING/MOMENTUM markets allowed for {symbol}"
        )

    logger.info(
        f"✅ Market conditions: {state} "
        f"(ADX={adx:.1f}, Chop={chop_val:.1f}) for {symbol}"
    )
    return True, f"✅ Market: {state} (ADX={adx:.1f}, Chop={chop_val:.1f})"
