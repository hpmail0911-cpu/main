#!/usr/bin/env python3
"""
PROJECTX/TOPSTEPX API CLIENT (CORRECTED)
Proper authentication with session tokens

Authentication Flow:
1. POST /api/Auth/loginKey with username + API key
2. Receive session token (JWT)
3. Use session token as Bearer token for all requests
4. Tokens expire after 24 hours
"""

import requests
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import logging
import time
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ProjectXClient:
    """
    ProjectX/TopstepX API Client
    
    Usage:
        client = ProjectXClient(
            username='your-username',
            api_key='your-api-key',
            environment='demo'  # or 'prod'
        )
        
        # Client auto-authenticates
        accounts = client.get_accounts()
        positions = client.get_positions(account_id)
    """
    
    def __init__(self, 
                 username: str,
                 api_key: str, 
                 environment: str = 'demo'):
        """
        Initialize ProjectX client
        
        Args:
            username: Your ProjectX username
            api_key: Your ProjectX API key
            environment: 'demo' or 'prod'
        """
        self.username = username
        self.api_key = api_key
        self.environment = environment
        
        # Set base URL based on environment
        # TopstepX uses same URL for both demo and prod
        self.base_url = "https://api.topstepx.com"
        
        self.session_token = None
        self.token_expires_at = None
        
        self.session = requests.Session()
        
        logger.info(f"✅ ProjectX client initialized: {self.base_url}")
        
        # Authenticate immediately
        self.authenticate()
    
    
    # ========================================================================
    # AUTHENTICATION
    # ========================================================================
    
    def authenticate(self) -> bool:
        """
        Authenticate and get session token
        
        Returns:
            True if successful
        """
        
        endpoint = f"{self.base_url}/api/Auth/loginKey"
        
        payload = {
            "userName": self.username,
            "apiKey": self.api_key
        }
        
        try:
            logger.info("🔐 Authenticating with ProjectX...")
            
            response = requests.post(
                endpoint,
                json=payload,
                headers={'Content-Type': 'application/json'},
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                
                if data.get('success'):
                    self.session_token = data.get('sessionToken') or data.get('token')
                    
                    # Set token expiration (24 hours)
                    self.token_expires_at = datetime.now() + timedelta(hours=24)
                    
                    # Update session headers
                    self.session.headers.update({
                        'Authorization': f'Bearer {self.session_token}',
                        'Content-Type': 'application/json'
                    })
                    
                    logger.info("✅ Authentication successful!")
                    logger.info(f"   Token expires: {self.token_expires_at.strftime('%Y-%m-%d %H:%M:%S')}")
                    return True
                else:
                    error_msg = data.get('errorMessage', 'Unknown error')
                    logger.error(f"❌ Authentication failed: {error_msg}")
                    return False
            else:
                logger.error(f"❌ Authentication failed: {response.status_code}")
                logger.error(f"   Response: {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Authentication error: {e}")
            return False
    
    
    def ensure_authenticated(self):
        """Ensure session token is valid, re-authenticate if needed"""
        
        if not self.session_token or not self.token_expires_at:
            self.authenticate()
            return
        
        # Re-authenticate if token expires within 1 hour
        if datetime.now() > (self.token_expires_at - timedelta(hours=1)):
            logger.info("⚠️ Token expiring soon, re-authenticating...")
            self.authenticate()
    
    
    # ========================================================================
    # ACCOUNT MANAGEMENT
    # ========================================================================
    
    def get_accounts(self, only_active: bool = True) -> List[Dict]:
        """
        Get list of accounts
        
        Args:
            only_active: Only return active accounts
            
        Returns:
            List of account dictionaries
        """
        
        self.ensure_authenticated()
        
        endpoint = f"{self.base_url}/api/Account/search"
        
        payload = {
            "onlyActiveAccounts": only_active
        }
        
        try:
            response = self.session.post(endpoint, json=payload, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                
                if data.get('success'):
                    accounts = data.get('accounts', [])
                    logger.info(f"✅ Retrieved {len(accounts)} account(s)")
                    return accounts
                else:
                    logger.error(f"❌ Error: {data.get('errorMessage')}")
                    return []
            else:
                logger.error(f"❌ Error fetching accounts: {response.status_code}")
                return []
                
        except Exception as e:
            logger.error(f"❌ Error: {e}")
            return []
    
    
    def get_account_details(self, account_id: int) -> Optional[Dict]:
        """
        Get detailed account information
        
        Args:
            account_id: Account ID
            
        Returns:
            Account details dictionary
        """
        
        self.ensure_authenticated()
        
        endpoint = f"{self.base_url}/api/Account/{account_id}"
        
        try:
            response = self.session.get(endpoint, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                
                if data.get('success'):
                    return data.get('account')
                else:
                    logger.error(f"❌ Error: {data.get('errorMessage')}")
                    return None
            else:
                logger.error(f"❌ Error: {response.status_code}")
                return None
                
        except Exception as e:
            logger.error(f"❌ Error: {e}")
            return None
    
    
    # ========================================================================
    # POSITIONS
    # ========================================================================
    
    def get_positions(self, account_id: int) -> List[Dict]:
        """
        Get open positions/trades for an account
        
        Args:
            account_id: Account ID
            
        Returns:
            List of trade dictionaries
        """
        
        self.ensure_authenticated()
        
        # TopstepX uses Trade/search endpoint for positions
        endpoint = f"{self.base_url}/api/Trade/search"
        
        payload = {
            "accountId": account_id
        }
        # NOTE: API returns individual FILLS not netted positions.
        # profitAndLoss=null means OPEN fill (entry not yet closed).
        # profitAndLoss=float means CLOSED fill (exit recorded).
        # We filter to open fills below in get_open_positions().
        
        try:
            response = self.session.post(endpoint, json=payload, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                
                if data.get('success'):
                    trades = data.get('trades', [])
                    logger.info(f"📊 Retrieved {len(trades)} trade(s)/position(s)")
                    return trades
                else:
                    logger.error(f"❌ Error: {data.get('errorMessage')}")
                    return []
            else:
                logger.error(f"❌ Error: {response.status_code}")
                return []
                
        except Exception as e:
            logger.error(f"❌ Error: {e}")
            return []
    
    
    def get_open_positions(self, account_id: int) -> List[Dict]:
        """
        Returns only OPEN positions, netted by contract.

        Uses only fills with profitAndLoss=null (unfilled entries) to determine
        open positions. Closed fills (pnl != null) are excluded from entry price
        calculation to prevent stale price averaging.

        FIX: Previously averaged ALL buy/sell fills including closed ones,
        producing wrong entry prices (e.g. $5166 when current price is $5206).
        Now only uses open (unmatched) fills for entry price.
        """
        all_fills = self.get_positions(account_id)

        from collections import defaultdict
        from datetime import datetime, timezone, timedelta
        from dateutil import parser as dtparser
        cutoff_dt = datetime.now(timezone.utc) - timedelta(hours=24)

        def _is_recent(f):
            ts = f.get('creationTimestamp', '')
            try:
                return dtparser.parse(ts) >= cutoff_dt
            except Exception:
                return False

        session_fills = [f for f in all_fills if _is_recent(f)]
        logger.info(f"📊 Session fills (last 24h): {len(session_fills)} of {len(all_fills)} total")

        contracts = defaultdict(list)
        for fill in session_fills:
            contracts[fill['contractId']].append(fill)

        open_positions = []
        for contract_id, fills in contracts.items():
            # Separate open fills (pnl=null) from closed fills (pnl has value)
            open_buys = []
            open_sells = []
            closed_buy_qty = 0
            closed_sell_qty = 0

            # Sort by timestamp so we process oldest first
            sorted_fills = sorted(fills, key=lambda f: f.get('creationTimestamp', ''))

            for f in sorted_fills:
                if f.get('voided'):
                    continue
                size = int(f.get('size', 0))
                has_pnl = f.get('profitAndLoss') is not None

                if f.get('side') == 1:  # Buy
                    if has_pnl:
                        closed_buy_qty += size
                    else:
                        open_buys.append(f)
                else:  # Sell
                    if has_pnl:
                        closed_sell_qty += size
                    else:
                        open_sells.append(f)

            # Net: open buys - open sells = position
            open_buy_qty = sum(int(f.get('size', 0)) for f in open_buys)
            open_sell_qty = sum(int(f.get('size', 0)) for f in open_sells)
            net_size = open_buy_qty - open_sell_qty

            if net_size > 0 and open_buys:
                # Long position — use only OPEN buy fills for entry price
                # Take the most recent fills that account for net_size
                recent_buys = sorted(open_buys, key=lambda f: f.get('creationTimestamp', ''), reverse=True)
                remaining = net_size
                entry_fills = []
                for f in recent_buys:
                    if remaining <= 0:
                        break
                    entry_fills.append(f)
                    remaining -= int(f.get('size', 0))

                total_qty = sum(int(f.get('size', 0)) for f in entry_fills)
                if total_qty > 0:
                    avg_entry = sum(f['price'] * int(f.get('size', 0)) for f in entry_fills) / total_qty
                else:
                    avg_entry = entry_fills[0]['price'] if entry_fills else 0

                latest = entry_fills[0]  # most recent
                open_positions.append({
                    'id':                latest['id'],
                    'accountId':         account_id,
                    'contractId':        contract_id,
                    'creationTimestamp': latest['creationTimestamp'],
                    'price':             round(avg_entry, 4),
                    'averagePrice':      round(avg_entry, 4),
                    'profitAndLoss':     None,
                    'side':              1,
                    'size':              net_size,
                    'netSize':           net_size,
                    'voided':            False,
                    'orderId':           latest.get('orderId'),
                    '_net_long':         True,
                })

            elif net_size < 0 and open_sells:
                # Short position — use only OPEN sell fills for entry price
                recent_sells = sorted(open_sells, key=lambda f: f.get('creationTimestamp', ''), reverse=True)
                remaining = abs(net_size)
                entry_fills = []
                for f in recent_sells:
                    if remaining <= 0:
                        break
                    entry_fills.append(f)
                    remaining -= int(f.get('size', 0))

                total_qty = sum(int(f.get('size', 0)) for f in entry_fills)
                if total_qty > 0:
                    avg_entry = sum(f['price'] * int(f.get('size', 0)) for f in entry_fills) / total_qty
                else:
                    avg_entry = entry_fills[0]['price'] if entry_fills else 0

                latest = entry_fills[0]
                open_positions.append({
                    'id':                latest['id'],
                    'accountId':         account_id,
                    'contractId':        contract_id,
                    'creationTimestamp': latest['creationTimestamp'],
                    'price':             round(avg_entry, 4),
                    'averagePrice':      round(avg_entry, 4),
                    'profitAndLoss':     None,
                    'side':              0,
                    'size':              abs(net_size),
                    'netSize':           net_size,
                    'voided':            False,
                    'orderId':           latest.get('orderId'),
                    '_net_short':        True,
                })
            # net_size == 0 means fully closed — skip

        logger.info(f"📊 Open positions: {len(open_positions)} (from {len(all_fills)} fills)")
        return open_positions

    # ========================================================================
    # ORDERS
    # ========================================================================
    
    def get_orders(self, account_id: int, days_back: int = 7) -> List[Dict]:
        """
        Get orders for an account
        
        Args:
            account_id: Account ID
            days_back: How many days back to search (default: 7)
            
        Returns:
            List of order dictionaries
        """
        
        self.ensure_authenticated()
        
        endpoint = f"{self.base_url}/api/Order/search"
        
        # Calculate start timestamp (required field)
        start_time = datetime.now() - timedelta(days=days_back)
        
        payload = {
            "accountId": account_id,
            "startTimestamp": start_time.isoformat()
        }
        
        try:
            response = self.session.post(endpoint, json=payload, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                
                if data.get('success'):
                    orders = data.get('orders', [])
                    logger.info(f"📋 Retrieved {len(orders)} order(s)")
                    return orders
                else:
                    logger.error(f"❌ Error: {data.get('errorMessage')}")
                    return []
            else:
                logger.error(f"❌ Error: {response.status_code}")
                return []
                
        except Exception as e:
            logger.error(f"❌ Error: {e}")
            return []
    
    
    def modify_order(self, account_id: int, order_id: int, 
                    size: Optional[int] = None,
                    limit_price: Optional[float] = None,
                    stop_price: Optional[float] = None,
                    trail_price: Optional[float] = None) -> bool:
        """
        Modify an existing order
        
        Args:
            account_id: Account ID
            order_id: Order ID to modify
            size: New order size (optional)
            limit_price: New limit price (optional)
            stop_price: New stop price (optional)
            trail_price: New trailing price (optional)
            
        Returns:
            True if successful
        """
        
        self.ensure_authenticated()
        
        endpoint = f"{self.base_url}/api/Order/modify"
        
        payload = {
            "accountId": account_id,
            "orderId": order_id
        }
        
        if size is not None:
            payload["size"] = size
        if limit_price is not None:
            payload["limitPrice"] = limit_price
        if stop_price is not None:
            payload["stopPrice"] = stop_price
        if trail_price is not None:
            payload["trailPrice"] = trail_price
        
        try:
            response = self.session.post(endpoint, json=payload, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                
                if data.get('success'):
                    logger.info(f"✅ Order modified: ID {order_id}")
                    return True
                else:
                    logger.error(f"❌ Modify failed: {data.get('errorMessage')}")
                    return False
            else:
                logger.error(f"❌ Modify error: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Error modifying order: {e}")
            return False
    
    
    def place_order(self, account_id: int, contract_id: str, order_type: int, 
                   side: int, size: int, limit_price: Optional[float] = None,
                   stop_price: Optional[float] = None) -> Optional[Dict]:
        """
        Place order via ProjectX
        
        Args:
            account_id: Account ID
            contract_id: Contract ID (e.g., "CON.F.US.MNQ.H26")
            order_type: 1=Limit, 2=Market, 3=Stop, 4=StopLimit, 5=TrailingStop
            side: 0=Bid (sell), 1=Ask (buy)
            size: Order size
            limit_price: Limit price (if applicable)
            stop_price: Stop price (if applicable)
            
        Returns:
            Order result dictionary
        """
        
        self.ensure_authenticated()
        
        endpoint = f"{self.base_url}/api/Order/place"
        
        payload = {
            "accountId": account_id,
            "contractId": contract_id,
            "type": order_type,
            "side": side,
            "size": size
        }
        
        if limit_price is not None:
            payload["limitPrice"] = limit_price
        if stop_price is not None:
            payload["stopPrice"] = stop_price
        
        try:
            response = self.session.post(endpoint, json=payload, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                
                if data.get('success'):
                    order_id = data.get('orderId')
                    logger.info(f"✅ Order placed: ID {order_id}")
                    return data
                else:
                    logger.error(f"❌ Order failed: {data.get('errorMessage')} | FULL={data} | PAYLOAD={payload}")
                    return None
            else:
                logger.error(f"❌ Order error: {response.status_code} | BODY={response.text[:500]} | PAYLOAD={payload}")
                return None
                
        except Exception as e:
            logger.error(f"❌ Error placing order: {e}")
            return None
    
    
    def cancel_order(self, order_id) -> bool:
        """Cancel an order. Returns True on success, False otherwise (non-fatal)."""
        if not order_id:
            logger.debug("cancel_order: no order_id, skipping")
            return False

        self.ensure_authenticated()
        endpoint = f"{self.base_url}/api/Order/cancel"
        payload  = {"orderId": order_id}

        try:
            response = self.session.post(endpoint, json=payload, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if data.get('success'):
                    logger.info(f"✅ Order cancelled: {order_id}")
                    return True
                else:
                    # Order may already be filled/cancelled — not a fatal error
                    logger.warning(f"⚠️  Cancel {order_id}: {data.get('errorMessage')} (may already be gone)")
                    return False
            else:
                # 400/404 = order not found or already gone — log as warning not error
                logger.warning(f"⚠️  Cancel {order_id}: HTTP {response.status_code} (stale order ID, continuing)")
                return False
        except Exception as e:
            logger.warning(f"⚠️  Cancel {order_id} exception: {e}")
            return False
    
    
    def close_position(self, account_id: int, contract_id: str, size: int) -> bool:
        """
        Close position by placing opposing market order
        
        Args:
            account_id: Account ID
            contract_id: Contract ID
            size: Position size to close
            
        Returns:
            True if successful
        """
        
        # Place market order on opposite side
        # If we're long, sell (side=0)
        # This is simplified - you'd need to determine current position direction
        
        result = self.place_order(
            account_id=account_id,
            contract_id=contract_id,
            order_type=2,  # Market order
            side=0,  # Sell/Bid (to close long)
            size=size
        )
        
        return result is not None


    # ========================================================================
    # MARKET DATA
    # ========================================================================

    def get_latest_price(self, contract_id: str):
        """
        Fetch current price via POST /api/History/retrieveBars.
        Tries multiple parameter combinations to maximise success.
        """
        self.ensure_authenticated()
        endpoint = f"{self.base_url}/api/History/retrieveBars"
        from datetime import timedelta

        now = datetime.utcnow()

        # Try progressively wider windows and both live flags
        attempts = [
            {"live": True,  "minutes": 10,  "unit": 3, "unitNumber": 1},
            {"live": False, "minutes": 10,  "unit": 3, "unitNumber": 1},
            {"live": True,  "minutes": 60,  "unit": 3, "unitNumber": 1},
            {"live": False, "minutes": 60,  "unit": 3, "unitNumber": 1},
            {"live": True,  "minutes": 120, "unit": 4, "unitNumber": 1},  # hourly bars
            {"live": False, "minutes": 120, "unit": 4, "unitNumber": 1},
        ]

        for attempt in attempts:
            start = now - timedelta(minutes=attempt["minutes"])
            payload = {
                "contractId":        contract_id,
                "live":              attempt["live"],
                "startTime":         start.strftime('%Y-%m-%dT%H:%M:%SZ'),
                "endTime":           now.strftime('%Y-%m-%dT%H:%M:%SZ'),
                "unit":              attempt["unit"],
                "unitNumber":        attempt["unitNumber"],
                "limit":             10,
                "includePartialBar": True,
            }
            try:
                r = self.session.post(endpoint, json=payload, timeout=8)
                if r.status_code == 200:
                    data = r.json()
                    if data.get('success'):
                        bars = data.get('bars', [])
                        if bars:
                            bars.sort(key=lambda b: b.get('t', b.get('timestamp', '')))
                            last = bars[-1]
                            for f in ('c', 'close', 'closePrice', 'last'):
                                v = last.get(f)
                                if v and float(v) > 0:
                                    logger.info(f"  📈 Live price {contract_id}: {float(v):.4f} "
                                                f"(live={attempt['live']}, unit={attempt['unit']})")
                                    return float(v)
                            logger.warning(f"get_latest_price: bar has no close — keys={list(last.keys())} bar={last}")
                        # bars=[] with success=True — try next attempt
                    else:
                        # Log FULL response so we can diagnose
                        logger.warning(f"get_latest_price attempt {attempt}: "
                                       f"success=False FULL={data}")
                else:
                    logger.warning(f"get_latest_price HTTP {r.status_code} body={r.text[:300]}")
            except Exception as e:
                logger.error(f"get_latest_price exception: {e}")

        logger.error(f"  ❌ get_latest_price: ALL attempts failed for {contract_id}")
        return None

    def get_quote(self, contract_id: str):
        """Shim — routes to get_latest_price()."""
        p = self.get_latest_price(contract_id)
        return {'lastPrice': p, 'last': p} if p else None


    def get_contracts(self, live: bool = True) -> List[Dict]:
        """
        Get available contracts
        
        Args:
            live: Get live contracts only
            
        Returns:
            List of contract dictionaries
        """
        
        self.ensure_authenticated()
        
        endpoint = f"{self.base_url}/api/Contract/available"
        
        payload = {
            "live": live
        }
        
        try:
            response = self.session.post(endpoint, json=payload, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                
                if data.get('success'):
                    contracts = data.get('contracts', [])
                    logger.info(f"📊 Retrieved {len(contracts)} contract(s)")
                    return contracts
                else:
                    logger.error(f"❌ Error: {data.get('errorMessage')}")
                    return []
            else:
                logger.error(f"❌ Error: {response.status_code}")
                return []
                
        except Exception as e:
            logger.error(f"❌ Error: {e}")
            return []
    
    
    # ========================================================================
    # HELPER METHODS
    # ========================================================================
    
    def test_connection(self) -> bool:
        """
        Test API connection
        
        Returns:
            True if connection successful
        """
        
        logger.info("🔍 Testing ProjectX API connection...")
        
        try:
            accounts = self.get_accounts()
            
            if accounts:
                logger.info("✅ Connection successful!")
                for acc in accounts:
                    logger.info(f"   Account: {acc.get('name')} (ID: {acc.get('id')})")
                return True
            else:
                logger.error("❌ Connection failed - no accounts found")
                return False
                
        except Exception as e:
            logger.error(f"❌ Connection test failed: {e}")
            return False


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================

def create_client_from_env() -> Optional[ProjectXClient]:
    """
    Create client from environment variables
    
    Expects:
        PROJECTX_USERNAME
        PROJECTX_API_KEY
        PROJECTX_ENV (optional, default: demo)
    """
    
    username = os.environ.get('PROJECTX_USERNAME')
    api_key = os.environ.get('PROJECTX_API_KEY')
    environment = os.environ.get('PROJECTX_ENV', 'demo')
    
    if not username or not api_key:
        logger.error("❌ Missing environment variables: PROJECTX_USERNAME and/or PROJECTX_API_KEY")
        return None
    
    return ProjectXClient(username, api_key, environment)


# ============================================================================
# TESTING
# ============================================================================

if __name__ == '__main__':
    """Test ProjectX client"""
    
    print("\n" + "="*80)
    print("PROJECTX API CLIENT TEST")
    print("="*80)
    
    # Get credentials
    username = os.environ.get('PROJECTX_USERNAME')
    api_key = os.environ.get('PROJECTX_API_KEY')
    environment = os.environ.get('PROJECTX_ENV', 'demo')
    
    if not username or not api_key:
        print("\n❌ Set environment variables:")
        print("   export PROJECTX_USERNAME='your-username'")
        print("   export PROJECTX_API_KEY='your-api-key'")
        print("   export PROJECTX_ENV='demo'  # or 'prod'")
        exit(1)
    
    # Initialize client
    client = ProjectXClient(username, api_key, environment)
    
    # Test connection
    print("\n" + "="*80)
    print("TESTING CONNECTION")
    print("="*80)
    
    if not client.test_connection():
        print("❌ Connection test failed")
        exit(1)
    
    # Get accounts
    print("\n" + "="*80)
    print("TESTING ACCOUNTS")
    print("="*80)
    
    accounts = client.get_accounts()
    
    if accounts:
        for acc in accounts:
            print(f"\n✅ Account: {acc.get('name')}")
            print(f"   ID: {acc.get('id')}")
            print(f"   Can Trade: {acc.get('canTrade')}")
            print(f"   Visible: {acc.get('isVisible')}")
            
            # Get account details
            details = client.get_account_details(acc['id'])
            if details:
                print(f"   Balance: ${details.get('balance', 0):,.2f}")
            
            # Get positions/trades
            positions = client.get_positions(acc['id'])
            print(f"   Trades/Positions: {len(positions)}")
            
            if positions:
                for pos in positions[:3]:  # Show first 3
                    pnl = pos.get('profitAndLoss') or 0.0
                    price = pos.get('price') or 0.0
                    fees = pos.get('fees') or 0.0
                    
                    print(f"\n   Trade ID: {pos.get('id')}")
                    print(f"   Contract: {pos.get('contractId')}")
                    print(f"   Entry Price: ${price:,.2f}")
                    print(f"   P&L: ${pnl:+,.2f}")
                    print(f"   Fees: ${fees:,.2f}")
            
            # Get orders
            orders = client.get_orders(acc['id'], days_back=7)
            print(f"\n   Orders (last 7 days): {len(orders)}")
    
    # Get contracts
    print("\n" + "="*80)
    print("TESTING CONTRACTS")
    print("="*80)
    
    contracts = client.get_contracts()
    print(f"\n✅ Retrieved {len(contracts)} contracts")
    
    # Show sample contracts
    for contract in contracts[:5]:
        print(f"\n   {contract.get('name')}: {contract.get('description')}")
    
    print("\n" + "="*80)
    print("✅ ALL TESTS COMPLETE")
    print("="*80)
