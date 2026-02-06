# TradingView Strategy - Contract Rollover Fix

## 🚨 Fixed Issue: ContractNotActive Error

This repository contains a fixed version of the TradingView Pine Script strategy that resolves the "ContractNotActive" error from Topstep.

### Problem
- **Error:** `ContractNotActive` when entering MGC (Micro Gold) positions
- **Cause:** Strategy was routing to February 2026 contract (MGCG2026) which is expiring
- **Date:** February 6, 2026 - within expiration window

### Solution
**Version 5.1** includes contract rollover logic to automatically handle expiring contracts.

## 📁 Files

- **`fixed_strategy.pine`** - Updated Pine Script with rollover protection
- **`CONTRACT_ROLLOVER_FIX.md`** - Detailed technical explanation
- **`QUICK_FIX_GUIDE.md`** - 2-minute setup guide

## ⚡ Quick Start

### 1. Apply the Fix (2 minutes)

1. Copy contents of `fixed_strategy.pine`
2. Paste into your TradingView Pine Editor
3. Save as new strategy
4. In strategy settings, find **"🆕 Contract Settings"**
5. Enable: **"Use 2nd Month Out (Skip Front Month)"**
6. Save and apply to chart

### 2. Verify It's Working

Check the strategy table (top right corner):
```
📅 CONTRACT
MGC2! | ROLL: ✓ | 2nd: YES | 🔄 ROLL
```

### 3. Test
- Run a paper trade
- Verify no "ContractNotActive" error
- Check TradersPost logs show active contract (MGCJ2026, not MGCG2026)

## 🔧 Configuration

### New Settings (V5.1)

Located in **"🆕 Contract Settings"** group:

```
🛡️ Enable Contract Rollover Logic: ON (default)
Days Before Expiration to Roll: 10 (default)
Use 2nd Month Out (Skip Front Month): OFF (default) ← Enable this now!
```

### Recommended for February 2026

```
☑️ Enable Contract Rollover Logic
☑️ Use 2nd Month Out (Skip Front Month)  ← CRITICAL for current issue
```

## 📅 Contract Rollover Schedule

| Period | Front Month | Expiring Contract | Use 2nd Month? |
|--------|-------------|-------------------|----------------|
| Feb 1-15 | Feb (G) | MGCG2026 | ✅ YES |
| Feb 16+ | Apr (J) | - | ❌ NO |
| Mar 25 - Apr 15 | Apr (J) | MGCJ2026 | ✅ YES |
| Apr 16+ | Jun (M) | - | ❌ NO |

## 🔍 What Changed

### Version 5.1 New Features:

1. **Contract Rollover Detection**
   - Automatically detects expiration windows
   - Visual warnings (yellow background during rollover week)

2. **2nd Month Out Option**
   - Forces use of next active contract
   - Prevents routing to expiring contracts

3. **Enhanced Alert JSON**
   - Added `rollover_protection` field
   - Added `use_2nd_month` field
   - Helps TradersPost route correctly

4. **Visual Feedback**
   - New contract status table row
   - Shows active ticker (MGC1! vs MGC2!)
   - Rollover status indicators

## 📊 Alert JSON Format

With V5.1, alerts now include:

```json
{
  "ticker": "MGC2!",
  "action": "buy",
  "orderType": "market",
  "quantity": 1,
  "stop_loss": 150,
  "take_profit": 100,
  "trailing_stop": 90,
  "strategy": "MTF_PROTECTED",
  "timeframe": "60",
  "instrument": "MGC",
  "signal_type": "STRONG",
  "confluence": 10,
  "session": "NY",
  "mtf_enabled": true,
  "early_trend": false,
  "slippage_buffer": 5.4,
  "dedup_enabled": true,
  "max_contracts": 1,
  "rollover_protection": true,  ← NEW
  "use_2nd_month": true         ← NEW
}
```

## 🛠️ Troubleshooting

### Still Getting ContractNotActive?

1. **Verify Settings:**
   - "Use 2nd Month Out" must be enabled
   - Check visual table shows `MGC2!`

2. **Check TradersPost:**
   - Go to your broker connection settings
   - Set contract month handling to "auto-roll" or "2nd month"

3. **Verify Chart:**
   - Must be on MGC1! continuous contract chart
   - NOT on specific month chart (MGCG2026)

4. **Check Topstep:**
   - Log into Topstep platform
   - Verify which MGC contracts are currently tradeable
   - May need to manually specify MGCJ2026 or MGCM2026

## 📚 Documentation

- **Quick Fix:** See `QUICK_FIX_GUIDE.md`
- **Technical Details:** See `CONTRACT_ROLLOVER_FIX.md`

## 🎯 Strategy Features (Original)

This is a multi-timeframe (MTF) protected trading strategy with:

- ✅ High win rate mode (70-80%+ target)
- ✅ Multi-timeframe confirmation
- ✅ Early trend detection
- ✅ Alert deduplication
- ✅ Emergency hard stop protection ($55 max loss)
- ✅ MAE (Maximum Adverse Excursion) protection
- ✅ 24/7 global session support
- ✅ Instrument-specific optimization (MGC, MES, MNQ, MYM, MCL, M2K)
- ✅ **NEW: Contract rollover protection**

## ⚠️ Important Notes

1. **Chart Timezone:** Must be set to America/Chicago (CDT/CST)
2. **Contract Format:** Use continuous contracts (MGC1!, MES1!, MNQ1!)
3. **Rollover Period:** Enable "2nd month out" during expiration weeks
4. **Paper Test First:** Always test after updates before live trading

## 🔄 Version History

- **V5.0:** Original strategy
- **V5.1:** Added contract rollover logic (fixes ContractNotActive error)

## 📞 Support

For issues:
1. Check `QUICK_FIX_GUIDE.md` for immediate solutions
2. Review `CONTRACT_ROLLOVER_FIX.md` for detailed explanation
3. Verify TradersPost and Topstep settings
4. Test with paper trading first

## ⏭️ Next Steps

1. ✅ Apply `fixed_strategy.pine` to TradingView
2. ✅ Enable "Use 2nd Month Out" setting
3. ✅ Test with paper trading
4. ✅ Monitor visual contract indicator
5. ✅ Deploy to live trading once verified
6. 📅 Disable "2nd month out" around Feb 15-20 when April becomes front month

---

**Status:** ✅ Fixed and ready for deployment
**Version:** 5.1
**Last Updated:** February 6, 2026
