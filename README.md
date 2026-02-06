# TL43 V17.1 - 5-Star Scalper Ultimate (Fixed)

## Strategy Overview
Micro futures scalping strategy for TradingView with TradersPost/Tradovate integration. Supports MNQ, MES, MYM, MCL, MGC, M2K across multiple sessions (NY, London, Asian, UAE).

## Key Features
- Strict candle direction enforcement (Long=Green, Short=Red)
- 4-tier progressive take profit (50/30/15/5%)
- Dynamic trailing stop after breakeven + slippage buffer
- Impulse/breakout override for momentum trades
- Choppy market filter with trend override bypass
- Multi-timeframe confirmation (optional)
- 5-star entry quality scoring
- Nuclear emergency exit system
- 24/7 session support with optimal instrument detection

## Files
- `strategy/TL43_V17_FIXED.pine` - Complete fixed PineScript strategy
- `ANALYSIS.md` - Bug analysis report (12 critical issues found and fixed)
- `docs/DEPLOYMENT_GUIDE.md` - Deployment checklist and configuration guide

## Critical Fixes (V17.1)
1. **Removed duplicate entry blocks** - Was causing stop order failures
2. **Unified SL/TP calculation** - Alert and strategy now use identical values
3. **Fixed TradersPost JSON** - Proper `stopLoss`/`takeProfit` nested objects
4. **Added slippage buffer** - Configurable per-instrument protection
5. **Trailing after breakeven + buffer** - Never drops below BE once reached
6. **Cooperative exit system** - Single `strategy.exit()` per bar, no competing exits
7. **Fixed MAE thresholds** - Minimum bars before trigger, less aggressive
8. **Fixed daily loss tracking** - `maxLossReached` now actually works
9. **ADX minimum for entries** - Prevents trades in ranging/sideways markets
10. **Exit alerts for TradersPost** - Sends exit signals for stop management

## Quick Start
1. Copy `strategy/TL43_V17_FIXED.pine` into TradingView Pine Editor
2. Add to chart (start with MNQ 1h)
3. Set up TradingView alert with webhook to TradersPost
4. Configure TradersPost to parse `stopLoss` and `takeProfit` from webhook
5. See `docs/DEPLOYMENT_GUIDE.md` for full setup instructions
