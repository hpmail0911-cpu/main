# WHERE TO APPLY EACH FIX - Exact Line Locations

## Your Script Structure with Fix Points Marked

```
//@version=5
strategy(...)

┌─────────────────────────────────────────────────────────────────
│ FIX #1: ADD HERE (immediately after strategy declaration)
│ 
│ isValidPrice(price) =>
│     not na(price) and not math.isinfinite(price) and price > 0
│ 
│ isValidPoints(pts) =>
│     not na(pts) and not math.isinfinite(pts) and pts > 0 and pts < 100000
└─────────────────────────────────────────────────────────────────

profitTargetGroup = "💰 Daily Profit Target"
enableDailyProfitTarget = input.bool(...)
... [all your input parameters] ...

atr10 = ta.atr(10)
... [indicator calculations] ...

┌─────────────────────────────────────────────────────────────────
│ FIX #2a: FIND THIS LINE (search for "safePointValue =")
│ 
│ OLD: safePointValue = syminfo.pointvalue > 0 ? syminfo.pointvalue : 1.0
│ NEW: safePointValue = syminfo.pointvalue > 0 ? syminfo.pointvalue : 5.0
└─────────────────────────────────────────────────────────────────

... [more calculations] ...

┌─────────────────────────────────────────────────────────────────
│ FIX #2b: FIND THESE LINES (search for "mesFixedActive =")
│ 
│ mesFixedActive = isMES and mesUseFixedDollarSLTP and (timeframe.period == "3" or timeframe.period == "5")
│ 
│ DELETE these 2 lines:
│   mesSL_pts = mesFixedActive ? ...
│   mesTP_pts = mesFixedActive ? ...
│ 
│ REPLACE with:
│   mesSL_pts_raw = mesFixedActive ? ((timeframe.period == "3" ? mesFixedSLDol_3m : mesFixedSLDol_5m) / safePointValue) : mes_customSL
│   mesTP_pts_raw = mesFixedActive ? (mesFixedTPDol / safePointValue) : mes_customTP
│   
│   mesSL_pts = isValidPoints(mesSL_pts_raw) ? math.min(mesSL_pts_raw, 50.0) : 9.0
│   mesTP_pts = isValidPoints(mesTP_pts_raw) ? math.min(mesTP_pts_raw, 50.0) : 16.0
└─────────────────────────────────────────────────────────────────

... [all your trading logic] ...
... [exit logic] ...

f_traderspostAlerts(_tpTradeId, _tookTrade) =>
    tradeIdLocal = _tpTradeId
    tookLocal = _tookTrade
    ... [function code] ...
    
    ┌─────────────────────────────────────────────────────────────
    │ FIX #3a: FIND "if entryOK and longCondition and tpFlat"
    │ (This is the LONG entry alert section)
    │ 
    │ FIND these lines:
    │     float currentSL_pts = isMES ? mesSL_pts : (useMnq3m ? mnq_customSL_3m : mnq_customSL)
    │     float currentTP1_pts = isMES ? mesTP_pts : (useMnq3m ? mnq_customTP1_3m : mnq_customTP1)
    │     if useManualDollarOverrides and manualDollarSL > 0
    │         currentSL_pts := manualDollarSL / safePointValue
    │     if useManualDollarOverrides and manualDollarTP > 0
    │         currentTP1_pts := manualDollarTP / safePointValue
    │     localSL = tpExecPrice - currentSL_pts
    │     localTP1 = tpExecPrice + currentTP1_pts
    │ 
    │ ADD VALIDATION between "...TP > 0" and "localSL =":
    │     
    │     if not isValidPoints(currentSL_pts)
    │         currentSL_pts := 9.0
    │     if not isValidPoints(currentTP1_pts)
    │         currentTP1_pts := 16.0
    │     
    │     localSL = tpExecPrice - currentSL_pts
    │     localTP1 = tpExecPrice + currentTP1_pts
    │     
    │     if not isValidPrice(localSL)
    │         localSL := tpExecPrice * 0.998
    │     if not isValidPrice(localTP1)
    │         localTP1 := tpExecPrice * 1.003
    └─────────────────────────────────────────────────────────────
    
    ┌─────────────────────────────────────────────────────────────
    │ FIX #3b: FIND "if entryOK and shortCondition and tpFlat"
    │ (This is the SHORT entry alert section - same fix as above)
    │ 
    │ FIND these lines:
    │     float currentSL_pts = isMES ? mesSL_pts : (useMnq3m ? mnq_customSL_3m : mnq_customSL)
    │     float currentTP1_pts = isMES ? mesTP_pts : (useMnq3m ? mnq_customTP1_3m : mnq_customTP1)
    │     if useManualDollarOverrides and manualDollarSL > 0
    │         currentSL_pts := manualDollarSL / safePointValue
    │     if useManualDollarOverrides and manualDollarTP > 0
    │         currentTP1_pts := manualDollarTP / safePointValue
    │     localSL = tpExecPrice + currentSL_pts  // NOTE: + for SHORT
    │     localTP1 = tpExecPrice - currentTP1_pts // NOTE: - for SHORT
    │ 
    │ ADD VALIDATION between "...TP > 0" and "localSL =":
    │     
    │     if not isValidPoints(currentSL_pts)
    │         currentSL_pts := 9.0
    │     if not isValidPoints(currentTP1_pts)
    │         currentTP1_pts := 16.0
    │     
    │     localSL = tpExecPrice + currentSL_pts
    │     localTP1 = tpExecPrice - currentTP1_pts
    │     
    │     if not isValidPrice(localSL)
    │         localSL := tpExecPrice * 1.002  // OPPOSITE for SHORT
    │     if not isValidPrice(localTP1)
    │         localTP1 := tpExecPrice * 0.997  // OPPOSITE for SHORT
    └─────────────────────────────────────────────────────────────

[newTradeIdTmp, newTookTmp] = f_traderspostAlerts(tpTradeId, tookTradeThisBar)
tpTradeId := newTradeIdTmp
tookTradeThisBar := newTookTmp
```

## Quick Search Terms to Find Each Location

1. **Fix #1 Location**: Right after `strategy("MES 3M-MES 5M...`
2. **Fix #2a Location**: Search for `safePointValue = syminfo.pointvalue`
3. **Fix #2b Location**: Search for `mesFixedActive = isMES and mesUseFixedDollarSLTP`
4. **Fix #3a Location**: Search for `if entryOK and longCondition and tpFlat and allowEntryLong`
5. **Fix #3b Location**: Search for `if entryOK and shortCondition and tpFlat and allowEntryShort`

## Verification After Applying Fixes

Add this at the VERY END of your script to verify the fixes are working:

```pinescript
// ===== DEBUG: Verify MES 5m calculations =====
if isMES and (timeframe.period == "5")
    var table debugTable = table.new(position.top_right, 2, 6, bgcolor=color.new(color.black, 80))
    
    if barstate.islast
        table.cell(debugTable, 0, 0, "MES 5m Debug", text_color=color.white)
        table.cell(debugTable, 1, 0, "", text_color=color.white)
        
        table.cell(debugTable, 0, 1, "Fixed Active:", text_color=color.yellow)
        table.cell(debugTable, 1, 1, str.tostring(mesFixedActive), text_color=color.white)
        
        table.cell(debugTable, 0, 2, "SL Points:", text_color=color.yellow)
        table.cell(debugTable, 1, 2, str.tostring(mesSL_pts, "#.##"), 
                   text_color=isValidPoints(mesSL_pts) ? color.lime : color.red)
        
        table.cell(debugTable, 0, 3, "TP Points:", text_color=color.yellow)
        table.cell(debugTable, 1, 3, str.tostring(mesTP_pts, "#.##"), 
                   text_color=isValidPoints(mesTP_pts) ? color.lime : color.red)
        
        table.cell(debugTable, 0, 4, "Point Value:", text_color=color.yellow)
        table.cell(debugTable, 1, 4, str.tostring(safePointValue), text_color=color.white)
        
        table.cell(debugTable, 0, 5, "Status:", text_color=color.yellow)
        statusText = isValidPoints(mesSL_pts) and isValidPoints(mesTP_pts) ? "✓ VALID" : "✗ INVALID"
        statusColor = isValidPoints(mesSL_pts) and isValidPoints(mesTP_pts) ? color.lime : color.red
        table.cell(debugTable, 1, 5, statusText, text_color=statusColor)
```

This debug table will show you:
- Whether fixed SL/TP is active
- The calculated SL and TP in points
- Whether values are valid (green) or invalid (red)
- The point value being used

If you see "✓ VALID" in green, the fix is working!
