# Routine: Trading: Live proposals (weekdays 5:50 AM PT and 12:30 PM PT)

Follow routines/common.md (Boot). Book: momentum-quality (LIVE, whole Agentic account).
HARD RULE: this routine NEVER calls place_equity_order or any order-placing/cancelling tool. It only proposes.
The Executor routine is the only thing that places orders.

1. Boot; import state for momentum-quality. If `$T status --book momentum-quality` says stopped/paused: stop here.
2. Load and follow the `robinhood-stock-trader` skill for ALL analysis (sizing, Phase 1-8, exits). Skill outputs are
   proposals here, never orders. Run STEP 0 snapshot, Phase 1 market regime, then:
   `$T regime RISK-ON` or `$T regime RISK-OFF` (record Phase 1). RISK-OFF => no buy proposals; exits still proposed.
3. Account snapshot to the engine: `echo '{"account_id":"<Agentic account number>","equity":V,"cash":C,
   "positions":[{"symbol":S,"qty":Q,"price":P},...]}' | $T snapshot --book momentum-quality`
   (equity = portfolio total_value; cash = buying power; positions from get_equity_positions).
4. Exits first (skill Phase 7 rules on every holding), then entries (skill Phases 2-6, all phases must pass).
   The 12:30 PM run is exits-only plus protective-stop checks; do not propose new entries then.
5. For EACH proposed trade build the request and run `$T check --book momentum-quality` (JSON on stdin):
   {"proposal":{"symbol","side","qty","order_type":"limit","limit_price","stop_price","target_price",
     "rationale":"plain English: why this trade, what setup",
     "signals":{"sector":"...","is_ai":bool,"earnings_days":N,"setup":"...","wave":"...","macd":"...",
       "exit_rule":"(sells)",
       "phases":{"market":{"pass":bool,"evidence":"values vs the rule"},"fundamentals":{...},"trend_template":{...},
                 "extension":{...},"stage2":{...},"momentum":{...}}}},
    "account":{...same account JSON as step 3...},"quote":<live ask or last>}
   limit_price = ask + 0.10 for buys (skill). Sells: limit slightly below bid. Evidence must quote real numbers.
6. Exit 0 => create a dashboard order: ArtifactData set DASH collection `live_orders` doc id
   `<YYYYMMDD>-<HHMM>-<SYMBOL>-<side>` with data:
   {"ts":<ISO UTC>,"book":"momentum-quality","symbol","side","qty":<qty returned by check>,"limit","stop","target",
    "rationale","phases":<the phases object>,"signals":<the signals object>,
    "guard":{"approved":true,"warnings":<warnings>,"decision_id":<decision_id>},
    "status": "awaiting_approval" if needs_user_approval else "approved",
    "approval_mode": "user" if needs_user_approval else "auto"}
   Exit 2 => do NOT create an order doc (the rejection reason is already in the engine's decision log).
7. Export state, sync-docs and publish per common.md.
8. FINAL MESSAGE = email to Sai. If any order is `awaiting_approval`:
   subject line 1: "Approval needed: <n> live trade(s) — <SYMBOL list>". Then for each: BUY/SELL qty SYMBOL limit $x,
   stop $y, target $z, risk $ and % of account, one-line why, and which of the six skill phases passed.
   Then: "Approve or reject on the dashboard: https://claude.ai/artifact/8QF5i8FpWPRuswKoJvcXMC (tap Approve). Unapproved
   orders expire at today's close." If nothing was proposed, one line: "Live proposals: nothing qualifies — <regime>."
   Plain text, under 250 words. Rules-based output, not financial advice.
