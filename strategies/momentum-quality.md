# momentum-quality (LIVE)
Minervini/O'Neil/Weinstein momentum-quality swing strategy on Russell 3000 leaders, defined by the `robinhood-stock-trader` skill.

**Source of truth: the `robinhood-stock-trader` skill.** The agent runs that skill's phases; this engine enforces its
hard rules in code (see `[overrides.momentum-quality]` in `config/default.toml`) and records the evidence for the dashboard.

## Hard rules enforced by the engine (reject the order if broken)
| Skill rule | Engine check |
|---|---|
| No buys in RISK-OFF; market filter first | `trader regime RISK-ON` must be recorded within 36h |
| All phases must pass | `signals.phases` needs `market, fundamentals, trend_template, extension, stage2, momentum`, each `pass: true` |
| Limit orders only | market orders rejected |
| 20% cash reserve | max invested 80% |
| 1% max risk per trade | qty shrunk so `qty × (entry − stop) ≤ 1%` of equity; `stop_price` required |
| Position size = 80% / 5 slots | max 16% per position, max 5 positions |
| Different sector per slot; max 2 AI names | `signals.sector` required, `signals.is_ai` counted |
| No averaging down | buy rejected if symbol already held |
| Earnings within 10 days → half size | `signals.earnings_days ≤ 10` halves the position cap |
| Equities only, no shorts | enforced for every strategy |

## What the agent must log for each trade (shown on the dashboard)
```json
{"rationale": "plain-English why",
 "stop_price": 0.0, "target_price": 0.0,
 "signals": {
   "sector": "Semiconductors", "is_ai": true, "earnings_days": 23,
   "setup": "VCP breakout", "wave": "2", "macd": "POSITIVE+RISING",
   "phases": {
     "market":        {"pass": true, "evidence": "SPY 612 > 50d 598 > 200d 560; EMA50>EMA100; QQQ ok"},
     "fundamentals":  {"pass": true, "evidence": "EPS +34% YoY, rev +41%; mkt cap $9B; catalyst: ..."},
     "trend_template":{"pass": true, "evidence": "price>50>150>200 MA, 200d rising, 12% below 52w high, +85% vs 52w low"},
     "extension":     {"pass": true, "evidence": "0.4 ATR above 21 EMA (CLEAN)"},
     "stage2":        {"pass": true, "evidence": "base breakout on 1.6x volume; +18% vs SPY over 30d"},
     "momentum":      {"pass": true, "evidence": "MACD hist positive & rising; closed above pivot on 1.5x avg volume"}}}}
```
Sells log `signals.exit_rule` (hard stop / 50d close / stage 3-4 / +15% half / +25% all / trailing / time stop).
