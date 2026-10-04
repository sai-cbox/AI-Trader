"""Phase 1 pipeline: data -> (analyst) -> fact check -> guard -> journal.  DRY-RUN ONLY: there is no order code here."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from . import controls
from .analyst import AnalystError, DecisionSet, call_analyst, verify_against_facts
from .config import Config
from .journal import Journal
from .models import Account, Position, Proposal
from .risk import ET, RiskGuard
from .robinhood.authcheck import leaf_errors
from .robinhood.client import SERVER_URL, connect_read_only
from .robinhood.market import build_context


def _today_cost(j: Journal, now: datetime) -> float:
    day = now.astimezone(ET).date().isoformat()
    total = 0.0
    for r in j.db.execute("SELECT ts, detail FROM events WHERE kind='analyst_cost'"):
        if datetime.fromisoformat(r["ts"]).astimezone(ET).date().isoformat() == day:
            try:
                total += float(json.loads(r["detail"]).get("cost_usd", 0))
            except ValueError:
                pass
    return total


def print_context(ctx: dict, log=print) -> None:
    r = ctx["regime"]
    log(f"\nREGIME: {r['state']}  - " + "; ".join(r["reasons"]))
    f = ctx["funnel"]
    log(f"FUNNEL: scan {f['scan_matches']} -> affordable {f['affordable']} -> after earnings filter "
        f"{f['after_earnings_filter']} -> strongest/sector-capped {f.get('strength_ranked')} -> not extended "
        f"{f.get('extension_ok')} -> finalists {f['finalists']}")
    for sym in ("SPY", "QQQ"):
        x = r["facts"][sym]
        log(f"  {sym}: price {x['price']:.2f} | 50d {x['sma50']:.2f} | 200d {x['sma200']:.2f} | EMA50 {x['ema50']:.2f} vs EMA100 "
            f"{x['ema100']:.2f} | distribution days {x['dist_days']} {x.get('dist_dates')} | new 4w low in 5d: {x['new_low_5d']} | last bar {x.get('last_bar')}")
    log("\nCANDIDATES (facts computed by code)")
    log(f"{'SYMBOL':7} {'SECTOR':22} {'PRICE':>8} {'TREND':>6} {'EXT(ATR)':>10} {'MACD':16} {'RS30':>6} {'SHARES':>6}")
    for c in ctx["candidates"]:
        tt = c["trend_template"]
        log(f"{c['symbol']:7} {(c.get('sector') or '')[:22]:22} {c['price']:8.2f} {'PASS' if tt['pass'] else 'fail':>6} "
            f"{c['extension_atr']:>6} {c['extension_label'][:10]:>3} {c['macd']:16} {str(c.get('rs_vs_spy_30d')):>6} "
            f"{c['shares_at_position_size']:>6}")
    if not ctx["candidates"]:
        log("  (none: nothing strong enough that is also not extended)")
    if ctx.get("extended_skipped"):
        log("  strong but extended, skipped: " + ", ".join(f"{x['symbol']} ({x['extension_atr']} ATR)" for x in ctx["extended_skipped"][:6]))
    if ctx["holdings"]:
        log("\nHOLDINGS")
        for h in ctx["holdings"]:
            log(f"  {h['symbol']}: " + (h["error"] if "error" in h else
                f"qty {h['qty']}, pnl {h['pnl_pct']}%, price vs 50d {'above' if h['price'] > h['sma50'] else 'BELOW'}, "
                f"macd {h['macd']}, ext {h['extension_label']}"))


async def run_pipeline(cfg: Config, j: Journal, with_analyst: bool, port: int = 8765, token_file=None, *,
                       connect=connect_read_only, analyst_fn=call_analyst, build=build_context, log=print,
                       now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    book = cfg.live_book
    if not book:
        log("[FAIL] no live strategy configured"); return 1
    try:
        async with connect(SERVER_URL, port, token_file, extra_allowed={"run_scan"}) as rh:
            ctx = await build(rh, cfg, book, log, now)
    except BaseException as e:
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        for x in leaf_errors(e):
            log(f"[FAIL] {type(x).__name__}: {str(x)[:300]}")
        log("\nRESULT: DATA STEP FAILED. Nothing was traded. Send me the [FAIL] lines.")
        return 1
    print_context(ctx, log)
    if not with_analyst:
        log("\nRESULT: DATA STEP OK. (No Claude call, no orders.)"); return 0

    if ctx["regime"]["state"] == "RISK-OFF" and not ctx["holdings"]:
        log("\nRESULT: RISK-OFF and no holdings: nothing the strategy can do. No Claude call (saves cost). No orders."); return 0
    guard = RiskGuard(cfg, j)
    j.set("market_regime", ctx["regime"]["state"]); j.set("market_regime_ts", now.isoformat())
    spent = _today_cost(j, now)
    if spent >= cfg.analyst_daily_cap_usd:
        log(f"\n[STOP] analyst spend today ${spent:.2f} reached the daily cap ${cfg.analyst_daily_cap_usd:.2f}. No Claude call."); return 1
    try:
        ds, usage = analyst_fn(cfg, ctx)
    except AnalystError as e:
        log(f"\n[FAIL] analyst: {e}. No decisions this run (fail closed)."); return 1
    j.event(book, "analyst_cost", json.dumps(usage), now)
    log(f"\nANALYST: {usage['input_tokens']} in / {usage['output_tokens']} out tokens, about ${usage['cost_usd']:.3f} "
        f"(today ${spent + usage['cost_usd']:.2f} of ${cfg.analyst_daily_cap_usd:.2f} cap)\n  {ds.summary}")

    held = {p["symbol"]: p for p in ctx["account"]["positions"]}
    price_of = {h["symbol"]: h.get("price") or held[h["symbol"]]["avg"] for h in ctx["holdings"] if "price" in h}
    meta_of = {h["symbol"]: h for h in ctx["holdings"] if "price" in h}
    acct = Account(ctx["account_number"], ctx["account"]["equity"], ctx["account"]["buying_power"] or ctx["account"]["cash"] or 0,
                   [Position(s, p["qty"], price_of.get(s) or p["avg"] or 0, (meta_of.get(s) or {}).get("sector"),
                             s in cfg.ai_symbols) for s, p in held.items()])
    if controls.get_state(j, book) == controls.STOPPED:
        log(f"  (hint: run `trader start --book {book}` once so the guard accepts dry-run decisions)")
    would = 0
    for d in ds.decisions[:cfg.analyst_max_proposals]:
        ok, problems, phases = verify_against_facts(d, ctx)
        sig = {"phases": phases, "sector": d.sector, "is_ai": bool(d.is_ai or d.symbol in cfg.ai_symbols), "earnings_days": d.earnings_days, "setup": d.setup,
               "exit_rule": d.exit_rule, "analyst": usage}
        cand = next((c for c in ctx["candidates"] if c["symbol"] == d.symbol), None)
        quote = (cand or {}).get("ask") or price_of.get(d.symbol) or d.limit_price
        p = Proposal(d.symbol, d.action, d.qty, "limit", d.limit_price, d.rationale, signals=sig,
                     stop_price=d.stop_price or None, target_price=d.target_price or None)
        if not ok:
            j.decision(book, p, quote, 0, False, problems, [], now)
            log(f"\n  REJECTED by fact check: {d.action.upper()} {d.qty} {d.symbol}\n    " + "\n    ".join(problems)); continue
        dec = guard.check(book, p, acct, quote, now)
        if dec.approved:
            would += 1
            log(f"\n  WOULD PLACE (dry run): {d.action.upper()} {dec.qty} {d.symbol} limit {d.limit_price}"
                + (f" stop {d.stop_price} target {d.target_price}" if d.action == "buy" else f" ({d.exit_rule})")
                + f"\n    why: {d.rationale}" + ("".join(f"\n    note: {w}" for w in dec.warnings)))
        else:
            log(f"\n  REJECTED by guard: {d.action.upper()} {d.qty} {d.symbol}: " + "; ".join(dec.reasons))
    if not ds.decisions:
        log("\n  No trades proposed (cash is a valid position).")
    log(f"\nRESULT: DRY RUN complete. {would} order(s) would be placed. Nothing was sent to Robinhood.")
    return 0
