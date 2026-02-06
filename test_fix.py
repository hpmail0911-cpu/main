#!/usr/bin/env python3
"""Test that apply_fix.py correctly patches all profit tick calculations."""

import subprocess
import sys
import os

# Create a minimal test Pine Script that contains all the patterns we need to fix
test_code = r'''atr10 = ta.atr(10)
atrMain = ta.atr(14)
futuresTPDistance = atrMain * tpMultiplier
ultraQuickTP = atrMain * (tpMultiplier * 0.5)
beTriggerDol = (atrMain * breakevenTrigger) * syminfo.pointvalue
getExitSLPoints() =>
    float sl = atrMain
    if isMES
        sl := mesSL_pts
    sl
// MJV6 Ultra LONG
    float mjvAtr = atr10
    float mjvTpMult = (isMNQ_3m ? mnqMJV6TPMult_3m_eff : tpMultiplier)
    float mjvFuturesTPDistance = mjvAtr * mjvTpMult
    float mjvUltraQuickTP = mjvAtr * (mjvTpMult * 0.5)
            int tp1Points = math.round((mjvUltraQuickTP * 0.4) / syminfo.mintick)
            int tp2Points = math.round((mjvFuturesTPDistance * 0.3) / syminfo.mintick)
            int tp3Points = math.round((mjvFuturesTPDistance * 0.6) / syminfo.mintick)
            int tp4Points = math.round((mjvFuturesTPDistance * 1.0) / syminfo.mintick)
            strategy.exit("MJV6U_TP1_L", from_entry="LONG", qty_percent=60, profit=tp1Points)
// MJV6 Ultra SHORT
            int tp1Points = math.round((mjvUltraQuickTP * 0.5) / syminfo.mintick)
            int tp2Points = math.round((mjvFuturesTPDistance * 0.4) / syminfo.mintick)
            int tp3Points = math.round((mjvFuturesTPDistance * 0.7) / syminfo.mintick)
            int tp4Points = math.round((mjvFuturesTPDistance * 1.1) / syminfo.mintick)
            strategy.exit("MJV6U_TP1_S", from_entry="SHORT", qty_percent=60, profit=tp1Points)
        float trailDistance = mjvAtr * 0.6
            strategy.exit("MJV6U_TRAIL_L", from_entry="LONG", stop=close - trailDistance)
        float trailDistance = mjvAtr * 0.6
            strategy.exit("MJV6U_TRAIL_S", from_entry="SHORT", stop=close + trailDistance)
// V20 LONG
        tp1Points = math.round((ultraQuickTP * 0.4) / syminfo.mintick)
        tp2Points = math.round((futuresTPDistance * 0.3) / syminfo.mintick)
        tp3Points = math.round((futuresTPDistance * 0.6) / syminfo.mintick)
        tp4Points = math.round((futuresTPDistance * 1.0) / syminfo.mintick)
        strategy.exit("V20_TP1_L", from_entry="LONG", qty_percent=60, profit=tp1Points)
// V20 SHORT
        tp1Points = math.round((ultraQuickTP * 0.5) / syminfo.mintick)
        tp2Points = math.round((futuresTPDistance * 0.4) / syminfo.mintick)
        tp3Points = math.round((futuresTPDistance * 0.7) / syminfo.mintick)
        tp4Points = math.round((futuresTPDistance * 1.1) / syminfo.mintick)
        strategy.exit("V20_TP1_S", from_entry="SHORT", qty_percent=60, profit=tp1Points)
            trailDistance = atrMain * 0.6
            v20ProtectStop := math.max(v20ProtectStop, close - trailDistance)
            trailDistance = atrMain * 0.6
            v20ProtectStopS := math.min(v20ProtectStopS, close + trailDistance)
// MES MJV6
    float mjvAtr = atr10
    float effectiveTPMult = tpMultiplier * 0.75
            int tp1Points = math.round((mjvUltraQuickTP * 0.4) / syminfo.mintick)
            int tp2Points = math.round((mjvFuturesTPDistance * 0.3) / syminfo.mintick)
            int tp3Points = math.round((mjvFuturesTPDistance * 0.6) / syminfo.mintick)
            int tp4Points = math.round((mjvFuturesTPDistance * 1.0) / syminfo.mintick)
            strategy.exit("MJV6_TP1_L", from_entry="LONG", qty_percent=60, profit=tp1Points)
            int tp1Points = math.round((mjvUltraQuickTP * 0.5) / syminfo.mintick)
            int tp2Points = math.round((mjvFuturesTPDistance * 0.4) / syminfo.mintick)
            int tp3Points = math.round((mjvFuturesTPDistance * 0.7) / syminfo.mintick)
            int tp4Points = math.round((mjvFuturesTPDistance * 1.1) / syminfo.mintick)
            strategy.exit("MJV6_TP1_S", from_entry="SHORT", qty_percent=60, profit=tp1Points)
                float trailDistance = mjvAtr * 0.6
                stopPrice := close - trailDistance
                float trailDistance = mjvAtr * 0.6
                stopPrice := close + trailDistance
// V71/Kenya
            tp1Ticks = math.round(currentTP1 / syminfo.mintick)
            tp2Ticks = math.round(currentTP2 / syminfo.mintick)
            tp3Ticks = math.round(currentTP3 / syminfo.mintick)
            tp4Ticks = math.round(currentTP4 / syminfo.mintick)
            int targetTicks = math.round(targetPts / syminfo.mintick)
        v71_quickScalpTicks = math.round(v71_quickScalpLevel / syminfo.mintick)
            int boosterTargetTicks = math.round(boosterTargetPts / syminfo.mintick)
            int mesTargetTicks = math.round(mesTargetPts / syminfo.mintick)
            int mes5mTargetTicks = math.round(mes5mTargetPts / syminfo.mintick)
'''

# Write test input
with open('/tmp/test_input.pine', 'w') as f:
    f.write(test_code)

# Run the fix
result = subprocess.run(
    [sys.executable, '/workspace/apply_fix.py', '/tmp/test_input.pine', '/tmp/test_output.pine'],
    capture_output=True, text=True
)

print("=== STDERR (fix log) ===")
print(result.stderr)

# Read output
with open('/tmp/test_output.pine', 'r') as f:
    output = f.read()

# Verify fixes
errors = []

# Check safeTicks helper was added
if 'safeTicks(pts) =>' not in output:
    errors.append("FAIL: safeTicks() helper not found")

# Check no remaining math.round(.../ syminfo.mintick) in profit contexts
import re
remaining_bad = re.findall(
    r'(?:tp\d(?:Points|Ticks)|targetTicks|boosterTargetTicks|mesTargetTicks|mes5mTargetTicks|v71_quickScalpTicks)\s*=\s*math\.round\(.*?/\s*syminfo\.mintick',
    output
)
if remaining_bad:
    errors.append(f"FAIL: {len(remaining_bad)} unpatched profit tick calculations found")
    for b in remaining_bad:
        errors.append(f"  {b}")

# Check safeTicks is actually used
safeticks_count = output.count('safeTicks(')
if safeticks_count < 30:
    errors.append(f"FAIL: Expected 30+ safeTicks() calls, found {safeticks_count}")

# Check nz guards
if 'nz(atr10, syminfo.mintick * 20)' not in output:
    errors.append("FAIL: nz(atr10) guard not found")
if 'nz(atrMain, syminfo.mintick * 20)' not in output:
    errors.append("FAIL: nz(atrMain) guard not found")

# Check trailing distance guards
if 'math.max(mjvAtr * 0.6, syminfo.mintick)' not in output:
    errors.append("FAIL: mjvAtr trailing guard not found")

if errors:
    print("\n=== TEST FAILURES ===")
    for e in errors:
        print(e)
    sys.exit(1)
else:
    print(f"\n=== ALL TESTS PASSED ===")
    print(f"  safeTicks() calls: {safeticks_count}")
    print(f"  Unpatched profit calcs: 0")
    print(f"  ATR nz() guards: present")
    print(f"  Trailing distance guards: present")
    sys.exit(0)
