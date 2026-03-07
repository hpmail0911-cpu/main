"""
Market Condition Engine — evaluates macro/micro conditions and returns
trade-level adjustments (block, quality bonus, SL/TP multiplier tweaks).

Used by:
  validator.py        — optional layer 2b (MCE gate + quality bonus)
  ai_chart_scanner.py — per-signal condition check before webhook send
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Dict, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public data class returned by evaluate_conditions()
# ---------------------------------------------------------------------------

@dataclass
class ConditionState:
    condition: str = "NORMAL"
    blocked: bool = False
    reason: str = ""
    quality_bonus: int = 0
    sl_multiplier: float = 2.5
    tp_multiplier: float = 1.5
    position_size_mult: float = 1.0


# ---------------------------------------------------------------------------
# News blackout windows (Eastern Time)
# ---------------------------------------------------------------------------

_HIGH_IMPACT_WINDOWS = [
    # (weekday, hour_start, hour_end, event_name)
    (0, 10, 10.5,  "ISM Manufacturing"),
    (1, 10, 10.5,  "JOLTS / CB Confidence"),
    (2, 8,  8.75,  "ADP Employment"),
    (2, 14, 14.5,  "FOMC Minutes"),
    (3, 8,  9,     "Jobless Claims / GDP"),
    (4, 8,  9,     "NFP / Employment Report"),
]


def _et_now() -> datetime:
    utc_now = datetime.now(timezone.utc)
    year = utc_now.year
    mar1 = datetime(year, 3, 1, tzinfo=timezone.utc)
    dst_start = (mar1 + timedelta(days=(6 - mar1.weekday()) % 7) + timedelta(weeks=1)).replace(hour=7)
    nov1 = datetime(year, 11, 1, tzinfo=timezone.utc)
    dst_end = (nov1 + timedelta(days=(6 - nov1.weekday()) % 7)).replace(hour=6)
    is_edt = dst_start <= utc_now < dst_end
    et = timezone(timedelta(hours=-4) if is_edt else timedelta(hours=-5))
    return utc_now.astimezone(et)


def get_news_status() -> Tuple[bool, str]:
    """Return (blocked, event_name) if inside a high-impact news window."""
    now = _et_now()
    wd = now.weekday()
    h = now.hour + now.minute / 60.0

    for win_wd, win_start, win_end, event in _HIGH_IMPACT_WINDOWS:
        if wd == win_wd and win_start <= h < win_end:
            return True, event

    return False, ""


# ---------------------------------------------------------------------------
# VIX regime classification
# ---------------------------------------------------------------------------

def _vix_regime(vix: float) -> str:
    if vix <= 0:
        return "UNKNOWN"
    if vix < 15:
        return "LOW_VOL"
    if vix < 20:
        return "NORMAL"
    if vix < 30:
        return "ELEVATED"
    if vix < 40:
        return "HIGH"
    return "EXTREME"


# ---------------------------------------------------------------------------
# Core evaluation
# ---------------------------------------------------------------------------

class MarketConditionEngine:
    """Stateless engine — call evaluate() or the module-level shortcut."""

    def evaluate(self, vix: float, adx: float, instrument: str,
                 action: str, extra: dict) -> ConditionState:
        return evaluate_conditions(vix, adx, instrument, action, extra)


def evaluate_conditions(vix: float, adx: float, instrument: str,
                        action: str, extra: dict) -> ConditionState:
    """Evaluate current market conditions and return a ConditionState.

    Parameters
    ----------
    vix       : Current VIX value (0 if unavailable)
    adx       : Current ADX value
    instrument: e.g. 'MES', 'MGC'
    action    : 'LONG' / 'SHORT' / 'buy' / 'sell'
    extra     : Additional alert fields (unused currently, reserved)
    """
    state = ConditionState()
    regime = _vix_regime(vix)
    action_up = action.upper()

    # ── EXTREME VIX ──────────────────────────────────────────────
    if regime == "EXTREME":
        if action_up in ("LONG", "BUY") and instrument in ("MES", "MNQ", "MYM", "M2K"):
            state.condition = "EXTREME_VIX"
            state.blocked = True
            state.reason = f"VIX {vix:.1f} — extreme fear, equity LONGs blocked"
            return state
        state.condition = "EXTREME_VIX"
        state.quality_bonus = -10
        state.sl_multiplier = 1.5
        state.tp_multiplier = 1.0
        state.position_size_mult = 0.5
        return state

    # ── HIGH VIX ─────────────────────────────────────────────────
    if regime == "HIGH":
        state.condition = "HIGH_VIX"
        state.quality_bonus = -5
        state.sl_multiplier = 2.0
        state.tp_multiplier = 1.2
        state.position_size_mult = 0.75
        return state

    # ── LOW VOL ──────────────────────────────────────────────────
    if regime == "LOW_VOL":
        state.condition = "LOW_VOL"
        state.quality_bonus = 5
        return state

    # ── NORMAL / ELEVATED ────────────────────────────────────────
    if regime == "ELEVATED":
        state.condition = "ELEVATED_VIX"
        state.quality_bonus = -2
        return state

    state.condition = "NORMAL"
    return state
