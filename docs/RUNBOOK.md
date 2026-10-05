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

## Paper strategies (4 mechanical strategies, paper money only)
```
trader start --paper     # once
trader paper-run         # run after the close (1:15 PM PT or later); safe to run more than once a day
trader report --book breakout
```
Rules are exactly those in `strategies/*.md`, coded without any LLM, so results are repeatable and cost nothing.
Paper fills happen at the latest close plus slippage, which is optimistic: treat paper results as an upper bound.

## Phone alerts (optional, free)
1. Install the ntfy app (iOS/Android), tap "+", and subscribe to a topic name you invent, e.g. `ait-` plus 16 random characters.
2. Put the same string in `config/default.toml` as `ntfy_topic = "..."`. Never put balances or secrets in alerts (anyone who guesses the topic can read it).
3. `trader alerts-test` should buzz your phone.

## Engine commands (paper/live bookkeeping)
`trader start|stop|pause|resume`, `trader status`, `trader check`, `trader paper-order`, `trader report`,
`trader dashboard` (local, 127.0.0.1) and `trader dashboard --export file.html`.
Kill switch: `touch data/STOP` (or `trader stop`).

See `docs/SPEC.md` for the full design and guardrails.

## Daily summary email
1. Google Account > Security > turn on 2-Step Verification, then search "App passwords" and create one (16 letters).
2. `trader set-email-password` (hidden input; saved to `~/.config/ai-trader/smtp_password`, mode 600).
3. `trader daily-summary --print` shows the text; `trader daily-summary` sends it to `email_to` in `config/default.toml`.
Sent straight from your machine to smtp.gmail.com over SSL. Run it after `paper-run` / `data-check` so the numbers are fresh.

## Schedule (4 runs a day, US/Eastern; still dry-run, no orders)
`trader tick` runs whichever slot is due and never twice a day: morning 09:45 (exits only), midday 12:30 (exits only),
entry 15:45 (the only run that may open positions), close 16:20 (paper strategies + summary email).
- Mac (only works while awake): `sh deploy/install_mac_schedule.sh`; log in `data/tick.log`.
- Cloud VM: see below.

## Move to a cloud VM
1. Create the smallest Ubuntu VM you can (1 vCPU, 1 GB is enough) at any provider. No inbound ports needed except SSH.
2. SSH in, then: `curl -fsSL https://raw.githubusercontent.com/sai-cbox/AI-Trader/claude/jolly-goodall-8vwsoh/deploy/vm_setup.sh | sh`
   (or clone the repo and run `sh deploy/vm_setup.sh`).
3. From the Mac copy the secrets (never commit them): the Anthropic key `~/.config/ai-trader/anthropic_key`, the Robinhood
   token file (path shown by `trader auth-check`), and optionally `smtp_password`, into `~/.config/ai-trader/` on the VM, mode 600.
   Robinhood sign-in is a browser step, so it is done once on the Mac and the token is copied; if it expires you repeat that.
4. Dashboard stays private: on your Mac run `ssh -L 8765:127.0.0.1:8765 user@VM`, then open http://127.0.0.1:8765
   (start it on the VM with `trader dashboard`, or as a service).
5. Turn the Mac schedule off (`launchctl unload ~/Library/LaunchAgents/com.ai-trader.tick.plist`) so two machines never run.
