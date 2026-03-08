"""
Stop-loss and market-condition validation for prop-firm risk management.

validate_stop_loss   — caps the stop at the instrument maximum and recalculates
                       TP to maintain the target R:R.  Never rejects; always caps.
validate_market_conditions — blocks SIDEWAYS / RANGING / CHOPPY regimes
                             (breakout strategies are exempt).
"""

import logging
from typing import Dict, Tuple

logger = logging.getLogger(__name__)

# ============================================================================
# INSTRUMENT RISK CONFIG  (prop-firm conservative)
# ============================================================================
#   max_stop_pts : widest allowable stop in points
#   dollar_per_pt: dollar value per point for the micro contract
#   tick_size    : minimum price increment
#   target_rr    : reward-to-risk ratio used when recalculating TP
#   adx_min      : minimum ADX to consider the market "trending"
# ============================================================================
INSTRUMENT_CONFIG: Dict[str, dict] = {
    'MES': {'max_stop_pts': 4.00,  'dollar_per_pt': 5.00,   'tick_size': 0.25, 'target_rr': 2.0, 'adx_min': 25},
    'MNQ': {'max_stop_pts': 9.00,  'dollar_per_pt': 2.00,   'tick_size': 0.25, 'target_rr': 2.0, 'adx_min': 25},
    'MGC': {'max_stop_pts': 1.80,  'dollar_per_pt': 10.00,  'tick_size': 0.10, 'target_rr': 2.0, 'adx_min': 25},
    'MCL': {'max_stop_pts': 0.15,  'dollar_per_pt': 100.00, 'tick_size': 0.01, 'target_rr': 2.0, 'adx_min': 30},
    'MYM': {'max_stop_pts': 40.00, 'dollar_per_pt': 0.50,   'tick_size': 1.00, 'target_rr': 2.0, 'adx_min': 35},
    'M2K': {'max_stop_pts': 4.00,  'dollar_per_pt': 5.00,   'tick_size': 0.10, 'target_rr': 2.0, 'adx_min': 25},
}

CHOP_MAX = 61.8
MTF_MIN_AGREEMENT = 2

ALLOWED_STATES = frozenset({'TRENDING', 'CONTINUATION', 'MOMENTUM', 'BREAKOUT', 'IMPULSE'})
BLOCKED_STATES = frozenset({'SIDEWAYS', 'CONSOLIDATING', 'RANGING', 'CHOPPY'})


def _snap_to_tick(price: float, tick_size: float) -> float:
    """Round *price* to the nearest valid tick increment."""
    return round(round(price / tick_size) * tick_size, 10)


# ============================================================================
# STOP-LOSS VALIDATION (caps, never rejects)
# ============================================================================

def validate_stop_loss(alert_data: Dict) -> Tuple[bool, str]:
    """Check the stop-loss distance against the instrument maximum.

    If the stop is wider than allowed the function **caps** it at the max,
    recalculates TP to preserve ``target_rr``, and mutates *alert_data*
    in-place so downstream code sees the corrected prices.

    Returns ``(True, info_message)`` — the trade always proceeds.
    """
    symbol = (alert_data.get('ticker') or alert_data.get('instrument') or '')[:3].upper()
    action = alert_data.get('action', '').upper()
    entry = alert_data.get('entry') or alert_data.get('entry_price') or alert_data.get('close_1m')
    stop_loss = alert_data.get('stop_loss')

    cfg = INSTRUMENT_CONFIG.get(symbol)
    if not cfg:
        return True, f"No stop config for {symbol} — passing through"

    if entry is None or stop_loss is None:
        return True, "No entry/stop data to validate"

    entry = float(entry)
    stop_loss = float(stop_loss)
    stop_dist = abs(entry - stop_loss)
    max_stop = cfg['max_stop_pts']
    tick = cfg['tick_size']
    dollar_per_pt = cfg['dollar_per_pt']
    target_rr = cfg['target_rr']

    if stop_dist <= max_stop + 1e-9:
        risk_dollars = stop_dist * dollar_per_pt
        logger.info(f"✅ Stop OK: {stop_dist:.2f} pts (${risk_dollars:.0f}) ≤ {max_stop} pts for {symbol}")
        return True, f"Stop OK: {stop_dist:.2f} pts (${risk_dollars:.0f})"

    old_risk = stop_dist * dollar_per_pt
    new_risk = max_stop * dollar_per_pt

    if action in ('LONG', 'BUY'):
        new_sl = _snap_to_tick(entry - max_stop, tick)
        new_tp = _snap_to_tick(entry + max_stop * target_rr, tick)
    else:
        new_sl = _snap_to_tick(entry + max_stop, tick)
        new_tp = _snap_to_tick(entry - max_stop * target_rr, tick)

    alert_data['stop_loss'] = new_sl
    alert_data['take_profit'] = new_tp

    logger.info(
        f"🔧 STOP CAPPED: {stop_dist:.2f} pts (${old_risk:.0f}) → "
        f"{max_stop:.2f} pts (${new_risk:.0f}) for {symbol} | "
        f"SL={new_sl} TP={new_tp} (R:R {target_rr}:1)"
    )
    return True, (
        f"Stop capped: {stop_dist:.2f} → {max_stop:.2f} pts "
        f"(${old_risk:.0f} → ${new_risk:.0f}) for {symbol}"
    )


def get_max_stop_distance(symbol: str) -> float:
    """Return the maximum stop-loss distance (in points) for *symbol*.

    Falls back to a conservative 5-point default.
    """
    cfg = INSTRUMENT_CONFIG.get(symbol[:3].upper())
    return cfg['max_stop_pts'] if cfg else 5.0


# ============================================================================
# MARKET-CONDITION VALIDATION
# ============================================================================

def _is_breakout_or_impulse(alert_data: Dict) -> str:
    """Detect breakout/impulse from strategy name, pattern, or explicit flag.

    Returns 'BREAKOUT', 'IMPULSE', or '' (not a breakout).
    """
    pattern = (alert_data.get('pattern') or '').upper()
    strategy = (alert_data.get('strategy') or '').upper()

    if pattern == 'IMPULSE':
        return 'IMPULSE'
    if pattern == 'BREAKOUT':
        return 'BREAKOUT'

    if alert_data.get('breakout', False):
        return 'BREAKOUT'

    breakout_tags = ('ORB', 'BREAKOUT', 'BREAK', 'RANGE', 'IMPULSE')
    if any(tag in strategy for tag in breakout_tags):
        return 'BREAKOUT'

    vol_ratio = float(alert_data.get('volume_ratio') or 0)
    if vol_ratio >= 1.8:
        adx = float(alert_data.get('adx_1m') or alert_data.get('adx') or 0)
        if adx >= 15:
            return 'IMPULSE'

    return ''


def _classify_market_state(alert_data: Dict, symbol: str) -> str:
    """Return a regime label for the current market conditions."""
    adx = float(alert_data.get('adx_1m') or alert_data.get('adx') or 0)
    chop = float(alert_data.get('chop_index') or 50)

    cfg = INSTRUMENT_CONFIG.get(symbol, {})
    adx_min = cfg.get('adx_min', 25)

    breakout_type = _is_breakout_or_impulse(alert_data)
    if breakout_type:
        return breakout_type

    if chop > CHOP_MAX:
        return 'CHOPPY'

    if adx < adx_min * 0.7:
        return 'RANGING'

    if adx < adx_min:
        return 'CONSOLIDATING'

    if adx >= adx_min * 1.4:
        return 'MOMENTUM'

    if adx >= adx_min:
        return 'TRENDING'

    return 'SIDEWAYS'


def validate_market_conditions(alert_data: Dict) -> Tuple[bool, str]:
    """Block trades in non-trending / choppy regimes.

    Breakout strategies bypass the block.
    """
    symbol = (alert_data.get('ticker') or alert_data.get('instrument') or '')[:3].upper()
    state = _classify_market_state(alert_data, symbol)

    adx = float(alert_data.get('adx_1m') or alert_data.get('adx') or 0)
    chop = float(alert_data.get('chop_index') or 50)

    if state in ALLOWED_STATES:
        return True, f"✅ Market: {state} (ADX={adx:.1f}, Chop={chop:.1f}) for {symbol}"

    return False, (
        f"🚫 MARKET {state}: ADX={adx:.1f}, Chop={chop:.1f} — "
        f"only {', '.join(sorted(ALLOWED_STATES))} allowed"
    )
