#!/usr/bin/env python3
"""
INSTRUMENT-SPECIFIC TRADING CONFIGURATIONS

Master configuration for all 6 micro futures instruments across 4 trading sessions.
Each instrument has tailored parameters for pattern detection, quality gates,
risk management, and session-specific behavior.

Instrument tiers:
  COMMODITY PRIMARY   — MCL (Crude Oil), MGC (Gold)   — all 4 sessions
  EQUITY PRIMARY      — MES (S&P 500), MNQ (Nasdaq)   — NY + London overlap
  EQUITY SECONDARY    — MYM (Dow), M2K (Russell 2000)  — NY only, stricter gates

Session hours (Eastern Time):
  NY      09:30–16:00 ET   All instruments
  London  03:00–09:30 ET   MGC, MCL primary; MES/MNQ pre-market
  Asia    18:00–03:00 ET   MGC, MCL only
  UAE     20:00–04:00 ET   MGC, MCL (Dubai gold/oil overlap with Asia)

Target: 80%+ win rate through strict quality gates and session-appropriate parameters.
"""

from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

# ==========================================================================
# Session definitions (Eastern Time)
# ==========================================================================

SESSIONS = {
    'NY': {
        'start_hour': 9, 'start_min': 30,
        'end_hour': 16, 'end_min': 0,
        'label': 'New York',
        'description': 'US regular session — all instruments active',
    },
    'LONDON': {
        'start_hour': 3, 'start_min': 0,
        'end_hour': 9, 'end_min': 30,
        'label': 'London',
        'description': 'European session — gold/oil primary, equity pre-market',
    },
    'ASIA': {
        'start_hour': 18, 'start_min': 0,
        'end_hour': 3, 'end_min': 0,
        'label': 'Asia',
        'description': 'Asian session — gold/oil only',
    },
    'UAE': {
        'start_hour': 20, 'start_min': 0,
        'end_hour': 4, 'end_min': 0,
        'label': 'UAE/Dubai',
        'description': 'Dubai overlap — gold/oil (physical market hours)',
    },
}

# ==========================================================================
# Instrument definitions
# ==========================================================================

INSTRUMENTS: Dict[str, dict] = {

    # ------------------------------------------------------------------
    # MCL — MICRO CRUDE OIL
    # ------------------------------------------------------------------
    'MCL': {
        'name': 'Micro Crude Oil',
        'tier': 'COMMODITY_PRIMARY',
        'max_stop_pts': 0.15,
        'dollar_per_pt': 100.00,
        'tick_size': 0.01,
        'target_rr': 2.0,
        'correlation_group': 'ENERGY',

        'sessions': {
            'NY': {
                'enabled': True,
                'timeframes': ['5m', '10m', '15m', '30m', '1h'],
                'adx_min': 28,
                'quality_min': 72,
                'patterns': ['TREND_PULLBACK', 'MOMENTUM_CONT', 'BREAKOUT',
                             'IMPULSE', 'TREND_RESUMPTION'],
                'atr_multiplier': 1.0,
                'position_expiry_min': 45,
                'max_positions': 2,
                'notes': 'Most liquid crude session — NYMEX open, EIA data',
            },
            'LONDON': {
                'enabled': True,
                'timeframes': ['15m', '30m', '1h'],
                'adx_min': 30,
                'quality_min': 75,
                'patterns': ['TREND_PULLBACK', 'MOMENTUM_CONT', 'BREAKOUT'],
                'atr_multiplier': 0.85,
                'position_expiry_min': 60,
                'max_positions': 1,
                'notes': 'ICE Brent open drives MCL, moderate volume',
            },
            'ASIA': {
                'enabled': True,
                'timeframes': ['15m', '30m', '1h'],
                'adx_min': 32,
                'quality_min': 78,
                'patterns': ['BREAKOUT', 'IMPULSE', 'MOMENTUM_CONT'],
                'atr_multiplier': 0.7,
                'position_expiry_min': 90,
                'max_positions': 1,
                'notes': 'Lower volume but geopolitical moves; wider timeframes',
            },
            'UAE': {
                'enabled': True,
                'timeframes': ['15m', '30m', '1h'],
                'adx_min': 30,
                'quality_min': 76,
                'patterns': ['BREAKOUT', 'IMPULSE', 'MOMENTUM_CONT'],
                'atr_multiplier': 0.75,
                'position_expiry_min': 90,
                'max_positions': 1,
                'notes': 'Dubai Mercantile Exchange overlap, physical oil flows',
            },
        },

        'strategy_params': {
            'stop_loss_points': 0.12,
            'take_profit_points': 0.24,
            'default_position_size': 1,
            'max_position_size': 1,
            'min_rr_ratio': 2.0,
        },

        'best_strategies': [
            'MCL-BREAKOUT',
            'MCL-MOMENTUM',
            'MCL-PULLBACK',
        ],
    },

    # ------------------------------------------------------------------
    # MGC — MICRO GOLD
    # ------------------------------------------------------------------
    'MGC': {
        'name': 'Micro Gold',
        'tier': 'COMMODITY_PRIMARY',
        'max_stop_pts': 1.80,
        'dollar_per_pt': 10.00,
        'tick_size': 0.10,
        'target_rr': 2.0,
        'correlation_group': 'METALS',

        'sessions': {
            'NY': {
                'enabled': True,
                'timeframes': ['5m', '10m', '15m', '30m', '1h'],
                'adx_min': 25,
                'quality_min': 70,
                'patterns': ['TREND_PULLBACK', 'MOMENTUM_CONT', 'BREAKOUT',
                             'IMPULSE', 'TREND_RESUMPTION', 'EMA_CROSSOVER'],
                'atr_multiplier': 1.0,
                'position_expiry_min': 60,
                'max_positions': 2,
                'notes': 'COMEX most active — full pattern suite',
            },
            'LONDON': {
                'enabled': True,
                'timeframes': ['5m', '15m', '30m', '1h'],
                'adx_min': 22,
                'quality_min': 68,
                'patterns': ['TREND_PULLBACK', 'MOMENTUM_CONT', 'BREAKOUT',
                             'IMPULSE', 'TREND_RESUMPTION', 'EMA_CROSSOVER'],
                'atr_multiplier': 1.0,
                'position_expiry_min': 60,
                'max_positions': 2,
                'notes': 'LBMA gold fix — THE gold session, excellent trends',
            },
            'ASIA': {
                'enabled': True,
                'timeframes': ['15m', '30m', '1h'],
                'adx_min': 22,
                'quality_min': 72,
                'patterns': ['TREND_PULLBACK', 'MOMENTUM_CONT', 'BREAKOUT',
                             'IMPULSE'],
                'atr_multiplier': 0.8,
                'position_expiry_min': 90,
                'max_positions': 1,
                'notes': 'Shanghai Gold Exchange, physical demand drives moves',
            },
            'UAE': {
                'enabled': True,
                'timeframes': ['15m', '30m', '1h'],
                'adx_min': 22,
                'quality_min': 70,
                'patterns': ['TREND_PULLBACK', 'MOMENTUM_CONT', 'BREAKOUT',
                             'IMPULSE', 'TREND_RESUMPTION'],
                'atr_multiplier': 0.85,
                'position_expiry_min': 90,
                'max_positions': 2,
                'notes': 'Dubai Gold & Commodities Exchange — strong physical market',
            },
        },

        'strategy_params': {
            'stop_loss_points': 1.50,
            'take_profit_points': 3.00,
            'default_position_size': 1,
            'max_position_size': 1,
            'min_rr_ratio': 2.0,
        },

        'best_strategies': [
            'MGC-TREND',
            'MGC-BREAKOUT',
            'MGC-PULLBACK',
            'MGC-LONDON',
        ],
    },

    # ------------------------------------------------------------------
    # MES — MICRO E-MINI S&P 500
    # ------------------------------------------------------------------
    'MES': {
        'name': 'Micro E-mini S&P 500',
        'tier': 'EQUITY_PRIMARY',
        'max_stop_pts': 4.00,
        'dollar_per_pt': 5.00,
        'tick_size': 0.25,
        'target_rr': 2.0,
        'correlation_group': 'US_EQUITY',

        'sessions': {
            'NY': {
                'enabled': True,
                'timeframes': ['3m', '5m', '10m', '15m', '30m', '1h'],
                'adx_min': 25,
                'quality_min': 68,
                'patterns': ['TREND_PULLBACK', 'MOMENTUM_CONT', 'BREAKOUT',
                             'IMPULSE', 'TREND_RESUMPTION', 'EMA_CROSSOVER'],
                'atr_multiplier': 1.0,
                'position_expiry_min': 30,
                'max_positions': 2,
                'notes': 'Most liquid period — full pattern suite, fast expiry',
            },
            'LONDON': {
                'enabled': True,
                'timeframes': ['15m', '30m', '1h'],
                'adx_min': 28,
                'quality_min': 75,
                'patterns': ['TREND_PULLBACK', 'BREAKOUT', 'MOMENTUM_CONT'],
                'atr_multiplier': 0.8,
                'position_expiry_min': 45,
                'max_positions': 1,
                'notes': 'Pre-market — trades European macro, lower volume',
            },
            'ASIA': {
                'enabled': False,
                'notes': 'Too thin — do not trade MES in Asia session',
            },
            'UAE': {
                'enabled': False,
                'notes': 'Too thin — do not trade MES in UAE session',
            },
        },

        'strategy_params': {
            'stop_loss_points': 3.50,
            'take_profit_points': 7.00,
            'default_position_size': 1,
            'max_position_size': 2,
            'min_rr_ratio': 2.0,
        },

        'best_strategies': [
            'MES-TREND',
            'MES-MOMENTUM',
            'MES-ORB',
            'TL40',
        ],
    },

    # ------------------------------------------------------------------
    # MNQ — MICRO E-MINI NASDAQ 100
    # ------------------------------------------------------------------
    'MNQ': {
        'name': 'Micro E-mini Nasdaq 100',
        'tier': 'EQUITY_PRIMARY',
        'max_stop_pts': 9.00,
        'dollar_per_pt': 2.00,
        'tick_size': 0.25,
        'target_rr': 2.0,
        'correlation_group': 'US_EQUITY',

        'sessions': {
            'NY': {
                'enabled': True,
                'timeframes': ['3m', '5m', '10m', '15m', '30m', '1h'],
                'adx_min': 25,
                'quality_min': 68,
                'patterns': ['TREND_PULLBACK', 'MOMENTUM_CONT', 'BREAKOUT',
                             'IMPULSE', 'TREND_RESUMPTION', 'EMA_CROSSOVER'],
                'atr_multiplier': 1.0,
                'position_expiry_min': 30,
                'max_positions': 2,
                'notes': 'Best for momentum — tech-driven directional moves',
            },
            'LONDON': {
                'enabled': True,
                'timeframes': ['15m', '30m', '1h'],
                'adx_min': 28,
                'quality_min': 75,
                'patterns': ['TREND_PULLBACK', 'BREAKOUT', 'MOMENTUM_CONT'],
                'atr_multiplier': 0.8,
                'position_expiry_min': 45,
                'max_positions': 1,
                'notes': 'Pre-market — follows European tech sentiment',
            },
            'ASIA': {
                'enabled': False,
                'notes': 'Too thin — do not trade MNQ in Asia session',
            },
            'UAE': {
                'enabled': False,
                'notes': 'Too thin — do not trade MNQ in UAE session',
            },
        },

        'strategy_params': {
            'stop_loss_points': 8.00,
            'take_profit_points': 16.00,
            'default_position_size': 1,
            'max_position_size': 2,
            'min_rr_ratio': 2.0,
        },

        'best_strategies': [
            'MNQ-MOMENTUM',
            'MNQ-TREND',
            'MNQ-ORB',
            'TL43',
        ],
    },

    # ------------------------------------------------------------------
    # MYM — MICRO E-MINI DOW
    # ------------------------------------------------------------------
    'MYM': {
        'name': 'Micro E-mini Dow',
        'tier': 'EQUITY_SECONDARY',
        'max_stop_pts': 40.00,
        'dollar_per_pt': 0.50,
        'tick_size': 1.00,
        'target_rr': 2.0,
        'correlation_group': 'US_EQUITY',

        'sessions': {
            'NY': {
                'enabled': True,
                'timeframes': ['15m', '30m', '1h'],
                'adx_min': 35,
                'quality_min': 78,
                'patterns': ['TREND_PULLBACK', 'MOMENTUM_CONT', 'BREAKOUT'],
                'atr_multiplier': 1.0,
                'position_expiry_min': 30,
                'max_positions': 1,
                'notes': 'Choppy Dow — strict ADX 35+, high quality gate only',
            },
            'LONDON': {
                'enabled': False,
                'notes': 'MYM too choppy pre-market — avoid',
            },
            'ASIA': {
                'enabled': False,
                'notes': 'Do not trade MYM in Asia',
            },
            'UAE': {
                'enabled': False,
                'notes': 'Do not trade MYM in UAE',
            },
        },

        'strategy_params': {
            'stop_loss_points': 35.00,
            'take_profit_points': 70.00,
            'default_position_size': 1,
            'max_position_size': 1,
            'min_rr_ratio': 2.0,
        },

        'best_strategies': [
            'MYM-TREND',
            'MYM-BREAKOUT',
        ],
    },

    # ------------------------------------------------------------------
    # M2K — MICRO E-MINI RUSSELL 2000
    # ------------------------------------------------------------------
    'M2K': {
        'name': 'Micro E-mini Russell 2000',
        'tier': 'EQUITY_SECONDARY',
        'max_stop_pts': 4.00,
        'dollar_per_pt': 5.00,
        'tick_size': 0.10,
        'target_rr': 2.0,
        'correlation_group': 'US_EQUITY',

        'sessions': {
            'NY': {
                'enabled': True,
                'timeframes': ['10m', '15m', '30m', '1h'],
                'adx_min': 28,
                'quality_min': 75,
                'patterns': ['TREND_PULLBACK', 'MOMENTUM_CONT', 'BREAKOUT',
                             'IMPULSE'],
                'atr_multiplier': 1.0,
                'position_expiry_min': 30,
                'max_positions': 1,
                'notes': 'Small-cap index — more volatile, strict quality gate',
            },
            'LONDON': {
                'enabled': False,
                'notes': 'M2K too thin pre-market — avoid',
            },
            'ASIA': {
                'enabled': False,
                'notes': 'Do not trade M2K in Asia',
            },
            'UAE': {
                'enabled': False,
                'notes': 'Do not trade M2K in UAE',
            },
        },

        'strategy_params': {
            'stop_loss_points': 3.50,
            'take_profit_points': 7.00,
            'default_position_size': 1,
            'max_position_size': 1,
            'min_rr_ratio': 2.0,
        },

        'best_strategies': [
            'M2K-BREAKOUT',
            'M2K-MOMENTUM',
        ],
    },
}


# ==========================================================================
# Session detection
# ==========================================================================

def get_current_et() -> datetime:
    """Return current time in Eastern Time (auto-adjusts for EDT/EST)."""
    utc_now = datetime.now(timezone.utc)
    year = utc_now.year
    mar1 = datetime(year, 3, 1, tzinfo=timezone.utc)
    dst_start = (mar1 + timedelta(days=(6 - mar1.weekday()) % 7) +
                 timedelta(weeks=1)).replace(hour=7)
    nov1 = datetime(year, 11, 1, tzinfo=timezone.utc)
    dst_end = (nov1 + timedelta(days=(6 - nov1.weekday()) % 7)).replace(hour=6)
    is_edt = dst_start <= utc_now < dst_end
    et = timezone(timedelta(hours=-4) if is_edt else timedelta(hours=-5))
    return utc_now.astimezone(et)


def get_current_session() -> str:
    """Determine which trading session is currently active."""
    et = get_current_et()
    h = et.hour
    m = et.minute
    t = h + m / 60.0

    if 9.5 <= t < 16.0:
        return 'NY'
    if 3.0 <= t < 9.5:
        return 'LONDON'
    if t >= 20.0 or t < 4.0:
        return 'UAE'
    if t >= 18.0 or t < 3.0:
        return 'ASIA'

    return 'ASIA'


def get_active_instruments(session: str = None) -> List[str]:
    """Return instruments enabled for the given (or current) session, sorted by tier."""
    if session is None:
        session = get_current_session()

    active = []
    tier_order = {
        'COMMODITY_PRIMARY': 0,
        'EQUITY_PRIMARY': 1,
        'EQUITY_SECONDARY': 2,
    }

    for inst, cfg in INSTRUMENTS.items():
        sess_cfg = cfg.get('sessions', {}).get(session, {})
        if sess_cfg.get('enabled', False):
            active.append((tier_order.get(cfg['tier'], 9), inst))

    active.sort()
    return [inst for _, inst in active]


def get_instrument_session_config(instrument: str,
                                   session: str = None) -> Optional[dict]:
    """Return the session-specific config for an instrument, or None if disabled."""
    if session is None:
        session = get_current_session()

    inst_cfg = INSTRUMENTS.get(instrument)
    if not inst_cfg:
        return None

    sess_cfg = inst_cfg.get('sessions', {}).get(session, {})
    if not sess_cfg.get('enabled', False):
        return None

    return {
        'instrument': instrument,
        'session': session,
        'tier': inst_cfg['tier'],
        'max_stop_pts': inst_cfg['max_stop_pts'],
        'dollar_per_pt': inst_cfg['dollar_per_pt'],
        'tick_size': inst_cfg['tick_size'],
        'target_rr': inst_cfg['target_rr'],
        'timeframes': sess_cfg.get('timeframes', ['15m', '30m', '1h']),
        'adx_min': sess_cfg.get('adx_min', 25),
        'quality_min': sess_cfg.get('quality_min', 70),
        'patterns': sess_cfg.get('patterns', []),
        'atr_multiplier': sess_cfg.get('atr_multiplier', 1.0),
        'position_expiry_min': sess_cfg.get('position_expiry_min', 60),
        'max_positions': sess_cfg.get('max_positions', 1),
        'strategy_params': inst_cfg.get('strategy_params', {}),
    }


def get_scan_plan(session: str = None) -> List[dict]:
    """Return a prioritized list of instrument+timeframe combos to scan."""
    if session is None:
        session = get_current_session()

    plan = []
    instruments = get_active_instruments(session)

    for inst in instruments:
        cfg = get_instrument_session_config(inst, session)
        if not cfg:
            continue
        for tf in cfg['timeframes']:
            plan.append({
                'instrument': inst,
                'timeframe': tf,
                'session': session,
                'config': cfg,
            })

    return plan


def is_pattern_allowed(instrument: str, pattern: str,
                       session: str = None) -> bool:
    """Check if a specific pattern is allowed for this instrument in this session."""
    cfg = get_instrument_session_config(instrument, session)
    if not cfg:
        return False
    allowed = cfg.get('patterns', [])
    if not allowed:
        return True
    return pattern in allowed


def get_quality_gate(instrument: str, session: str = None) -> int:
    """Return the minimum quality score for this instrument in this session."""
    cfg = get_instrument_session_config(instrument, session)
    if not cfg:
        return 80
    return cfg.get('quality_min', 70)


def get_adx_threshold(instrument: str, session: str = None) -> int:
    """Return the minimum ADX for this instrument in this session."""
    cfg = get_instrument_session_config(instrument, session)
    if not cfg:
        return 25
    return cfg.get('adx_min', 25)


# ==========================================================================
# Summary / startup log
# ==========================================================================

def log_config_summary():
    """Print a startup summary of all instrument configurations."""
    lines = []
    lines.append("=" * 80)
    lines.append("INSTRUMENT-SPECIFIC TRADING CONFIGURATIONS")
    lines.append("=" * 80)

    for session_name in ['NY', 'LONDON', 'ASIA', 'UAE']:
        active = get_active_instruments(session_name)
        if not active:
            continue
        sess_info = SESSIONS[session_name]
        lines.append(f"\n  {sess_info['label']} ({session_name}) "
                     f"— {sess_info['start_hour']:02d}:{sess_info['start_min']:02d}"
                     f"–{sess_info['end_hour']:02d}:{sess_info['end_min']:02d} ET")
        lines.append(f"  {sess_info['description']}")
        lines.append(f"  Active: {', '.join(active)}")

        for inst in active:
            cfg = get_instrument_session_config(inst, session_name)
            if not cfg:
                continue
            risk = cfg['max_stop_pts'] * cfg['dollar_per_pt']
            lines.append(
                f"    {inst}: ADX>={cfg['adx_min']} Q>={cfg['quality_min']} "
                f"TF={','.join(cfg['timeframes'])} "
                f"SL={cfg['max_stop_pts']}pts(${risk:.0f}) "
                f"Patterns={len(cfg['patterns'])} "
                f"MaxPos={cfg['max_positions']}"
            )

    lines.append("\n" + "=" * 80)
    return "\n".join(lines)
