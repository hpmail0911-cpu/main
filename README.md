# TL32 Trading Strategy - Contract Resolution Fix

## Problem: ContractNotActive Error from Topstep

**Error**: When sending trade signals through TradersPost to Topstep, the order was rejected with `ContractNotActive` because the strategy was sending continuous contract symbols (e.g., `MGC1!`) which resolved to expired contract months (e.g., `MGCG2026` - February 2026 Gold, which expired around Feb 3-4, 2026).

**Example of the failing signal**:
```json
{
    "ticker": "MGC1!",
    "action": "sell",
    "orderType": "stop_limit",
    "quantity": "1",
    "price": "4826.2"
}
```

TradersPost resolved `MGC1!` to `MGCG2026` (February 2026 Gold), but Topstep had already rolled to `MGCJ2026` (April 2026 Gold). The First Notice Day for gold futures is the last business day of the month before the contract month, so MGCG2026's FND was around January 30, 2026. Topstep does not allow trading past FND.

## Solution: Dynamic Front-Month Contract Resolution

The fix adds a **Contract Month Resolution System** that dynamically calculates the correct front-month contract symbol based on:

1. **Instrument type** (Gold, Equity Index, Crude Oil) - each has different contract months and expiry schedules
2. **Current date** - determines which contract is active
3. **Configurable roll timing** - adjustable days-before-expiry to trigger roll to next contract

### How It Works

Instead of sending `MGC1!` (continuous contract) in alert JSON, the strategy now sends the specific resolved contract, e.g., `MGCJ2026` (April 2026 Gold).

**Fixed signal example**:
```json
{
    "ticker": "MGCJ2026",
    "action": "sell",
    "orderType": "market",
    "quantity": 1,
    "instrument": "MGC",
    "contract": "MGCJ2026",
    "continuous_ref": "MGC1!"
}
```

### Contract Month Schedules

| Instrument | Type | Active Months | Month Codes |
|-----------|------|---------------|-------------|
| MGC/GC | Gold/Metals | Bi-monthly | G(Feb), J(Apr), M(Jun), Q(Aug), V(Oct), Z(Dec) |
| MES/ES | S&P 500 | Quarterly | H(Mar), M(Jun), U(Sep), Z(Dec) |
| MNQ/NQ | Nasdaq | Quarterly | H(Mar), M(Jun), U(Sep), Z(Dec) |
| MYM/YM | Dow Jones | Quarterly | H(Mar), M(Jun), U(Sep), Z(Dec) |
| M2K/RTY | Russell 2000 | Quarterly | H(Mar), M(Jun), U(Sep), Z(Dec) |
| MCL/CL | Crude Oil | Monthly | F,G,H,J,K,M,N,Q,U,V,X,Z |

### Roll Timing

The strategy rolls to the next contract BEFORE expiry to match Topstep's requirements:

| Instrument Type | Default Roll Timing | Roll Logic |
|----------------|-------------------|------------|
| **Gold** (MGC) | Day 20 of month before contract | Gold FND is last business day of prior month. Rolling on the 20th gives 5-7 business day buffer. |
| **Equity** (MES, MNQ, MYM, M2K) | Day 8 of contract month | Equity futures expire 3rd Friday (~15-21st). Rolling on the 8th gives ~1 week buffer. |
| **Crude** (MCL) | Day 15 of month before contract | Crude expires ~20th of prior month. Rolling on the 15th gives ~3-5 day buffer. |

### Configurable Settings

Under the **"Contract Settings"** input group in TradingView:

| Setting | Default | Description |
|---------|---------|-------------|
| Use Manual Contract Override | `false` | Enable to manually specify the contract suffix |
| Manual Contract Suffix | `""` | e.g., `J2026` for April 2026 (only when manual override enabled) |
| Gold Roll Day | `20` | Day of month before contract month to roll (range: 10-28) |
| Equity Roll Day | `8` | Day of contract month to roll (range: 1-15) |
| Crude Roll Day | `15` | Day of month before contract month to roll (range: 5-25) |
| Use Continuous Contract Fallback | `false` | Falls back to `MGC1!` style symbols (NOT recommended for Topstep) |

### Resolution Examples

**Gold (MGC) on February 6, 2026**:
- Month = 2, Day = 6
- Check: `mo < 1` → false; `mo == 1 and dy <= 20` → false
- Check: `mo < 3` → true
- Result: **April contract (J)** → `MGCJ2026` ✅

**MES on March 1, 2026**:
- Month = 3, Day = 1
- Check: `mo < 3` → false; `mo == 3 and dy <= 8` → true
- Result: **March contract (H)** → `MESH2026` ✅

**MES on March 10, 2026**:
- Month = 3, Day = 10
- Check: `mo < 3` → false; `mo == 3 and dy <= 8` → false
- Check: `mo < 6` → true
- Result: **June contract (M)** → `MESM2026` ✅

## Files

- `strategy.pine` - The complete fixed PineScript v5 strategy with contract resolution system

## Key Changes from V.5 to V.6

1. **Added Contract Month Resolution System** (`getGoldFrontMonth`, `getEquityFrontMonth`, `getCrudeFrontMonth`)
2. **Updated `getInstrumentTicker()`** - Now returns specific front-month contract instead of continuous contract
3. **Added new inputs** - Contract settings group with configurable roll timing
4. **Updated all alert JSON** - Both entry and exit alerts now include `"contract"` and `"continuous_ref"` fields
5. **Added visual display** - Two new table rows showing resolved contract and warning if using continuous fallback
6. **Strategy name updated** - `V.6 CONTRACT-FIX` to distinguish from previous version

## Important Notes

- **Keep your TradingView chart on continuous contracts** (`MGC1!`, `MES1!`, etc.) - this is required for proper charting and backtesting
- The contract resolution only affects the **alert JSON** sent to TradersPost
- If the automatic resolution picks the wrong contract (edge case around roll dates), use the **Manual Contract Override** setting
- The roll day settings should match your broker's (Topstep's) rollover schedule
- After a contract roll, you may need to adjust the roll day settings if Topstep's timing differs from defaults
