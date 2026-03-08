#!/usr/bin/env python3
"""
Threshold Tuner — loads learned quality thresholds from the learning monitor
and exposes them to the AI chart scanner for adaptive quality filtering.

The learning monitor writes learned_thresholds.json; this module reads it.

API used by ai_chart_scanner.py:
  load_learned_thresholds()
  get_learned_quality_adjustment(strategy, instrument, session) → int
  is_strategy_disabled(strategy) → bool
  is_strategy_preferred(strategy) → bool
"""

import json
import os
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

_THRESHOLDS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                'learned_thresholds.json')

_data = {
    'strategy_thresholds': {},
    'instrument_thresholds': {},
    'disabled_strategies': [],
    'preferred_strategies': [],
}

_loaded = False


def load_learned_thresholds(path: str = None):
    """Load learned thresholds from JSON file written by learning_monitor."""
    global _data, _loaded
    fpath = path or _THRESHOLDS_PATH

    if not os.path.exists(fpath):
        logger.info("No learned_thresholds.json found — using defaults")
        _loaded = True
        return

    try:
        with open(fpath, 'r') as f:
            _data = json.load(f)
        count = len(_data.get('strategy_thresholds', {}))
        disabled = len(_data.get('disabled_strategies', []))
        preferred = len(_data.get('preferred_strategies', []))
        logger.info(f"Loaded learned thresholds: {count} strategies, "
                    f"{disabled} disabled, {preferred} preferred")
        _loaded = True
    except Exception as e:
        logger.warning(f"Could not load learned thresholds: {e}")
        _loaded = True


def get_learned_quality_adjustment(strategy: str, instrument: str = '',
                                   session: str = '') -> int:
    """Return quality score adjustment for a strategy/instrument/session combo.

    Positive = raise the quality bar (worse performance historically).
    Negative = lower the quality bar (good performance historically).
    """
    if not _loaded:
        load_learned_thresholds()

    thresholds = _data.get('strategy_thresholds', {})

    key = f"{strategy}_{instrument}_{session}"
    entry = thresholds.get(key)
    if entry:
        return entry.get('quality_adj', 0)

    key_no_session = f"{strategy}_{instrument}_"
    for k, v in thresholds.items():
        if k.startswith(f"{strategy}_{instrument}_"):
            return v.get('quality_adj', 0)

    return 0


def is_strategy_disabled(strategy: str) -> bool:
    """Return True if the strategy has been auto-disabled due to poor performance."""
    if not _loaded:
        load_learned_thresholds()
    return strategy in _data.get('disabled_strategies', [])


def is_strategy_preferred(strategy: str) -> bool:
    """Return True if the strategy has been marked as preferred (high WR)."""
    if not _loaded:
        load_learned_thresholds()
    return strategy in _data.get('preferred_strategies', [])


def get_instrument_threshold(instrument: str, session: str = '') -> dict:
    """Return learned quality thresholds for an instrument/session combo."""
    if not _loaded:
        load_learned_thresholds()

    inst_thresholds = _data.get('instrument_thresholds', {})

    key = f"{instrument}_{session}"
    entry = inst_thresholds.get(key)
    if entry:
        return entry

    for k, v in inst_thresholds.items():
        if k.startswith(f"{instrument}_"):
            return v

    return {}
