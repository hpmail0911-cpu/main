"""
Contract Resolver - Maps continuous futures tickers to active contract months.

Fixes the "ContractNotActive" error from Topstep by ensuring signals always
reference the correct active contract month instead of expired ones.

Example: MGC1! -> MGCJ2026 (not MGCG2026 which is expired)
"""

import json
import os
import re
from datetime import datetime, timedelta
from typing import Optional


# CME month codes
MONTH_TO_CODE = {
    1: "F", 2: "G", 3: "H", 4: "J", 5: "K", 6: "M",
    7: "N", 8: "Q", 9: "U", 10: "V", 11: "X", 12: "Z",
}
CODE_TO_MONTH = {v: k for k, v in MONTH_TO_CODE.items()}


class ContractResolver:
    """
    Resolves continuous futures tickers to the correct active contract month.
    
    Prevents "ContractNotActive" errors by:
    1. Mapping continuous contracts (MGC1!, MES1!) to specific active months
    2. Auto-detecting contract rollovers based on expiry schedules
    3. Validating that the target contract is actually tradeable
    """

    def __init__(self, config_path: Optional[str] = None):
        if config_path is None:
            config_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "config",
                "contract_rollover.json",
            )
        self.config_path = config_path
        self.config = self._load_config()
        self.instruments = self.config.get("instruments", {})

    def _load_config(self) -> dict:
        """Load the contract rollover configuration."""
        try:
            with open(self.config_path, "r") as f:
                return json.load(f)
        except FileNotFoundError:
            raise FileNotFoundError(
                f"Contract rollover config not found at {self.config_path}. "
                "Please create it with active contract information."
            )
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in contract rollover config: {e}")

    def reload_config(self):
        """Reload config from disk (useful for dynamic updates)."""
        self.config = self._load_config()
        self.instruments = self.config.get("instruments", {})

    def parse_ticker(self, ticker: str) -> dict:
        """
        Parse a futures ticker into its components.
        
        Examples:
            MGC1!    -> {"instrument": "MGC", "type": "continuous", "number": 1}
            MGCJ2026 -> {"instrument": "MGC", "type": "specific", "month": "J", "year": 2026}
            MGC      -> {"instrument": "MGC", "type": "root"}
        """
        # Continuous contract: MGC1!, MES1!, etc.
        continuous_match = re.match(r"^([A-Z0-9]+?)(\d+)!$", ticker)
        if continuous_match:
            return {
                "instrument": continuous_match.group(1),
                "type": "continuous",
                "number": int(continuous_match.group(2)),
            }

        # Specific contract: MGCJ2026, MESH2026, etc.
        specific_match = re.match(r"^([A-Z0-9]+?)([FGHJKMNQUVXZ])(\d{4})$", ticker)
        if specific_match:
            return {
                "instrument": specific_match.group(1),
                "type": "specific",
                "month_code": specific_match.group(2),
                "year": int(specific_match.group(3)),
            }

        # Root symbol: MGC, MES, etc.
        return {"instrument": ticker, "type": "root"}

    def get_active_contract(self, instrument: str, reference_date: Optional[datetime] = None) -> str:
        """
        Get the active (front-month) contract ticker for an instrument.
        
        Args:
            instrument: Root symbol (e.g., "MGC", "MES") or continuous ticker (e.g., "MGC1!")
            reference_date: Date to determine active contract (defaults to now)
            
        Returns:
            Active contract ticker (e.g., "MGCJ2026")
            
        Raises:
            ValueError: If instrument is not found in configuration
        """
        if reference_date is None:
            reference_date = datetime.now()

        # Extract root instrument from any ticker format
        parsed = self.parse_ticker(instrument)
        root = parsed["instrument"]

        if root not in self.instruments:
            raise ValueError(
                f"Unknown instrument '{root}'. "
                f"Known instruments: {list(self.instruments.keys())}"
            )

        inst_config = self.instruments[root]

        # First, check the static active_contract in config
        active = inst_config.get("active_contract", "")
        
        # Then try to auto-detect based on expiry schedule
        auto_detected = self._auto_detect_active(root, reference_date)
        if auto_detected:
            active = auto_detected

        return active

    def _auto_detect_active(self, instrument: str, reference_date: datetime) -> Optional[str]:
        """
        Auto-detect the active contract based on expiry schedule and first notice dates.
        
        For prop firms like Topstep, contracts become inactive for new positions
        well before the exchange expiry date. The rollover is determined by:
        1. First notice date (if available) - prop firms close before this
        2. Exchange expiry date minus rollover buffer
        
        Returns the first contract that is still active for new position entry.
        """
        inst_config = self.instruments.get(instrument, {})
        tradeable_months = inst_config.get("tradeable_months", [])
        rollover_days = inst_config.get("rollover_days_before_expiry", 3)

        # Check current year and next year
        current_year = reference_date.year
        for year_key_suffix in [str(current_year), str(current_year + 1)]:
            # Get both first notice dates and expiry schedules
            first_notice_key = f"first_notice_dates_{year_key_suffix}"
            expiry_key = f"expiry_schedule_{year_key_suffix}"
            
            first_notice_dates = inst_config.get(first_notice_key, {})
            expiry_schedule = inst_config.get(expiry_key, {})
            
            # Merge all contracts from both schedules
            all_contracts = set(list(first_notice_dates.keys()) + list(expiry_schedule.keys()))
            
            # Sort by expiry/notice date
            contracts_with_dates = []
            for contract_ticker in all_contracts:
                # Determine the effective cutoff date for this contract
                # Priority: first notice date (prop firm cutoff) > expiry - rollover
                cutoff_date = None
                
                if contract_ticker in first_notice_dates:
                    try:
                        notice_date = datetime.strptime(first_notice_dates[contract_ticker], "%Y-%m-%d")
                        # Prop firms typically stop new positions 2-3 days before first notice
                        cutoff_date = notice_date - timedelta(days=3)
                    except ValueError:
                        pass
                
                if cutoff_date is None and contract_ticker in expiry_schedule:
                    try:
                        expiry_date = datetime.strptime(expiry_schedule[contract_ticker], "%Y-%m-%d")
                        cutoff_date = expiry_date - timedelta(days=rollover_days)
                    except ValueError:
                        pass
                
                if cutoff_date:
                    # Also get expiry for sorting
                    sort_date = cutoff_date
                    if contract_ticker in expiry_schedule:
                        try:
                            sort_date = datetime.strptime(expiry_schedule[contract_ticker], "%Y-%m-%d")
                        except ValueError:
                            pass
                    contracts_with_dates.append((contract_ticker, cutoff_date, sort_date))
            
            # Sort by expiry date
            contracts_with_dates.sort(key=lambda x: x[2])
            
            for contract_ticker, cutoff_date, _ in contracts_with_dates:
                if reference_date < cutoff_date:
                    return contract_ticker

        return None

    def _compute_next_active(self, instrument: str, reference_date: datetime) -> str:
        """
        Compute the next active contract based on tradeable months.
        
        Fallback when expiry schedule is not available.
        """
        inst_config = self.instruments[instrument]
        tradeable_months = inst_config.get("tradeable_months", [])
        current_month = reference_date.month
        current_year = reference_date.year

        # Find the next tradeable month
        for month_code in tradeable_months:
            month_num = CODE_TO_MONTH[month_code]
            if month_num > current_month:
                return f"{instrument}{month_code}{current_year}"

        # Wrap to next year's first tradeable month
        first_month = tradeable_months[0]
        return f"{instrument}{first_month}{current_year + 1}"

    def resolve_signal_ticker(self, signal: dict) -> dict:
        """
        Resolve the ticker in a trading signal to the correct active contract.
        
        Takes a signal dict (from TradingView/webhook) and returns a corrected
        version with the proper active contract ticker.
        
        Args:
            signal: Dict containing at minimum a "ticker" field
            
        Returns:
            Updated signal dict with resolved ticker
        """
        signal = signal.copy()
        ticker = signal.get("ticker", "")
        instrument = signal.get("instrument", "")

        if not ticker:
            return signal

        parsed = self.parse_ticker(ticker)

        # Only resolve continuous contracts and root symbols
        if parsed["type"] in ("continuous", "root"):
            root = parsed["instrument"]
            try:
                active = self.get_active_contract(root)
                signal["ticker"] = active
                signal["_original_ticker"] = ticker
                signal["_resolved"] = True
                signal["_active_contract"] = active
            except ValueError:
                signal["_resolved"] = False
                signal["_error"] = f"Unknown instrument: {root}"
        elif parsed["type"] == "specific":
            # Validate that this specific contract is actually active
            root = parsed["instrument"]
            try:
                active = self.get_active_contract(root)
                if ticker != active:
                    signal["_warning"] = (
                        f"Signal references {ticker} but active contract is {active}. "
                        f"Updating to active contract."
                    )
                    signal["_original_ticker"] = ticker
                    signal["ticker"] = active
                    signal["_resolved"] = True
                    signal["_active_contract"] = active
                else:
                    signal["_resolved"] = True
            except ValueError:
                signal["_resolved"] = False

        return signal

    def is_contract_active(self, ticker: str, reference_date: Optional[datetime] = None) -> bool:
        """
        Check if a specific contract ticker is the current active contract.
        
        Args:
            ticker: Specific contract ticker (e.g., "MGCG2026")
            reference_date: Date to check against
            
        Returns:
            True if the contract is the current active front month
        """
        if reference_date is None:
            reference_date = datetime.now()

        parsed = self.parse_ticker(ticker)
        if parsed["type"] != "specific":
            return False

        root = parsed["instrument"]
        try:
            active = self.get_active_contract(root, reference_date)
            return ticker == active
        except ValueError:
            return False

    def get_contract_status(self, ticker: str) -> dict:
        """
        Get detailed status information about a contract.
        
        Returns:
            Dict with contract status details including whether it's active,
            expired, or upcoming.
        """
        now = datetime.now()
        parsed = self.parse_ticker(ticker)
        
        if parsed["type"] != "specific":
            return {
                "ticker": ticker,
                "status": "unknown",
                "message": "Not a specific contract ticker",
            }

        root = parsed["instrument"]
        inst_config = self.instruments.get(root, {})
        
        if not inst_config:
            return {
                "ticker": ticker,
                "status": "unknown",
                "message": f"Unknown instrument: {root}",
            }

        try:
            active = self.get_active_contract(root, now)
        except ValueError:
            active = None

        # Find expiry date for this contract
        expiry_str = None
        for year_suffix in [str(now.year), str(now.year + 1)]:
            schedule_key = f"expiry_schedule_{year_suffix}"
            schedule = inst_config.get(schedule_key, {})
            if ticker in schedule:
                expiry_str = schedule[ticker]
                break

        expiry_date = None
        if expiry_str:
            try:
                expiry_date = datetime.strptime(expiry_str, "%Y-%m-%d")
            except ValueError:
                pass

        is_active = ticker == active
        is_expired = expiry_date is not None and now > expiry_date

        status = "active" if is_active else ("expired" if is_expired else "upcoming")

        return {
            "ticker": ticker,
            "instrument": root,
            "status": status,
            "is_active": is_active,
            "is_expired": is_expired,
            "expiry_date": expiry_str,
            "active_contract": active,
            "message": (
                f"{ticker} is {'the active' if is_active else 'NOT the active'} contract. "
                f"Active contract: {active}"
            ),
        }

    def update_active_contract(self, instrument: str, new_contract: str):
        """
        Update the active contract in the configuration file.
        
        Args:
            instrument: Root symbol (e.g., "MGC")
            new_contract: New active contract ticker (e.g., "MGCJ2026")
        """
        if instrument not in self.instruments:
            raise ValueError(f"Unknown instrument: {instrument}")

        parsed = self.parse_ticker(new_contract)
        if parsed["type"] != "specific":
            raise ValueError(f"Invalid contract ticker: {new_contract}")

        old_contract = self.instruments[instrument].get("active_contract", "")

        self.instruments[instrument]["active_contract"] = new_contract
        self.instruments[instrument]["active_month_code"] = parsed["month_code"]
        self.instruments[instrument]["active_year"] = parsed["year"]
        self.instruments[instrument]["previous_contract"] = old_contract

        # Compute next contract
        tradeable_months = self.instruments[instrument].get("tradeable_months", [])
        current_idx = tradeable_months.index(parsed["month_code"]) if parsed["month_code"] in tradeable_months else -1
        if current_idx >= 0 and current_idx < len(tradeable_months) - 1:
            next_code = tradeable_months[current_idx + 1]
            self.instruments[instrument]["next_contract"] = f"{instrument}{next_code}{parsed['year']}"
        elif current_idx == len(tradeable_months) - 1:
            next_code = tradeable_months[0]
            self.instruments[instrument]["next_contract"] = f"{instrument}{next_code}{parsed['year'] + 1}"

        # Save back to config
        self.config["instruments"] = self.instruments
        self.config["_updated"] = datetime.now().strftime("%Y-%m-%d")
        
        with open(self.config_path, "w") as f:
            json.dump(self.config, f, indent=2)


def diagnose_contract_error(signal: dict) -> str:
    """
    Diagnose a ContractNotActive error and return a human-readable explanation.
    
    Args:
        signal: The original signal dict that caused the error
        
    Returns:
        Diagnostic message explaining the issue and fix
    """
    resolver = ContractResolver()
    ticker = signal.get("ticker", "Unknown")
    instrument = signal.get("instrument", "")

    parsed = resolver.parse_ticker(ticker)
    root = parsed.get("instrument", instrument)

    lines = [
        f"=== ContractNotActive Error Diagnosis ===",
        f"Signal ticker: {ticker}",
        f"Instrument: {root}",
    ]

    if root in resolver.instruments:
        status = resolver.get_contract_status(ticker)
        active = resolver.get_active_contract(root)

        lines.extend([
            f"Contract status: {status['status']}",
            f"Active contract: {active}",
            f"",
            f"DIAGNOSIS: The signal tried to trade '{ticker}' but the active",
            f"contract is '{active}'. The contract '{ticker}' is {status['status']}.",
            f"",
            f"FIX: Update your TradingView alerts or webhook middleware to",
            f"send '{active}' instead of '{ticker}'.",
        ])

        if parsed["type"] == "continuous":
            lines.extend([
                f"",
                f"NOTE: Continuous contracts ({ticker}) should be resolved to",
                f"specific active months by the webhook middleware.",
                f"Use the ContractResolver to automatically map {ticker} -> {active}",
            ])
    else:
        lines.extend([
            f"",
            f"WARNING: Instrument '{root}' not found in configuration.",
            f"Add it to config/contract_rollover.json",
        ])

    return "\n".join(lines)


if __name__ == "__main__":
    # Demonstrate the fix for the user's specific error
    print("=" * 60)
    print("ContractNotActive Error Fix - Diagnosis")
    print("=" * 60)
    print()

    # The signal that caused the error
    error_signal = {
        "ticker": "MGC1!",
        "action": "sell",
        "orderType": "stop_limit",
        "quantity": "1",
        "price": "4826.2",
        "stopPrice": "4907.7",
        "limitPrice": "4913.2",
        "take_profit": "4788.1",
        "strategy": "MTF_PROTECTED",
        "timeframe": "60",
        "instrument": "MGC",
        "signal_type": "STRONG",
    }

    resolver = ContractResolver()

    # Diagnose the error
    print(diagnose_contract_error(error_signal))
    print()

    # Show the fix
    print("=" * 60)
    print("Resolving signal to active contract...")
    print("=" * 60)
    resolved = resolver.resolve_signal_ticker(error_signal)
    print(f"Original ticker:  {error_signal['ticker']}")
    print(f"Resolved ticker:  {resolved['ticker']}")
    print(f"Active contract:  {resolved.get('_active_contract', 'N/A')}")
    print()

    # Show all active contracts
    print("=" * 60)
    print("Current Active Contracts")
    print("=" * 60)
    for inst in resolver.instruments:
        try:
            active = resolver.get_active_contract(inst)
            print(f"  {inst:6s} -> {active}")
        except ValueError as e:
            print(f"  {inst:6s} -> ERROR: {e}")
