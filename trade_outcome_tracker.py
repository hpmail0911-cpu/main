#!/usr/bin/env python3
"""
Trade Outcome Tracker — polls for closed positions and records real P&L.

Three data sources (tried in order):
  1. Trade Manager DB — trade_manager_auto.db tracks every managed position
     with entry price, direction, stops, and close events (MAE, BE, trailing)
  2. ProjectX API     — live trade history from TopStepX broker via real API
  3. Validator DB     — open_positions table with SL/TP prices + live price checks

Writes outcomes to:
  - validator's trading_performance.db (updates todays_trades.result & pnl)
  - strategy_learning.db (via RealtimeStrategyLearner)
  - rag_signals.db (via setup_rag_database.update_signal_outcome)

This module is the critical missing link between "trade was approved" and
"what actually happened", closing the feedback loop for the learning agent.
"""

import os
import sys
import time
import sqlite3
import logging
import requests
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from realtime_strategy_learner import RealtimeStrategyLearner

try:
    from setup_rag_database import init_rag_database, update_signal_outcome, log_signal_to_rag
    RAG_AVAILABLE = True
except ImportError:
    RAG_AVAILABLE = False

try:
    from projectx_api_client import ProjectXClient
    PROJECTX_CLIENT_AVAILABLE = True
except ImportError:
    PROJECTX_CLIENT_AVAILABLE = False

logger = logging.getLogger('trade_outcome_tracker')

VALIDATOR_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'trading_performance.db')
LEARNING_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'strategy_learning.db')
TRADE_MANAGER_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                'trade_manager_auto.db')

TICK_VALUES = {
    'MES': 1.25, 'MNQ': 0.50, 'MGC': 0.10, 'MCL': 0.01,
    'MYM': 1.00, 'M2K': 0.10,
}
POINT_VALUES = {
    'MES': 5.0, 'MNQ': 2.0, 'MGC': 10.0, 'MCL': 1000.0,
    'MYM': 0.50, 'M2K': 5.0,
}


def _get_instrument(symbol: str) -> str:
    if not symbol:
        return 'UNKNOWN'
    s = symbol.upper().replace('/', '').strip()
    for key in ('MNQ', 'MES', 'MGC', 'MCL', 'MYM', 'M2K'):
        if key in s:
            return key
    return s[:3]


def _current_session() -> str:
    et = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-5)))
    h = et.hour
    if 9 <= h < 16:
        return 'NY'
    elif 3 <= h < 9:
        return 'London'
    elif 18 <= h or h < 3:
        return 'Asia'
    return 'UAE'


class TradeOutcomeTracker:
    """Tracks trade outcomes and feeds results back to the learning system."""

    def __init__(self, projectx_username: str = '', projectx_api_key: str = '',
                 account_id: str = ''):
        self.projectx_username = projectx_username or os.getenv('PROJECTX_USERNAME', '')
        self.projectx_api_key = projectx_api_key or os.getenv('PROJECTX_API_KEY', '')
        self.account_id = account_id or os.getenv('PROJECTX_ACCOUNT_ID', '')
        self.learner = RealtimeStrategyLearner(LEARNING_DB)
        self._projectx_client = None
        self._tracked_trades = set()
        self._last_tm_mod_count = 0
        self._load_tracked_trades()

        if RAG_AVAILABLE:
            init_rag_database()

    def _load_tracked_trades(self):
        """Load IDs of trades we've already processed."""
        try:
            conn = sqlite3.connect(LEARNING_DB)
            c = conn.cursor()
            c.execute("SELECT trade_id FROM strategy_performance")
            self._tracked_trades = {row[0] for row in c.fetchall() if row[0]}
            conn.close()
        except Exception:
            pass
        logger.info(f"Loaded {len(self._tracked_trades)} previously tracked trades")

    # ── ProjectX API (real client) ───────────────────────────────────────────

    def _get_projectx_client(self) -> Optional['ProjectXClient']:
        if self._projectx_client:
            return self._projectx_client
        if not PROJECTX_CLIENT_AVAILABLE:
            return None
        if not self.projectx_username or not self.projectx_api_key:
            return None
        try:
            self._projectx_client = ProjectXClient(
                self.projectx_username, self.projectx_api_key, 'prod'
            )
            logger.info("ProjectX client initialized for outcome tracking")
            return self._projectx_client
        except Exception as e:
            logger.debug(f"ProjectX client init failed: {e}")
            return None

    def fetch_closed_trades_projectx(self, since_hours: int = 24) -> List[Dict]:
        """Fetch closed trades from ProjectX/TopStepX API.

        Only returns fills with a real (non-zero) profitAndLoss value.
        Fills with pnl=0 or pnl=None are open or empty — skip them.
        """
        client = self._get_projectx_client()
        if not client or not self.account_id:
            return []
        try:
            all_fills = client.get_positions(int(self.account_id))
            closed = [f for f in all_fills
                      if f.get('profitAndLoss') is not None
                      and float(f.get('profitAndLoss', 0)) != 0.0
                      and str(f.get('id', '')) not in self._tracked_trades]
            return closed
        except Exception as e:
            logger.debug(f"ProjectX trades fetch error: {e}")
            return []

    # ── Trade Manager DB (primary outcome source) ────────────────────────────

    def fetch_outcomes_from_trade_manager(self) -> List[Dict]:
        """Read closed trade events from Trade Manager's modifications log.

        The Trade Manager logs every POSITION_CLOSED event with the action
        reason (MAE, trailing stop, manual). We match these back to the
        managed_trades table for entry price and direction.
        """
        if not os.path.exists(TRADE_MANAGER_DB):
            return []

        closed = []
        try:
            conn = sqlite3.connect(TRADE_MANAGER_DB)
            c = conn.cursor()
            c.execute("""SELECT m.id, m.timestamp, m.trade_id, m.action,
                                m.old_stop, m.new_stop, m.success
                         FROM modifications m
                         WHERE m.action = 'POSITION_CLOSED'
                           AND m.success = 1
                         ORDER BY m.id DESC
                         LIMIT 100""")
            close_events = c.fetchall()

            for event in close_events:
                mod_id, ts, trade_id, action, old_stop, new_stop, success = event
                trade_key = f"tm_{trade_id}"
                if trade_key in self._tracked_trades:
                    continue

                c.execute("""SELECT trade_id, contract_id, direction,
                                    entry_price, original_stop, current_stop,
                                    first_seen
                             FROM managed_trades WHERE trade_id = ?""",
                          (trade_id,))
                managed = c.fetchone()

                if not managed:
                    c.execute("""SELECT DISTINCT trade_id FROM modifications
                                 WHERE trade_id = ?""", (trade_id,))
                    if not c.fetchone():
                        continue

                inst = 'UNKNOWN'
                entry_price = 0.0
                direction = 'LONG'
                entry_time = ts or ''

                if managed:
                    _, contract_id, direction, entry_price, orig_stop, cur_stop, first_seen = managed
                    inst = _get_instrument(contract_id)
                    entry_time = first_seen or ts

                if inst == 'UNKNOWN' or not entry_price:
                    continue

                closed.append({
                    'trade_id': trade_key,
                    'instrument': inst,
                    'direction': direction or 'LONG',
                    'entry_price': float(entry_price or 0),
                    'exit_price': 0.0,
                    'pnl': 0.0,
                    'entry_time': entry_time,
                    'exit_time': ts,
                    'strategy': 'UNKNOWN',
                    'quality_score': 0,
                    'outcome': 'CLOSED',
                })

            new_mod_count = len(close_events)

            c.execute("""SELECT m.trade_id, m.action, m.old_stop, m.new_stop,
                                m.timestamp
                         FROM modifications m
                         WHERE m.action IN ('STOP_MOVED')
                           AND m.success = 1
                         ORDER BY m.id DESC LIMIT 50""")

            conn.close()
        except Exception as e:
            logger.error(f"Trade Manager DB read error: {e}")

        return closed

    # ── Validator DB position checking ────────────────────────────────────────

    def check_position_outcomes_from_db(self) -> List[Dict]:
        """Check open positions against current prices to detect SL/TP hits."""
        if not os.path.exists(VALIDATOR_DB):
            return []

        closed = []
        try:
            conn = sqlite3.connect(VALIDATOR_DB)
            c = conn.cursor()
            c.execute("""SELECT id, strategy, symbol, action, quantity,
                                entry_time, entry_price, stop_loss, take_profit
                         FROM open_positions""")
            positions = c.fetchall()
            conn.close()

            if not positions:
                return []

            try:
                from data_feed import get_live_price
            except ImportError:
                return []

            for pos in positions:
                pos_id, strategy, symbol, action, qty, entry_time, entry_px, sl, tp = pos
                inst = _get_instrument(symbol)
                price = get_live_price(inst)
                if price is None:
                    continue

                outcome = self._check_sl_tp(action, float(entry_px), float(sl), float(tp), price)
                if outcome:
                    pnl = self._calculate_pnl(action, float(entry_px), price, inst, int(qty or 1))
                    closed.append({
                        'position_id': pos_id,
                        'trade_id': f"pos_{pos_id}",
                        'strategy': strategy or 'UNKNOWN',
                        'instrument': inst,
                        'direction': action,
                        'entry_price': float(entry_px),
                        'exit_price': price,
                        'stop_loss': float(sl),
                        'take_profit': float(tp),
                        'pnl': pnl,
                        'outcome': outcome,
                        'entry_time': entry_time,
                        'exit_time': datetime.now().isoformat(),
                        'quantity': int(qty or 1),
                    })

        except Exception as e:
            logger.error(f"Position outcome check error: {e}")

        return closed

    def _check_sl_tp(self, action: str, entry: float, sl: float, tp: float,
                     current: float) -> Optional[str]:
        action_upper = action.upper()
        if action_upper in ('LONG', 'BUY'):
            if current <= sl:
                return 'SL_HIT'
            if current >= tp:
                return 'TP_HIT'
        elif action_upper in ('SHORT', 'SELL'):
            if current >= sl:
                return 'SL_HIT'
            if current <= tp:
                return 'TP_HIT'
        return None

    def _calculate_pnl(self, action: str, entry: float, exit_price: float,
                       instrument: str, quantity: int) -> float:
        point_val = POINT_VALUES.get(instrument, 1.0)
        if action.upper() in ('LONG', 'BUY'):
            return (exit_price - entry) * point_val * quantity
        else:
            return (entry - exit_price) * point_val * quantity

    # ── Process outcomes ─────────────────────────────────────────────────────

    def process_closed_trade(self, trade: Dict):
        """Record a closed trade into all learning databases."""
        trade_id = str(trade.get('trade_id', ''))
        if not trade_id or trade_id in self._tracked_trades:
            return

        instrument = trade.get('instrument', _get_instrument(trade.get('symbol', '')))
        if instrument == 'UNKNOWN' or not instrument:
            self._tracked_trades.add(trade_id)
            return

        pnl = float(trade.get('pnl', 0))
        outcome = trade.get('outcome', 'WIN' if pnl > 0 else 'LOSS')
        strategy = trade.get('strategy', 'UNKNOWN')
        direction = trade.get('direction', trade.get('action', 'Long'))
        entry_time = trade.get('entry_time', '')

        entry_hour = 12
        try:
            if entry_time:
                dt = datetime.fromisoformat(entry_time.replace('Z', '+00:00'))
                entry_hour = dt.hour
        except Exception:
            pass

        trade_record = {
            'trade_id': trade_id,
            'instrument': instrument,
            'direction': direction,
            'entry_price': float(trade.get('entry_price', 0)),
            'exit_price': float(trade.get('exit_price', 0)),
            'pnl': pnl,
            'entry_hour': entry_hour,
            'hold_time_minutes': float(trade.get('hold_time_minutes', 0)),
            'strategy': strategy,
            'quality_score': int(trade.get('quality_score', 0)),
            'adx': float(trade.get('adx', 0)),
            'confidence': float(trade.get('confidence', 0)),
        }

        self.learner.record_trade(trade_record)
        self._tracked_trades.add(trade_id)

        self._update_validator_db(trade_id, outcome, pnl)

        if outcome in ('WIN', 'TP_HIT'):
            win_loss = 'WIN'
        else:
            win_loss = 'LOSS'
        logger.info(f"  {win_loss} {instrument} {strategy} {direction} "
                    f"PnL=${pnl:+.2f} ({outcome})")

        try:
            from enhanced_signal_filter import EnhancedSignalFilter
            EnhancedSignalFilter.record_instrument_pnl(instrument, pnl)
        except Exception:
            pass

        if pnl < 0:
            try:
                from signal_gate import record_loss
                record_loss(instrument)
            except Exception:
                pass

    def _update_validator_db(self, trade_id: str, outcome: str, pnl: float):
        """Update the validator's todays_trades with the actual result."""
        if not os.path.exists(VALIDATOR_DB):
            return
        try:
            result = 'win' if pnl > 0 else 'loss'
            conn = sqlite3.connect(VALIDATOR_DB)
            c = conn.cursor()
            if trade_id.startswith('pos_'):
                pos_id = int(trade_id.replace('pos_', ''))
                c.execute("DELETE FROM open_positions WHERE id = ?", (pos_id,))
            today = datetime.now().strftime('%Y-%m-%d')
            if pnl > 0:
                c.execute("UPDATE daily_performance SET wins = wins + 1, total_pnl = total_pnl + ? WHERE date = ?",
                          (pnl, today))
            else:
                c.execute("UPDATE daily_performance SET losses = losses + 1, total_pnl = total_pnl + ? WHERE date = ?",
                          (pnl, today))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.debug(f"Validator DB update error: {e}")

    def process_projectx_trades(self, raw_trades: List[Dict]) -> int:
        """Convert raw ProjectX API trades into our format and process them."""
        count = 0
        for raw in raw_trades:
            trade_id = str(raw.get('id', raw.get('tradeId', '')))
            if not trade_id or trade_id in self._tracked_trades:
                continue

            contract = raw.get('contractId', raw.get('contractName', raw.get('contract', '')))
            instrument = _get_instrument(contract)
            if instrument == 'UNKNOWN':
                self._tracked_trades.add(trade_id)
                continue

            raw_pnl = raw.get('profitAndLoss', raw.get('pnl', raw.get('realizedPnl', 0)))
            pnl = float(raw_pnl) if raw_pnl is not None else 0.0
            direction = raw.get('type', raw.get('side', 'Long'))

            entry_time = raw.get('enteredAt', raw.get('openTime', ''))
            exit_time = raw.get('exitedAt', raw.get('closeTime', ''))
            hold_minutes = 0
            try:
                duration = raw.get('tradeDuration', '')
                if duration:
                    parts = duration.split(':')
                    hold_minutes = int(parts[0]) * 60 + int(parts[1]) + float(parts[2].split('.')[0]) / 60
            except Exception:
                pass

            trade = {
                'trade_id': trade_id,
                'instrument': instrument,
                'direction': direction,
                'entry_price': float(raw.get('entryPrice', raw.get('openPrice', 0))),
                'exit_price': float(raw.get('exitPrice', raw.get('closePrice', 0))),
                'pnl': pnl,
                'entry_time': entry_time,
                'exit_time': exit_time,
                'hold_time_minutes': hold_minutes,
                'strategy': raw.get('strategy', 'UNKNOWN'),
                'quality_score': int(raw.get('quality', 0)),
                'adx': 0,
                'confidence': 0,
            }
            self.process_closed_trade(trade)
            count += 1
        return count

    def poll_once(self) -> int:
        """Run one polling cycle. Returns number of new closed trades found."""
        total = 0

        tm_closed = self.fetch_outcomes_from_trade_manager()
        for trade in tm_closed:
            self.process_closed_trade(trade)
            total += 1
        if tm_closed:
            logger.info(f"  Trade Manager: {len(tm_closed)} closed trade(s)")

        projectx_trades = self.fetch_closed_trades_projectx()
        if projectx_trades:
            count = self.process_projectx_trades(projectx_trades)
            if count:
                logger.info(f"  ProjectX: {count} new closed trades")
                total += count

        db_closed = self.check_position_outcomes_from_db()
        for trade in db_closed:
            self.process_closed_trade(trade)
            total += 1
        if db_closed:
            logger.info(f"  Position check: {len(db_closed)} SL/TP hit(s)")

        return total

    def get_summary(self) -> Dict:
        """Return a summary of all tracked outcomes."""
        try:
            conn = sqlite3.connect(LEARNING_DB)
            c = conn.cursor()
            c.execute("""SELECT COUNT(*), 
                                SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END),
                                SUM(pnl)
                         FROM strategy_performance""")
            row = c.fetchone()
            conn.close()
            if row and row[0]:
                trades, wins, total_pnl = row
                return {
                    'total_trades': trades,
                    'wins': wins,
                    'losses': trades - wins,
                    'win_rate': wins / trades if trades > 0 else 0,
                    'total_pnl': total_pnl or 0,
                }
        except Exception:
            pass
        return {'total_trades': 0, 'wins': 0, 'losses': 0, 'win_rate': 0, 'total_pnl': 0}
