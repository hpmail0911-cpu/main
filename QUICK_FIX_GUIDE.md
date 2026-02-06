# Quick Fix Guide - ContractNotActive Error

## ⚡ Immediate Solution (2 Minutes)

### Problem
```
Error: ContractNotActive
Contract: MGCG2026 (February 2026)
Date: February 6, 2026
```

### Root Cause
February Gold contract is expiring - no longer accepting new positions.

### ✅ Immediate Fix

**Step 1:** Replace your current Pine Script with `fixed_strategy.pine`

**Step 2:** In Strategy Settings, find **"🆕 Contract Settings"**

**Step 3:** Enable this checkbox:
```
☑️ Use 2nd Month Out (Skip Front Month)
```

**Step 4:** Save and reload

### What This Does
- Changes ticker from `MGC1!` → `MGC2!`
- Routes to next active contract (April MGCJ2026)
- Avoids expired February contract

## 📊 Visual Confirmation

After applying fix, check the strategy table (top right):

```
📅 CONTRACT
┌─────────────────────────────────┐
│ MGC2!  │ ROLL: ✓  │ 2nd: YES  │
│        │ 🔄 ROLL   │           │
└─────────────────────────────────┘
```

Should see:
- Ticker shows `MGC2!` (not MGC1!)
- ROLL status: ✓
- 2nd month: YES
- Status: 🔄 ROLL (orange indicator)

## 🧪 Test Before Live

1. **Paper Trade Test:**
   - Wait for next signal
   - Check alert goes through without error
   - Verify order placed on active contract

2. **Check TradersPost Logs:**
   - Should show MGCJ2026 or another active contract
   - NOT MGCG2026

## ⏰ When to Revert

Around **February 15-20**, disable "Use 2nd Month Out"
- April will be the front month
- Can return to MGC1! notation

## 🚨 If Still Fails

### Check Your TradersPost Settings:

1. Go to TradersPost → Connections
2. Find your Topstep connection
3. Look for "Contract Month Settings"
4. Set to: **"Auto-roll"** or **"2nd month out"**

### Alternative: Use Explicit Contract

In TradersPost, instead of continuous contracts, specify:
- **"MGCJ2026"** (April 2026)
- Or whatever month Topstep shows as active

## 📞 Still Need Help?

Check these:
1. Topstep platform - which MGC contracts are available?
2. TradersPost documentation - broker-specific contract notation
3. Chart settings - must be on MGC1! (continuous), not specific month

## Summary

```
❌ Before: MGC1! → MGCG2026 → ContractNotActive
✅ After:  MGC2! → MGCJ2026 → Success
```

The fix is in `fixed_strategy.pine` with the new setting:
```
🛡️ Enable Contract Rollover Logic: ON
Use 2nd Month Out (Skip Front Month): ON ← KEY FIX
```
