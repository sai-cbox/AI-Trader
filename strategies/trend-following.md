# trend-following (paper)
Rides long, slow trends in liquid ETFs/large caps using moving-average regime filters.

## Universe
Liquid equity ETFs (SPY, QQQ, IWM, XLK, XLF, XLE, XLV) plus mega-caps.

## Entry (all must hold)
- SMA50 crosses above SMA200 (or price > both with SMA50 rising for 10 days)
- ADX14 > 20

## Exit (any)
- SMA50 crosses below SMA200; or close below SMA100 for 2 consecutive days

## Sizing
Equal weight, up to 10% each. Rebalance at most weekly (low turnover).

## Explain (log in `signals`)
`sma50`, `sma200`, `adx14`, `regime`, `exit_rule`.
