"""
Data Feed — unified OHLCV retrieval layer.

Supports two backends:
  1. ProjectX API client (live futures data) — activated via init_feed(client)
  2. yfinance fallback (free delayed equity proxies)

Used by ai_chart_scanner.py:
    from data_feed import get_ohlcv, get_live_price, init_feed, TF_CONFIG
"""

import logging
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Timeframe → (yfinance_interval, yfinance_period)
# ---------------------------------------------------------------------------

TF_CONFIG = {
    "1m":  ("1m",  "7d"),
    "2m":  ("2m",  "7d"),
    "3m":  ("5m",  "7d"),
    "5m":  ("5m",  "5d"),
    "7m":  ("5m",  "5d"),
    "10m": ("15m", "5d"),
    "12m": ("15m", "5d"),
    "15m": ("15m", "5d"),
    "18m": ("15m", "5d"),
    "20m": ("30m", "1mo"),
    "23m": ("30m", "1mo"),
    "30m": ("30m", "1mo"),
    "45m": ("1h",  "1mo"),
    "1h":  ("1h",  "1mo"),
    "2h":  ("1h",  "3mo"),
    "3h":  ("1h",  "3mo"),
    "4h":  ("1h",  "3mo"),
    "1d":  ("1d",  "6mo"),
}

_INSTRUMENT_TO_YF = {
    "MNQ": "NQ=F",
    "MES": "ES=F",
    "MGC": "GC=F",
    "MCL": "CL=F",
    "MYM": "YM=F",
    "M2K": "RTY=F",
    "SPY": "SPY",
    "QQQ": "QQQ",
}

# ---------------------------------------------------------------------------
# Backend state
# ---------------------------------------------------------------------------

_projectx_client = None


def init_feed(client=None):
    """Initialize the data feed with an optional ProjectX API client.

    If *client* is None, the module falls back to yfinance.
    """
    global _projectx_client
    _projectx_client = client
    if client:
        logger.info("data_feed: ProjectX client attached — live data active")
    else:
        logger.info("data_feed: yfinance fallback active")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_ohlcv(instrument: str, timeframe: str = "5m") -> pd.DataFrame:
    """Return an OHLCV DataFrame for *instrument* at *timeframe*.

    Columns: Open, High, Low, Close, Volume (pandas DatetimeIndex).
    Raises on total failure so callers can handle gracefully.
    """
    inst = instrument.upper().replace("1!", "")
    for key in ("MNQ", "MES", "MGC", "MCL", "MYM", "M2K"):
        if key in inst:
            inst = key
            break

    if _projectx_client is not None:
        try:
            return _get_from_projectx(inst, timeframe)
        except Exception as e:
            logger.debug(f"ProjectX fetch failed for {inst}/{timeframe}: {e}")

    return _get_from_yfinance(inst, timeframe)


def get_live_price(instrument: str) -> Optional[float]:
    """Return the latest price for *instrument*, or None if unavailable."""
    if _projectx_client is not None:
        try:
            return _projectx_client.get_price(instrument)
        except Exception:
            pass

    try:
        df = get_ohlcv(instrument, "1m")
        if df is not None and len(df) > 0:
            return float(df["Close"].iloc[-1])
    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------

def _get_from_projectx(instrument: str, timeframe: str) -> pd.DataFrame:
    """Fetch OHLCV from ProjectX client (must support .get_bars())."""
    bars = _projectx_client.get_bars(instrument, timeframe)
    df = pd.DataFrame(bars)
    df.columns = [c.capitalize() for c in df.columns]
    for col in ("Open", "High", "Low", "Close", "Volume"):
        if col not in df.columns:
            df[col] = 0
    return df


def _get_from_yfinance(instrument: str, timeframe: str) -> pd.DataFrame:
    """Fetch OHLCV from yfinance (free delayed data)."""
    import yfinance as yf

    ticker_symbol = _INSTRUMENT_TO_YF.get(instrument, instrument)
    yf_interval, period = TF_CONFIG.get(timeframe, ("5m", "5d"))

    ticker = yf.Ticker(ticker_symbol)
    df = ticker.history(period=period, interval=yf_interval)

    if df is None or df.empty:
        raise ValueError(f"No yfinance data for {ticker_symbol} {yf_interval}/{period}")

    return df
