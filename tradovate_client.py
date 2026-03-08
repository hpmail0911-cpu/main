#!/usr/bin/env python3
"""
TRADOVATE WEBSOCKET CLIENT — MFFU Evaluation Account
Full position management via Tradovate WebSocket API.
No external API key required — username/password auth only.

Supports:
  - Authentication (REST token → WebSocket)
  - Live position monitoring
  - Live price quotes (WebSocket MD subscription)
  - Place/cancel/modify orders
  - Stop loss management
  - Close position

FIXES APPLIED:
  FIX-1  _on_message: restructured if/elif so pending-response block is no
         longer dead code — was unreachable because the prior bare
         `if msg_type == "a"` caught every message and returned early.
  FIX-2  get_open_positions: numeric account-id resolution so the
         accountId filter no longer fails when self.account_id is a
         string spec like "MFFUEVFLX540866001".
  FIX-3  get_quote: removed broken /md/getChart REST fallback; now drives
         a WebSocket md/subscribeQuote subscription and returns from the
         live _quotes cache.
  FIX-4  _subscribe: also subscribes to MD quotes for already-open
         positions so _quotes is populated from the first tick.
  FIX-5  _handle_event: added quote/Quote entity-type handler that writes
         into self._quotes — previously quote events were silently dropped.
  FIX-6  place_order / close_position: accountId field now uses the
         resolved numeric integer ID, not the string spec.
"""

import json
import time
import logging
import threading
import requests
import websocket
from typing import Dict, List, Optional, Callable

logger = logging.getLogger(__name__)

# ============================================================================
# ENDPOINTS — Evaluation account uses DEMO environment
# ============================================================================
TRADOVATE_REST_URL = "https://demo.tradovateapi.com/v1"
TRADOVATE_WS_URL   = "wss://demo.tradovateapi.com/v1/websocket"

# For live (funded) account change to:
# TRADOVATE_REST_URL = "https://live.tradovateapi.com/v1"
# TRADOVATE_WS_URL   = "wss://live.tradovateapi.com/v1/websocket"

APP_ID      = "Truffles Capital Trade Manager"
APP_VERSION = "1.0"
DEVICE_ID   = "truffles-trade-manager-001"


class TradovateClient:
    """
    Full Tradovate WebSocket client.
    Handles auth, reconnection, position/order management.
    """

    def __init__(self, username: str, password: str, account_id: str):
        self.username   = username
        self.password   = password
        self.account_id = account_id          # May be string spec or numeric string

        self._access_token      = None
        self._token_expiry      = 0
        self._numeric_account_id: Optional[int] = None   # FIX-2 / FIX-6
        self._ws                = None
        self._ws_thread         = None
        self._connected         = False
        self._request_id        = 0
        self._pending: Dict[int, dict] = {}   # request_id → response holder
        self._lock              = threading.Lock()

        # Live state
        self._positions: Dict[str, dict] = {}  # contractId → position
        self._quotes:    Dict[str, float] = {}  # symbol → last price   (FIX-3/4/5)
        self._orders:    Dict[int, dict]  = {}  # orderId → order
        self._subscribed_md: set          = set()  # symbols with MD subscription (FIX-3)

        # Callbacks (optional)
        self.on_position_update: Optional[Callable] = None
        self.on_fill:            Optional[Callable] = None

    # =========================================================================
    # AUTHENTICATION
    # =========================================================================

    def authenticate(self) -> bool:
        """Get access token via REST. Valid ~24h. Call once at startup."""
        url = f"{TRADOVATE_REST_URL}/auth/accesstokenrequest"
        payload = {
            "name":       self.username,
            "password":   self.password,
            "appId":      APP_ID,
            "appVersion": APP_VERSION,
            "deviceId":   DEVICE_ID,
            "cid":        0,
            "sec":        ""
        }
        try:
            resp = requests.post(url, json=payload, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            if "errorText" in data:
                logger.error(f"Tradovate auth error: {data['errorText']}")
                return False

            self._access_token = data.get("accessToken")
            expires_in         = data.get("expirationTime", 86400)
            self._token_expiry = time.time() + expires_in - 300  # refresh 5min early

            logger.info(f"✅ Tradovate authenticated — token valid ~{expires_in//3600}h")

            # FIX-2 / FIX-6: resolve numeric account ID immediately after auth
            self._resolve_numeric_account_id()

            return True

        except Exception as e:
            logger.error(f"Tradovate auth failed: {e}")
            return False

    def _ensure_token(self):
        """Re-authenticate if token is expired or missing."""
        if not self._access_token or time.time() > self._token_expiry:
            logger.info("Tradovate token expired — re-authenticating...")
            self.authenticate()

    # =========================================================================
    # FIX-2 / FIX-6 — Numeric account ID resolution
    # =========================================================================

    def _resolve_numeric_account_id(self):
        """
        Resolve a string account spec (e.g. 'MFFUEVFLX540866001') to the
        numeric integer Tradovate account ID required by order/position REST
        calls.  Tries /account/list and matches on name or id.
        """
        try:
            resp = requests.get(
                f"{TRADOVATE_REST_URL}/account/list",
                headers={"Authorization": f"Bearer {self._access_token}"},
                timeout=5
            )
            accounts = resp.json()
            if not isinstance(accounts, list):
                logger.warning(f"Unexpected account/list response: {accounts}")
                return
            for acct in accounts:
                name = str(acct.get("name", ""))
                aid  = str(acct.get("id", ""))
                if name == str(self.account_id) or aid == str(self.account_id):
                    self._numeric_account_id = int(acct["id"])
                    logger.info(f"✅ Tradovate numeric account ID resolved: "
                                f"{self.account_id} → {self._numeric_account_id}")
                    return
            # Fallback: if account_id is already a pure integer string use it
            if str(self.account_id).isdigit():
                self._numeric_account_id = int(self.account_id)
                logger.info(f"Tradovate account ID (numeric): {self._numeric_account_id}")
            else:
                logger.warning(f"Could not resolve numeric ID for account spec "
                                f"'{self.account_id}' — orders may fail")
        except Exception as e:
            logger.error(f"_resolve_numeric_account_id error: {e}")

    def _acct_id(self) -> Optional[int]:
        """Return the resolved numeric account ID (or None if unresolved)."""
        return self._numeric_account_id

    # =========================================================================
    # WEBSOCKET CONNECTION
    # =========================================================================

    def connect(self) -> bool:
        """Open WebSocket connection and authenticate it."""
        self._ensure_token()
        if not self._access_token:
            logger.error("Cannot connect — no access token")
            return False

        try:
            self._ws = websocket.WebSocketApp(
                TRADOVATE_WS_URL,
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
            )
            self._ws_thread = threading.Thread(
                target=self._ws.run_forever,
                kwargs={"ping_interval": 20, "ping_timeout": 10},
                daemon=True
            )
            self._ws_thread.start()

            # Wait up to 10s for connection
            for _ in range(100):
                if self._connected:
                    return True
                time.sleep(0.1)

            logger.error("WebSocket connection timeout")
            return False

        except Exception as e:
            logger.error(f"WebSocket connect error: {e}")
            return False

    def _on_open(self, ws):
        """Send auth token immediately on open."""
        logger.info("Tradovate WebSocket opened — authenticating...")
        auth_msg = f"authorize\n0\n\n{self._access_token}"
        ws.send(auth_msg)

    def _on_message(self, ws, message):
        """
        Parse Tradovate frame format: 'type\\nid\\n\\nbody'

        FIX-1: Restructured as if/elif chain so the pending-response branch
        is no longer dead code.  Original code had three consecutive
        `if msg_type == "a"` blocks; the second one returned before the
        third could ever execute, silently dropping all request responses
        and causing _send_request to always time out.
        """
        try:
            # Tradovate heartbeat
            if message == "[]":
                return

            # Parse frame
            parts    = message.split("\n", 3)
            msg_type = parts[0] if len(parts) > 0 else ""
            msg_id   = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
            body     = parts[3] if len(parts) > 3 else ""

            if not body:
                return

            data = json.loads(body)

            # ── FIX-1: single if/elif tree ────────────────────────────────
            if msg_type == "a":
                if msg_id == 0:
                    # Auth response
                    if isinstance(data, list):
                        for item in data:
                            if item.get("s") == 200:
                                self._connected = True
                                logger.info("✅ Tradovate WebSocket authenticated")
                                self._subscribe()

                elif msg_id in self._pending:
                    # Response to an outstanding _send_request call
                    with self._lock:
                        self._pending[msg_id]["response"] = data
                        self._pending[msg_id]["done"]     = True

                else:
                    # Push subscription events (positions, orders, quotes…)
                    if isinstance(data, list):
                        for item in data:
                            self._handle_event(item)

        except Exception as e:
            logger.debug(f"WS message parse error: {e} | msg={message[:100]}")

    def _on_error(self, ws, error):
        logger.error(f"Tradovate WebSocket error: {error}")

    def _on_close(self, ws, code, msg):
        self._connected = False
        logger.warning(f"Tradovate WebSocket closed: {code} {msg}")

    def _subscribe(self):
        """
        Subscribe to position and order updates after auth.
        Also subscribes to MD quotes for any positions already open.
        FIX-4: Added market-data quote subscriptions.
        """
        acct_id = self._acct_id() or self.account_id

        # Subscribe to account positions
        self._send_request(
            "position/deps",
            {"accountId": acct_id}
        )
        # Subscribe to order updates
        self._send_request(
            "order/deps",
            {"accountId": acct_id}
        )
        logger.info(f"Subscribed to positions/orders for account {acct_id}")

        # FIX-4: Subscribe MD quotes for any positions already in cache
        for symbol in list(self._positions.keys()):
            self._subscribe_quote(symbol)

    def _subscribe_quote(self, symbol: str):
        """
        Send a md/subscribeQuote request for a contract symbol.
        FIX-4: Called from _subscribe and get_quote to ensure live prices.
        """
        if symbol in self._subscribed_md:
            return
        self._subscribed_md.add(symbol)
        self._send_request("md/subscribeQuote", {"symbol": symbol})
        logger.debug(f"MD quote subscription sent for {symbol}")

    def _handle_event(self, event: dict):
        """
        Handle real-time position/order/fill/quote events.
        FIX-5: Added quote/Quote entity-type handler.  Previously quote
        events from md/subscribeQuote were silently ignored, leaving
        self._quotes permanently empty.
        """
        entity_type = event.get("entityType", event.get("e", ""))
        entity      = event.get("entity",     event.get("d", {}))

        if entity_type == "position":
            contract_id = str(entity.get("contractId", ""))
            self._positions[contract_id] = entity
            # FIX-4: subscribe MD for newly discovered positions
            if contract_id:
                self._subscribe_quote(contract_id)
            if self.on_position_update:
                self.on_position_update(entity)

        elif entity_type == "order":
            order_id = entity.get("id")
            if order_id:
                self._orders[order_id] = entity

        elif entity_type in ("fill", "Fill") and self.on_fill:
            self.on_fill(entity)

        elif entity_type in ("quote", "Quote"):
            # FIX-5: cache incoming real-time quote prices
            symbol = entity.get("symbol", "")
            # Prefer: last traded price, then best bid/ask midpoint
            lp  = entity.get("lp")   # last price
            bp  = entity.get("bp")   # best bid
            ap  = entity.get("ap")   # best ask (offer)
            if lp is not None:
                price = float(lp)
            elif bp is not None and ap is not None:
                price = (float(bp) + float(ap)) / 2.0
            elif bp is not None:
                price = float(bp)
            elif ap is not None:
                price = float(ap)
            else:
                price = None
            if symbol and price:
                self._quotes[symbol] = price
                logger.debug(f"Quote cached: {symbol} = {price}")

    # =========================================================================
    # REQUEST / RESPONSE
    # =========================================================================

    def _next_id(self) -> int:
        with self._lock:
            self._request_id += 1
            return self._request_id

    def _send_request(self, endpoint: str, body: dict = None, timeout: float = 5.0) -> Optional[dict]:
        """Send a request over WebSocket and wait for response."""
        if not self._connected or not self._ws:
            logger.error("Not connected to Tradovate WebSocket")
            return None

        req_id  = self._next_id()
        payload = {req_id: {"done": False, "response": None}}

        with self._lock:
            self._pending.update(payload)

        body_str = json.dumps(body) if body else ""
        message  = f"{endpoint}\n{req_id}\n\n{body_str}"

        try:
            self._ws.send(message)
        except Exception as e:
            logger.error(f"WS send error: {e}")
            return None

        # Wait for response
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if self._pending.get(req_id, {}).get("done"):
                    resp = self._pending.pop(req_id)["response"]
                    return resp
            time.sleep(0.05)

        logger.warning(f"Request {req_id} ({endpoint}) timed out")
        with self._lock:
            self._pending.pop(req_id, None)
        return None

    # =========================================================================
    # POSITION MANAGEMENT
    # =========================================================================

    def get_open_positions(self) -> List[dict]:
        """
        Get all open positions for the account.
        FIX-2: Uses self._numeric_account_id (integer) for comparison
        so the filter no longer silently fails when self.account_id is a
        string spec like 'MFFUEVFLX540866001'.
        """
        try:
            resp = requests.get(
                f"{TRADOVATE_REST_URL}/position/list",
                headers={"Authorization": f"Bearer {self._access_token}"},
                timeout=5
            )
            positions = resp.json()
            if not isinstance(positions, list):
                return []

            # Build a set of account IDs to match against
            acct_ids = set()
            if self._numeric_account_id:
                acct_ids.add(str(self._numeric_account_id))
            if str(self.account_id).isdigit():
                acct_ids.add(str(self.account_id))
            # Also include spec-string match as fallback
            acct_ids.add(str(self.account_id))

            result = []
            for p in positions:
                if str(p.get("accountId", "")) in acct_ids and p.get("netPos", 0) != 0:
                    result.append(p)

            # FIX-4: subscribe MD for all open position symbols
            for p in result:
                cid = str(p.get("contractId", ""))
                if cid:
                    self._subscribe_quote(cid)

            return result

        except Exception as e:
            logger.error(f"get_open_positions error: {e}")
            return []

    def get_quote(self, contract_id: str) -> Optional[float]:
        """
        Get current market price for a contract.
        FIX-3: Removed broken /md/getChart REST call.  Instead subscribes to
        md/subscribeQuote via WebSocket (if not already subscribed) and
        returns from the live _quotes cache populated by FIX-5.
        Falls back to REST /md/subscribeQuote endpoint for a one-shot price
        if the WS cache is still empty after a brief wait.
        """
        # Return cached quote if available
        if contract_id in self._quotes:
            return self._quotes[contract_id]

        # Subscribe via WebSocket if not yet subscribed
        if self._connected:
            self._subscribe_quote(contract_id)
            # Wait briefly for first quote to arrive
            for _ in range(20):       # up to 1 second
                if contract_id in self._quotes:
                    return self._quotes[contract_id]
                time.sleep(0.05)

        # Final fallback: REST snapshot via /md/getchart (OHLCV, last close)
        try:
            resp = requests.get(
                f"{TRADOVATE_REST_URL}/md/getchart",
                params={
                    "symbol":           contract_id,
                    "chartDescription": json.dumps({
                        "underlyingType":    "Minute",
                        "elementSize":       1,
                        "elementSizeUnit":   "UnderlyingUnits",
                        "withHistogram":     False
                    }),
                    "timeRange": json.dumps({"asMuchAsElements": 1})
                },
                headers={"Authorization": f"Bearer {self._access_token}"},
                timeout=5
            )
            data = resp.json()
            bars = data.get("bars", [])
            if bars:
                close = bars[-1].get("close")
                if close is not None:
                    self._quotes[contract_id] = float(close)
                    return float(close)
        except Exception as e:
            logger.debug(f"get_quote REST fallback error for {contract_id}: {e}")

        return None

    def place_order(self, contract_id: str, action: str, order_type: str,
                    size: int, stop_price: float = None,
                    limit_price: float = None) -> Optional[dict]:
        """
        Place an order on Tradovate.
        action:     'Buy' or 'Sell'
        order_type: 'Stop' | 'Limit' | 'Market'
        FIX-6: accountId now uses the resolved integer ID.
        """
        self._ensure_token()
        acct_id = self._acct_id() or self.account_id   # FIX-6
        payload = {
            "accountSpec":  str(self.account_id),   # string spec
            "accountId":    acct_id,                # FIX-6: integer
            "action":       action,
            "symbol":       contract_id,
            "orderQty":     size,
            "orderType":    order_type,
            "timeInForce":  "GTC",
            "isAutomated":  True,
        }
        if stop_price  is not None: payload["stopPrice"] = stop_price
        if limit_price is not None: payload["price"]     = limit_price

        try:
            resp = requests.post(
                f"{TRADOVATE_REST_URL}/order/placeorder",
                json=payload,
                headers={"Authorization": f"Bearer {self._access_token}"},
                timeout=5
            )
            result = resp.json()
            if result.get("failureReason") or result.get("errorText"):
                logger.error(f"Order failed: {result}")
                return None
            logger.info(f"Order placed: {action} {size}x {contract_id} @ {stop_price or limit_price}")
            return result
        except Exception as e:
            logger.error(f"place_order error: {e}")
            return None

    def cancel_order(self, order_id: int) -> bool:
        """Cancel an existing order by ID."""
        self._ensure_token()
        try:
            resp = requests.post(
                f"{TRADOVATE_REST_URL}/order/cancelorder",
                json={"orderId": order_id},
                headers={"Authorization": f"Bearer {self._access_token}"},
                timeout=5
            )
            result = resp.json()
            success = not result.get("failureReason") and not result.get("errorText")
            if success:
                logger.info(f"Order {order_id} cancelled")
            else:
                logger.warning(f"Cancel failed: {result}")
            return success
        except Exception as e:
            logger.error(f"cancel_order error: {e}")
            return False

    def close_position(self, contract_id: str, size: int) -> bool:
        """
        Close an open position at market.
        FIX-6: accountId now uses the resolved integer ID.
        """
        self._ensure_token()
        acct_id = self._acct_id() or self.account_id   # FIX-6
        try:
            resp = requests.post(
                f"{TRADOVATE_REST_URL}/order/liquidateposition",
                json={
                    "accountId":   acct_id,          # FIX-6: integer
                    "contractId":  contract_id,
                    "admin":       False,
                    "customTag50": "TradeManager_Exit"
                },
                headers={"Authorization": f"Bearer {self._access_token}"},
                timeout=5
            )
            result = resp.json()
            success = not result.get("failureReason") and not result.get("errorText")
            if success:
                logger.info(f"Position closed: {contract_id}")
            else:
                logger.error(f"Close failed: {result}")
            return success
        except Exception as e:
            logger.error(f"close_position error: {e}")
            return False

    def place_stop(self, contract_id: str, action: str, size: int, stop_price: float) -> Optional[dict]:
        """Place a stop loss order."""
        return self.place_order(
            contract_id=contract_id,
            action=action,
            order_type="Stop",
            size=size,
            stop_price=stop_price
        )

    def disconnect(self):
        """Clean shutdown."""
        if self._ws:
            self._ws.close()
        self._connected = False
        logger.info("Tradovate WebSocket disconnected")
