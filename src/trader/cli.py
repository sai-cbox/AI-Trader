from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import controls
from .brokers.paper import PaperBroker
from .config import Config, load_config
from .journal import Journal, utcnow
from .models import Account, Proposal
from .reporting import build_report
from .risk import ET, RiskGuard


def out(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def books_for(cfg: Config, arg: str | None) -> list[str]:
    if arg in (None, "all"):
        return cfg.books
    if arg not in cfg.strategies:
        raise SystemExit(f"unknown strategy {arg!r}; configured: {cfg.books}")
    return [arg]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="trader")
    ap.add_argument("--config", default="config/default.toml")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def book_arg(s, required=False, default=None):
        s.add_argument("--book", "--strategy", dest="book", required=required, default=default)

    s = sub.add_parser("start", help="start strategy run + evaluation window (live must be named explicitly)")
    book_arg(s); s.add_argument("--paper", action="store_true", help="start ALL paper strategies")
    s.add_argument("--days", type=int)
    s = sub.add_parser("stop", help="KILL SWITCH: block all new orders"); book_arg(s, default="all")
    s = sub.add_parser("pause", help="block new buys, allow exits"); book_arg(s, default="all")
    s = sub.add_parser("resume"); book_arg(s, required=True)
    s = sub.add_parser("status"); book_arg(s, default="all")
    s = sub.add_parser("check", help="risk-check an order (JSON on stdin). REQUIRED before every live order")
    book_arg(s, required=True)
    s = sub.add_parser("regime", help="record today's market regime (Phase 1). Live buys require fresh RISK-ON")
    s.add_argument("value", choices=("RISK-ON", "RISK-OFF")); s.add_argument("--note", default="")
    s = sub.add_parser("approve", help="record YOUR approval of a pending live order (confirm period)")
    s.add_argument("--decision-id", type=int, required=True)
    s = sub.add_parser("snapshot", help="record account equity (JSON account on stdin); feeds the dashboard")
    book_arg(s, required=True)
    s = sub.add_parser("record", help="record a real fill after the broker executes it")
    book_arg(s, required=True)
    s.add_argument("--symbol", required=True); s.add_argument("--side", choices=("buy", "sell"), required=True)
    s.add_argument("--qty", type=float, required=True); s.add_argument("--price", type=float, required=True)
    s.add_argument("--ref", default=""); s.add_argument("--rationale", default="")
    s.add_argument("--decision-id", type=int)
    s = sub.add_parser("paper-order", help="risk-check then fill in a paper strategy's account")
    book_arg(s, required=True)
    s.add_argument("--symbol", required=True); s.add_argument("--side", choices=("buy", "sell"), required=True)
    s.add_argument("--qty", type=int, required=True); s.add_argument("--quote", type=float, required=True)
    s.add_argument("--limit", type=float); s.add_argument("--rationale", default="")
    s.add_argument("--signals", default="{}", help="JSON of indicators/rule hits behind the trade")
    s = sub.add_parser("paper-mark", help="mark paper positions, e.g. AAPL=190.5"); book_arg(s, required=True)
    s.add_argument("prices", nargs="+")
    s = sub.add_parser("report"); book_arg(s, default="all")
    s.add_argument("--bench-start", type=float); s.add_argument("--bench-end", type=float)
    s = sub.add_parser("auth-check", help="PHASE 0: sign in to Robinhood's official MCP server and READ account/portfolio (no orders)")
    s.add_argument("--url"); s.add_argument("--port", type=int, default=8765)
    s.add_argument("--token-file", default=None)
    s = sub.add_parser("dashboard", help="serve the monitoring dashboard or export a static snapshot")
    s.add_argument("--port", type=int, default=8765); s.add_argument("--export", metavar="FILE.html")

    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    j = Journal(cfg.db_path)
    guard = RiskGuard(cfg, j)
    now = utcnow()

    if a.cmd == "start":
        books = [b for b in cfg.books if cfg.kind(b) == "paper"] if a.paper else books_for(cfg, a.book)
        if not a.paper and a.book in (None, "all"):
            raise SystemExit("name a strategy with --book, or use --paper")
        res = {}
        for b in books:
            res[b] = controls.start(j, b, a.days or cfg.window_trading_days, now.astimezone(ET).date()).isoformat()
            if cfg.kind(b) == "paper":  # opening snapshot so the dashboard has a baseline
                guard.refresh(b, PaperBroker(cfg, j, b).account(), now)
            if cfg.kind(b) == "live" and not cfg.allowed_account_id:
                print("WARNING: allowed_account_id unset; live orders will be rejected.", file=sys.stderr)
        out({"started": res})
    elif a.cmd in ("stop", "pause"):
        state = controls.STOPPED if a.cmd == "stop" else controls.PAUSED
        books = books_for(cfg, a.book)
        for b in books:
            controls.set_state(j, b, state, f"manual {a.cmd}")
        out({"state": state, "books": books})
    elif a.cmd == "resume":
        books_for(cfg, a.book)
        controls.set_state(j, a.book, controls.RUNNING, "manual resume")
        out({"book": a.book, "state": "running"})
    elif a.cmd == "status":
        out({"stop_file": controls.kill_file_present(cfg.db_path)} | {
            b: {"kind": cfg.kind(b), "state": controls.get_state(j, b), "reason": j.get(f"{b}:state_reason"),
                "window_end": j.get(f"{b}:window_end"), "peak_equity": j.get(f"{b}:peak_equity"),
                "day_start_equity": j.get(f"{b}:day_start_equity")} for b in books_for(cfg, a.book)})
    elif a.cmd == "check":
        req = json.load(sys.stdin)
        d = guard.check(a.book, Proposal.from_dict(req["proposal"]), Account.from_dict(req["account"]),
                        float(req["quote"]), now)
        out(d.to_dict())
        return 0 if d.approved else 2
    elif a.cmd == "regime":
        j.set("market_regime", a.value); j.set("market_regime_ts", now.isoformat())
        j.event("all", "regime", f"{a.value} {a.note}".strip(), now)
        out({"market_regime": a.value})
    elif a.cmd == "approve":
        row = j.get_decision(a.decision_id)
        if not row or not row["approved"]:
            print("decision not found or was rejected by the risk guard", file=sys.stderr); return 3
        j.approve_decision(a.decision_id, now)
        out({"decision_id": a.decision_id, "user_approved": True, "symbol": row["symbol"], "side": row["side"],
             "qty": row["qty"]})
    elif a.cmd == "snapshot":
        guard.refresh(a.book, Account.from_dict(json.load(sys.stdin)), now)
        out({"recorded": True, "state": controls.get_state(j, a.book)})
    elif a.cmd == "record":
        if cfg.kind(a.book) == "live" and a.decision_id is None:
            print("live fills must reference the risk-checked --decision-id", file=sys.stderr); return 3
        if a.decision_id is not None:
            row = j.get_decision(a.decision_id)
            if not row or not row["approved"] or row["book"] != a.book:
                print("decision not found / rejected / other strategy", file=sys.stderr); return 3
            if row["needs_approval"] and not row["user_approved_at"]:
                print("confirm period: this order was never approved by the user", file=sys.stderr); return 3
        if cfg.kind(a.book) == "live" and not j.get(f"{a.book}:first_fill_date"):
            j.set(f"{a.book}:first_fill_date", now.astimezone(ET).date().isoformat())
        j.fill(a.book, a.symbol.upper(), a.side, a.qty, a.price, a.ref, a.rationale, a.decision_id, now)
        j.db.commit()
        out({"recorded": True})
    elif a.cmd == "paper-order":
        broker = PaperBroker(cfg, j, a.book)
        p = Proposal(a.symbol.upper(), a.side, a.qty, "limit" if a.limit else "market", a.limit,
                     a.rationale, signals=json.loads(a.signals))
        d = guard.check(a.book, p, broker.account(), a.quote, now)
        res = d.to_dict()
        if d.approved:
            res["fill_price"] = broker.fill(p.symbol, p.side, d.qty, a.limit or a.quote, a.rationale, d.id, now)
        out(res)
        return 0 if d.approved else 2
    elif a.cmd == "paper-mark":
        broker = PaperBroker(cfg, j, a.book)
        broker.mark({k.upper(): float(v) for k, v in (x.split("=") for x in a.prices)})
        acct = broker.account()
        guard.refresh(a.book, acct, now)
        out({"equity": acct.equity, "cash": acct.cash, "positions": [p.__dict__ for p in acct.positions]})
    elif a.cmd == "report":
        out({b: build_report(j, b, a.bench_start, a.bench_end) for b in books_for(cfg, a.book)})
    elif a.cmd == "auth-check":
        import asyncio
        from .robinhood.authcheck import run_auth_check
        return asyncio.run(run_auth_check(cfg, a.url, a.port, a.token_file))
    elif a.cmd == "dashboard":
        from . import dashboard
        if a.export:
            Path(a.export).write_text(dashboard.render_html(dashboard.snapshot(j, cfg)))
            print(f"wrote {a.export}")
        else:
            dashboard.serve(cfg, a.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
