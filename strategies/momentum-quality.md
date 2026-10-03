# momentum-quality (LIVE)
Buys strong-trend stocks that also pass basic quality screens; sells when trend or quality breaks.

> DRAFT rules: replace/align with your `robinhood-stock-trader` skill rules before going live.

## Universe
US large/mid-cap equities, avg volume > 1M shares, price > $10.

## Entry (all must hold)
- 6-month return in the top quintile of the universe; price > SMA50 > SMA200
- RSI14 between 50 and 75 (strong but not extreme)
- Quality: positive net income, debt/equity below sector median
- No earnings release within 3 trading days

## Exit (any)
- Close below SMA50; or trailing stop 8% from the high since entry; or hard stop 7% from entry
- Quality screen fails after a new filing

## Sizing
Equal weight, up to the global 10% per-position cap. Max 8 open positions.

## Explain (log in `signals`)
`ret_6m`, `rsi14`, `price_vs_sma50`, `price_vs_sma200`, `quality_checks`, `exit_rule` (on sells).
