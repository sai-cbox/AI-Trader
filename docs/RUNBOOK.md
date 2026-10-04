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

## Engine commands (paper/live bookkeeping)
`trader start|stop|pause|resume`, `trader status`, `trader check`, `trader paper-order`, `trader report`,
`trader dashboard` (local, 127.0.0.1) and `trader dashboard --export file.html`.
Kill switch: `touch data/STOP` (or `trader stop`).

See `docs/SPEC.md` for the full design and guardrails.
