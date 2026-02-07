# MGC Contract Fix - Quick Reference

## 🎯 Problem Solved

Your bot was trying to trade **MGCG2026** (February 2026 contract - EXPIRED) on February 6, 2026.

**Error**: `ContractNotActive` from Topstep

## ✅ Solution Applied

The bot now correctly sends **MGCJ2026** (April 2026 contract - ACTIVE) for Feb 6, 2026.

---

## 🔍 What Changed

### Before Fix
```json
Alert: {"ticker": "MGC1!", ...}
↓
Topstep resolves: MGCG2026 (EXPIRED) ❌
↓
Error: ContractNotActive
```

### After Fix
```json
Alert: {"ticker": "MGCJ2026", ...}
↓
Topstep uses: MGCJ2026 (ACTIVE) ✅
↓
Trade executes successfully
```

---

## 📊 New Visual Indicator

### Row 2: MGC TICKER STATUS
Look for this new row in your trading display:

```
MGC TICKER | MGCJ2026 | Auto:ON | Roll:1 | Month:J
           (LIME)      (LIME)
```

**What to check:**
- ✅ **MGCJ2026** shown in LIME GREEN = Correct contract
- ✅ **Auto:ON** in LIME = Auto-contract working
- ❌ **MGC1!** in YELLOW/WHITE = Still using continuous (wrong!)

---

## 🗓️ MGC Contract Calendar

| Month | Contract Code | Example Ticker |
|-------|---------------|----------------|
| Feb | G | MGCG2026 |
| Apr | J | MGCJ2026 |
| Jun | M | MGCM2026 |
| Aug | Q | MGCQ2026 |
| Oct | V | MGCV2026 |
| Dec | Z | MGCZ2026 |

---

## ⚙️ Settings to Verify

1. **Auto MGC Active Contract (Topstep)**: ✅ ON (default: enabled)
2. **MGC Roll Day**: 1 (rolls on 1st of contract month)
3. **TradersPost Ticker Override**: Leave as `{{ticker}}` or empty

**Location**: Inputs → TradersPost Alerts section

---

## 🧪 How to Test

### Step 1: Check Display
Load the bot on MGC1! chart and verify row 2 shows:
- **MGCJ2026** (not MGC1!)
- **Auto:ON** in lime green

### Step 2: Test Alert
Create a test alert and check the JSON:
```json
{
  "ticker": "MGCJ2026",  // ✅ Should be specific contract
  "action": "buy",
  ...
}
```

### Step 3: Verify Trades
- Entry orders should execute without errors
- Exit orders should work properly
- No more "ContractNotActive" errors

---

## 🔧 Troubleshooting

### Still seeing MGC1! in alerts?

**Check 1**: Is "Auto MGC Active Contract" enabled?
- Go to Inputs → TradersPost Alerts
- Ensure checkbox is ✅ checked

**Check 2**: Is there a manual ticker override?
- Check "TradersPost Ticker Override" input
- Should be `{{ticker}}` or empty
- If you see "MGC1!" entered manually, clear it

**Check 3**: Are you on an MGC chart?
- Chart must have "MGC" or "GC" in ticker name
- Examples: MGC1!, MGCJ2026, GC1!

### Still getting ContractNotActive?

**Possible causes:**
1. Old alerts still in queue (wait for cooldown)
2. TradersPost cached old ticker (restart strategy)
3. Topstep doesn't have MGCJ2026 available (contact Topstep)

---

## 📅 Current Date: Feb 6, 2026

**Active Contract**: MGCJ2026 (April 2026)
**Roll Date**: April 1, 2026 → Will switch to MGCM2026 (June)

**Contract Timeline:**
- Jan 1 - Mar 31: **MGCJ2026** (April)
- Apr 1 - May 31: **MGCM2026** (June)
- Jun 1 - Jul 31: **MGCQ2026** (August)

---

## 🚀 Summary

1. ✅ **Fixed** MGC contract calculation
2. ✅ **Added** visual ticker display
3. ✅ **Sends** specific contracts (MGCJ2026) instead of continuous (MGC1!)
4. ✅ **Prevents** ContractNotActive errors
5. ✅ **Auto-rolls** to next contract on roll day

**Result**: MGC trading now works correctly! 🎉

---

## 📁 Files

- **Fixed Script**: `TL32-2.6.2026-REPAIRED-V.5-FIXED.pine`
- **Documentation**: `FIX-SUMMARY.md`
- **This Guide**: `MGC-CONTRACT-FIX-QUICK-GUIDE.md`

---

## 💡 Pro Tips

1. **Monitor Row 2** for ticker changes on roll days
2. **Check display color**: LIME = working, YELLOW/RED = issue
3. **Keep rollDay = 1** for Topstep (recommended)
4. **Don't override ticker** unless you know the exact contract

---

**Need Help?**
- Check FIX-SUMMARY.md for detailed technical info
- Verify settings in TradersPost Alerts section
- Monitor the MGC TICKER row in real-time
