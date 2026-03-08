#!/usr/bin/env python3
"""
Trade Outcome Tracker — polls for closed positions and records real P&L.

Two data sources (tried in order):
  1. ProjectX API  — live trade history from broker
  2. Validator DB   — open_positions table with SL/TP prices + live price checks

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

logger = logging.getLogger('trade_outcome_tracker')

VALIDATOR_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'trading_performance.db')
LEARNING_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'strategy_learning.db')

PROJECTX_API_BASE = "https://api.projectx.com"

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
        self._projectx_token = None
        self._token_expires = datetime.min
        self._tracked_trades = set()
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

    # ── ProjectX API ─────────────────────────────────────────────────────────

    def _authenticate_projectx(self) -> bool:
        if not self.projectx_username or not self.projectx_api_key:
            return False
        if self._projectx_token and datetime.now() < self._token_expires:
            return True
        try:
            resp = requests.post(
                f"{PROJECTX_API_BASE}/api/v1/auth/login",
                json={
                    'username': self.projectx_username,
                    'apiKey': self.projectx_api_key,
                },
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                self._projectx_token = data.get('token', '')
                self._token_expires = datetime.now() + timedelta(hours=1)
                logger.info("ProjectX API authenticated")
                return True
        except Exception as e:
            logger.debug(f"ProjectX auth failed: {e}")
        return False

    def _get_projectx_headers(self) -> dict:
        return {'Authorization': f'Bearer {self._projectx_token}',
                'Content-Type': 'application/json'}

    def fetch_closed_trades_projectx(self, since_hours: int = 24) -> List[Dict]:
        """Fetch closed trades from ProjectX API."""
        if not self._authenticate_projectx():
            return []
        if not self.account_id:
            return []
        try:
            since = (datetime.now(timezone.utc) - timedelta(hours=since_hours)).isoformat()
            resp = requests.get(
                f"{PROJECTX_API_BASE}/api/v1/accounts/{self.account_id}/trades",
                params={'since': since, 'status': 'closed'},
                headers=self._get_projectx_headers(),
                timeout=15,
            )
            if resp.status_code != 200:
                logger.debug(f"ProjectX trades fetch failed: {resp.status_code}")
                return []
            data = resp.json()
            trades = data if isinstance(data, list) else data.get('trades', [])
            return trades
        except Exception as e:
            logger.debug(f"ProjectX trades error: {e}")
            return []

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

            contract = raw.get('contractName', raw.get('contract', ''))
            instrument = _get_instrument(contract)
            pnl = float(raw.get('pnl', raw.get('realizedPnl', 0)))
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
