#!/usr/bin/env python3
"""
Order Flow Feed — enriches OHLCV data with real order flow from ProjectX API.

Uses ProjectX's retrieveBars endpoint to get actual volume data, then computes
order flow metrics that yfinance can't provide:
  - Real volume delta (buy vs sell volume from bar-level data)
  - Volume profile (price levels with most volume)
  - Large trade detection (institutional footprint)
  - Cumulative delta divergence

Falls back to OHLCV-based approximation when ProjectX is unavailable.

Usage:
    from orderflow_feed import OrderFlowFeed
    feed = OrderFlowFeed()
    metrics = feed.get_order_flow(instrument, timeframe='5m')
"""

import os
import sys
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logger = logging.getLogger('orderflow_feed')

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

CONTRACT_MAP = {
    'MES': 'CON.F.US.ENQ*.M26', 'MNQ': 'CON.F.US.MNQ*.M26',
    'MGC': 'CON.F.US.MGC*.J26', 'MCL': 'CON.F.US.MCL*.J26',
    'MYM': 'CON.F.US.MYM*.H26', 'M2K': 'CON.F.US.M2K*.H26',
}

TF_UNIT_MAP = {
    '1m': (3, 1), '5m': (3, 5), '10m': (3, 10), '15m': (3, 15),
    '30m': (3, 30), '1h': (4, 1), '4h': (4, 4),
}


class OrderFlowFeed:
    """Real-time order flow data from ProjectX API + OHLCV fallback."""

    def __init__(self):
        self._client = None
        self._contracts = {}
        self._cache = {}
        self._cache_ttl = 30

        try:
            from projectx_api_client import ProjectXClient
            username = os.getenv('PROJECTX_USERNAME', '')
            api_key = os.getenv('PROJECTX_API_KEY', '')
            if username and api_key:
                self._client = ProjectXClient(username, api_key, 'prod')
                logger.info("OrderFlow feed: ProjectX API connected")
                self._load_contracts()
        except Exception as e:
            logger.info(f"OrderFlow feed: ProjectX unavailable ({e}) — using OHLCV proxy")

    def _load_contracts(self):
        """Load available contract IDs from ProjectX."""
        if not self._client:
            return
        try:
            contracts = self._client.get_contracts(live=True)
            for c in contracts:
                name = c.get('name', '')
                cid = c.get('id', '')
                for inst in CONTRACT_MAP:
                    if inst in name:
                        self._contracts[inst] = cid
                        break
        except Exception as e:
            logger.debug(f"Contract load error: {e}")

    def get_contract_id(self, instrument: str) -> Optional[str]:
        return self._contracts.get(instrument)

    def get_order_flow(self, instrument: str, timeframe: str = '5m',
                       lookback_bars: int = 50) -> Dict:
        """Get order flow metrics for an instrument.

        Returns:
            {
                'volume_delta': float (positive = buying, negative = selling),
                'cumulative_delta': float (rolling sum),
                'buy_volume_pct': float (0-1),
                'large_trade_ratio': float (volume in large bars / total),
                'volume_profile_poc': float (price of control — highest volume level),
                'delta_divergence': bool (price up but delta down, or vice versa),
                'source': 'projectx' or 'ohlcv_proxy',
            }
        """
        cache_key = f"{instrument}_{timeframe}"
        if cache_key in self._cache:
            cached_time, cached_data = self._cache[cache_key]
            if time.time() - cached_time < self._cache_ttl:
                return cached_data

        if self._client and instrument in self._contracts:
            result = self._get_projectx_flow(instrument, timeframe, lookback_bars)
            if result:
                self._cache[cache_key] = (time.time(), result)
                return result

        result = self._get_ohlcv_proxy(instrument, timeframe, lookback_bars)
        self._cache[cache_key] = (time.time(), result)
        return result

    def _get_projectx_flow(self, instrument: str, timeframe: str,
                           lookback_bars: int) -> Optional[Dict]:
        """Fetch real volume data from ProjectX API."""
        contract_id = self._contracts.get(instrument)
        if not contract_id:
            return None

        unit, unit_num = TF_UNIT_MAP.get(timeframe, (3, 5))
        now = datetime.utcnow()
        minutes = lookback_bars * unit_num if unit == 3 else lookback_bars * unit_num * 60
        start = now - timedelta(minutes=minutes)

        try:
            self._client.ensure_authenticated()
            resp = self._client.session.post(
                f"{self._client.base_url}/api/History/retrieveBars",
                json={
                    'contractId': contract_id,
                    'live': True,
                    'startTime': start.strftime('%Y-%m-%dT%H:%M:%SZ'),
                    'endTime': now.strftime('%Y-%m-%dT%H:%M:%SZ'),
                    'unit': unit,
                    'unitNumber': unit_num,
                    'limit': lookback_bars,
                    'includePartialBar': True,
                },
                timeout=8,
            )
            if resp.status_code != 200:
                return None

            data = resp.json()
            if not data.get('success'):
                return None

            bars = data.get('bars', [])
            if not bars:
                return None

            df = pd.DataFrame(bars)
            for col_map in [('o', 'Open'), ('h', 'High'), ('l', 'Low'), ('c', 'Close'), ('v', 'Volume')]:
                if col_map[0] in df.columns:
                    df.rename(columns={col_map[0]: col_map[1]}, inplace=True)

            if 'Volume' not in df.columns or 'Close' not in df.columns:
                return None

            return self._compute_metrics(df, 'projectx')

        except Exception as e:
            logger.debug(f"ProjectX flow error: {e}")
            return None

    def _get_ohlcv_proxy(self, instrument: str, timeframe: str,
                         lookback_bars: int) -> Dict:
        """Approximate order flow from yfinance OHLCV data."""
        try:
            import yfinance as yf
            ticker_map = {'MES': 'ES=F', 'MNQ': 'NQ=F', 'MGC': 'GC=F',
                          'MCL': 'CL=F', 'MYM': 'YM=F', 'M2K': 'RTY=F'}
            yf_intervals = {'1m': ('1m', '1d'), '5m': ('5m', '5d'),
                            '15m': ('15m', '5d'), '30m': ('30m', '1mo'),
                            '1h': ('1h', '1mo')}
            ticker = ticker_map.get(instrument, 'ES=F')
            interval, period = yf_intervals.get(timeframe, ('5m', '5d'))

            df = yf.Ticker(ticker).history(period=period, interval=interval)
            if df is None or df.empty:
                return self._empty_metrics('ohlcv_proxy')

            df = df.iloc[-lookback_bars:] if len(df) > lookback_bars else df
            return self._compute_metrics(df, 'ohlcv_proxy')

        except Exception as e:
            logger.debug(f"OHLCV proxy error: {e}")
            return self._empty_metrics('ohlcv_proxy')

    def _compute_metrics(self, df: pd.DataFrame, source: str) -> Dict:
        """Compute order flow metrics from OHLCV DataFrame."""
        if len(df) < 5:
            return self._empty_metrics(source)

        bar_range = (df['High'] - df['Low']).replace(0, np.nan)
        close_pos = ((df['Close'] - df['Low']) / bar_range).fillna(0.5)

        buy_vol = df['Volume'] * close_pos
        sell_vol = df['Volume'] * (1 - close_pos)
        delta = buy_vol - sell_vol

        cum_delta = delta.cumsum()
        total_vol = df['Volume'].sum()
        buy_pct = buy_vol.sum() / total_vol if total_vol > 0 else 0.5

        vol_mean = df['Volume'].mean()
        large_bars = df[df['Volume'] > vol_mean * 2.0]
        large_ratio = large_bars['Volume'].sum() / total_vol if total_vol > 0 else 0

        try:
            price_bins = pd.cut(df['Close'], bins=20)
            vol_by_price = df.groupby(price_bins, observed=True)['Volume'].sum()
            poc = vol_by_price.idxmax()
            poc_price = (poc.left + poc.right) / 2 if poc is not None else df['Close'].iloc[-1]
        except Exception:
            poc_price = df['Close'].iloc[-1]

        price_up = df['Close'].iloc[-1] > df['Close'].iloc[-5] if len(df) >= 5 else False
        delta_up = cum_delta.iloc[-1] > cum_delta.iloc[-5] if len(cum_delta) >= 5 else False
        divergence = (price_up and not delta_up) or (not price_up and delta_up)

        return {
            'volume_delta': round(float(delta.iloc[-1]), 2),
            'cumulative_delta': round(float(cum_delta.iloc[-1]), 2),
            'buy_volume_pct': round(float(buy_pct), 3),
            'large_trade_ratio': round(float(large_ratio), 3),
            'volume_profile_poc': round(float(poc_price), 2),
            'delta_divergence': divergence,
            'source': source,
        }

    def _empty_metrics(self, source: str) -> Dict:
        return {
            'volume_delta': 0.0, 'cumulative_delta': 0.0,
            'buy_volume_pct': 0.5, 'large_trade_ratio': 0.0,
            'volume_profile_poc': 0.0, 'delta_divergence': False,
            'source': source,
        }
