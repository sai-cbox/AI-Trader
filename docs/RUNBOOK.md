# Runbook

## Phase 0: sign-in check (run on YOUR machine, needs a browser)
```
git clone -b claude/jolly-goodall-8vwsoh https://github.com/sai-cbox/AI-Trader && cd AI-Trader
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev,exec]"
trader auth-check
```
A browser tab opens once for Robinhood to approve access. Expected: PASS lines for sign-in, the read-only guard,
the Agentic account (shown masked) matching `allowed_account_id`, and a portfolio read. Nothing is traded: the client
refuses every tool that is not a `get_*` read. Tokens are stored in `~/.config/ai-trader/robinhood_oauth.json` (0600).
If it fails, send me the printed `[FAIL]` lines (they contain no secrets).

## Phase 1a: probe (read-only; shows the SHAPE of Robinhood replies, no amounts)
```
trader probe
```
Calls ~20 read tools (portfolio, positions, orders, quotes, indicators, history, fundamentals, earnings, your saved scan).
Prints field names and types only. Full replies are saved to `data/probe/` on your machine (private, gitignored).

## Phase 1b: data check (read-only, no Claude, no orders)
```
trader data-check
```
Fetches your account, SPY/QQQ regime, your saved scan, fundamentals, earnings, and computes (in code) the trend template,
21-EMA extension, MACD state and 30-day strength for the top candidates and your holdings. Prints the funnel and a table.
If a Robinhood reply has an unexpected shape, it prints `[FAIL]` with the field names it found: send me those lines.

## Phase 1c: dry run with Claude (costs a few cents; NO order code exists)
```
trader set-key                          # paste your Anthropic key (hidden); stored privately in ~/.config/ai-trader/
trader start --book momentum-quality    # once: lets the guard accept dry-run decisions
trader run
```
Prints what the analyst proposed, the code-side fact check, the guard verdict, token cost, and "WOULD PLACE (dry run)".
Everything is journaled. The script stops calling Claude once the day's estimated spend reaches `analyst_daily_cap_usd`.

## Engine commands (paper/live bookkeeping)
`trader start|stop|pause|resume`, `trader status`, `trader check`, `trader paper-order`, `trader report`,
`trader dashboard` (local, 127.0.0.1) and `trader dashboard --export file.html`.
Kill switch: `touch data/STOP` (or `trader stop`).

See `docs/SPEC.md` for the full design and guardrails.
