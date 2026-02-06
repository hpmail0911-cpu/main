# Fix: Topstep `ContractNotActive` (Failed to enter new position)

## What happened

Your alert is using the continuous symbol `MGC1!`, but Topstep ultimately tried to route the entry to **`MGCG2026`** (February 2026). Topstep rejected it with **`ContractNotActive`**, which typically happens when:

- The contract is in / past its **restricted window** (often around first notice day / delivery month), and Topstep blocks new positions.
- The symbol mapping picks an **expired or non-tradable** month.

On **Feb 6, 2026**, it’s common for the Feb gold contract to be considered “not active” for new entries, and the correct active month is usually the **next** major month (often **April: `MGCJ2026`**).

## The fix

Instead of sending `MGC1!` (continuous) in the alert JSON, send an **active month contract** computed from the current date (with a conservative roll rule):

- If the current month is a gold delivery month (Feb/Apr/Jun/Aug/Oct/Dec), **roll immediately** to the next delivery month.
- Optionally roll **a few days before month-end** in the month *before* a delivery month to avoid Topstep cutoffs.

## Pine Script change (drop-in)

1. Copy/paste the helper functions from `pinescript/topstep_active_contract_roll.pine` into your TradingView strategy (anywhere above where you build alert JSON).
2. Replace your `getInstrumentTicker()` with the version below.

```pinescript
// --- Topstep Active Contract Roll (drop-in) ---
useTopstepActiveContract = input.bool(true, "✅ Use Topstep Active Contract (avoid ContractNotActive)", group="TradersPost")
rollDaysBeforeMonthEnd   = input.int(5, "Roll Days Before Month End", minval=0, maxval=15, group="TradersPost")

getInstrumentTicker() =>
    int y = year(time)
    int m = month(time)
    int d = dayofmonth(time)

    // Keep your existing continuous mapping for most symbols if you want,
    // but override MGC/GC to a concrete active month for Topstep.
    if useTopstepActiveContract and (isMGC)
        int activeM = f_activeGoldMonth(y, m, d, rollDaysBeforeMonthEnd)
        f_topstepContract("MGC", activeM, y)
    else
        // Your previous behavior (continuous contracts)
        isMYM ? "MYM1!" :
        isMNQ ? "MNQ1!" :
        isMES ? "MES1!" :
        isMCL ? "MCL1!" :
        isMGC ? "MGC1!" :
        isM2K ? "M2K1!" : syminfo.ticker
```

## Expected behavior (example)

- Date: **2026-02-06**
- `isMGC = true`
- Output ticker: **`MGCJ2026`** (April 2026) instead of `MGCG2026`

## Notes

- This is a **conservative** roll rule designed to avoid Topstep blocking new entries.
- If you prefer to roll later/earlier, adjust `rollDaysBeforeMonthEnd`.

