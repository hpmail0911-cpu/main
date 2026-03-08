#!/usr/bin/env python3
"""
Data Feed — unified OHLCV data interface for the trading system.

Supports two backends:
  1. ProjectX API client (live futures data) — used when init_feed(client) is called
  2. yfinance fallback (free, delayed) — used by default

API:
  init_feed(client)                  — initialize with a ProjectX client
  get_ohlcv(instrument, tf='5m')     — return DataFrame with OHLCV data
  get_live_price(instrument)         — return latest price or None
"""

import logging
from typing import Optional
from datetime import datetime

logger = logging.getLogger(__name__)

try:
    import pandas as pd
except ImportError:
    pd = None

try:
    import yfinance as yf
    _YF_AVAILABLE = True
except ImportError:
    yf = None
    _YF_AVAILABLE = False

_projectx_client = None

TICKER_MAP = {
    'MES': 'ES=F',
    'MNQ': 'NQ=F',
    'MGC': 'GC=F',
    'MCL': 'CL=F',
    'MYM': 'YM=F',
    'M2K': 'RTY=F',
    'SPY': 'SPY',
    'QQQ': 'QQQ',
    '^VIX': '^VIX',
}

TF_CONFIG = {
    '1m':  ('1m',  '1d'),
    '3m':  ('2m',  '5d'),
    '5m':  ('5m',  '5d'),
    '7m':  ('5m',  '5d'),
    '10m': ('15m', '5d'),
    '12m': ('15m', '5d'),
    '15m': ('15m', '5d'),
    '18m': ('15m', '5d'),
    '20m': ('30m', '1mo'),
    '23m': ('30m', '1mo'),
    '30m': ('30m', '1mo'),
    '45m': ('1h',  '1mo'),
    '1h':  ('1h',  '1mo'),
    '2h':  ('1h',  '1mo'),
    '3h':  ('1h',  '3mo'),
    '4h':  ('1h',  '3mo'),
    '1d':  ('1d',  '6mo'),
}


def init_feed(client=None):
    """Initialize the data feed with an optional ProjectX client."""
    global _projectx_client
    if client is not None:
        _projectx_client = client
        logger.info("Data feed initialized with ProjectX client")
    else:
        logger.info("Data feed initialized with yfinance fallback")


def get_ohlcv(instrument: str, tf: str = '5m') -> 'Optional[pd.DataFrame]':
    """Fetch OHLCV data for an instrument at the given timeframe.

    Returns a pandas DataFrame with columns: Open, High, Low, Close, Volume.
    """
    if _projectx_client is not None:
        return _get_projectx_data(instrument, tf)

    return _get_yfinance_data(instrument, tf)


def get_live_price(instrument: str) -> Optional[float]:
    """Return the most recent price for an instrument, or None."""
    if _projectx_client is not None:
        try:
            return _projectx_client.get_price(instrument)
        except Exception:
            pass

    try:
        df = get_ohlcv(instrument, '1m')
        if df is not None and len(df) > 0:
            return float(df['Close'].iloc[-1])
    except Exception:
        pass

    return None


def _get_yfinance_data(instrument: str, tf: str) -> 'Optional[pd.DataFrame]':
    """Fetch data via yfinance."""
    if not _YF_AVAILABLE:
        logger.warning("yfinance not installed — cannot fetch data")
        return None

    ticker = TICKER_MAP.get(instrument, instrument)
    yf_interval, period = TF_CONFIG.get(tf, ('5m', '5d'))

    try:
        data = yf.Ticker(ticker).history(period=period, interval=yf_interval)
        if data is None or data.empty:
            return None
        return data
    except Exception as e:
        logger.debug(f"yfinance fetch failed for {ticker} ({tf}): {e}")
        return None


def _get_projectx_data(instrument: str, tf: str) -> 'Optional[pd.DataFrame]':
    """Fetch data via ProjectX API client."""
    try:
        data = _projectx_client.get_bars(instrument, tf)
        if data is None:
            return _get_yfinance_data(instrument, tf)
        return data
    except Exception as e:
        logger.debug(f"ProjectX fetch failed for {instrument} ({tf}): {e}")
        return _get_yfinance_data(instrument, tf)
