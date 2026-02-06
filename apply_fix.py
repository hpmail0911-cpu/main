#!/usr/bin/env python3
"""
Apply 'Stop Calculation Error' fix to Pine Script strategy.

Usage:
  python3 apply_fix.py original_strategy.pine > fixed_strategy.pine

This script:
1. Adds safeTicks() helper function after atr10/atrMain definitions
2. Replaces all math.round(X / syminfo.mintick) profit calculations with safeTicks(X)
3. Adds nz() guards on ATR values in profit logic blocks
4. Guards trailing stop distances against zero
"""

import re
import sys

def apply_fix(code):
    """Apply all Stop Calculation Error fixes to the Pine Script code."""
    
    fixes_applied = 0
    
    # === FIX 1: Add safeTicks() helper after atrMain definition ===
    safeticks_helper = (
        '\n// FIX: Safe tick helper - prevents "Stop Calculation Error" when ATR is small/na\n'
        'safeTicks(pts) =>\n'
        '    math.max(1, nz(math.round(pts / syminfo.mintick)))\n'
    )
    
    if 'safeTicks' not in code:
        marker = 'atrMain = ta.atr(14)\n'
        if marker in code:
            code = code.replace(marker, marker + safeticks_helper, 1)
            fixes_applied += 1
            print("  [+] Added safeTicks() helper function", file=sys.stderr)
        else:
            print("  [!] Could not find atrMain definition to insert helper", file=sys.stderr)
    
    # === FIX 2: Guard atr10 with nz() in profit logic blocks ===
    # MJV6 Ultra block
    old = '    float mjvAtr = atr10\n    float mjvTpMult = (isMNQ_3m'
    new = '    float mjvAtr = nz(atr10, syminfo.mintick * 20)\n    float mjvTpMult = (isMNQ_3m'
    if old in code:
        code = code.replace(old, new, 1)
        fixes_applied += 1
    
    # MES MJV6 block
    old = '    float mjvAtr = atr10\n    float effectiveTPMult'
    new = '    float mjvAtr = nz(atr10, syminfo.mintick * 20)\n    float effectiveTPMult'
    if old in code:
        code = code.replace(old, new, 1)
        fixes_applied += 1
    
    # V20 general TP calculations
    old = 'futuresTPDistance = atrMain * tpMultiplier\nultraQuickTP = atrMain * (tpMultiplier * 0.5)'
    new = 'futuresTPDistance = nz(atrMain, syminfo.mintick * 20) * tpMultiplier\nultraQuickTP = nz(atrMain, syminfo.mintick * 20) * (tpMultiplier * 0.5)'
    if old in code:
        code = code.replace(old, new, 1)
        fixes_applied += 1
    
    # beTriggerDol
    old = 'beTriggerDol = (atrMain * breakevenTrigger)'
    new = 'beTriggerDol = (nz(atrMain, syminfo.mintick * 20) * breakevenTrigger)'
    if old in code:
        code = code.replace(old, new, 1)
        fixes_applied += 1
    
    # getExitSLPoints
    old = '    float sl = atrMain\n    if isMES'
    new = '    float sl = nz(atrMain, syminfo.mintick * 20)\n    if isMES'
    if old in code:
        code = code.replace(old, new, 1)
        fixes_applied += 1
    
    print(f"  [+] Applied {fixes_applied} ATR nz() guards", file=sys.stderr)
    
    # === FIX 3: Replace all profit tick calculations with safeTicks() ===
    # Pattern: captures variable assignment of math.round(expr / syminfo.mintick)
    
    tick_var_names = [
        'tp1Points', 'tp2Points', 'tp3Points', 'tp4Points',
        'tp1Ticks', 'tp2Ticks', 'tp3Ticks', 'tp4Ticks',
        'targetTicks', 'boosterTargetTicks', 'mesTargetTicks', 
        'mes5mTargetTicks', 'v71_quickScalpTicks'
    ]
    
    tick_fixes = 0
    for var in tick_var_names:
        # Match: [int ]varname = math.round((expr) / syminfo.mintick)
        # or:    [int ]varname = math.round(expr / syminfo.mintick)
        pattern = rf'((?:int\s+)?{re.escape(var)})\s*=\s*math\.round\((.+?)\s*/\s*syminfo\.mintick\)'
        
        def make_replacer(varname_match):
            def replacer(match):
                varname = match.group(1)
                expr = match.group(2).strip()
                # Remove outermost parens if they wrap the entire expression
                if expr.startswith('(') and expr.endswith(')'):
                    # Check if these parens are balanced (the outer ones)
                    depth = 0
                    balanced = True
                    for i, ch in enumerate(expr[1:-1]):
                        if ch == '(':
                            depth += 1
                        elif ch == ')':
                            depth -= 1
                            if depth < 0:
                                balanced = False
                                break
                    if balanced and depth == 0:
                        expr = expr[1:-1]
                return f'{varname} = safeTicks({expr})'
            return replacer
        
        new_code = re.sub(pattern, make_replacer(var), code)
        count = len(re.findall(pattern, code))
        if count > 0:
            tick_fixes += count
            code = new_code
    
    print(f"  [+] Replaced {tick_fixes} profit tick calculations with safeTicks()", file=sys.stderr)
    
    # === FIX 4: Guard trailing stop distances ===
    trail_fixes = 0
    
    # MJV6 Ultra trailing
    for old, new in [
        ('float trailDistance = mjvAtr * 0.6\n            strategy.exit("MJV6U_TRAIL_L"',
         'float trailDistance = math.max(mjvAtr * 0.6, syminfo.mintick)\n            strategy.exit("MJV6U_TRAIL_L"'),
        ('float trailDistance = mjvAtr * 0.6\n            strategy.exit("MJV6U_TRAIL_S"',
         'float trailDistance = math.max(mjvAtr * 0.6, syminfo.mintick)\n            strategy.exit("MJV6U_TRAIL_S"'),
    ]:
        if old in code:
            code = code.replace(old, new, 1)
            trail_fixes += 1
    
    # MES MJV6 trailing (inside if blocks, different indentation)
    old = 'float trailDistance = mjvAtr * 0.6\n                stopPrice := close - trailDistance'
    new = 'float trailDistance = math.max(mjvAtr * 0.6, syminfo.mintick)\n                stopPrice := close - trailDistance'
    if old in code:
        code = code.replace(old, new, 1)
        trail_fixes += 1
    
    old = 'float trailDistance = mjvAtr * 0.6\n                stopPrice := close + trailDistance'
    new = 'float trailDistance = math.max(mjvAtr * 0.6, syminfo.mintick)\n                stopPrice := close + trailDistance'
    if old in code:
        code = code.replace(old, new, 1)
        trail_fixes += 1
    
    # V20 trailing
    old = 'trailDistance = atrMain * 0.6\n            v20ProtectStop := math.max(v20ProtectStop, close - trailDistance)'
    new = 'trailDistance = math.max(nz(atrMain, syminfo.mintick * 20) * 0.6, syminfo.mintick)\n            v20ProtectStop := math.max(v20ProtectStop, close - trailDistance)'
    if old in code:
        code = code.replace(old, new, 1)
        trail_fixes += 1
    
    old = 'trailDistance = atrMain * 0.6\n            v20ProtectStopS := math.min(v20ProtectStopS, close + trailDistance)'
    new = 'trailDistance = math.max(nz(atrMain, syminfo.mintick * 20) * 0.6, syminfo.mintick)\n            v20ProtectStopS := math.min(v20ProtectStopS, close + trailDistance)'
    if old in code:
        code = code.replace(old, new, 1)
        trail_fixes += 1
    
    print(f"  [+] Applied {trail_fixes} trailing distance guards", file=sys.stderr)
    
    # === Verification ===
    remaining = re.findall(
        r'(?:tp\d(?:Points|Ticks)|targetTicks|boosterTargetTicks|mesTargetTicks|mes5mTargetTicks|v71_quickScalpTicks)\s*=\s*math\.round\(.*?/\s*syminfo\.mintick',
        code
    )
    
    if remaining:
        print(f"\n  [!] WARNING: {len(remaining)} potentially unpatched profit tick calculations:", file=sys.stderr)
        for r in remaining:
            print(f"      {r}", file=sys.stderr)
    else:
        print(f"\n  [✓] All profit tick calculations are protected by safeTicks()", file=sys.stderr)
    
    safeticks_count = code.count('safeTicks(')
    print(f"  [✓] Total safeTicks() calls in output: {safeticks_count}", file=sys.stderr)
    
    return code


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 apply_fix.py <input.pine> [output.pine]", file=sys.stderr)
        print("  If output not specified, writes to stdout", file=sys.stderr)
        sys.exit(1)
    
    input_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else None
    
    print(f"Reading: {input_file}", file=sys.stderr)
    with open(input_file, 'r') as f:
        code = f.read()
    
    print(f"Applying Stop Calculation Error fixes...", file=sys.stderr)
    fixed = apply_fix(code)
    
    if output_file:
        with open(output_file, 'w') as f:
            f.write(fixed)
        lines = fixed.count('\n') + 1
        print(f"\nFixed strategy written to: {output_file} ({lines} lines)", file=sys.stderr)
    else:
        print(fixed)


if __name__ == '__main__':
    main()
