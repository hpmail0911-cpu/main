# TL43 (Alerts-Only, Strict Direction) — Live-safe TradingView→TradersPost template

This repo contains a Pine v5 strategy designed for **webhook alerts only** (TradingView → TradersPost → Tradovate/TopstepX).

The main goal is to prevent a common live failure mode: **“alerts-only” scripts do not update `strategy.position_size`, so they keep firing opposite entries while the broker is already in a trade**. TradersPost then flattens/reverses early, which *looks like a stop-loss failure* even when the stop was never reached.

## Files

- `TL43_alerts_only_strict_direction.pine`: alerts-only, strict candle direction, with bracket SL/TP, breakeven + trailing logic (with optional stop update alerts).

## Why your stop “failed” (most likely)

If your script has logic like:

- “Only alert when `strategy.position_size == 0`”
- **AND** “alerts-only mode disables `strategy.entry()`”

…then `strategy.position_size` stays **0 forever**, so the script can emit:

- a `sell` entry alert (broker opens SHORT),
- then a few minutes later a `buy` entry alert (broker closes/reverses the SHORT **before** your stop is hit).

In logs, that looks like: *entered short → exited early before stop*, but it was **an opposite-signal flatten/reverse**, not a stop trigger.

This repo’s script fixes that by tracking a **shadow position** (`shadowPos`, `shadowEntry`, `shadowSL`, `shadowTP`) while in alerts-only mode.

## Webhook JSON reliability fixes

Many webhook parsers are picky about types. This script emits:

- **numeric fields unquoted**: `"stop_loss": 24621.75` not `"stop_loss":"24621.75"`
- `signal_id` on entries (and optionally on stop updates/exits) so your automation can correlate messages
- optional `"order_type":"limit"` and `"limit_price": ...` fields for slippage control
- optional `stop_loss_amount`/`take_profit_amount` dollar offsets (computed using `syminfo.pointvalue`)

## Trailing stops with TradersPost (important)

Broker-side trailing requires **updating the stop** after entry. This script can emit optional stop-update alerts:

- Enable `Send STOP UPDATE alerts (advanced)`
- Ensure your TradersPost bot is configured to treat the `action` (default `"update"`) as a stop update, **not** as a new entry.

If your TradersPost bot does **not** support stop updates, keep stop updates disabled and rely on broker brackets (static SL/TP).

## Recommended “perfect setup” checklist (practical, not a guarantee)

- **Chart / signals**
  - Use **bar-close confirmation** (`Confirm on bar close`) for live to reduce intrabar flip noise.
  - Keep **STRICT candle direction** on (Long=green, Short=red).
  - Keep the **chop filter** on (ADX/ATR/BB width) to avoid ranging markets.
  - If you enable MTF, use it as a **filter** (higher win rate, fewer trades).

- **Execution**
  - Prefer **LIMIT entries** with a small allowed slippage window.
    - This script uses a *marketable limit cap* (buy limit slightly above / sell limit slightly below) to reduce catastrophic slippage while still filling quickly.
  - Add a small **stop buffer** (ticks) to survive spread/fast prints.
  - Use **ATR brackets** on volatile symbols; use fixed brackets only if you’ve validated they fit that instrument’s behavior.

- **Stop management**
  - Use **breakeven** once profit is real (ATR-based trigger).
  - Trail **after** breakeven (don’t trail too early).
  - Only enable **STOP UPDATE alerts** if your TradersPost bot is configured to interpret them.

## Important realism note (no guarantees)

It’s not possible to guarantee outcomes like **80% win rate**, **$1500/day**, or **1.5% daily returns** purely from code changes. What we can do (and what this repo focuses on) is:

- ensure the live alert flow is **state-consistent** (no accidental flips),
- ensure SL/TP fields are **parsed correctly** by TradersPost,
- reduce low-quality entries via **regime + momentum filters**,
- make exits predictable (breakeven → trail).
