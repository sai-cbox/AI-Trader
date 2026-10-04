# Routine: Trading: Paper strategies (weekdays 1:35 PM PT, after the close)

Follow routines/common.md (Boot). Books: mean-reversion, breakout, trend-following, earnings-drift (all PAPER).
HARD RULE: this routine NEVER calls any order tool. Paper only. Use Robinhood data tools (quotes, historicals,
technical indicators, fundamentals, earnings) for data.

1. Boot; import state for the 4 paper books. If it is the first run: `$T start --paper`.
2. If the US market was closed today, finish with "Market closed".
3. For each paper book, read `strategies/<book>.md` (rules are the source of truth), then:
   a. Mark open positions to today's close: `$T paper-mark --book <b> SYM=PRICE ...` (also records the equity snapshot).
   b. Apply the exit rules to every open position; for each exit, `$T paper-order --book <b> --symbol S --side sell
      --qty N --quote P --rationale "..." --signals '{"exit_rule":"..."}'`.
   c. Screen the strategy's universe with today's data. For each entry that satisfies ALL entry rules:
      `$T paper-order --book <b> --symbol S --side buy --qty N --quote P --rationale "plain-English why"
      --signals '{...the indicator values and rules that fired, as listed in the strategy file...}'`.
      Sizing: equal weight up to the 10% cap; the guard shrinks it if needed. Quote = today's close.
   Max 6 new orders per book per run. If nothing qualifies, place nothing (cash is fine).
4. Export state, sync-docs and publish per common.md.
5. Final message (this becomes the email): one line per book: name, equity, return %, trades today; plus anything
   the guard rejected. Under 120 words. Rules-based paper results, not financial advice.
