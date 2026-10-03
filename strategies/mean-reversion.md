# mean-reversion (paper)
Buys short-term oversold quality stocks expecting a bounce; sells on reversion to the mean.

## Universe
S&P 500 members, price > SMA200 (long-term uptrend only).

## Entry (all must hold)
- RSI2 < 10 or close below the lower Bollinger band (20, 2)
- Down at least 3 days in a row; no earnings within 3 trading days

## Exit (any)
- RSI2 > 60 or close above SMA5; time stop after 7 trading days; hard stop 6% below entry

## Sizing
Equal weight, up to 10% each, max 6 open positions.

## Explain (log in `signals`)
`rsi2`, `bollinger_pos`, `down_days`, `price_vs_sma200`, `exit_rule`.
