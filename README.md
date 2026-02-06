# MES 5m Alert: Stop Calculation Error Fix

## Problem

TradingView throws a **"Stop Calculation Error"** when creating alerts on the MES 5m chart.

## Root Cause

`strategy.exit()` receives `profit=0` when ATR-based tick calculations round to zero during
low-volatility periods. For MES 5m specifically, the MES MJV6 profit logic calculates:

```
tp1Points = math.round((atr10 * 0.375 * 0.4) / syminfo.mintick) = math.round(atr10 * 0.6)
```

When `atr10` (ATR period 10) drops below ~0.42 points — common during lunch hours (12-2 PM ET),
overnight sessions, or early chart bars — this rounds to **0**, causing the error.

## Solution

A `safeTicks()` helper function ensures all profit tick values are always >= 1:

```pine
safeTicks(pts) =>
    math.max(1, nz(math.round(pts / syminfo.mintick)))
```

This is applied to all ~40 profit tick calculations across every profit logic module
(MJV6 Ultra, V20, MES MJV6, V71, Kenya).

## Files

| File | Purpose |
|------|---------|
| `FIX_STOP_CALCULATION_ERROR.md` | Detailed root cause analysis and every line that needs changing |
| `apply_fix.py` | Automated Python script to apply the fix to the original Pine Script |

## How to Apply

### Option 1: Automated (recommended)

1. Save your current strategy code to a file (e.g., `my_strategy.pine`)
2. Run: `python3 apply_fix.py my_strategy.pine fixed_strategy.pine`
3. Copy the contents of `fixed_strategy.pine` into TradingView

### Option 2: Manual

Follow the step-by-step instructions in `FIX_STOP_CALCULATION_ERROR.md`.

### Quick Manual Fix (MES 5m only)

If you only need to fix MES 5m and don't want to patch everything:

1. Add the `safeTicks()` function after `atrMain = ta.atr(14)`:
   ```pine
   safeTicks(pts) =>
       math.max(1, nz(math.round(pts / syminfo.mintick)))
   ```

2. In the MES MJV6 profit block, change `float mjvAtr = atr10` to:
   ```pine
   float mjvAtr = nz(atr10, syminfo.mintick * 20)
   ```

3. Replace all 8 `math.round(... / syminfo.mintick)` lines in the MES MJV6 block with
   `safeTicks(...)` equivalents (4 for LONG, 4 for SHORT). See `FIX_STOP_CALCULATION_ERROR.md`
   for exact replacements.
