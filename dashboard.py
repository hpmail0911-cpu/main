#!/usr/bin/env python3
"""
Trading Dashboard — Flask web UI on port 8080.

Shows:
  - Real-time open positions from ProjectX API
  - Daily PnL by strategy from strategy_learning.db
  - System status: scanners, validators, trade manager, learning agent
  - Risk limits from risk_config.json
  - Evolution engine state

Usage:
  python3 dashboard.py
  Open http://localhost:8080
"""

import os
import sys
import json
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Dict, List

from flask import Flask, jsonify, render_template_string

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
if os.path.exists(_ENV_PATH):
    with open(_ENV_PATH) as _f:
        for _ln in _f:
            _ln = _ln.strip()
            if not _ln or _ln.startswith('#') or '=' not in _ln:
                continue
            _k, _, _v = _ln.partition('=')
            _k = _k.strip(); _v = _v.strip().strip('"').strip("'")
            if _k and _k not in os.environ:
                os.environ[_k] = _v

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('dashboard')

app = Flask(__name__)

LEARNING_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'strategy_learning.db')
TRADE_MANAGER_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                'trade_manager_auto.db')
VALIDATOR_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'trading_performance.db')
RISK_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                'risk_config.json')
EVOLUTION_STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    'evolution_state.json')
THRESHOLDS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               'learned_thresholds.json')

_px_client = None


def _get_px_client():
    global _px_client
    if _px_client:
        return _px_client
    try:
        from projectx_api_client import ProjectXClient
        username = os.getenv('PROJECTX_USERNAME', '')
        api_key = os.getenv('PROJECTX_API_KEY', '')
        if username and api_key:
            _px_client = ProjectXClient(username, api_key, 'prod')
            return _px_client
    except Exception:
        pass
    return None


def _query_db(db_path: str, query: str, params: tuple = ()) -> list:
    if not os.path.exists(db_path):
        return []
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Trading Dashboard</title>
<meta http-equiv="refresh" content="30">
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #0a0a1a; color: #e0e0e0; padding: 20px; }
  h1 { color: #00d4aa; margin-bottom: 20px; font-size: 24px; }
  h2 { color: #00bfff; margin: 20px 0 10px; font-size: 18px; border-bottom: 1px solid #333; padding-bottom: 5px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 15px; margin-bottom: 20px; }
  .card { background: #1a1a2e; border-radius: 8px; padding: 15px; border: 1px solid #2a2a4a; }
  .card-title { font-size: 12px; color: #888; text-transform: uppercase; letter-spacing: 1px; }
  .card-value { font-size: 28px; font-weight: bold; margin: 5px 0; }
  .positive { color: #00d4aa; }
  .negative { color: #ff4757; }
  .neutral { color: #ffd700; }
  table { width: 100%; border-collapse: collapse; margin: 10px 0; }
  th { background: #1a1a2e; color: #00bfff; padding: 8px 12px; text-align: left; font-size: 12px;
       text-transform: uppercase; letter-spacing: 1px; }
  td { padding: 8px 12px; border-bottom: 1px solid #222; font-size: 14px; }
  tr:hover { background: #1a1a2e; }
  .badge { padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: bold; }
  .badge-win { background: #00d4aa33; color: #00d4aa; }
  .badge-loss { background: #ff475733; color: #ff4757; }
  .badge-active { background: #00bfff33; color: #00bfff; }
  .badge-disabled { background: #66666633; color: #888; }
  .timestamp { color: #666; font-size: 12px; margin-top: 20px; }
</style>
</head>
<body>
<h1>Trading Dashboard</h1>

<div class="grid">
  <div class="card">
    <div class="card-title">Daily P&L</div>
    <div class="card-value {{ 'positive' if daily_pnl >= 0 else 'negative' }}">${{ "%.2f"|format(daily_pnl) }}</div>
  </div>
  <div class="card">
    <div class="card-title">Today's Trades</div>
    <div class="card-value">{{ today_trades }}</div>
  </div>
  <div class="card">
    <div class="card-title">Win Rate (All Time)</div>
    <div class="card-value {{ 'positive' if win_rate >= 60 else 'negative' if win_rate < 50 else 'neutral' }}">{{ "%.1f"|format(win_rate) }}%</div>
  </div>
  <div class="card">
    <div class="card-title">Open Positions</div>
    <div class="card-value">{{ open_positions|length }}</div>
  </div>
</div>

{% if open_positions %}
<h2>Open Positions</h2>
<table>
<tr><th>ID</th><th>Instrument</th><th>Dir</th><th>Entry</th><th>Current Stop</th><th>BE</th><th>TP1</th></tr>
{% for p in open_positions %}
<tr>
  <td>{{ p.trade_id }}</td>
  <td>{{ p.instrument }}</td>
  <td><span class="badge {{ 'badge-win' if p.direction == 'LONG' else 'badge-loss' }}">{{ p.direction }}</span></td>
  <td>{{ "%.4f"|format(p.entry_price) }}</td>
  <td>{{ "%.4f"|format(p.current_stop) }}</td>
  <td>{{ "YES" if p.be_moved else "NO" }}</td>
  <td>{{ "CLOSED" if p.partial_closed else "PENDING" }}</td>
</tr>
{% endfor %}
</table>
{% endif %}

<h2>Strategy Performance</h2>
<table>
<tr><th>Strategy</th><th>Instrument</th><th>Session</th><th>Trades</th><th>Win Rate</th><th>Total PnL</th><th>Avg PnL</th></tr>
{% for s in strategies %}
<tr>
  <td>{{ s.strategy }}</td>
  <td>{{ s.instrument }}</td>
  <td>{{ s.session }}</td>
  <td>{{ s.trades }}</td>
  <td><span class="badge {{ 'badge-win' if s.win_rate >= 60 else 'badge-loss' if s.win_rate < 45 else '' }}">{{ "%.1f"|format(s.win_rate) }}%</span></td>
  <td class="{{ 'positive' if s.total_pnl >= 0 else 'negative' }}">${{ "%.2f"|format(s.total_pnl) }}</td>
  <td>${{ "%.2f"|format(s.avg_pnl) }}</td>
</tr>
{% endfor %}
</table>

{% if evolution %}
<h2>Evolution Engine</h2>
<div class="grid">
  <div class="card">
    <div class="card-title">Evolutions Run</div>
    <div class="card-value">{{ evolution.evolutions_run }}</div>
  </div>
  <div class="card">
    <div class="card-title">Min Confluence</div>
    <div class="card-value">{{ evolution.min_confluence }}</div>
  </div>
  <div class="card">
    <div class="card-title">Position Size Mode</div>
    <div class="card-value">{{ evolution.position_size_mode or 'LOCKED_1' }}</div>
  </div>
</div>
{% endif %}

<p class="timestamp">Last updated: {{ now }}</p>
</body>
</html>"""


@app.route('/')
def index():
    daily_pnl = 0.0
    today_trades = 0
    win_rate = 0.0

    rows = _query_db(LEARNING_DB,
                     "SELECT COUNT(*) as t, SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) as w, SUM(pnl) as p FROM strategy_performance")
    if rows and rows[0]['t']:
        win_rate = (rows[0]['w'] or 0) / rows[0]['t'] * 100
        daily_pnl = rows[0]['p'] or 0

    today = datetime.now().strftime('%Y-%m-%d')
    today_rows = _query_db(LEARNING_DB,
                           "SELECT COUNT(*) as t, SUM(pnl) as p FROM strategy_performance WHERE timestamp >= ?",
                           (today,))
    if today_rows and today_rows[0]['t']:
        today_trades = today_rows[0]['t']
        daily_pnl = today_rows[0]['p'] or 0

    strategies = _query_db(LEARNING_DB, """
        SELECT strategy, instrument, session,
               COUNT(*) as trades,
               ROUND(SUM(CASE WHEN outcome='WIN' THEN 1.0 ELSE 0 END) / COUNT(*) * 100, 1) as win_rate,
               ROUND(SUM(pnl), 2) as total_pnl,
               ROUND(AVG(pnl), 2) as avg_pnl
        FROM strategy_performance
        WHERE strategy != 'UNKNOWN'
        GROUP BY strategy, instrument, session
        ORDER BY total_pnl DESC
    """)

    open_positions = []
    tm_rows = _query_db(TRADE_MANAGER_DB, "SELECT * FROM managed_trades")
    for r in tm_rows:
        inst = 'UNKNOWN'
        cid = r.get('contract_id', '')
        for k in ['MNQ', 'MES', 'MGC', 'MCL', 'MYM', 'M2K']:
            if k in cid:
                inst = k
                break
        open_positions.append({
            'trade_id': r['trade_id'],
            'instrument': inst,
            'direction': r.get('direction', 'LONG'),
            'entry_price': r.get('entry_price', 0),
            'current_stop': r.get('current_stop', 0),
            'be_moved': bool(r.get('be_moved', 0)),
            'partial_closed': bool(r.get('partial_closed', 0)),
        })

    evolution = None
    if os.path.exists(EVOLUTION_STATE_PATH):
        try:
            with open(EVOLUTION_STATE_PATH) as f:
                evolution = json.load(f)
        except Exception:
            pass

    return render_template_string(
        DASHBOARD_HTML,
        daily_pnl=daily_pnl,
        today_trades=today_trades,
        win_rate=win_rate,
        open_positions=open_positions,
        strategies=strategies,
        evolution=evolution,
        now=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    )


@app.route('/api/positions')
def api_positions():
    client = _get_px_client()
    if not client:
        return jsonify({'error': 'ProjectX not configured'}), 503
    account_id = int(os.getenv('PROJECTX_ACCOUNT_ID') or '16129707')
    positions = client.get_open_positions(account_id)
    return jsonify({'positions': positions, 'count': len(positions)})


@app.route('/api/strategies')
def api_strategies():
    rows = _query_db(LEARNING_DB, """
        SELECT strategy, instrument, session,
               COUNT(*) as trades,
               SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) as wins,
               ROUND(SUM(pnl), 2) as total_pnl
        FROM strategy_performance WHERE strategy != 'UNKNOWN'
        GROUP BY strategy, instrument, session ORDER BY total_pnl DESC
    """)
    return jsonify({'strategies': rows})


@app.route('/api/daily_pnl')
def api_daily_pnl():
    rows = _query_db(LEARNING_DB, """
        SELECT DATE(timestamp) as date,
               COUNT(*) as trades,
               SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) as wins,
               ROUND(SUM(pnl), 2) as pnl
        FROM strategy_performance
        GROUP BY DATE(timestamp) ORDER BY date DESC LIMIT 30
    """)
    return jsonify({'daily': rows})


@app.route('/api/risk')
def api_risk():
    if os.path.exists(RISK_CONFIG_PATH):
        with open(RISK_CONFIG_PATH) as f:
            return jsonify(json.load(f))
    return jsonify({})


@app.route('/api/evolution')
def api_evolution():
    if os.path.exists(EVOLUTION_STATE_PATH):
        with open(EVOLUTION_STATE_PATH) as f:
            return jsonify(json.load(f))
    return jsonify({})


if __name__ == '__main__':
    logger.info("=" * 60)
    logger.info("  TRADING DASHBOARD — http://localhost:8080")
    logger.info("=" * 60)
    app.run(host='0.0.0.0', port=8080, debug=False)
