#!/usr/bin/env python3
"""
AI LEARNING AGENT — Unified orchestrator for the self-improving trading system.

Runs a continuous loop that:
  1. Polls for closed trade outcomes (ProjectX API + SL/TP position checks)
  2. Records outcomes into the learning database
  3. Recomputes adaptive parameters (quality gates, SL/TP multipliers, sizing)
  4. Writes learned_thresholds.json (consumed by scanner + threshold_tuner)
  5. Updates RAG context (pattern + strategy history for AI prompts)
  6. Generates performance reports

The scanner and validator read learned_thresholds.json at each cycle,
closing the feedback loop: trades → outcomes → learning → better trades.

Usage:
  python3 learning_agent.py               # continuous monitoring
  python3 learning_agent.py --once        # single analysis pass
  python3 learning_agent.py --report      # generate report only
  python3 learning_agent.py --status      # show current learning state
"""

import os
import sys
import time
import json
import logging
import signal
import sqlite3
from datetime import datetime, timedelta
from typing import Dict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from trade_outcome_tracker import TradeOutcomeTracker
from realtime_strategy_learner import RealtimeStrategyLearner
from adaptive_params import write_learned_thresholds, compute_sl_tp_adjustments
from rag_updater import update_all as update_rag_context
from learning_monitor_fixed import LearningMonitor
from evolution_engine import run_evolution

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger('learning_agent')

_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
if os.path.exists(_ENV_PATH):
    with open(_ENV_PATH) as _f:
        for _ln in _f:
            _ln = _ln.strip()
            if not _ln or _ln.startswith('#') or '=' not in _ln:
                continue
            _k, _, _v = _ln.partition('=')
            _k = _k.strip()
            _v = _v.strip().strip('"').strip("'")
            if _k and _k not in os.environ:
                os.environ[_k] = _v

LEARNING_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'strategy_learning.db')
THRESHOLDS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               'learned_thresholds.json')

POLL_INTERVAL_SECONDS = 60
FULL_RECOMPUTE_INTERVAL = 300
REPORT_INTERVAL = 900
EVOLUTION_INTERVAL = 14400  # 4 hours


class LearningAgent:
    """
    Orchestrates the full learning feedback loop:

    Scanner (signals) → Validator (approves) → Broker (executes)
        ↑                                            ↓
    threshold_tuner ← learned_thresholds.json ← LearningAgent
        ↑                                            ↓
    RAG DB ← rag_updater ←────── trade outcomes ←────┘
    """

    def __init__(self):
        self.tracker = TradeOutcomeTracker()
        self.learner = RealtimeStrategyLearner(LEARNING_DB)
        self.csv_monitor = LearningMonitor()

        self._running = False
        self._cycle = 0
        self._last_recompute = datetime.min
        self._last_report = datetime.min
        self._last_evolution = datetime.min
        self._total_outcomes = 0

    def run_once(self):
        """Single pass: poll outcomes → learn → update thresholds → update RAG."""
        logger.info("=" * 70)
        logger.info("  LEARNING AGENT — SINGLE PASS")
        logger.info("=" * 70)

        outcomes = self._poll_outcomes()
        self._process_csv_trades()
        self._recompute_all()
        self._generate_report()
        self._print_status()

        return outcomes

    def run_report(self):
        """Generate and print a performance report only."""
        self.learner.generate_report()
        self._print_adaptive_summary()

    def run_status(self):
        """Print current learning state."""
        self._print_status()

    def run_loop(self):
        """Continuous monitoring loop."""
        self._running = True
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

        logger.info("=" * 70)
        logger.info("  AI LEARNING AGENT — STARTED")
        logger.info("  Scanners: GROQ_V5 (:8765) | ULTIMATE_60 (:5002) | Scalping (:5002)")
        logger.info("  Trade Manager: trade_manager_AUTOMATED.py (primary outcome source)")
        logger.info("  Poll interval: %ds | Recompute: %ds | Report: %ds",
                     POLL_INTERVAL_SECONDS, FULL_RECOMPUTE_INTERVAL, REPORT_INTERVAL)
        logger.info("  ProjectX: %s | Account: %s",
                     'connected' if self.tracker.projectx_username else 'disabled',
                     self.tracker.account_id or 'N/A')
        logger.info("  Learning DB: %s", LEARNING_DB)
        logger.info("  Thresholds:  %s", THRESHOLDS_PATH)
        logger.info("=" * 70)

        self._recompute_all()

        while self._running:
            try:
                self._cycle += 1
                now = datetime.now()

                outcomes = self._poll_outcomes()

                if outcomes > 0 or (now - self._last_recompute).total_seconds() > FULL_RECOMPUTE_INTERVAL:
                    self._recompute_all()
                    self._last_recompute = now

                if (now - self._last_report).total_seconds() > REPORT_INTERVAL:
                    self._generate_report()
                    self._last_report = now

                if (now - self._last_evolution).total_seconds() > EVOLUTION_INTERVAL:
                    self._run_evolution()
                    self._last_evolution = now

                if outcomes > 0:
                    logger.info(f"Cycle {self._cycle}: {outcomes} new outcome(s) "
                                f"| Total tracked: {self._total_outcomes}")
                elif self._cycle % 10 == 0:
                    logger.info(f"Cycle {self._cycle}: monitoring... "
                                f"({self._total_outcomes} total tracked)")

                time.sleep(POLL_INTERVAL_SECONDS)

            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error(f"Cycle error: {e}", exc_info=True)
                time.sleep(POLL_INTERVAL_SECONDS)

        self._shutdown()

    # ── Internal methods ─────────────────────────────────────────────────────

    def _poll_outcomes(self) -> int:
        """Poll for new trade outcomes from all sources."""
        try:
            count = self.tracker.poll_once()
            self._total_outcomes += count
            return count
        except Exception as e:
            logger.error(f"Outcome poll error: {e}")
            return 0

    def _process_csv_trades(self):
        """Process any new trades from the CSV export (backup data source)."""
        try:
            self.csv_monitor.load_validator_signals()
            new = self.csv_monitor.process_new_trades()
            if new > 0:
                logger.info(f"CSV monitor: {new} new trades processed")
        except Exception as e:
            logger.debug(f"CSV processing: {e}")

    def _recompute_all(self):
        """Recompute all adaptive parameters, thresholds, and RAG context."""
        try:
            thresholds = write_learned_thresholds()
            n_strat = len(thresholds.get('strategy_thresholds', {}))
            n_dis = len(thresholds.get('disabled_strategies', []))
            n_pref = len(thresholds.get('preferred_strategies', []))

            if n_strat > 0:
                logger.info(f"  Thresholds: {n_strat} strategies "
                            f"({n_dis} disabled, {n_pref} preferred)")
        except Exception as e:
            logger.error(f"Threshold computation error: {e}")

        try:
            rag_count = update_rag_context()
            if rag_count > 0:
                logger.info(f"  RAG context: {rag_count} entries updated")
        except Exception as e:
            logger.error(f"RAG update error: {e}")

    def _run_evolution(self):
        """Run the autonomous evolution engine."""
        try:
            state = run_evolution()
            n = state.get('evolutions_run', 0)
            logger.info(f"  Evolution #{n} complete")
        except Exception as e:
            logger.error(f"Evolution error: {e}")

    def _generate_report(self):
        """Generate and log a performance report."""
        try:
            self.learner.generate_report()
        except Exception as e:
            logger.debug(f"Report error: {e}")

    def _print_status(self):
        """Print current learning agent status."""
        summary = self.tracker.get_summary()
        logger.info("")
        logger.info("=" * 50)
        logger.info("  LEARNING AGENT STATUS")
        logger.info("=" * 50)
        logger.info(f"  Total tracked trades: {summary['total_trades']}")
        logger.info(f"  Wins: {summary['wins']} | Losses: {summary['losses']}")
        if summary['total_trades'] > 0:
            logger.info(f"  Win rate: {summary['win_rate']:.1%}")
            logger.info(f"  Total P&L: ${summary['total_pnl']:+.2f}")

        if os.path.exists(THRESHOLDS_PATH):
            try:
                with open(THRESHOLDS_PATH) as f:
                    th = json.load(f)
                logger.info(f"  Strategy thresholds: {len(th.get('strategy_thresholds', {}))}")
                logger.info(f"  Disabled strategies: {th.get('disabled_strategies', [])}")
                logger.info(f"  Preferred strategies: {th.get('preferred_strategies', [])}")
                logger.info(f"  SL/TP adjustments: {len(th.get('sl_tp_adjustments', {}))}")
                logger.info(f"  Last updated: {th.get('updated_at', 'never')}")
            except Exception:
                pass
        else:
            logger.info("  No learned_thresholds.json yet (waiting for trade data)")
        logger.info("=" * 50)

    def _print_adaptive_summary(self):
        """Print adaptive parameter summary."""
        if not os.path.exists(THRESHOLDS_PATH):
            logger.info("No adaptive parameters yet.")
            return

        with open(THRESHOLDS_PATH) as f:
            data = json.load(f)

        sl_tp = data.get('sl_tp_adjustments', {})
        if sl_tp:
            logger.info("\n  SL/TP ADAPTIVE ADJUSTMENTS:")
            logger.info(f"  {'Instrument':<12} {'Dir':<8} {'SL Mult':>8} {'TP Mult':>8} "
                        f"{'Size Mult':>10} {'WR':>8} {'R:R':>6} {'Trades':>7}")
            logger.info("  " + "-" * 70)
            for key, adj in sorted(sl_tp.items()):
                inst, direction = key.split('_', 1)
                logger.info(f"  {inst:<12} {direction:<8} "
                            f"{adj['sl_multiplier']:>8.2f} {adj['tp_multiplier']:>8.2f} "
                            f"{adj['size_multiplier']:>10.2f} "
                            f"{adj['win_rate']:>7.1%} {adj['realized_rr']:>5.1f} "
                            f"{adj['trades']:>7}")

        disabled = data.get('disabled_strategies', [])
        preferred = data.get('preferred_strategies', [])
        if disabled:
            logger.info(f"\n  DISABLED strategies (WR < {30}%): {', '.join(disabled)}")
        if preferred:
            logger.info(f"  PREFERRED strategies (WR >= {70}%): {', '.join(preferred)}")

    def _handle_shutdown(self, signum, frame):
        logger.info("\nShutdown signal received...")
        self._running = False

    def _shutdown(self):
        logger.info("Performing final computation before shutdown...")
        self._recompute_all()
        self._generate_report()
        self._print_status()
        logger.info("Learning agent stopped.")


def main():
    agent = LearningAgent()

    if '--once' in sys.argv:
        agent.run_once()
    elif '--report' in sys.argv:
        agent.run_report()
    elif '--status' in sys.argv:
        agent.run_status()
    else:
        agent.run_loop()


if __name__ == '__main__':
    main()
