"""
Prop Firm Validation Rules
Conservative stop-loss limits and market-condition filters.

Per-instrument limits are sized so max risk per trade stays $15-$20.
All trades target 2:1 reward-to-risk.
"""

import logging
from typing import Dict, Tuple

logger = logging.getLogger(__name__)

# ============================================================================
# PROP FIRM STOP-LOSS LIMITS
# ============================================================================
# max_stop_pts : widest stop allowed (in instrument points)
# tick_size    : minimum price increment for the instrument
# point_value  : dollar value of one full point move per contract
# target_rr    : minimum reward:risk ratio
#
#   Instrument  Max Stop   Max Risk$   Tick    $/pt
#   -------------------------------------------------
#   MES         4.00 pts   $20.00      0.25    $5.00
#   MNQ         9.00 pts   $18.00      0.25    $2.00
#   MGC         1.80 pts   $18.00      0.10   $10.00
#   MCL         0.15 pts   $15.00      0.01  $100.00
#   MYM        40.00 pts   $20.00      1.00    $0.50
#   M2K         4.00 pts   $20.00      0.10    $5.00
# ============================================================================

PROP_FIRM_LIMITS = {
    'MES': {'max_stop_pts': 4.00,  'tick_size': 0.25, 'point_value':   5.00, 'target_rr': 2.0},
    'MNQ': {'max_stop_pts': 9.00,  'tick_size': 0.25, 'point_value':   2.00, 'target_rr': 2.0},
    'MGC': {'max_stop_pts': 1.80,  'tick_size': 0.10, 'point_value':  10.00, 'target_rr': 2.0},
    'MCL': {'max_stop_pts': 0.15,  'tick_size': 0.01, 'point_value': 100.00, 'target_rr': 2.0},
    'MYM': {'max_stop_pts': 40.00, 'tick_size': 1.00, 'point_value':   0.50, 'target_rr': 2.0},
    'M2K': {'max_stop_pts': 4.00,  'tick_size': 0.10, 'point_value':   5.00, 'target_rr': 2.0},
}

# ============================================================================
# MARKET CONDITION FILTERS
# ============================================================================
# Per-instrument ADX minimums — below this the market is considered ranging.
# MCL gets a higher bar because crude is notoriously choppy.
# MYM gets a higher bar because the Dow whipsaws more than NQ/ES.

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

ALLOWED_REGIMES  = {'TRENDING', 'CONTINUATION', 'MOMENTUM', 'BREAKOUT'}
BLOCKED_REGIMES  = {'SIDEWAYS', 'CONSOLIDATING', 'RANGING', 'CHOPPY'}
BREAKOUT_KEYWORDS = ('ORB', 'BREAKOUT', 'BREAK', 'RANGE')


# ============================================================================
# HELPERS
# ============================================================================

def round_to_tick(price: float, tick_size: float) -> float:
    """Round a price to the nearest valid tick."""
    if tick_size <= 0:
        return price
    return round(round(price / tick_size) * tick_size, 10)


# ============================================================================
# STOP-LOSS VALIDATION
# ============================================================================

def validate_stop_loss(alert_data: Dict) -> Tuple[bool, str]:
    """
    Verify stop-loss distance is within prop-firm limits.

    This is a *safety check* — the main validator should already have capped
    stops in validate_risk_management().  If something slips through, this
    catches it.
    """
    symbol = (alert_data.get('ticker') or alert_data.get('instrument') or '')[:3].upper()
    action = alert_data.get('action', '').upper()
    entry = alert_data.get('entry') or alert_data.get('entry_price') or alert_data.get('close_1m')
    stop_loss = alert_data.get('stop_loss')

    if entry is None or stop_loss is None:
        return True, "No entry/stop data to validate"

    limits = PROP_FIRM_LIMITS.get(symbol)
    if not limits:
        return True, f"No prop firm limits defined for {symbol}"

    entry_f = float(entry)
    sl_f = float(stop_loss)
    stop_dist = abs(entry_f - sl_f)

    max_stop = limits['max_stop_pts']
    tick = limits['tick_size']
    max_risk_dollars = round(max_stop * limits['point_value'], 2)
    actual_risk_dollars = round(stop_dist * limits['point_value'], 2)

    # Half-tick tolerance for floating-point rounding
    if stop_dist > max_stop + tick * 0.5:
        return False, (
            f"\U0001f6ab STOP TOO WIDE: {stop_dist:.2f} pts (${actual_risk_dollars}) "
            f"\u2014 MAX: {max_stop} pts (${max_risk_dollars}) for {symbol}"
        )

    logger.info(
        f"\u2705 Stop OK: {stop_dist:.2f} pts (${actual_risk_dollars}) "
        f"\u2264 {max_stop} pts (${max_risk_dollars}) for {symbol}"
    )
    return True, f"Stop within limits: {stop_dist:.2f} pts (${actual_risk_dollars})"


# ============================================================================
# MARKET REGIME CLASSIFICATION
# ============================================================================

def _classify_regime(adx: float, chop: float, adx_min: int) -> str:
    """Derive market regime from ADX + choppiness index."""
    if chop > MAX_CHOPPINESS:
        return 'CHOPPY'
    if adx < adx_min:
        return 'RANGING'
    if adx >= adx_min * 1.5:
        return 'MOMENTUM'
    if chop <= 38.2:
        return 'TRENDING'
    return 'CONTINUATION'


def validate_market_conditions(alert_data: Dict) -> Tuple[bool, str]:
    """
    Only allow trades in TRENDING / MOMENTUM / CONTINUATION markets.
    Block RANGING / CHOPPY unless the strategy is a breakout type.
    """
    symbol = (alert_data.get('ticker') or alert_data.get('instrument') or '')[:3].upper()
    strategy = (alert_data.get('strategy') or '').upper()
    adx = alert_data.get('adx_1m') or alert_data.get('adx')
    chop = alert_data.get('chop_index')

    if adx is None:
        return True, "\u2705 Market conditions: no ADX data, passing"

    adx = float(adx)
    chop = float(chop) if chop is not None else 50.0
    adx_min = ADX_MINIMUMS.get(symbol, 25)

    regime = _classify_regime(adx, chop, adx_min)
    is_breakout = any(kw in strategy for kw in BREAKOUT_KEYWORDS)

    if regime in BLOCKED_REGIMES:
        if is_breakout:
            return True, (
                f"\u2705 Market {regime} but breakout strategy \u2014 exception applied "
                f"(ADX={adx:.1f}, Chop={chop:.1f})"
            )
        return False, (
            f"\U0001f6ab BLOCKED: Market is {regime} \u2014 only trade "
            f"TRENDING/MOMENTUM markets (ADX={adx:.1f} min={adx_min}, Chop={chop:.1f})"
        )

    return True, (
        f"\u2705 Market regime: {regime} (ADX={adx:.1f}, Chop={chop:.1f}) \u2014 trading allowed"
    )
