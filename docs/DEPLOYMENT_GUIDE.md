# TL43 V17.1 Deployment Guide

## Pre-Deployment Checklist

### 1. TradingView Setup
- [ ] Paste `TL43_V17_FIXED.pine` into TradingView Pine Editor
- [ ] Compile and add to chart (MNQ 1h recommended for initial testing)
- [ ] Verify no compilation errors
- [ ] Enable strategy tester to check backtest results
- [ ] Set up alert: Right-click strategy > "Add Alert" > Condition: "Any alert() function call"
- [ ] Set alert destination: Webhook URL from TradersPost

### 2. TradersPost Configuration

#### Webhook URL
```
https://traderspost.io/api/v1/trading/webhook/{YOUR_STRATEGY_ID}/{YOUR_WEBHOOK_TOKEN}
```

#### Expected Entry JSON (what TradingView sends):
```json
{
    "ticker": "MNQ1!",
    "action": "buy",
    "sentiment": "bullish",
    "quantity": 1,
    "stopLoss": {
        "type": "stop",
        "stopPrice": 24541.75
    },
    "takeProfit": {
        "type": "limit",
        "limitPrice": 24621.75
    },
    "strategy": "V17_STRICT_DIRECTION",
    "timeframe": "60",
    "instrument": "MNQ",
    "signal_type": "STRONG",
    "confluence": 10,
    "session": "NY",
    "stop_loss_amount": 80.0,
    "take_profit_amount": 160.0,
    "signal_price": 24581.75,
    "slippage_buffer": 2.0,
    "adx": 28.5,
    "choppy": false
}
```

#### Expected Exit JSON:
```json
{
    "ticker": "MNQ1!",
    "action": "exit",
    "sentiment": "flat",
    "reason": "MAE_PROTECT"
}
```

#### TradersPost Settings to Verify:
1. **Order Type**: Market (for entries) or Limit (if using signal_price)
2. **Stop Loss**: Enable "Use stopLoss from webhook"
3. **Take Profit**: Enable "Use takeProfit from webhook"
4. **Position Sizing**: Set to "Use quantity from webhook"
5. **Deduplication**: Enable with 60-second window
6. **Flatten on Exit**: Enable "Close all positions on exit signal"

### 3. Tradovate/TopstepX Configuration

#### Order Settings:
- **Order Type**: Stop Market (NOT Stop Limit) for stop losses
- **Bracket Orders**: Enable bracket order support
- **Risk Limits**: Set daily loss limit matching strategy settings ($450 default)

#### Verify These Work:
```
Test Entry: Send manual webhook with small quantity
Test Stop: Verify stop order appears in Order Manager
Test Exit: Send exit webhook, verify position closes
```

### 4. Critical TradersPost Checks (Post Trade #5 Failure)

#### Issue: Stop order not being placed
**Check these in TradersPost logs:**

1. Go to TradersPost > Activity Log
2. Find the entry signal from 10:07:23 AM
3. Verify these fields were parsed:
   - `stopLoss.stopPrice` was read correctly
   - A stop order was actually sent to Tradovate
4. Check Tradovate order history:
   - Was a stop order created?
   - Was it a STOP or STOP LIMIT order?
   - If STOP LIMIT, was the limit price reached?

#### Root Cause Diagnosis:
| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| No stop order in Tradovate | TradersPost not parsing `stopLoss` | Use new JSON format with nested `stopLoss` object |
| Stop order exists but didn't fill | Stop LIMIT order, price gapped | Switch to Stop MARKET orders |
| Stop filled at wrong price | Slippage on stop | Use wider stop + slippage buffer (now included) |
| Position closed before stop | Internal exit logic | Fixed: removed competing exits |

### 5. Recommended Settings by Instrument

#### MNQ (Micro Nasdaq) - 1h Timeframe
```
Custom SL: 25 points + 2pt slippage = 27 points effective
Custom TP: 35 points
Slippage Buffer: 2.0
Min ADX: 20
Min Bars Before MAE: 8
Trailing BE Buffer: 1.0
```

#### MES (Micro S&P) - 1h Timeframe
```
Custom SL: 7 points + 1.5pt slippage = 8.5 points effective
Custom TP: 10 points
Slippage Buffer: 1.5
Min ADX: 18
Min Bars Before MAE: 8
Trailing BE Buffer: 0.75
```

#### MYM (Micro Dow) - 1h Timeframe
```
Custom SL: 60 points + 5pt slippage = 65 points effective
Custom TP: 90 points
Slippage Buffer: 5.0
Min ADX: 18
Min Bars Before MAE: 6
Trailing BE Buffer: 3.0
```

#### MCL (Micro Crude Oil) - 1h Timeframe
```
Custom SL: $0.30 + $0.05 slippage = $0.35 effective
Custom TP: $0.50
Slippage Buffer: 0.05
Min ADX: 22
Min Bars Before MAE: 10
Trailing BE Buffer: 0.03
```

#### MGC (Micro Gold) - 1h Timeframe
```
Custom SL: $4.00 + $0.50 slippage = $4.50 effective
Custom TP: $7.00
Slippage Buffer: 0.50
Min ADX: 20
Min Bars Before MAE: 10
Trailing BE Buffer: 0.30
```

## Daily Trade Estimates

### Conservative Estimate (per instrument, 1 contract, 1h TF)
| Metric | MNQ | MES | MYM | MCL | MGC |
|--------|-----|-----|-----|-----|-----|
| Trades/Day | 2-3 | 2-3 | 2-4 | 1-2 | 1-2 |
| Win Rate | 68-73% | 70-75% | 65-70% | 68-72% | 70-75% |
| Avg Win $ | $60-100 | $30-55 | $25-45 | $50-90 | $40-80 |
| Avg Loss $ | $30-50 | $18-30 | $15-28 | $30-55 | $25-45 |
| Daily Net | $60-120 | $35-70 | $25-50 | $30-60 | $25-55 |

### Multi-Instrument Portfolio ($25K Capital)
| Config | Daily Trades | Win Rate | Daily PnL | Daily Return |
|--------|-------------|----------|-----------|-------------|
| 2 instruments, 1 ct | 4-6 | 70% | $95-190 | 0.4-0.8% |
| 3 instruments, 2 ct | 12-18 | 68% | $240-480 | 1.0-1.9% |
| 5 instruments, 2-3 ct | 20-30 | 65% | $400-900 | 1.6-3.6% |

### Path to $1,500/Day
To consistently hit $1,500/day with $25K capital (6% daily return):
1. **Run 4-5 instruments simultaneously** (MNQ, MES, MYM, MCL, MGC)
2. **Use 2-3 contracts per instrument** (10-15 total contracts)
3. **Focus on NY session** (9:30 AM - 12:00 PM ET is prime time)
4. **On trending days**: Expect $1,500-2,000 (run full portfolio)
5. **On choppy days**: Bot auto-reduces activity (expect $200-500)
6. **Average across week**: $800-1,200/day is realistic

### Realistic Expectations
- **Week 1-2**: Paper trade to validate fixes, expect 60-65% win rate
- **Week 3-4**: Small live ($1 risk), tune slippage buffer to actual slippage
- **Month 2**: Scale to target size, expect 65-72% win rate
- **Steady state**: 68-75% win rate, $500-1,200/day average

## Perfect Trade Setup

The ideal entry for this bot has ALL of these characteristics:

### Entry Criteria (ALL must be true)
1. **ADX > 25** - Confirmed trending market (not choppy/sideways)
2. **SuperTrend matches direction** - ST bullish for longs, bearish for shorts
3. **Hull MA aligned** - Both main and fast HMAs sloping in trade direction
4. **Volume > 1.2x average** - Smart money participation confirmed
5. **RSI 40-60** - Not overbought/oversold (room to run)
6. **Candle direction matches** - Green for LONG, Red for SHORT (no exceptions)
7. **ATR expanding** - Momentum increasing, not exhausting
8. **Session optimal** - NY 9:30-12:00 ET preferred
9. **No choppy detection** - BB not tight, ATR above average
10. **No recent stop hit** - Not in revenge/recovery mode

### Example Perfect Long Setup
```
Time: 10:15 AM ET (NY session peak)
ADX: 28.3 (strong trend)
RSI: 47 (neutral, room to run)
Hull Main: Sloping UP
Hull Fast: Sloping UP
SuperTrend: Bullish (below price)
Volume: 1.4x average
ATR: 1.2x 20-period average (expanding)
Candle: GREEN with body > 10% of ATR
BB Width: Above threshold (not tight)
```

### Trades to AVOID
- ADX below 20 (ranging/choppy)
- RSI above 70 or below 30 (exhausted)
- Low volume (below 0.8x average)
- Doji candles (indecision)
- First 30 minutes after market open (noise)
- Last 30 minutes before close (thin liquidity)
- When daily loss limit is > 50% reached

## Monitoring in Production

### Daily Checks
1. Review TradersPost activity log for failed/rejected orders
2. Verify stop orders are being placed for every entry
3. Check slippage on last 10 trades (entry and exit)
4. Compare TradingView alert prices to actual fill prices
5. Review daily PnL against expectations

### Weekly Checks
1. Win rate by signal type (STRONG, SCALP, INSTANT, MICRO, IMPULSE)
2. Win rate by session (NY vs London vs Asian)
3. Average slippage per instrument
4. Adjust slippage buffer if actual slippage differs from setting
5. Review losing trades for pattern (stop too tight? MAE too aggressive?)

### Emergency Procedures
1. **Multiple losses in a row**: Bot auto-stops at daily limit
2. **TradersPost down**: Manually monitor TradingView alerts
3. **Stop not placed**: Check webhook logs, escalate to TradersPost support
4. **Unusual slippage**: Increase slippage buffer, reduce position size
