# Runbook: how the agent drives the engine

Robinhood is reached through MCP tools in the agent session. The engine (`trader`) is the gate and the journal.

## One scheduled run (per strategy, during market hours)
1. `trader status` — if the strategy is not `running`, or `stop_file` is true: **stop, place nothing**.
2. Pull data (quotes, historicals, indicators, earnings) with the Robinhood data tools.
3. Apply `strategies/<name>.md`. For every idea, build a proposal with `rationale` and `signals`
   (the indicator values and rules that fired). This is what the dashboard shows as the "why".
4. **Paper strategies:** `trader paper-order --book <name> --symbol S --side buy --qty N --quote P --rationale "..." --signals '{...}'`
   then `trader paper-mark --book <name> S=P ...` to mark positions.
5. **Live strategy:**
   a. `trader snapshot --book <live>` with the real account JSON (`account_id`, `equity`, `cash`, `positions`).
   b. `trader check --book <live>` with `{"proposal":{...},"account":{...},"quote":P}` on stdin.
   c. Exit code 0 only: place the order for the **returned `qty`** with the Robinhood order tool
      (limit orders preferred). Exit code 2 = rejected: do nothing, do not retry with a tweaked order.
   d. After the broker confirms, `trader record --book <live> --symbol S --side buy --qty N --price P --decision-id ID --ref ORDER_ID`.
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
