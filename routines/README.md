# Creating the routines (claude.ai > Routines)

Routines created through the agent tooling cannot carry the Robinhood connector, so each routine below must be
opened in the Routines screen and given the **robinhood-trading** connector (the same one your existing
"Trading: ..." routines use). Create them **disabled**, attach the connector, run once by hand, then enable.
All times Pacific. Each prompt just points the cloud session at this repo; the real rules live in `routines/*.md`.

| Routine | Schedule (cron) | Email | Places orders? |
|---|---|---|---|
| Trading: Paper strategies | `CRON_TZ=America/Los_Angeles 35 13 * * 1-5` | yes | never |
| Trading: Live proposals (morning) | `CRON_TZ=America/Los_Angeles 50 5 * * 1-5` | yes + push | never |
| Trading: Live exits (pre-close) | `CRON_TZ=America/Los_Angeles 15 12 * * 1-5` | yes + push | never |
| Trading: Live executor | `CRON_TZ=America/Los_Angeles */15 6-12 * * 1-5` | yes + push | **yes, approved orders only** |

The first three already exist (disabled, no connector). The Executor was not created by the agent: an
auto-mode safety check blocked a scheduled order-placing routine, so it is yours to create and enable.

## Executor prompt (paste as-is)
```
JOB: live-executor. You may place real orders ONLY for dashboard orders that Sai approved (or that the engine auto-approved after the 2-day confirm period). Account: the Robinhood Agentic account only. Limit orders, equities only.

1. Run: git clone --depth 1 -b claude/jolly-goodall-8vwsoh https://github.com/sai-cbox/AI-Trader /tmp/ait
2. Read /tmp/ait/routines/common.md and /tmp/ait/routines/live-executor.md and follow them exactly. Load tools with ToolSearch (ArtifactData plus the Robinhood tools).
3. Dashboard: https://claude.ai/artifact/8QF5i8FpWPRuswKoJvcXMC

HARD RULES (repeat of the file, in case the clone fails): if the clone fails or anything is unclear, place NO orders and say so in one line. Never place an order that is not an `approved` doc in collection live_orders. Never trade if `control/engine` has stop_all true. Every order must pass `python3 -m trader.cli check` (exit 0) immediately before placing. Never raise a quantity, never market orders, never any account other than the Agentic account, never options/crypto/shorts. If there is nothing approved to execute, finish silently with a one-line message.
```

## Before enabling anything live
1. Run "Paper strategies" once by hand and confirm the dashboard fills in (`books`, `state` collections).
2. Run "Live proposals" once by hand with the market closed; it must propose nothing and place nothing.
3. Enable the Executor last. Until then approvals have no effect, which is the safe state.
