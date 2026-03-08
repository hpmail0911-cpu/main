#!/usr/bin/env python3
"""
TRADERSPOST WEBHOOK CLIENT — MFFU Account
Sends risk management actions to MFFU via TradersPost webhooks.

TradersPost is MFFU's official automation partner. There is no direct
Tradovate REST/WebSocket API available for prop-firm hosted accounts.
This client fires webhook signals to TradersPost which routes them to
the MFFU Tradovate account.

SUPPORTED ACTIONS:
  - close_position  → exit + sentiment=flat  (MAE, auto-shutdown)
  - place_stop      → stop order with cancel=true (BE, trailing)

POSITION DATA / PRICES:
  TradersPost does not expose position state or market quotes.
  The trade manager mirrors ProjectX positions to MFFU and uses
  ProjectX quotes for live pricing (same underlying instruments).

SETUP (one-time, in TradersPost dashboard):
  1. Connect MFFU account to TradersPost
  2. Create a new Strategy → copy the Webhook URL
  3. Strategy settings:
       - Asset class : Futures
       - Sides       : Both (bullish + bearish)
       - Auto submit : ON
       - Use signal quantity: ON
       - Allow add to position: OFF
  4. Add TRADERSPOST_WEBHOOK_URL to your .env file
"""

import logging
import requests
from typing import Optional

logger = logging.getLogger(__name__)

TRADERSPOST_HOST = "https://webhooks.traderspost.io"


class TradersPostClient:
    """
    Thin webhook client for TradersPost → MFFU/Tradovate.

    All methods match the interface used in trade_manager_AUTOMATED.py
    so it can be swapped in place of TradovateClient with zero changes
    to the manager logic.
    """

    MFFU_WEBHOOK_URL = (
        "https://webhooks.traderspost.io/trading/webhook/"
        "67867e2e-5bdf-4395-a96f-5d811d06dbd4/1afa43cae5775d38abc93b5eae7481a8"
    )

    def __init__(self, webhook_url: str = None):
        """
        Args:
            webhook_url: Full TradersPost webhook URL including uuid and
                         password, e.g.:
                         https://webhooks.traderspost.io/trading/webhook
                         /{uuid}/{password}
        """
        if webhook_url is None:
            webhook_url = self.MFFU_WEBHOOK_URL
        if not webhook_url or "webhooks.traderspost.io" not in webhook_url:
            raise ValueError(
                "Invalid TradersPost webhook URL. "
                "Copy the full URL from your TradersPost strategy dashboard."
            )
        self.webhook_url = webhook_url
        logger.info(f"✅ TradersPost client ready — webhook configured")

    # =========================================================================
    # LIFECYCLE — no-ops (webhooks are stateless)
    # =========================================================================

    def authenticate(self) -> bool:
        """No auth required for TradersPost webhooks."""
        return True

    def connect(self) -> bool:
        """No persistent connection needed."""
        return True

    def disconnect(self):
        """No connection to close."""
        pass

    # =========================================================================
    # POSITION DATA — not available via TradersPost
    # =========================================================================

    def get_open_positions(self) -> list:
        """
        TradersPost does not expose position state.
        Returns empty list — positions are sourced from ProjectX mirror.
        """
        return []

    def get_quote(self, contract_id: str) -> Optional[float]:
        """
        TradersPost does not provide market quotes.
        Returns None — prices are sourced from ProjectX quote cache.
        """
        return None

    # =========================================================================
    # WEBHOOK SIGNAL SENDER
    # =========================================================================

    def _send(self, payload: dict) -> bool:
        """
        POST a JSON signal to the TradersPost webhook URL.
        Returns True if TradersPost accepted it (success=true).
        """
        try:
            resp = requests.post(
                self.webhook_url,
                json=payload,
                timeout=10
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("success"):
                logger.info(
                    f"[TP] ✅ Webhook accepted — "
                    f"ticker={payload.get('ticker')} "
                    f"action={payload.get('action')} "
                    f"orderType={payload.get('orderType', 'market')} "
                    f"stopPrice={payload.get('stopPrice', '')}"
                )
                return True
            else:
                logger.error(
                    f"[TP] ❌ Webhook rejected — {data} | payload={payload}"
                )
                return False

        except requests.exceptions.Timeout:
            logger.error(f"[TP] Webhook timeout for {payload.get('ticker')}")
            return False
        except Exception as e:
            logger.error(f"[TP] Webhook error: {e} | payload={payload}")
            return False

    # =========================================================================
    # STOP MANAGEMENT
    # =========================================================================

    def place_stop(self, ticker: str, direction: str,
                   stop_price: float, size: int) -> bool:
        """
        Cancel any existing stop orders for the ticker and place a new
        stop-loss at stop_price.

        TradersPost "cancel=true" automatically cancels open orders for
        the ticker before submitting the new stop, making this idempotent.

        Args:
            ticker    : Instrument symbol, e.g. "MNQ", "MES", "MGC"
            direction : "LONG" or "SHORT"
            stop_price: New stop price
            size      : Number of contracts
        """
        # Stop order to close the position:
        #   LONG  → Sell stop below current price
        #   SHORT → Buy  stop above current price
        action = "sell" if direction.upper() == "LONG" else "buy"

        payload = {
            "ticker":    ticker,
            "action":    action,
            "sentiment": "flat",
            "orderType": "stop",
            "stopPrice": round(stop_price, 4),
            "quantity":  size,
            "cancel":    True,
        }
        logger.info(
            f"[TP] Placing stop — {ticker} {direction} x{size} "
            f"@ {stop_price:.4f}"
        )
        return self._send(payload)

    def cancel_order(self, ticker: str) -> bool:
        """
        Cancel all open orders for a ticker.
        TradersPost uses ticker-scoped cancel, not order-ID cancel.
        """
        payload = {
            "ticker": ticker,
            "action": "cancel",
        }
        logger.info(f"[TP] Cancelling open orders for {ticker}")
        return self._send(payload)

    # =========================================================================
    # POSITION CLOSE
    # =========================================================================

    def close_position(self, ticker: str, size: int) -> bool:
        """
        Close (flatten) the open position for ticker at market.

        Uses action=exit + sentiment=flat which always exits the full
        position regardless of direction — safe for both LONG and SHORT.

        Args:
            ticker: Instrument symbol, e.g. "MNQ"
            size  : Passed for logging only; TradersPost exits full position
        """
        payload = {
            "ticker":    ticker,
            "action":    "exit",
            "sentiment": "flat",
            "orderType": "market",
        }
        logger.warning(
            f"[TP] Closing position — {ticker} x{size} at market"
        )
        return self._send(payload)
