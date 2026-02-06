# Contract Rollover Fix for "ContractNotActive" Error

## Problem Summary

**Error:** `ContractNotActive` from Topstep when trying to enter MGC position

**Root Cause:** The strategy was sending alerts with "MGC1!" (continuous contract), which TradersPost translated to "MGCG2026" (February 2026 contract). On February 6, 2026, this contract is in its expiration week and is no longer accepting new positions.

## The Fix

### Key Changes in V5.1:

1. **Contract Rollover Logic Added** (Lines 42-47)
   - New input: `enableContractRollover` - Enables smart contract month selection
   - New input: `daysBeforeExpiration` - Days before expiration to roll (default: 10)
   - New input: `useSecondMonthOut` - Force use of 2nd month out contract (skips front month)

2. **Smart Ticker Generation** (Lines 549-564)
   - `getInstrumentTicker()` function now supports rollover logic
   - Uses `MGC1!` for front month (default)
   - Uses `MGC2!` for 2nd month out when `useSecondMonthOut` is enabled
   - Automatic detection of expiration week

3. **Rollover Week Detection** (Lines 566-578)
   - `isRolloverWeek()` function detects when you're in the danger zone
   - Checks if current day of month is near expiration (configurable)
   - For Feb 6, 2026, this will detect you're in the February contract expiration period

4. **Visual Feedback** (Lines 4056-4066)
   - New table row showing active contract ticker
   - Rollover status indicator
   - 2nd month out status
   - Current day of month display
   - Background color warning during rollover week (yellow tint)

## How to Use

### Option 1: Enable 2nd Month Out (Recommended for Now)
1. In strategy settings, find "🆕 Contract Settings"
2. Enable "Use 2nd Month Out (Skip Front Month)"
3. This will change ticker from `MGC1!` to `MGC2!`
4. TradersPost will route to the next active contract (likely MGCJ2026 - April)

### Option 2: Use Automatic Rollover Detection
1. Keep "Enable Contract Rollover Logic" enabled (default)
2. Set "Days Before Expiration to Roll" to 10 (default)
3. The script will automatically detect expiration windows
4. Visual indicator (yellow background) appears during rollover week

### Option 3: Manual TradersPost Configuration
Even with these fixes, you should also check your TradersPost broker connector settings:

1. Log into TradersPost
2. Go to your Topstep connection settings
3. Look for "Contract Month" or "Roll Settings"
4. Set it to:
   - "Auto-roll to front month" OR
   - "Use 2nd month out" OR
   - "Continuous contract with X days rollover"

## Understanding the Error

### Gold Futures (MGC) Contract Months:
- **G = February** (MGCG2026) ← This was the problem contract
- **J = April** (MGCJ2026) ← Target contract for Feb 6, 2026
- **M = June** (MGCM2026)
- **Q = August**
- **V = October**
- **Z = December**

### Expiration Schedule:
Most futures contracts stop accepting new positions 1-2 weeks before official expiration. On February 6, 2026, the February contract (G2026) is in its final trading week.

## Testing the Fix

### Before Deploying Live:
1. **Check the ticker in the visual table:**
   - Look at the "📅 CONTRACT" row
   - Verify it shows `MGC2!` if you enabled 2nd month out
   - Check the "ROLL" status shows "✓"

2. **Test with Paper Trading:**
   - Send a test alert to TradersPost
   - Verify it routes to MGCJ2026 or another active contract
   - Confirm no "ContractNotActive" error

3. **Monitor the Alert JSON:**
   ```json
   {
     "ticker": "MGC2!",  // Should show MGC2! if 2nd month enabled
     "action": "buy",
     "orderType": "market",
     "quantity": 1,
     "stop_loss": 150,
     "take_profit": 100,
     "rollover_protection": true,  // New field confirms fix is active
     "use_2nd_month": true         // Shows 2nd month out is enabled
   }
   ```

## Additional Alerts JSON Fields

The fixed strategy now includes these new fields in alerts:
- `rollover_protection`: true/false - Shows if contract rollover logic is enabled
- `use_2nd_month`: true/false - Shows if using 2nd month out
- `strategy`: "MTF_PROTECTED" - Confirms protected strategy version

## Recommended Settings for February 2026

Since we're currently in February:

```
✅ Enable Contract Rollover Logic: ON
✅ Days Before Expiration to Roll: 10
✅ Use 2nd Month Out (Skip Front Month): ON  ← ENABLE THIS NOW
```

This will force the use of MGC2! which should map to April (J2026) contract.

## When to Revert Settings

Around **February 15-20, 2026**, you can:
1. Disable "Use 2nd Month Out"
2. The script will automatically use the April contract as front month
3. Continue monitoring for the next rollover in late March/early April

## Rollover Calendar (Approximate)

| Month | Contract | Enable 2nd Month |
|-------|----------|------------------|
| Feb 1-15 | Feb (G) expiring | ✅ YES |
| Feb 16+ | Apr (J) is front | ❌ NO |
| Mar 25 - Apr 15 | Apr (J) expiring | ✅ YES |
| Apr 16+ | Jun (M) is front | ❌ NO |
| May 25 - Jun 15 | Jun (M) expiring | ✅ YES |

## Troubleshooting

### Still Getting "ContractNotActive"?

1. **Check TradersPost Logs:**
   - What contract month is it actually sending?
   - Is the broker connector configured correctly?

2. **Verify Chart Settings:**
   - Make sure you're on an MGC1! continuous contract chart
   - Not on a specific month chart like MGCG2026

3. **Check Topstep Contract Availability:**
   - Log into Topstep platform
   - Verify which MGC contracts are actively tradeable
   - The front month might be April (MGCJ2026) or June (MGCM2026)

4. **Try Explicit Contract Month:**
   - If continuous contracts aren't working, you might need to:
   - Manually specify "MGCJ2026" in TradersPost settings
   - Or update your broker connector configuration

## Support

If issues persist:
1. Check TradersPost documentation for your specific broker connector
2. Verify Topstep's current active contract months
3. Consider using a different continuous contract notation (like @MGC or MGC!)
4. Contact TradersPost support with the alert JSON to verify contract translation

## Version History

- **V5.0**: Original version - used MGC1! always
- **V5.1**: Added contract rollover logic and 2nd month out option
- **Status**: ✅ FIXED - Ready for deployment

---

**Next Steps:**
1. Upload fixed_strategy.pine to TradingView
2. Enable "Use 2nd Month Out" in settings
3. Test with paper trading first
4. Monitor the visual contract indicator
5. Deploy to live trading once verified
