"""
Webhook Middleware Server - Sits between TradingView and TradersPost.

Intercepts trading signals from TradingView, resolves continuous contract
tickers to the correct active contract month, and forwards the corrected
signal to TradersPost.

This fixes the "ContractNotActive" error from Topstep by ensuring that
signals always reference the currently active contract (e.g., MGCJ2026
instead of expired MGCG2026).

Usage:
    python webhook/server.py

Environment Variables:
    TRADERSPOST_WEBHOOK_URL - Your TradersPost webhook URL
    WEBHOOK_PORT            - Port to listen on (default: 8080)
    WEBHOOK_SECRET          - Optional shared secret for authentication
    LOG_LEVEL               - Logging level (default: INFO)
"""

import json
import logging
import os
import sys
import time
import hashlib
import hmac
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from typing import Optional

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.contract_resolver import ContractResolver, diagnose_contract_error


# Configuration
TRADERSPOST_WEBHOOK_URL = os.environ.get("TRADERSPOST_WEBHOOK_URL", "")
WEBHOOK_PORT = int(os.environ.get("WEBHOOK_PORT", "8080"))
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

# Setup logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("webhook-middleware")


class SignalProcessor:
    """
    Processes incoming trading signals and resolves contract tickers.
    
    Maintains a log of all processed signals for debugging and audit.
    """

    def __init__(self):
        self.resolver = ContractResolver()
        self.signal_log = []
        self.error_count = 0
        self.success_count = 0
        self.last_signal_time = None

    def process_signal(self, raw_signal: dict) -> dict:
        """
        Process an incoming signal: validate, resolve contracts, and prepare for forwarding.
        
        Args:
            raw_signal: The raw signal dict from TradingView
            
        Returns:
            Processed signal dict with resolved ticker
        """
        self.last_signal_time = datetime.now()
        
        logger.info(f"Processing signal: {json.dumps(raw_signal, indent=2)}")

        # Step 1: Validate required fields
        validation_errors = self._validate_signal(raw_signal)
        if validation_errors:
            self.error_count += 1
            return {
                "_status": "error",
                "_errors": validation_errors,
                "_original": raw_signal,
            }

        # Step 2: Resolve the contract ticker
        resolved_signal = self.resolver.resolve_signal_ticker(raw_signal)
        
        original_ticker = raw_signal.get("ticker", "")
        resolved_ticker = resolved_signal.get("ticker", "")
        
        if original_ticker != resolved_ticker:
            logger.warning(
                f"CONTRACT RESOLVED: {original_ticker} -> {resolved_ticker} "
                f"(would have caused ContractNotActive error!)"
            )
        else:
            logger.info(f"Ticker '{resolved_ticker}' is already the active contract")

        # Step 3: Validate the resolved contract
        if resolved_signal.get("_resolved") is False:
            error_msg = resolved_signal.get("_error", "Unknown resolution error")
            logger.error(f"Failed to resolve contract: {error_msg}")
            self.error_count += 1
            return {
                "_status": "error",
                "_errors": [error_msg],
                "_original": raw_signal,
            }

        # Step 4: Enforce max contracts (safety check)
        max_contracts = raw_signal.get("max_contracts", 1)
        quantity = int(raw_signal.get("quantity", 1))
        if quantity > max_contracts:
            logger.warning(
                f"Quantity {quantity} exceeds max_contracts {max_contracts}, "
                f"clamping to {max_contracts}"
            )
            resolved_signal["quantity"] = str(max_contracts)

        # Step 5: Add metadata
        resolved_signal["_processed_at"] = datetime.now().isoformat()
        resolved_signal["_status"] = "success"

        self.success_count += 1
        
        # Log the processed signal
        self.signal_log.append({
            "timestamp": datetime.now().isoformat(),
            "original_ticker": original_ticker,
            "resolved_ticker": resolved_ticker,
            "action": resolved_signal.get("action", ""),
            "instrument": resolved_signal.get("instrument", ""),
            "status": "resolved" if original_ticker != resolved_ticker else "pass-through",
        })

        logger.info(f"Signal processed successfully: {resolved_ticker} {resolved_signal.get('action', '')}")
        
        return resolved_signal

    def _validate_signal(self, signal: dict) -> list:
        """Validate that a signal has the minimum required fields."""
        errors = []
        
        if "ticker" not in signal and "instrument" not in signal:
            errors.append("Signal must contain 'ticker' or 'instrument' field")
        
        if "action" not in signal:
            errors.append("Signal must contain 'action' field (buy/sell/exit)")
        
        action = signal.get("action", "").lower()
        if action and action not in ("buy", "sell", "exit"):
            errors.append(f"Invalid action '{action}'. Must be buy, sell, or exit")

        return errors

    def get_stats(self) -> dict:
        """Get processing statistics."""
        return {
            "total_processed": self.success_count + self.error_count,
            "success_count": self.success_count,
            "error_count": self.error_count,
            "last_signal_time": self.last_signal_time.isoformat() if self.last_signal_time else None,
            "recent_signals": self.signal_log[-10:],  # Last 10 signals
        }


def forward_to_traderspost(signal: dict, webhook_url: str) -> dict:
    """
    Forward a processed signal to TradersPost.
    
    Args:
        signal: The processed signal dict
        webhook_url: TradersPost webhook URL
        
    Returns:
        Response from TradersPost
    """
    if not webhook_url:
        logger.warning("TRADERSPOST_WEBHOOK_URL not set - signal not forwarded")
        return {"status": "dry_run", "message": "No webhook URL configured"}

    # Remove internal metadata fields before forwarding
    forward_signal = {
        k: v for k, v in signal.items()
        if not k.startswith("_")
    }

    payload = json.dumps(forward_signal).encode("utf-8")
    
    logger.info(f"Forwarding to TradersPost: {json.dumps(forward_signal, indent=2)}")

    req = Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen(req, timeout=10) as response:
            response_body = response.read().decode("utf-8")
            logger.info(f"TradersPost response ({response.status}): {response_body}")
            return {
                "status": "forwarded",
                "http_status": response.status,
                "response": response_body,
            }
    except HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else ""
        logger.error(f"TradersPost HTTP error {e.code}: {error_body}")
        return {
            "status": "error",
            "http_status": e.code,
            "error": error_body,
        }
    except URLError as e:
        logger.error(f"TradersPost connection error: {e.reason}")
        return {
            "status": "error",
            "error": str(e.reason),
        }
    except Exception as e:
        logger.error(f"Unexpected error forwarding to TradersPost: {e}")
        return {
            "status": "error",
            "error": str(e),
        }


class WebhookHandler(BaseHTTPRequestHandler):
    """HTTP handler for webhook requests."""

    processor = SignalProcessor()

    def do_POST(self):
        """Handle incoming webhook POST requests."""
        # Read request body
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self._send_response(400, {"error": "Empty request body"})
            return

        body = self.rfile.read(content_length)

        # Verify webhook secret if configured
        if WEBHOOK_SECRET:
            signature = self.headers.get("X-Webhook-Signature", "")
            expected = hmac.new(
                WEBHOOK_SECRET.encode(), body, hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(signature, expected):
                logger.warning("Invalid webhook signature - rejecting request")
                self._send_response(401, {"error": "Invalid signature"})
                return

        # Parse JSON body
        try:
            signal = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON: {e}")
            self._send_response(400, {"error": f"Invalid JSON: {e}"})
            return

        # Route based on path
        if self.path == "/webhook" or self.path == "/":
            self._handle_signal(signal)
        elif self.path == "/diagnose":
            self._handle_diagnose(signal)
        else:
            self._send_response(404, {"error": f"Unknown path: {self.path}"})

    def do_GET(self):
        """Handle GET requests (health check and stats)."""
        if self.path == "/health":
            self._send_response(200, {
                "status": "healthy",
                "timestamp": datetime.now().isoformat(),
            })
        elif self.path == "/stats":
            self._send_response(200, self.processor.get_stats())
        elif self.path == "/contracts":
            resolver = self.processor.resolver
            contracts = {}
            for inst in resolver.instruments:
                try:
                    active = resolver.get_active_contract(inst)
                    contracts[inst] = {
                        "active_contract": active,
                        "continuous_ticker": resolver.instruments[inst].get("continuous_ticker", ""),
                    }
                except ValueError as e:
                    contracts[inst] = {"error": str(e)}
            self._send_response(200, contracts)
        else:
            self._send_response(404, {"error": f"Unknown path: {self.path}"})

    def _handle_signal(self, signal: dict):
        """Process and forward a trading signal."""
        # Process the signal (resolve contracts)
        processed = self.processor.process_signal(signal)

        if processed.get("_status") == "error":
            self._send_response(400, processed)
            return

        # Forward to TradersPost
        forward_result = forward_to_traderspost(processed, TRADERSPOST_WEBHOOK_URL)

        response = {
            "status": "processed",
            "original_ticker": signal.get("ticker", ""),
            "resolved_ticker": processed.get("ticker", ""),
            "contract_resolved": signal.get("ticker", "") != processed.get("ticker", ""),
            "forward_result": forward_result,
        }

        self._send_response(200, response)

    def _handle_diagnose(self, signal: dict):
        """Diagnose a ContractNotActive error."""
        diagnosis = diagnose_contract_error(signal)
        self._send_response(200, {
            "diagnosis": diagnosis,
            "signal": signal,
        })

    def _send_response(self, status_code: int, body: dict):
        """Send a JSON response."""
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body, indent=2).encode("utf-8"))

    def log_message(self, format, *args):
        """Override to use our logger."""
        logger.debug(f"HTTP: {format % args}")


def run_server(port: int = WEBHOOK_PORT):
    """Start the webhook middleware server."""
    server = HTTPServer(("0.0.0.0", port), WebhookHandler)

    logger.info(f"{'=' * 60}")
    logger.info(f"Contract Resolver Webhook Middleware")
    logger.info(f"{'=' * 60}")
    logger.info(f"Listening on port {port}")
    logger.info(f"TradersPost URL: {'configured' if TRADERSPOST_WEBHOOK_URL else 'NOT SET'}")
    logger.info(f"Webhook secret: {'configured' if WEBHOOK_SECRET else 'not set'}")
    logger.info(f"")
    logger.info(f"Endpoints:")
    logger.info(f"  POST /webhook   - Process and forward trading signals")
    logger.info(f"  POST /diagnose  - Diagnose ContractNotActive errors")
    logger.info(f"  GET  /health    - Health check")
    logger.info(f"  GET  /stats     - Processing statistics")
    logger.info(f"  GET  /contracts - Current active contracts")
    logger.info(f"{'=' * 60}")

    # Show current active contracts
    resolver = ContractResolver()
    logger.info("Current active contracts:")
    for inst in resolver.instruments:
        try:
            active = resolver.get_active_contract(inst)
            logger.info(f"  {inst:6s} -> {active}")
        except ValueError as e:
            logger.warning(f"  {inst:6s} -> ERROR: {e}")

    logger.info(f"{'=' * 60}")
    logger.info("Ready to process signals!")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        server.shutdown()


if __name__ == "__main__":
    run_server()
