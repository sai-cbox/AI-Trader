# earnings-drift (paper)
Buys stocks after a strong earnings beat with a positive price reaction (post-earnings drift).

## Universe
US equities with analyst coverage, price > $10.

## Entry (all must hold)
- EPS surprise > +5% and revenue beat; day-after-earnings gap up > 3% that holds above the gap-day low
- Entry no later than 2 trading days after the report

## Exit (any)
- Hold up to 30 trading days; stop if close fills the gap (below pre-earnings close); trailing stop 8%

## Sizing
Equal weight, up to 10% each, max 5 positions.

## Explain (log in `signals`)
`eps_surprise_pct`, `revenue_beat`, `gap_pct`, `days_since_report`, `exit_rule`.
