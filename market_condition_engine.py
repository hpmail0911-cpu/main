#!/usr/bin/env python3
"""
Market Condition Engine — classifies market regime and adjusts risk parameters.

Provides:
  evaluate_conditions(vix, adx, instrument, action, alert_data) → ConditionState
  get_news_status() → (blocked: bool, event_name: str)
  MarketConditionEngine  (stateful wrapper, optional)
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Tuple

logger = logging.getLogger(__name__)

# ==========================================================================
# ConditionState — returned by evaluate_conditions()
# ==========================================================================

@dataclass
class ConditionState:
    condition: str = 'NORMAL'
    blocked: bool = False
    reason: str = ''
    quality_bonus: int = 0
    sl_multiplier: float = 1.5
    tp_multiplier: float = 3.0
    position_size_mult: float = 1.0


# ==========================================================================
# VIX regime thresholds
# ==========================================================================

VIX_LOW       = 15.0
VIX_ELEVATED  = 22.0
VIX_HIGH      = 30.0
VIX_EXTREME   = 40.0

# ==========================================================================
# Major news events (simplified calendar)
# ==========================================================================

_HIGH_IMPACT_EVENTS = {
    'FOMC': {'weekday': 2, 'hour': 14, 'duration_min': 120},
    'NFP':  {'weekday': 4, 'hour': 8,  'duration_min': 60},
    'CPI':  {'weekday': None, 'hour': 8, 'duration_min': 60},
}

_NEWS_BLACKOUT_MINUTES = 30


def get_news_status() -> Tuple[bool, str]:
    """Return (blocked, event_name) if inside a high-impact news window."""
    try:
        et = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-5)))
        hour = et.hour
        minute = et.minute
        weekday = et.weekday()
        day = et.day

        if weekday == 4 and 1 <= day <= 7 and 8 <= hour < 9:
            return True, 'NFP (Non-Farm Payroll)'

        if weekday == 2 and 10 <= day <= 17 and 14 <= hour < 16:
            return True, 'FOMC Statement'

        if 10 <= day <= 15 and weekday <= 4 and 8 <= hour < 9:
            return True, 'CPI/PPI Release Window'

        return False, ''
    except Exception as e:
        logger.debug(f"News status check error: {e}")
        return False, ''


# ==========================================================================
# Core classification
# ==========================================================================

def _classify_vix_regime(vix: float) -> str:
    if vix <= 0:
        return 'UNKNOWN'
    if vix < VIX_LOW:
        return 'LOW_VOL'
    if vix < VIX_ELEVATED:
        return 'NORMAL'
    if vix < VIX_HIGH:
        return 'ELEVATED'
    if vix < VIX_EXTREME:
        return 'HIGH_VOL'
    return 'EXTREME'


def _classify_trend(adx: float) -> str:
    if adx >= 40:
        return 'STRONG_TREND'
    if adx >= 25:
        return 'TRENDING'
    if adx >= 18:
        return 'WEAK_TREND'
    return 'RANGE_BOUND'


def evaluate_conditions(vix: float, adx: float, instrument: str,
                        action: str, alert_data: dict) -> ConditionState:
    """Evaluate market conditions and return risk adjustments.

    Parameters
    ----------
    vix : float
        Current VIX level (0 if unavailable).
    adx : float
        ADX value for the instrument.
    instrument : str
        e.g. 'MES', 'MNQ', 'MGC'.
    action : str
        'LONG', 'SHORT', 'buy', 'sell'.
    alert_data : dict
        Full alert payload (for additional context).

    Returns
    -------
    ConditionState with risk parameter adjustments.
    """
    state = ConditionState()
    vix_regime = _classify_vix_regime(vix)
    trend_class = _classify_trend(adx)

    # News blackout
    news_blocked, event_name = get_news_status()
    if news_blocked:
        state.blocked = True
        state.reason = f'{event_name} — news blackout active'
        state.condition = 'NEWS_BLACKOUT'
        return state

    # VIX-based regime adjustments
    if vix_regime == 'EXTREME':
        action_upper = action.upper()
        equity_insts = {'MES', 'MNQ', 'MYM', 'M2K'}
        if instrument[:3].upper() in equity_insts and action_upper in ('LONG', 'BUY'):
            state.blocked = True
            state.reason = f'VIX {vix:.1f} extreme — blocking equity LONGs'
            state.condition = 'VIX_EXTREME'
            return state
        state.condition = 'VIX_EXTREME'
        state.quality_bonus = -15
        state.sl_multiplier = 1.5
        state.tp_multiplier = 3.5
        state.position_size_mult = 0.5

    elif vix_regime == 'HIGH_VOL':
        state.condition = 'HIGH_VOLATILITY'
        state.quality_bonus = -10
        state.sl_multiplier = 1.5
        state.tp_multiplier = 3.5
        state.position_size_mult = 0.7

    elif vix_regime == 'ELEVATED':
        state.condition = 'ELEVATED_VOL'
        state.quality_bonus = -5
        state.sl_multiplier = 1.5
        state.tp_multiplier = 3.0
        state.position_size_mult = 0.85

    elif vix_regime == 'LOW_VOL':
        state.condition = 'LOW_VOLATILITY'
        state.quality_bonus = 5
        state.sl_multiplier = 1.5
        state.tp_multiplier = 3.5
        state.position_size_mult = 1.0

    else:
        state.condition = 'NORMAL'
        state.quality_bonus = 0

    # Trend-based quality bonus
    if trend_class == 'STRONG_TREND':
        state.quality_bonus += 5
    elif trend_class == 'RANGE_BOUND':
        state.quality_bonus -= 5

    return state


# ==========================================================================
# MarketConditionEngine (stateful class wrapper)
# ==========================================================================

class MarketConditionEngine:
    """Stateful wrapper around evaluate_conditions for scan loops."""

    def __init__(self):
        self._last_vix = 0.0
        self._last_check = None
        self._cache_ttl = timedelta(minutes=5)

    def evaluate(self, vix: float, adx: float, instrument: str,
                 action: str, alert_data: dict) -> ConditionState:
        self._last_vix = vix
        self._last_check = datetime.now(timezone.utc)
        return evaluate_conditions(vix, adx, instrument, action, alert_data)

    @staticmethod
    def log_state(state: 'ConditionState', log=None):
        """Log the current market condition state."""
        if log is None:
            import logging
            log = logging.getLogger('market_condition_engine')
        condition = state.condition if hasattr(state, 'condition') else 'UNKNOWN'
        blocked = state.blocked if hasattr(state, 'blocked') else False
        reason = state.reason if hasattr(state, 'reason') else ''
        if blocked:
            log.info(f"  Market: {condition} — BLOCKED: {reason}")
        else:
            log.info(f"  Market: {condition} — OK")

    @property
    def last_vix(self) -> float:
        return self._last_vix

    @property
    def last_check(self):
        return self._last_check
