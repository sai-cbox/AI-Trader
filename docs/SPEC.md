# AI-Trader specification

## 1. Goal
Run your strategy automatically on the Robinhood **Agentic** account: analyze the market, decide, and place the trade
**in the same run** (minutes, not hours), with no per-trade approval. Safety comes from hard guardrails in code, not
from a human gate. Five strategies are tracked: 1 live, 4 paper, on the same pipeline.

## 2. Principles
1. The LLM decides; deterministic code acts. The analyst has no order tools.
2. Only Robinhood's **official** Trading MCP server (`https://agent.robinhood.com/mcp/trading`). No unofficial APIs, no password login.
3. Fail closed: any error, ambiguity or mismatch means no trade.
4. Every decision is journaled with its evidence and later linked to its outcome.
5. Suggestions to change the strategy never apply themselves.

## 3. Architecture
```
 scheduled job (your machine)
   1 fetch   data via official MCP (read tools)            seconds
   2 analyst Claude API: data in, decision JSON out (no tools)   30-120 s
   3 guard   src/trader/risk.py (deterministic)            instant
   4 execute re-quote, place limit order, wait for fill    seconds-minutes
   5 reconcile compare with Robinhood orders/positions; journal; dashboard refresh
```
Between runs, protective GTC stop orders sit on Robinhood's side. Protection never depends on the script being up.

## 4. Decision contract (analyst output, schema-validated)
```json
{"strategy":"momentum-quality","strategy_version":"1.0.0","created_at":"ISO","expires_at":"created_at+5min",
 "decisions":[{"symbol":"VRT","side":"buy","qty":3,"order_type":"limit","limit_price":110.10,
   "stop_price":103.20,"target_price":132.90,"rationale":"plain English",
   "signals":{"sector":"...","is_ai":true,"earnings_days":24,
     "phases":{"market":{"pass":true,"evidence":"values vs rule"},"fundamentals":{},"trend_template":{},
               "extension":{},"stage2":{},"momentum":{}}}}]}
```
Rejected outright: bad schema, expired, more than N proposals, missing evidence, unknown symbol, non-equity.

## 5. Guardrails (all enforced in code)
| Area | Rule |
|---|---|
| Account | Agentic account only; checked every run against `allowed_account_id`. Equities only. No options/crypto/shorts. Limit orders only |
| Order | Limit within 1.5% of live quote and of decision-time price; max dollars per order; no duplicate (check open orders first); idempotency key; one order per symbol per day; decision TTL 5 min |
| Portfolio | 16% per stock; 80% invested; 5 positions; one per sector; max 2 AI names; 1% risk per trade with stop required; no averaging down |
| Loss breakers | 3% daily loss stops new buys; 12% drawdown pauses everything; pause after N consecutive losses; max orders per day |
| Market | Buys only when regime is fresh RISK-ON; skip when data stale, market closed, halted, spread wide or liquidity low; earnings within 10 days halves size |
| Operational | `STOP` file kills everything; dry-run is the default and live needs an explicit flag; fail closed; never retry an order blindly (reconcile first); timeouts and rate limits |
| Security | Token in a 0600 file or secret store, never in the repo or logs; 2FA on Robinhood; least privilege |
| LLM | Schema validation; analyst has no tools; Guard re-checks everything (news/web text can try to steer an LLM); evidence required |
| Change control | Strategy changes are versioned; new versions run in shadow/paper first; caps ramp on a schedule, not on approval |

## 6. Executor
States: `proposed -> guarded -> submitted -> filled | partial | cancelled | expired | failed`; each transition journaled.
After a fill: place the protective GTC stop at `stop_price`. If it cannot be placed, alert loudly and flatten per config.
Reconcile before and after every run (positions, open orders, cash vs journal); any mismatch halts trading.

## 7. Dashboard (served locally; phone access via Tailscale)
Overview (equity vs SPY, P&L, state, limit usage) | Strategy leaderboard | Decision funnel (screened -> phases -> guard ->
submitted -> filled, with reasons) | Decision explorer (why + verdict + fill + eventual P&L) | Performance analytics (by symbol,
sector, setup, exit rule; win rate, expectancy, profit factor, drawdown, slippage) | System health | Improvement panel
(evidence-based suggestions with sample sizes; weekly Claude review; suggestions only).
Every figure shows its sample size; no conclusions under a minimum.

## 8. Rollout
0. **Auth spike** (`trader auth-check`, run by you): sign in, read the Agentic account. No orders.
1. Dry-run: executor logs what it would do.
2. Paper: the four paper strategies on the same pipeline.
3. Live, small: hard cap about $100 per order for a week.
4. Raise caps automatically per schedule.
5. Weekly improvement review and analytics.

## 9. Open items
- Headless sign-in (token refresh) is unverified; run Phase 0 on a machine with a browser, then copy the token file to a server.
- Check Robinhood's agentic-trading terms; you are responsible for trades the agent places.
- Runtime location and Anthropic API key storage.
