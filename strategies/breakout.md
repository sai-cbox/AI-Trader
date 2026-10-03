# breakout (paper)
Buys stocks breaking out of tight consolidations to new highs on heavy volume.

## Universe
US equities, price > $15, avg volume > 1M.

## Entry (all must hold)
- Close above the 55-day high; volume >= 1.5x its 50-day average
- ATR14 / price below 4% over the prior 20 days (a tight base)

## Exit (any)
- Close below the 20-day low; trailing stop 2.5x ATR14; hard stop 8%

## Sizing
Risk-based: 1% of equity risked per trade (stop distance), capped at 10% position size.

## Explain (log in `signals`)
`high55_break`, `volume_ratio`, `atr_pct`, `stop_price`, `exit_rule`.
