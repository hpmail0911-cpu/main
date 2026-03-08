#!/usr/bin/env python3
"""
TOPSTEPX API CLIENT
Complete integration for market data and position monitoring

Features:
- Real-time market data (OHLCV bars)
- Position monitoring
- Order status
- Account information
- Historical data

Documentation:
TopstepX API docs: https://docs.topstepx.com/api
"""

import requests
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import logging
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TopStepXClient:
    """
    TopstepX API Client
    
    Usage:
        client = TopStepXClient(api_key='your-key-here')
        data = client.get_bars('MNQ', '5m', limit=100)
    """
    
    def __init__(self, api_key: str, base_url: str = "https://api.topstepx.com/v1"):
        """
        Initialize TopstepX client
        
        Args:
            api_key: Your TopstepX API key
            base_url: API base URL (default: production)
        """
        self.api_key = api_key
        self.base_url = base_url
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json'
        })
        
        logger.info(f"✅ TopStepX client initialized: {base_url}")
    
    
    # ========================================================================
    # MARKET DATA
    # ========================================================================
    
    def get_bars(self, 
                 symbol: str, 
                 timeframe: str, 
                 limit: int = 100,
                 start_time: Optional[datetime] = None,
                 end_time: Optional[datetime] = None) -> Optional[pd.DataFrame]:
        """
        Get OHLCV bars for a symbol
        
        Args:
            symbol: Instrument (MNQ, MES, MGC, etc.)
            timeframe: Bar size (1m, 3m, 5m, 15m, 30m, 1h, etc.)
            limit: Number of bars to fetch
            start_time: Optional start datetime
            end_time: Optional end datetime
            
        Returns:
            DataFrame with columns: time, open, high, low, close, volume
        """
        
        endpoint = f"{self.base_url}/market-data/bars"
        
        # Build query parameters
        params = {
            'symbol': symbol,
            'timeframe': timeframe,
            'limit': limit
        }
        
        if start_time:
            params['start'] = start_time.isoformat()
        if end_time:
            params['end'] = end_time.isoformat()
        
        try:
            response = self.session.get(endpoint, params=params, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                bars = data.get('bars', [])
                
                if not bars:
                    logger.warning(f"⚠️ No bars returned for {symbol} {timeframe}")
                    return None
                
                # Convert to DataFrame
                df = pd.DataFrame(bars)
                
                # Standardize column names
                column_mapping = {
                    't': 'time',
                    'timestamp': 'time',
                    'o': 'open',
                    'h': 'high',
                    'l': 'low',
                    'c': 'close',
                    'v': 'volume',
                    'vol': 'volume'
                }
                
                df = df.rename(columns=column_mapping)
                
                # Ensure time is datetime
                if 'time' in df.columns:
                    df['time'] = pd.to_datetime(df['time'])
                
                # Add technical indicators
                df = self._add_indicators(df)
                
                logger.info(f"✅ Fetched {len(df)} bars for {symbol} {timeframe}")
                return df
                
            elif response.status_code == 401:
                logger.error("❌ Authentication failed - check API key")
                return None
            elif response.status_code == 429:
                logger.error("❌ Rate limit exceeded - slow down requests")
                return None
            else:
                logger.error(f"❌ Error fetching bars: {response.status_code} - {response.text}")
                return None
                
        except requests.exceptions.Timeout:
            logger.error("❌ Request timeout")
            return None
        except Exception as e:
            logger.error(f"❌ Error: {e}")
            return None
    
    
    def get_current_price(self, symbol: str) -> Optional[float]:
        """
        Get current price for a symbol
        
        Args:
            symbol: Instrument (MNQ, MES, etc.)
            
        Returns:
            Current price or None
        """
        
        endpoint = f"{self.base_url}/market-data/quote"
        
        params = {'symbol': symbol}
        
        try:
            response = self.session.get(endpoint, params=params, timeout=5)
            
            if response.status_code == 200:
                data = response.json()
                price = data.get('last') or data.get('price') or data.get('close')
                return float(price) if price else None
            else:
                logger.error(f"❌ Error fetching price: {response.status_code}")
                return None
                
        except Exception as e:
            logger.error(f"❌ Error: {e}")
            return None
    
    
    # ========================================================================
    # POSITIONS
    # ========================================================================
    
    def get_positions(self) -> List[Dict]:
        """
        Get all open positions
        
        Returns:
            List of position dictionaries
        """
        
        endpoint = f"{self.base_url}/positions"
        
        try:
            response = self.session.get(endpoint, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                positions = data.get('positions', [])
                
                logger.info(f"📊 Fetched {len(positions)} open positions")
                return positions
            else:
                logger.error(f"❌ Error fetching positions: {response.status_code}")
                return []
                
        except Exception as e:
            logger.error(f"❌ Error: {e}")
            return []
    
    
    def get_position(self, position_id: str) -> Optional[Dict]:
        """
        Get specific position by ID
        
        Args:
            position_id: Position identifier
            
        Returns:
            Position dictionary or None
        """
        
        endpoint = f"{self.base_url}/positions/{position_id}"
        
        try:
            response = self.session.get(endpoint, timeout=5)
            
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"❌ Error fetching position: {response.status_code}")
                return None
                
        except Exception as e:
            logger.error(f"❌ Error: {e}")
            return None
    
    
    # ========================================================================
    # ORDERS
    # ========================================================================
    
    def get_orders(self, status: str = 'open') -> List[Dict]:
        """
        Get orders
        
        Args:
            status: Order status filter (open, filled, cancelled, all)
            
        Returns:
            List of order dictionaries
        """
        
        endpoint = f"{self.base_url}/orders"
        
        params = {'status': status}
        
        try:
            response = self.session.get(endpoint, params=params, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                orders = data.get('orders', [])
                
                logger.info(f"📋 Fetched {len(orders)} {status} orders")
                return orders
            else:
                logger.error(f"❌ Error fetching orders: {response.status_code}")
                return []
                
        except Exception as e:
            logger.error(f"❌ Error: {e}")
            return []
    
    
    # ========================================================================
    # ACCOUNT
    # ========================================================================
    
    def get_account_info(self) -> Optional[Dict]:
        """
        Get account information
        
        Returns:
            Account dictionary with balance, equity, etc.
        """
        
        endpoint = f"{self.base_url}/account"
        
        try:
            response = self.session.get(endpoint, timeout=5)
            
            if response.status_code == 200:
                data = response.json()
                logger.info("✅ Account info retrieved")
                return data
            else:
                logger.error(f"❌ Error fetching account: {response.status_code}")
                return None
                
        except Exception as e:
            logger.error(f"❌ Error: {e}")
            return None
    
    
    # ========================================================================
    # HELPER METHODS
    # ========================================================================
    
    def _add_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Add technical indicators to DataFrame
        Required for AI scanner pattern detection
        """
        
        # EMAs
        df['ema9'] = df['close'].ewm(span=9, adjust=False).mean()
        df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()
        df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
        
        # ATR
        df['tr'] = df[['high', 'low', 'close']].apply(
            lambda row: max(
                row['high'] - row['low'],
                abs(row['high'] - row['close']),
                abs(row['low'] - row['close'])
            ), axis=1
        )
        df['atr'] = df['tr'].rolling(14).mean()
        
        # RSI
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        df['rsi'] = 100 - (100 / (1 + rs))
        
        # ADX (simplified)
        plus_dm = df['high'].diff()
        minus_dm = -df['low'].diff()
        
        plus_dm[plus_dm < 0] = 0
        minus_dm[minus_dm < 0] = 0
        
        tr_sum = df['tr'].rolling(14).sum()
        plus_di = 100 * (plus_dm.rolling(14).sum() / tr_sum)
        minus_di = 100 * (minus_dm.rolling(14).sum() / tr_sum)
        
        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
        df['adx'] = dx.rolling(14).mean()
        
        # Fill NaN with reasonable defaults
        df['adx'].fillna(25, inplace=True)
        
        return df
    
    
    def test_connection(self) -> bool:
        """
        Test API connection
        
        Returns:
            True if connection successful
        """
        
        logger.info("🔍 Testing TopstepX API connection...")
        
        try:
            account = self.get_account_info()
            
            if account:
                logger.info("✅ Connection successful!")
                logger.info(f"   Account ID: {account.get('account_id', 'N/A')}")
                logger.info(f"   Balance: ${account.get('balance', 0):,.2f}")
                return True
            else:
                logger.error("❌ Connection failed")
                return False
                
        except Exception as e:
            logger.error(f"❌ Connection test failed: {e}")
            return False


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================

def fetch_market_data(symbol: str, 
                     timeframe: str, 
                     bars: int = 100,
                     api_key: str = None) -> Optional[pd.DataFrame]:
    """
    Convenience function to fetch market data
    
    Args:
        symbol: Instrument (MNQ, MES, MGC)
        timeframe: Bar size (1m, 3m, 5m, etc.)
        bars: Number of bars
        api_key: TopstepX API key (or set TOPSTEPX_API_KEY env var)
        
    Returns:
        DataFrame with OHLCV + indicators
    """
    
    import os
    
    if not api_key:
        api_key = os.environ.get('TOPSTEPX_API_KEY')
    
    if not api_key:
        logger.error("❌ No API key provided")
        return None
    
    client = TopStepXClient(api_key)
    return client.get_bars(symbol, timeframe, limit=bars)


# ============================================================================
# TESTING
# ============================================================================

if __name__ == '__main__':
    """Test TopstepX client"""
    
    import os
    
    # Get API key from environment
    api_key = os.environ.get('TOPSTEPX_API_KEY')
    
    if not api_key:
        print("❌ Set TOPSTEPX_API_KEY environment variable")
        print("   export TOPSTEPX_API_KEY='your-key-here'")
        exit(1)
    
    # Initialize client
    client = TopStepXClient(api_key)
    
    # Test connection
    print("\n" + "="*80)
    print("TESTING TOPSTEPX API CONNECTION")
    print("="*80)
    
    if not client.test_connection():
        print("❌ Connection test failed")
        exit(1)
    
    # Test market data
    print("\n" + "="*80)
    print("TESTING MARKET DATA")
    print("="*80)
    
    df = client.get_bars('MNQ', '5m', limit=10)
    
    if df is not None:
        print(f"\n✅ Fetched {len(df)} bars for MNQ 5m")
        print("\nLast 5 bars:")
        print(df[['time', 'open', 'high', 'low', 'close', 'volume']].tail())
        print("\nIndicators available:")
        print(df[['ema9', 'ema21', 'ema50', 'atr', 'rsi', 'adx']].tail(1))
    else:
        print("❌ Failed to fetch market data")
    
    # Test positions
    print("\n" + "="*80)
    print("TESTING POSITIONS")
    print("="*80)
    
    positions = client.get_positions()
    print(f"\n✅ Found {len(positions)} open positions")
    
    if positions:
        for pos in positions:
            print(f"\n   Symbol: {pos.get('symbol')}")
            print(f"   Qty: {pos.get('quantity')}")
            print(f"   Entry: ${pos.get('entry_price')}")
            print(f"   Current: ${pos.get('current_price')}")
            print(f"   P&L: ${pos.get('unrealized_pnl')}")
    
    # Test current price
    print("\n" + "="*80)
    print("TESTING CURRENT PRICE")
    print("="*80)
    
    price = client.get_current_price('MNQ')
    if price:
        print(f"\n✅ MNQ current price: ${price:.2f}")
    
    print("\n" + "="*80)
    print("✅ ALL TESTS COMPLETE")
    print("="*80)
