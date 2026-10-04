# Runbook: how the agent drives the engine

Robinhood is reached through MCP tools in the agent session. The engine (`trader`) is the gate and the journal.

## One scheduled run (per strategy, during market hours)
1. `trader status` — if the strategy is not `running`, or `stop_file` is true: **stop, place nothing**.
2. Pull data (quotes, historicals, indicators, earnings) with the Robinhood data tools.
3. Apply `strategies/<name>.md`. For every idea, build a proposal with `rationale` and `signals`
   (the indicator values and rules that fired). This is what the dashboard shows as the "why".
4. **Paper strategies:** `trader paper-order --book <name> --symbol S --side buy --qty N --quote P --rationale "..." --signals '{...}'`
   then `trader paper-mark --book <name> S=P ...` to mark positions.
5. **Live strategy (momentum-quality = your `robinhood-stock-trader` skill):**
   a. Run the skill's Step 1-2 (portfolio, SPY/QQQ regime). Record it: `trader regime RISK-ON|RISK-OFF`.
      RISK-OFF => no buys this run (exits still run).
   b. `trader snapshot --book momentum-quality` with the real account JSON (`account_id` = the Agentic account number, `equity`, `cash`, `positions`).
   c. For each exit/entry the skill produces, `trader check --book momentum-quality` with
      `{"proposal":{symbol,side,qty,order_type:"limit",limit_price,stop_price,target_price,rationale,signals:{phases:{...},sector,is_ai,earnings_days}},"account":{...},"quote":P}`.
      Exit code 2 = rejected: do nothing, do not retry with a tweaked order.
   d. If the result has `needs_user_approval: true` (first 2 days): **stop and ask the user** with the full trade card
      (what, why, phase evidence, stop/target, size). Only after an explicit yes: `trader approve --decision-id ID`.
   e. Place the order for the returned `qty` with `review_equity_order` then `place_equity_order` (limit).
   f. `trader record --book momentum-quality --symbol S --side buy --qty N --price P --decision-id ID --ref ORDER_ID`
      (refuses if the decision was rejected or an unapproved confirm-period order).
6. Never place an order that did not pass `check`. Never trade outside the allow-listed account.

## Human controls
- `trader stop [--book X]` or `touch data/STOP` — kill switch (blocks everything, incl. exits).
- `trader pause [--book X]` — no new buys, exits allowed. `trader resume --book X` to continue.
- Dashboard: `trader dashboard` (http://127.0.0.1:8765) has Pause/Stop buttons; resume/start only via CLI.
- Auto-stops: daily loss 3% (no new buys that day), drawdown 12% from peak (auto-pause), warning at 6%.

## Setup checklist
1. Set `allowed_account_id` in `config/default.toml` to your Agentic account ID (`get_accounts`).
2. `trader start --paper` (all four paper strategies), run a few paper days as a smoke test.
3. `trader start --book momentum-quality` only when you decide to go live.
4. `trader dashboard --export report.html` for a shareable snapshot.


## Cloud operation (scheduled routines)
Live mode is fully automatic (`confirm_days = 0`): proposals are auto-approved, with a 10-minute veto window on the dashboard.
Routine prompts live in `routines/` (common boot + paper-strategies, live-proposer, live-executor). State is kept per
strategy in the dashboard database (`state/<book>`) because every cloud run starts in a fresh container; the dashboard
page also reads `books/<book>`, `live_orders` and `control/engine`. See `routines/common.md`.
