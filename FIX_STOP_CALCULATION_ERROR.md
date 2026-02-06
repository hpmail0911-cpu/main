# Fix: "Stop Calculation Error" on MES 5m Alert

## Root Cause

TradingView's `strategy.exit()` function throws a **"Stop Calculation Error"** when the `profit` parameter equals `0` or `na`. This happens in the MES 5m strategy because:

1. **ATR-based profit tick calculations can round to zero** during low-volatility periods (lunch hours, overnight, early chart bars)
2. For MES on 5m, the MES MJV6 profit logic calculates:
   ```
   tp1Points = math.round((atr10 * 0.75 * 0.5 * 0.4) / 0.25)
            = math.round(atr10 * 0.6)
   ```
3. When `atr10` (ATR period 10) drops below ~0.42 points, `tp1Points` rounds to **0**
4. `strategy.exit("MJV6_TP1_L", ..., profit=0)` → **"Stop Calculation Error"**

The same vulnerability exists across ALL profit logic modules: MJV6 Ultra, V20, MES MJV6, V71, and Kenya.

## Fix (2 Changes)

### Change 1: Add `safeTicks()` helper function

Insert this immediately after the `atrMain = ta.atr(14)` line:

```pine
// FIX: Safe tick helper - prevents "Stop Calculation Error" when ATR is small/na
safeTicks(pts) =>
    math.max(1, nz(math.round(pts / syminfo.mintick)))
```

This function:
- Converts points to ticks via `math.round(pts / syminfo.mintick)`
- Handles `na` values via `nz()` (converts na → 0)
- Ensures minimum 1 tick via `math.max(1, ...)`

### Change 2: Replace all `math.round(X / syminfo.mintick)` profit calculations with `safeTicks(X)`

Every `strategy.exit()` call that uses `profit=` with a tick calculation needs this fix.

Run the `apply_fix.py` script to automatically apply all changes, or manually replace
each occurrence listed below.

## All Affected Locations

### MJV6 Ultra LONG (MNQ)
```
BEFORE: int tp1Points = math.round((mjvUltraQuickTP * 0.4) / syminfo.mintick)
AFTER:  int tp1Points = safeTicks(mjvUltraQuickTP * 0.4)

BEFORE: int tp2Points = math.round((mjvFuturesTPDistance * 0.3) / syminfo.mintick)
AFTER:  int tp2Points = safeTicks(mjvFuturesTPDistance * 0.3)

BEFORE: int tp3Points = math.round((mjvFuturesTPDistance * 0.6) / syminfo.mintick)
AFTER:  int tp3Points = safeTicks(mjvFuturesTPDistance * 0.6)

BEFORE: int tp4Points = math.round((mjvFuturesTPDistance * 1.0) / syminfo.mintick)
AFTER:  int tp4Points = safeTicks(mjvFuturesTPDistance * 1.0)
```

### MJV6 Ultra SHORT (MNQ)
```
BEFORE: int tp1Points = math.round((mjvUltraQuickTP * 0.5) / syminfo.mintick)
AFTER:  int tp1Points = safeTicks(mjvUltraQuickTP * 0.5)

BEFORE: int tp2Points = math.round((mjvFuturesTPDistance * 0.4) / syminfo.mintick)
AFTER:  int tp2Points = safeTicks(mjvFuturesTPDistance * 0.4)

BEFORE: int tp3Points = math.round((mjvFuturesTPDistance * 0.7) / syminfo.mintick)
AFTER:  int tp3Points = safeTicks(mjvFuturesTPDistance * 0.7)

BEFORE: int tp4Points = math.round((mjvFuturesTPDistance * 1.1) / syminfo.mintick)
AFTER:  int tp4Points = safeTicks(mjvFuturesTPDistance * 1.1)
```

### V20 LONG
```
BEFORE: tp1Points = math.round((ultraQuickTP * 0.4) / syminfo.mintick)
AFTER:  tp1Points = safeTicks(ultraQuickTP * 0.4)

BEFORE: tp2Points = math.round((futuresTPDistance * 0.3) / syminfo.mintick)
AFTER:  tp2Points = safeTicks(futuresTPDistance * 0.3)

BEFORE: tp3Points = math.round((futuresTPDistance * 0.6) / syminfo.mintick)
AFTER:  tp3Points = safeTicks(futuresTPDistance * 0.6)

BEFORE: tp4Points = math.round((futuresTPDistance * 1.0) / syminfo.mintick)
AFTER:  tp4Points = safeTicks(futuresTPDistance * 1.0)
```

### V20 SHORT
```
BEFORE: tp1Points = math.round((ultraQuickTP * 0.5) / syminfo.mintick)
AFTER:  tp1Points = safeTicks(ultraQuickTP * 0.5)

BEFORE: tp2Points = math.round((futuresTPDistance * 0.4) / syminfo.mintick)
AFTER:  tp2Points = safeTicks(futuresTPDistance * 0.4)

BEFORE: tp3Points = math.round((futuresTPDistance * 0.7) / syminfo.mintick)
AFTER:  tp3Points = safeTicks(futuresTPDistance * 0.7)

BEFORE: tp4Points = math.round((futuresTPDistance * 1.1) / syminfo.mintick)
AFTER:  tp4Points = safeTicks(futuresTPDistance * 1.1)
```

### MES MJV6 LONG (This is the module that fires on MES 5m)
```
BEFORE: int tp1Points = math.round((mjvUltraQuickTP * 0.4) / syminfo.mintick)
AFTER:  int tp1Points = safeTicks(mjvUltraQuickTP * 0.4)

BEFORE: int tp2Points = math.round((mjvFuturesTPDistance * 0.3) / syminfo.mintick)
AFTER:  int tp2Points = safeTicks(mjvFuturesTPDistance * 0.3)

BEFORE: int tp3Points = math.round((mjvFuturesTPDistance * 0.6) / syminfo.mintick)
AFTER:  int tp3Points = safeTicks(mjvFuturesTPDistance * 0.6)

BEFORE: int tp4Points = math.round((mjvFuturesTPDistance * 1.0) / syminfo.mintick)
AFTER:  int tp4Points = safeTicks(mjvFuturesTPDistance * 1.0)
```

### MES MJV6 SHORT
```
BEFORE: int tp1Points = math.round((mjvUltraQuickTP * 0.5) / syminfo.mintick)
AFTER:  int tp1Points = safeTicks(mjvUltraQuickTP * 0.5)

BEFORE: int tp2Points = math.round((mjvFuturesTPDistance * 0.4) / syminfo.mintick)
AFTER:  int tp2Points = safeTicks(mjvFuturesTPDistance * 0.4)

BEFORE: int tp3Points = math.round((mjvFuturesTPDistance * 0.7) / syminfo.mintick)
AFTER:  int tp3Points = safeTicks(mjvFuturesTPDistance * 0.7)

BEFORE: int tp4Points = math.round((mjvFuturesTPDistance * 1.1) / syminfo.mintick)
AFTER:  int tp4Points = safeTicks(mjvFuturesTPDistance * 1.1)
```

### V71/Kenya LONG & SHORT (tp1-tp4 ticks, used in both Kenya ladder and V71 paths)
```
BEFORE: tp1Ticks = math.round(currentTP1 / syminfo.mintick)
AFTER:  tp1Ticks = safeTicks(currentTP1)

BEFORE: tp2Ticks = math.round(currentTP2 / syminfo.mintick)
AFTER:  tp2Ticks = safeTicks(currentTP2)

BEFORE: tp3Ticks = math.round(currentTP3 / syminfo.mintick)
AFTER:  tp3Ticks = safeTicks(currentTP3)

BEFORE: tp4Ticks = math.round(currentTP4 / syminfo.mintick)
AFTER:  tp4Ticks = safeTicks(currentTP4)
```
(These appear 4 times: Kenya LONG, Kenya SHORT, V71 LONG, V71 SHORT)

### Kenya Single Booster (LONG & SHORT)
```
BEFORE: int targetTicks = math.round(targetPts / syminfo.mintick)
AFTER:  int targetTicks = safeTicks(targetPts)
```

### V71 Quick Scalp (LONG & SHORT)
```
BEFORE: v71_quickScalpTicks = math.round(v71_quickScalpLevel / syminfo.mintick)
AFTER:  v71_quickScalpTicks = safeTicks(v71_quickScalpLevel)
```

### V71 Booster Targets (LONG & SHORT)
```
BEFORE: int boosterTargetTicks = math.round(boosterTargetPts / syminfo.mintick)
AFTER:  int boosterTargetTicks = safeTicks(boosterTargetPts)

BEFORE: int mesTargetTicks = math.round(mesTargetPts / syminfo.mintick)
AFTER:  int mesTargetTicks = safeTicks(mesTargetPts)

BEFORE: int mes5mTargetTicks = math.round(mes5mTargetPts / syminfo.mintick)
AFTER:  int mes5mTargetTicks = safeTicks(mes5mTargetPts)
```

## Additional Guards (Recommended)

### Guard `atr10` in MJV6 blocks
```
BEFORE: float mjvAtr = atr10
AFTER:  float mjvAtr = nz(atr10, syminfo.mintick * 20)
```

### Guard `atrMain` in V20 TP calculations
```
BEFORE: futuresTPDistance = atrMain * tpMultiplier
AFTER:  futuresTPDistance = nz(atrMain, syminfo.mintick * 20) * tpMultiplier
```

### Guard trailing stop distances
```
BEFORE: float trailDistance = mjvAtr * 0.6
AFTER:  float trailDistance = math.max(mjvAtr * 0.6, syminfo.mintick)
```

## Total Changes

- **1** new helper function (`safeTicks`)
- **~40** profit tick calculations patched
- **~5** ATR `nz()` guards added
- **~6** trailing distance guards added
