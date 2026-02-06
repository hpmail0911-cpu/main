## MES 5m "Stop Calculation Error" fix

This error is caused when a `strategy.exit(..., stop=...)` stop price
lands on the **wrong side** of the current price (for example, a long
stop above the current price after a fast gap down). TradingView rejects
the order and raises `Stop Calculation Error`.

On MES 5m, this most often happens inside the MJV6 MES block because the
stop is recalculated each bar and can end up above/below the market after
an abrupt move.

### Fix: validate stops and fallback to a market close

Add a small helper to validate stops, then use it in the MJV6 MES exits.
If the stop is invalid because price already crossed it, immediately
close the position to avoid the error.

```pine
// --- Stop validation helpers (place near other helpers) ---
stopCheckLong(stopPrice) =>
    bool ok = false
    bool crossed = false
    float val = na
    if not na(stopPrice) and stopPrice > 0
        if stopPrice < close
            ok := true
            val := stopPrice
        else
            crossed := true
    [ok, val, crossed]

stopCheckShort(stopPrice) =>
    bool ok = false
    bool crossed = false
    float val = na
    if not na(stopPrice) and stopPrice > 0
        if stopPrice > close
            ok := true
            val := stopPrice
        else
            crossed := true
    [ok, val, crossed]
```

Then update the MJV6 MES exits (the block under
`if (allowMultiProfitModules_eff or (not mjv6UltraActive and not v20Active_profit)) and isMES ...`):

```pine
// LONG
if useSL and not exitIssuedThisBar
    float stopPrice = stopLoss
    float actRatio = mesMJV6HighWinProtect_eff ? mesMJV6TrailActivationRatio_eff : trailActivationRatio
    if useSmartTrailing and close > mjvEntryPrice + (mjvFuturesTPDistance * actRatio)
        float trailDistance = mjvAtr * 0.6
        stopPrice := close - trailDistance
    if mesMJV6HighWinProtect_eff and close > mjvEntryPrice + (mjvFuturesTPDistance * mesMJV6BETriggerPct_eff)
        stopPrice := math.max(stopPrice, mjvEntryPrice + mjvBEOffsetPts)

    [stopOkL, stopValL, stopCrossedL] = stopCheckLong(stopPrice)
    if stopOkL
        strategy.exit("MJV6_STOP_L", from_entry="LONG", stop=stopValL)
    else if stopCrossedL and not exitIssuedThisBar
        strategy.close("LONG", comment="MJV6_STOP_CROSSED")
        exitIssuedThisBar := true
        lastExitReason := "MJV6_STOP_CROSSED"

// SHORT
if useSL and not exitIssuedThisBar
    float stopPrice = stopLoss
    float actRatio = mesMJV6HighWinProtect_eff ? mesMJV6TrailActivationRatio_eff : trailActivationRatio
    if useSmartTrailing and close < mjvEntryPrice - (mjvFuturesTPDistance * actRatio)
        float trailDistance = mjvAtr * 0.6
        stopPrice := close + trailDistance
    if mesMJV6HighWinProtect_eff and close < mjvEntryPrice - (mjvFuturesTPDistance * mesMJV6BETriggerPct_eff)
        stopPrice := math.min(stopPrice, mjvEntryPrice - mjvBEOffsetPts)

    [stopOkS, stopValS, stopCrossedS] = stopCheckShort(stopPrice)
    if stopOkS
        strategy.exit("MJV6_STOP_S", from_entry="SHORT", stop=stopValS)
    else if stopCrossedS and not exitIssuedThisBar
        strategy.close("SHORT", comment="MJV6_STOP_CROSSED")
        exitIssuedThisBar := true
        lastExitReason := "MJV6_STOP_CROSSED"
```

### Notes

- If you also use other blocks with dynamic stops (V71, V20, Kenya,
  hard brackets), apply the same `stopCheckLong/Short` pattern there.
- This avoids TradingView rejecting the stop order while preserving the
  intended safety behavior when price already crossed the stop.
