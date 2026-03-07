"""
Threshold Tuner — loads learned quality adjustments from the JSON file
produced by learning_monitor_fixed.py and exposes simple query functions.

Used by ai_chart_scanner.py:
    from threshold_tuner import (
        load_learned_thresholds,
        get_learned_quality_adjustment,
        is_strategy_disabled,
        is_strategy_preferred,
    )
    load_learned_thresholds()
"""

import json
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_THRESHOLDS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "learned_thresholds.json",
)

_data: dict = {}


def load_learned_thresholds(path: Optional[str] = None):
    """Load the learned_thresholds.json file into memory.

    Called once at scanner startup and again whenever new data is written.
    """
    global _data
    p = path or _THRESHOLDS_PATH
    if not os.path.exists(p):
        logger.info("threshold_tuner: no learned_thresholds.json yet — defaults active")
        _data = {}
        return
    try:
        with open(p, "r") as f:
            _data = json.load(f)
        n_strat = len(_data.get("strategy_thresholds", {}))
        n_inst = len(_data.get("instrument_thresholds", {}))
        logger.info(f"threshold_tuner: loaded {n_strat} strategy + {n_inst} instrument thresholds")
    except Exception as e:
        logger.warning(f"threshold_tuner: failed to load {p}: {e}")
        _data = {}


def get_learned_quality_adjustment(strategy: str, instrument: str,
                                   session: str = "NY") -> int:
    """Return a quality-score adjustment for *strategy* on *instrument*/*session*.

    Positive → raise the bar (strategy is under-performing).
    Negative → lower the bar (strategy is out-performing).
    Zero     → no adjustment.
    """
    strats = _data.get("strategy_thresholds", {})

    for key in (f"{strategy}_{instrument}_{session}",
                f"{strategy}_{instrument}_",
                f"{strategy}__"):
        entry = strats.get(key)
        if entry:
            return int(entry.get("quality_adj", 0))

    return 0


def is_strategy_disabled(strategy: str) -> bool:
    """Return True if the learning system has disabled this strategy."""
    return strategy in _data.get("disabled_strategies", [])


def is_strategy_preferred(strategy: str) -> bool:
    """Return True if the learning system considers this strategy high-performing."""
    return strategy in _data.get("preferred_strategies", [])
