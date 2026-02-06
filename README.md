# Pine Script execution fix notes

This repo currently documents a compile fix for the TradingView Pine v5 error:

`Syntax error at input ',"reason":"max_holding_time"'`

## Root cause

In Pine, multiline string concatenation **must** keep the `+` operator between lines. If the line above `,"reason":"max_holding_time"` doesn’t end with `+`, Pine reads the next line as a new statement and throws the syntax error.

## Fix

Ensure the previous line ends with `+` (or put `+` at the start of the `reason` line).

Example (correct):

```pine
exitAlert = '{"ticker":"' + alertSymbol + '"' +
    ',"action":"exit"' +
    ',"intent":"CLOSE"' +                      // critical `+`
    ',"reason":"max_holding_time"' +
    ',"side":"long"' +
    ',"holding_bars":' + str.tostring(holdingPeriod) +
    ',"signal_id":"' + mkSignalId("MAXTIME_LONG") + '"' +
    ',"timestamp":"' + str.tostring(time) + '"' +
    '}'
```

See `snippets/max_holding_time_exit_alert_fix.pine` for long + short blocks.