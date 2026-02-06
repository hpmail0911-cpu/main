# TL43 V.18 PRODUCTION - 5-Star Scalper Strategy

## Files

| File | Description |
|------|-------------|
| `TL43_V18_PRODUCTION.pine` | Production-ready PineScript v5 strategy with 14 critical bug fixes |
| `ANALYSIS_AND_DEPLOYMENT_GUIDE.md` | Comprehensive bug analysis, trade estimates, and deployment checklist |

## Quick Start

1. Copy the contents of `TL43_V18_PRODUCTION.pine` into TradingView Pine Editor
2. Apply to your chart (MNQ, MES, MYM, MGC, MCL, or M2K)
3. Configure per-instrument SL/TP in the settings panel
4. Set up TradersPost webhook with the alert
5. Paper trade for 1 week minimum before going live

## Critical Fixes from V.17

- **Duplicate entries removed** - was creating 2 positions per signal
- **Unified SL/TP** - alerts and strategy.exit now use identical values
- **Slippage buffer added** - configurable per instrument (default 4 ticks)
- **Trailing stop after BE** - activates after recovering slippage + profit buffer
- **Bar-based timing** - barsInPosition was counting ticks (firing exits in seconds)
- **Loss cooldown** - prevents revenge trading after stops
- **Proper TradersPost JSON** - bracket order format with stopLoss/takeProfit objects
- **Reduced signal types** - only STRONG, SCALP, IMPULSE (removed low-quality MICRO/INSTANT)

## Recommended Settings for Live Trading

- High Win Rate Mode: ON
- Slippage Buffer: 4 ticks
- Trailing Stop: ON
- Loss Cooldown: ON
- Confluence Level: 10
- Fixed Contract Size: 1 (start small)
- Daily Trade Limit: 15

See `ANALYSIS_AND_DEPLOYMENT_GUIDE.md` for full deployment checklist and trade estimates.
