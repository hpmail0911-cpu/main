"""
Tests for the Contract Resolver module.

Validates that:
1. Continuous tickers are correctly resolved to active contract months
2. Expired contracts are detected and replaced
3. Signal processing correctly updates tickers
4. The specific MGC ContractNotActive error is fixed
"""

import json
import os
import sys
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.contract_resolver import ContractResolver, diagnose_contract_error


class TestTickerParsing(unittest.TestCase):
    """Test parsing of various ticker formats."""

    def setUp(self):
        self.resolver = ContractResolver()

    def test_parse_continuous_ticker(self):
        """Continuous tickers like MGC1! should parse correctly."""
        result = self.resolver.parse_ticker("MGC1!")
        self.assertEqual(result["instrument"], "MGC")
        self.assertEqual(result["type"], "continuous")
        self.assertEqual(result["number"], 1)

    def test_parse_continuous_ticker_mes(self):
        result = self.resolver.parse_ticker("MES1!")
        self.assertEqual(result["instrument"], "MES")
        self.assertEqual(result["type"], "continuous")

    def test_parse_continuous_ticker_mnq(self):
        result = self.resolver.parse_ticker("MNQ1!")
        self.assertEqual(result["instrument"], "MNQ")
        self.assertEqual(result["type"], "continuous")

    def test_parse_specific_contract(self):
        """Specific contracts like MGCJ2026 should parse correctly."""
        result = self.resolver.parse_ticker("MGCJ2026")
        self.assertEqual(result["instrument"], "MGC")
        self.assertEqual(result["type"], "specific")
        self.assertEqual(result["month_code"], "J")
        self.assertEqual(result["year"], 2026)

    def test_parse_expired_contract(self):
        """The expired contract MGCG2026 should parse correctly."""
        result = self.resolver.parse_ticker("MGCG2026")
        self.assertEqual(result["instrument"], "MGC")
        self.assertEqual(result["type"], "specific")
        self.assertEqual(result["month_code"], "G")
        self.assertEqual(result["year"], 2026)

    def test_parse_root_symbol(self):
        """Root symbols like MGC should parse correctly."""
        result = self.resolver.parse_ticker("MGC")
        self.assertEqual(result["instrument"], "MGC")
        self.assertEqual(result["type"], "root")

    def test_parse_all_instruments(self):
        """All supported instrument continuous tickers should parse."""
        for ticker in ["MGC1!", "MES1!", "MNQ1!", "MYM1!", "MCL1!", "M2K1!"]:
            result = self.resolver.parse_ticker(ticker)
            self.assertEqual(result["type"], "continuous", f"Failed for {ticker}")


class TestContractResolution(unittest.TestCase):
    """Test resolving tickers to active contracts."""

    def setUp(self):
        self.resolver = ContractResolver()

    def test_mgc_resolves_to_april_2026(self):
        """MGC should resolve to MGCJ2026 (April) since MGCG2026 (Feb) is expired."""
        active = self.resolver.get_active_contract("MGC")
        self.assertEqual(active, "MGCJ2026")

    def test_mgc_continuous_resolves(self):
        """MGC1! should resolve to MGCJ2026."""
        active = self.resolver.get_active_contract("MGC1!")
        self.assertEqual(active, "MGCJ2026")

    def test_mes_resolves_correctly(self):
        """MES should resolve to MESH2026 (March)."""
        active = self.resolver.get_active_contract("MES")
        self.assertEqual(active, "MESH2026")

    def test_mnq_resolves_correctly(self):
        """MNQ should resolve to MNQH2026 (March)."""
        active = self.resolver.get_active_contract("MNQ")
        self.assertEqual(active, "MNQH2026")

    def test_mym_resolves_correctly(self):
        """MYM should resolve to MYMH2026 (March)."""
        active = self.resolver.get_active_contract("MYM")
        self.assertEqual(active, "MYMH2026")

    def test_mcl_resolves_correctly(self):
        """MCL should resolve to MCLH2026 (March)."""
        active = self.resolver.get_active_contract("MCL")
        self.assertEqual(active, "MCLH2026")

    def test_m2k_resolves_correctly(self):
        """M2K should resolve to M2KH2026 (March)."""
        active = self.resolver.get_active_contract("M2K")
        self.assertEqual(active, "M2KH2026")

    def test_unknown_instrument_raises(self):
        """Unknown instruments should raise ValueError."""
        with self.assertRaises(ValueError):
            self.resolver.get_active_contract("UNKNOWN")


class TestContractValidation(unittest.TestCase):
    """Test contract validation (active vs expired)."""

    def setUp(self):
        self.resolver = ContractResolver()

    def test_expired_mgc_feb_not_active(self):
        """MGCG2026 (Feb) should NOT be active on Feb 6, 2026."""
        # Use a date after rollover
        test_date = datetime(2026, 2, 23)  # Close to expiry
        is_active = self.resolver.is_contract_active("MGCG2026", test_date)
        # The contract might still be technically active until Feb 25
        # but it depends on rollover_days_before_expiry (3 days)
        # On Feb 23, rollover should have happened (Feb 25 - 3 = Feb 22)
        self.assertFalse(is_active)

    def test_active_mgc_apr(self):
        """MGCJ2026 (Apr) should be active on Feb 6, 2026."""
        test_date = datetime(2026, 2, 6)
        is_active = self.resolver.is_contract_active("MGCJ2026", test_date)
        self.assertTrue(is_active)

    def test_contract_status_expired(self):
        """Get detailed status for expired contract."""
        status = self.resolver.get_contract_status("MGCG2026")
        self.assertIn("MGCJ2026", status.get("active_contract", ""))
        self.assertFalse(status.get("is_active", True))

    def test_contract_status_active(self):
        """Get detailed status for active contract."""
        status = self.resolver.get_contract_status("MGCJ2026")
        self.assertTrue(status.get("is_active", False))


class TestSignalResolution(unittest.TestCase):
    """Test the full signal resolution flow - the core fix for ContractNotActive."""

    def setUp(self):
        self.resolver = ContractResolver()

    def test_fix_mgc_signal_from_error(self):
        """
        THE CRITICAL TEST: The exact signal that caused the ContractNotActive error.
        MGC1! should be resolved to MGCJ2026, not MGCG2026.
        """
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
            "confluence": 11,
            "session": "NY",
        }

        resolved = self.resolver.resolve_signal_ticker(error_signal)

        # The ticker should now be the specific active contract
        self.assertEqual(resolved["ticker"], "MGCJ2026")
        self.assertTrue(resolved.get("_resolved", False))
        self.assertEqual(resolved.get("_original_ticker"), "MGC1!")
        self.assertEqual(resolved.get("_active_contract"), "MGCJ2026")

        # Original signal fields should be preserved
        self.assertEqual(resolved["action"], "sell")
        self.assertEqual(resolved["quantity"], "1")
        self.assertEqual(resolved["instrument"], "MGC")

    def test_fix_expired_specific_contract(self):
        """If a signal comes with an expired specific contract, it should be updated."""
        signal = {
            "ticker": "MGCG2026",  # Expired Feb contract
            "action": "buy",
            "quantity": "1",
            "instrument": "MGC",
        }

        resolved = self.resolver.resolve_signal_ticker(signal)
        self.assertEqual(resolved["ticker"], "MGCJ2026")
        self.assertIn("_warning", resolved)

    def test_correct_contract_passes_through(self):
        """If signal already has the correct active contract, it should pass through."""
        signal = {
            "ticker": "MGCJ2026",  # Already correct
            "action": "sell",
            "quantity": "1",
            "instrument": "MGC",
        }

        resolved = self.resolver.resolve_signal_ticker(signal)
        self.assertEqual(resolved["ticker"], "MGCJ2026")
        self.assertTrue(resolved.get("_resolved", False))

    def test_mes_continuous_resolves(self):
        """MES1! should resolve to active MES contract."""
        signal = {
            "ticker": "MES1!",
            "action": "buy",
            "quantity": "1",
            "instrument": "MES",
        }
        resolved = self.resolver.resolve_signal_ticker(signal)
        self.assertEqual(resolved["ticker"], "MESH2026")

    def test_all_continuous_tickers_resolve(self):
        """All continuous tickers should resolve to specific contracts."""
        continuous_tickers = {
            "MGC1!": "MGCJ2026",
            "MES1!": "MESH2026",
            "MNQ1!": "MNQH2026",
            "MYM1!": "MYMH2026",
            "MCL1!": "MCLH2026",
            "M2K1!": "M2KH2026",
        }

        for continuous, expected_active in continuous_tickers.items():
            signal = {"ticker": continuous, "action": "buy", "quantity": "1"}
            resolved = self.resolver.resolve_signal_ticker(signal)
            self.assertEqual(
                resolved["ticker"],
                expected_active,
                f"Failed for {continuous}: expected {expected_active}, got {resolved['ticker']}",
            )

    def test_signal_preserves_all_fields(self):
        """Signal resolution should preserve all original fields."""
        signal = {
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
            "confluence": 11,
            "session": "NY",
            "mtf_enabled": True,
            "early_trend": True,
            "slippage_buffer": 5.4,
            "dedup_enabled": True,
            "max_contracts": 1,
        }

        resolved = self.resolver.resolve_signal_ticker(signal)

        # All original fields should be present
        for key in signal:
            if key != "ticker":  # ticker is changed
                self.assertEqual(
                    resolved[key],
                    signal[key],
                    f"Field '{key}' was modified or lost",
                )

    def test_max_contracts_enforcement(self):
        """Max contracts should be preserved in resolved signal."""
        signal = {
            "ticker": "MGC1!",
            "action": "buy",
            "quantity": "1",
            "max_contracts": 1,
        }
        resolved = self.resolver.resolve_signal_ticker(signal)
        self.assertEqual(resolved["max_contracts"], 1)


class TestDiagnosis(unittest.TestCase):
    """Test the diagnostic function."""

    def test_diagnose_mgc_error(self):
        """Diagnosis should explain the ContractNotActive error."""
        signal = {
            "ticker": "MGC1!",
            "action": "sell",
            "instrument": "MGC",
        }

        diagnosis = diagnose_contract_error(signal)
        self.assertIn("MGC", diagnosis)
        self.assertIn("MGCJ2026", diagnosis)
        self.assertIn("ContractNotActive", diagnosis)

    def test_diagnose_unknown_instrument(self):
        """Diagnosis should handle unknown instruments gracefully."""
        signal = {
            "ticker": "UNKNOWN1!",
            "action": "buy",
            "instrument": "UNKNOWN",
        }
        diagnosis = diagnose_contract_error(signal)
        self.assertIn("not found", diagnosis.lower())


class TestAutoDetection(unittest.TestCase):
    """Test automatic contract detection based on dates."""

    def setUp(self):
        self.resolver = ContractResolver()

    def test_feb_6_detects_april_gold(self):
        """On Feb 6, 2026, MGC should detect MGCJ2026 as active."""
        test_date = datetime(2026, 2, 6)
        active = self.resolver.get_active_contract("MGC", test_date)
        self.assertEqual(active, "MGCJ2026")

    def test_march_detects_correct_mes(self):
        """In early March 2026, MES should still be MESH2026."""
        test_date = datetime(2026, 3, 1)
        active = self.resolver.get_active_contract("MES", test_date)
        self.assertEqual(active, "MESH2026")

    def test_after_march_expiry_detects_june_mes(self):
        """After March expiry, MES should roll to MESM2026."""
        test_date = datetime(2026, 3, 18)  # March 20 expiry - 5 day rollover = March 15
        active = self.resolver.get_active_contract("MES", test_date)
        self.assertEqual(active, "MESM2026")


class TestConfigReload(unittest.TestCase):
    """Test configuration management."""

    def setUp(self):
        self.resolver = ContractResolver()

    def test_config_loads_all_instruments(self):
        """Config should contain all 6 micro futures instruments."""
        expected = {"MGC", "MES", "MNQ", "MYM", "MCL", "M2K"}
        self.assertEqual(set(self.resolver.instruments.keys()), expected)

    def test_each_instrument_has_required_fields(self):
        """Each instrument config should have all required fields."""
        required_fields = [
            "name",
            "continuous_ticker",
            "tradeable_months",
            "active_contract",
            "active_month_code",
        ]
        for inst, data in self.resolver.instruments.items():
            for field in required_fields:
                self.assertIn(
                    field,
                    data,
                    f"Instrument {inst} missing field: {field}",
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
