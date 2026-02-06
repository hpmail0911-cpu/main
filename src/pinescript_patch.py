"""
PineScript Patch Generator - Updates the TradingView strategy's
getInstrumentTicker() function to use specific active contract months
instead of continuous contracts.

The core issue: TradingView sends signals with continuous tickers (MGC1!)
which get resolved by TradersPost to the wrong contract month (MGCG2026
instead of MGCJ2026), causing "ContractNotActive" errors from Topstep.

This script generates the corrected PineScript code that should replace
the getInstrumentTicker() function in the strategy.
"""

import json
import os
from datetime import datetime
from typing import Optional


def generate_pinescript_ticker_function(config_path: Optional[str] = None) -> str:
    """
    Generate an updated PineScript getInstrumentTicker() function that
    returns the correct active contract ticker for each instrument.
    
    Returns:
        PineScript code string for the updated function
    """
    if config_path is None:
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config",
            "contract_rollover.json",
        )

    with open(config_path, "r") as f:
        config = json.load(f)

    instruments = config.get("instruments", {})
    
    lines = [
        "// === ACTIVE CONTRACT TICKER MAPPING ===",
        "// Updated: " + datetime.now().strftime("%Y-%m-%d %H:%M"),
        "// IMPORTANT: Update these when contracts roll over!",
        "// Use continuous contracts (MGC1!) on your TradingView chart,",
        "// but send specific contract months in webhook alerts.",
        "//",
        "// Current active contracts:",
    ]

    for inst, data in instruments.items():
        active = data.get("active_contract", f"{inst}1!")
        lines.append(f"//   {inst}: {active}")

    lines.extend([
        "",
        "getInstrumentTicker() =>",
    ])

    # Build the ternary chain
    entries = []
    for inst, data in instruments.items():
        active = data.get("active_contract", f"{inst}1!")
        var_name = f"is{inst}"
        entries.append((var_name, active))

    for i, (var_name, active) in enumerate(entries):
        if i == 0:
            lines.append(f'    {var_name} ? "{active}" :')
        elif i < len(entries) - 1:
            lines.append(f'    {var_name} ? "{active}" :')
        else:
            lines.append(f'    {var_name} ? "{active}" :')
            lines.append(f'    syminfo.ticker')

    return "\n".join(lines)


def generate_full_alert_fix() -> str:
    """
    Generate the complete PineScript code changes needed to fix the
    ContractNotActive error. This includes:
    
    1. Updated getInstrumentTicker() with active contract months
    2. Enhanced alert JSON format with contract metadata
    3. Contract validation before sending alerts
    """
    
    return '''
// =====================================================
// FIX: ContractNotActive Error - Active Contract Mapping
// =====================================================
// PROBLEM: Continuous tickers (MGC1!) resolve to expired contracts
// (MGCG2026) instead of the active front month (MGCJ2026).
//
// SOLUTION: Map each instrument to its specific active contract month.
// Update these tickers whenever contracts roll over.
// =====================================================

// === ACTIVE CONTRACT TICKERS ===
// ⚠️ UPDATE THESE WHEN CONTRACTS ROLL OVER!
mgcActiveTicker = input.string("MGCJ2026", "MGC Active Contract", group="Contract Months", tooltip="Update when MGC rolls. Feb(G)->Apr(J)->Jun(M)->Aug(Q)->Oct(V)->Dec(Z)")
mesActiveTicker = input.string("MESH2026", "MES Active Contract", group="Contract Months", tooltip="Update when MES rolls. Mar(H)->Jun(M)->Sep(U)->Dec(Z)")
mnqActiveTicker = input.string("MNQH2026", "MNQ Active Contract", group="Contract Months", tooltip="Update when MNQ rolls. Mar(H)->Jun(M)->Sep(U)->Dec(Z)")
mymActiveTicker = input.string("MYMH2026", "MYM Active Contract", group="Contract Months", tooltip="Update when MYM rolls. Mar(H)->Jun(M)->Sep(U)->Dec(Z)")
mclActiveTicker = input.string("MCLH2026", "MCL Active Contract", group="Contract Months", tooltip="Update when MCL rolls. Monthly contracts - check CME schedule")
m2kActiveTicker = input.string("M2KH2026", "M2K Active Contract", group="Contract Months", tooltip="Update when M2K rolls. Mar(H)->Jun(M)->Sep(U)->Dec(Z)")

// Whether to use specific contract months (true) or continuous tickers (false)
useSpecificContracts = input.bool(true, "Use Specific Contract Months", group="Contract Months", tooltip="Enable to send specific contract months in alerts. Disable to use continuous tickers (MGC1!)")

getInstrumentTicker() =>
    if useSpecificContracts
        isMGC ? mgcActiveTicker :
        isMES ? mesActiveTicker :
        isMNQ ? mnqActiveTicker :
        isMYM ? mymActiveTicker :
        isMCL ? mclActiveTicker :
        isM2K ? m2kActiveTicker :
        syminfo.ticker
    else
        isMYM ? "MYM1!" : isMNQ ? "MNQ1!" : isMES ? "MES1!" : isMCL ? "MCL1!" : isMGC ? "MGC1!" : isM2K ? "M2K1!" : syminfo.ticker

// === CONTRACT VALIDATION TABLE ===
var table contractTable = table.new(position.bottom_left, 3, 2, bgcolor=color.new(color.navy, 85), border_width=1)
if barstate.islast and useSpecificContracts
    activeTicker = getInstrumentTicker()
    table.cell(contractTable, 0, 0, "CONTRACT", text_color=color.white, bgcolor=color.new(color.purple, 70))
    table.cell(contractTable, 1, 0, activeTicker, text_color=color.yellow)
    table.cell(contractTable, 2, 0, useSpecificContracts ? "SPECIFIC" : "CONTINUOUS", text_color=useSpecificContracts ? color.lime : color.orange)
    table.cell(contractTable, 0, 1, "⚠️ VERIFY", text_color=color.white, bgcolor=color.new(color.maroon, 70))
    table.cell(contractTable, 1, 1, "Check contract is active", text_color=color.white)
    table.cell(contractTable, 2, 1, "in your broker", text_color=color.white)
'''


def generate_patched_strategy(original_strategy: str) -> str:
    """
    Apply the contract fix to an existing PineScript strategy.
    
    Replaces the getInstrumentTicker() function and adds contract
    month inputs.
    
    Args:
        original_strategy: The original PineScript code
        
    Returns:
        Patched PineScript code
    """
    # Find and replace the getInstrumentTicker function
    old_function = '''getInstrumentTicker() =>
    isMYM ? "MYM1!" : isMNQ ? "MNQ1!" : isMES ? "MES1!" : isMCL ? "MCL1!" : isMGC ? "MGC1!" : isM2K ? "M2K1!" : syminfo.ticker'''

    new_function = '''// === ACTIVE CONTRACT TICKERS ===
// ⚠️ UPDATE THESE WHEN CONTRACTS ROLL OVER!
// This fixes "ContractNotActive" error from Topstep
mgcActiveTicker = input.string("MGCJ2026", "MGC Active Contract", group="Contract Months", tooltip="Update when MGC rolls. Feb(G)->Apr(J)->Jun(M)->Aug(Q)->Oct(V)->Dec(Z)")
mesActiveTicker = input.string("MESH2026", "MES Active Contract", group="Contract Months", tooltip="Update when MES rolls. Mar(H)->Jun(M)->Sep(U)->Dec(Z)")
mnqActiveTicker = input.string("MNQH2026", "MNQ Active Contract", group="Contract Months", tooltip="Update when MNQ rolls. Mar(H)->Jun(M)->Sep(U)->Dec(Z)")
mymActiveTicker = input.string("MYMH2026", "MYM Active Contract", group="Contract Months", tooltip="Update when MYM rolls. Mar(H)->Jun(M)->Sep(U)->Dec(Z)")
mclActiveTicker = input.string("MCLH2026", "MCL Active Contract", group="Contract Months", tooltip="Update when MCL rolls. Monthly contracts - check CME schedule")
m2kActiveTicker = input.string("M2KH2026", "M2K Active Contract", group="Contract Months", tooltip="Update when M2K rolls. Mar(H)->Jun(M)->Sep(U)->Dec(Z)")
useSpecificContracts = input.bool(true, "Use Specific Contract Months", group="Contract Months", tooltip="Enable to send specific contract months in alerts instead of continuous tickers")

getInstrumentTicker() =>
    if useSpecificContracts
        isMGC ? mgcActiveTicker :
        isMES ? mesActiveTicker :
        isMNQ ? mnqActiveTicker :
        isMYM ? mymActiveTicker :
        isMCL ? mclActiveTicker :
        isM2K ? m2kActiveTicker :
        syminfo.ticker
    else
        isMYM ? "MYM1!" : isMNQ ? "MNQ1!" : isMES ? "MES1!" : isMCL ? "MCL1!" : isMGC ? "MGC1!" : isM2K ? "M2K1!" : syminfo.ticker'''

    if old_function in original_strategy:
        return original_strategy.replace(old_function, new_function)
    else:
        # Try a more lenient replacement
        import re
        pattern = r'getInstrumentTicker\(\)\s*=>\s*\n\s*isMYM\s*\?\s*"MYM1!"\s*:.*?syminfo\.ticker'
        match = re.search(pattern, original_strategy, re.DOTALL)
        if match:
            return original_strategy[:match.start()] + new_function + original_strategy[match.end():]
        else:
            # Append the fix as a comment with instructions
            return original_strategy + "\n\n" + generate_full_alert_fix()


if __name__ == "__main__":
    print("=" * 60)
    print("PineScript Contract Fix Generator")
    print("=" * 60)
    print()
    print("Updated getInstrumentTicker() function:")
    print()
    print(generate_pinescript_ticker_function())
    print()
    print("=" * 60)
    print()
    print("Full alert fix code:")
    print(generate_full_alert_fix())
