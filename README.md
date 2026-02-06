# Fix: ContractNotActive Error - Topstep Trade Signal

## Problem

Trade signals sent through TradersPost to Topstep were failing with the error:

```
ContractNotActive
```

The signal was attempting to trade `MGCG2026` (February 2026 Micro Gold contract), which had already expired or entered its delivery period. The strategy was sending continuous contract tickers (`MGC1!`) which TradersPost resolved to the wrong (expired) contract month.

**Original failing signal:**
```json
{
    "ticker": "MGC1!",
    "action": "sell",
    "orderType": "stop_limit",
    "quantity": "1",
    "price": "4826.2",
    "instrument": "MGC",
    "signal_type": "STRONG"
}
```

TradersPost resolved `MGC1!` to `MGCG2026` (February), but the active front month is `MGCJ2026` (April).

## Root Cause

1. **Continuous contract mapping**: The TradingView strategy used `MGC1!` (continuous contract) in alert messages
2. **Expired contract resolution**: TradersPost/broker resolved this to `MGCG2026` (Feb 2026), which had already become inactive for new positions
3. **Prop firm early cutoff**: Topstep stops allowing new positions well before the exchange expiry date (typically around first notice day)

For MGC (Micro Gold):
- Contract months: Feb(G), Apr(J), Jun(M), Aug(Q), Oct(V), Dec(Z)
- MGCG2026 first notice: ~Jan 30, 2026
- MGCG2026 exchange expiry: Feb 25, 2026
- **Topstep cutoff**: Several days before first notice (late January)
- Active front month as of Feb 6, 2026: **MGCJ2026** (April)

## Solution

Three-layer fix to prevent ContractNotActive errors:

### 1. PineScript Strategy Update (`strategy/TL32-V5-PATCHED.pine`)

The `getInstrumentTicker()` function was updated to send **specific active contract months** instead of continuous tickers:

```pine
// NEW: Input fields for active contract months
mgcActiveTicker = input.string("MGCJ2026", "MGC Active Contract", group="Contract Months")
mesActiveTicker = input.string("MESH2026", "MES Active Contract", group="Contract Months")
// ... all instruments

getInstrumentTicker() =>
    if useSpecificContracts
        isMGC ? mgcActiveTicker :
        isMES ? mesActiveTicker :
        // ... maps to specific contract months
    else
        // Falls back to continuous tickers (MGC1!)
```

**How to apply:**
1. Open TradingView
2. Replace your current strategy code with the contents of `strategy/TL32-V5-PATCHED.pine`
3. In the strategy settings, find the "Contract Months" group
4. Verify the active contract months are correct for your instruments
5. Enable "Use Specific Contract Months" (enabled by default)

### 2. Contract Resolver (`src/contract_resolver.py`)

A Python module that automatically resolves continuous tickers to the correct active contract month based on:
- Expiry schedules
- First notice dates
- Prop-firm-specific rollover buffers

```python
from src.contract_resolver import ContractResolver

resolver = ContractResolver()

# Resolve a signal
signal = {"ticker": "MGC1!", "action": "sell", "quantity": "1"}
resolved = resolver.resolve_signal_ticker(signal)
print(resolved["ticker"])  # "MGCJ2026"
```

### 3. Webhook Middleware (`webhook/server.py`)

An optional HTTP middleware that sits between TradingView and TradersPost to automatically resolve contract tickers before forwarding signals.

```
TradingView -> Webhook Middleware -> TradersPost -> Topstep
  (MGC1!)      (resolves to MGCJ2026)   (correct!)
```

## Quick Fix (Immediate)

If you need to fix this immediately without the middleware:

1. **In TradingView**, open the strategy settings
2. Go to the **"Contract Months"** input group
3. Set the correct active contract for each instrument:
   - MGC: `MGCJ2026` (April 2026)
   - MES: `MESH2026` (March 2026)
   - MNQ: `MNQH2026` (March 2026)
   - MYM: `MYMH2026` (March 2026)
   - MCL: `MCLH2026` (March 2026)
   - M2K: `M2KH2026` (March 2026)
4. Make sure "Use Specific Contract Months" is **enabled**

## Contract Rollover Schedule

When contracts expire, update the active contract in:
1. TradingView strategy settings (Contract Months inputs)
2. `config/contract_rollover.json` (if using the middleware)

### MGC (Micro Gold) - Bimonthly
| Contract | Month Code | 2026 Expiry | First Notice |
|----------|-----------|-------------|--------------|
| MGCG2026 | G (Feb)   | Feb 25      | Jan 30       |
| MGCJ2026 | J (Apr)   | Apr 28      | Mar 31       |
| MGCM2026 | M (Jun)   | Jun 26      | May 29       |
| MGCQ2026 | Q (Aug)   | Aug 27      | Jul 31       |
| MGCV2026 | V (Oct)   | Oct 28      | Sep 30       |
| MGCZ2026 | Z (Dec)   | Dec 29      | Nov 30       |

### MES/MNQ/MYM/M2K (Index Futures) - Quarterly
| Contract | Month Code | 2026 Expiry |
|----------|-----------|-------------|
| xxxH2026 | H (Mar)   | Mar 20      |
| xxxM2026 | M (Jun)   | Jun 19      |
| xxxU2026 | U (Sep)   | Sep 18      |
| xxxZ2026 | Z (Dec)   | Dec 18      |

### MCL (Micro Crude Oil) - Monthly
See `config/contract_rollover.json` for full schedule.

## Project Structure

```
.
├── README.md                          # This file
├── config/
│   └── contract_rollover.json         # Contract rollover configuration
├── src/
│   ├── contract_resolver.py           # Core contract resolution logic
│   └── pinescript_patch.py            # PineScript code generator
├── strategy/
│   └── TL32-V5-PATCHED.pine          # Patched TradingView strategy
├── webhook/
│   └── server.py                      # Webhook middleware server
├── tests/
│   └── test_contract_resolver.py      # Unit tests (33 tests)
└── requirements.txt                   # Python dependencies
```

## Running Tests

```bash
pip install pytest
python -m pytest tests/ -v
```

## Running the Webhook Middleware (Optional)

```bash
export TRADERSPOST_WEBHOOK_URL="https://traderspost.io/trading/webhook/YOUR_ID"
export WEBHOOK_PORT=8080
python webhook/server.py
```

Then point your TradingView alerts to `http://your-server:8080/webhook` instead of directly to TradersPost.

## API Endpoints (Webhook Middleware)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/webhook` | Process and forward trading signals |
| POST | `/diagnose` | Diagnose ContractNotActive errors |
| GET | `/health` | Health check |
| GET | `/stats` | Processing statistics |
| GET | `/contracts` | Current active contracts |
