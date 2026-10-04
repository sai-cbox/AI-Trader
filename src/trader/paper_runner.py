"""Runs the four paper strategies: exits first, then entries, through the same guard and paper broker as everything else.

Paper fills happen at the latest close +/- slippage. That is optimistic (a real order after the close fills at the next
open), so treat paper results as an upper bound.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from . import controls, records
from .alerts import notify
from .brokers.paper import PaperBroker
from .config import Config
from .journal import Journal
from .models import Proposal
from .reporting import open_positions
from .risk import ET, RiskGuard
from .robinhood.authcheck import leaf_errors
from .robinhood.client import SERVER_URL, connect_read_only
from .robinhood.market import Fetcher, drop_partial, parse_bars, parse_earnings, parse_earnings_detail, parse_scan
from .strategy.paper_rules import STRATEGIES, TREND_UNIVERSE, Ctx


def _state(j: Journal, book: str, sym: str) -> dict:
    try:
        return json.loads(j.get(f"{book}:pos:{sym}") or "{}")
    except ValueError:
        return {}


def run_books(cfg: Config, j: Journal, now: datetime, data: dict, log=print, only: str | None = None) -> dict:
    """Pure with respect to the network: `data` = {bars, scan_symbols, earn_days, earn_detail}."""
    guard, summary = RiskGuard(cfg, j), {}
    week = "%d-W%02d" % now.astimezone(ET).isocalendar()[:2]
    for book in [b for b in cfg.books if cfg.kind(b) == "paper" and (only in (None, b))]:
        if controls.get_state(j, book) != controls.RUNNING:
            log(f"\n[{book}] skipped: state is {controls.get_state(j, book)} (run `trader start --paper` once, or resume it)")
            continue
        broker = PaperBroker(cfg, j, book)
        held = open_positions(j.fills(book))
        broker.mark({s: data["bars"][s][-1]["close"] for s in held if data["bars"].get(s)})
        acct = broker.account()
        universe = list(TREND_UNIVERSE) if book == "trend-following" else (
            list(data["earn_detail"]) if book == "earnings-drift" else list(data["scan_symbols"]))
        entries_ok = True
        if book == "trend-following":
            entries_ok = j.get(f"{book}:entry_week") != week
        ctx = Ctx(bars=data["bars"], equity=acct.equity, universe=universe, earn_days=data["earn_days"],
                  earn_detail=data["earn_detail"], entries_allowed=entries_ok,
                  positions={s: {"qty": v["qty"], "avg": v["avg_cost"], "state": _state(j, book, s)} for s, v in held.items()})
        exits, entries = STRATEGIES[book](ctx)
        done = {"exits": 0, "entries": 0, "rejected": 0}
        log(f"\n[{book}] equity {acct.equity:,.0f} | positions {len(held)} | exits due {len(exits)} | entries found {len(entries)}")
        for ex in exits:
            price = data["bars"][ex.symbol][-1]["close"]
            qty = int(held[ex.symbol]["qty"])
            p = Proposal(ex.symbol, "sell", qty, "limit", price, ex.rule, signals={"exit_rule": ex.rule, **ex.signals})
            d = guard.check(book, p, broker.account(), price, now)
            if d.approved:
                broker.fill(ex.symbol, "sell", d.qty, price, ex.rule, d.id, now)
                j.set(f"{book}:pos:{ex.symbol}", "{}")
                done["exits"] += 1
                log(f"  SELL {d.qty} {ex.symbol} @ ~{price:.2f}: {ex.rule}")
            else:
                done["rejected"] += 1
                log(f"  SELL {ex.symbol} rejected by guard: {'; '.join(d.reasons)}")
        for en in entries:
            p = Proposal(en.symbol, "buy", en.qty, "limit", en.price, en.rationale, signals=en.signals, stop_price=en.stop_price)
            d = guard.check(book, p, broker.account(), en.price, now)
            if d.approved:
                broker.fill(en.symbol, "buy", d.qty, en.price, en.rationale, d.id, now)
                j.set(f"{book}:pos:{en.symbol}", json.dumps({"entry_date": data["bars"][en.symbol][-1].get("t"), **en.state}))
                done["entries"] += 1
                log(f"  BUY {d.qty} {en.symbol} @ ~{en.price:.2f}: {en.rationale}")
            else:
                done["rejected"] += 1
                log(f"  BUY {en.symbol} rejected by guard: {'; '.join(d.reasons)}")
        if book == "trend-following" and entries_ok:
            j.set(f"{book}:entry_week", week)
        guard.refresh(book, broker.account(), now)
        summary[book] = done | {"equity": round(broker.account().equity, 2)}
    return summary


async def paper_run(cfg: Config, j: Journal, port: int = 8765, token_file=None, only: str | None = None, log=print,
                    connect=connect_read_only, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    try:
        async with connect(SERVER_URL, port, token_file, extra_allowed={"run_scan"}) as rh:
            f = Fetcher(rh, now)
            scan = [x["symbol"] for x in parse_scan(await f.call("run_scan", {"scan_id": cfg.scan_id}))]
            earn_days: dict[str, int | None] = {}
            try:
                fwd = parse_earnings(await f.call("get_earnings_calendar", {"days": 31, "filter": "high_market_cap"}))
                today = now.date()
                earn_days = {s: (datetime.strptime(d, "%Y-%m-%d").date() - today).days for s, d in fwd.items()}
            except Exception as e:
                log(f"[data] WARNING forward earnings unavailable ({type(e).__name__}); earnings filter skipped")
            detail: dict = {}
            try:
                detail = parse_earnings_detail(await f.call("get_earnings_calendar", {"days": -14, "filter": "high_market_cap"}))
            except Exception as e:
                log(f"[data] WARNING recent earnings unavailable ({type(e).__name__}); earnings-drift will find nothing")
            held = {s for b in cfg.books if cfg.kind(b) == "paper" for s in open_positions(j.fills(b))}
            symbols = sorted(set(scan) | set(TREND_UNIVERSE) | {s for s, d in detail.items() if d.get("eps_actual") is not None} | held)
            log(f"[data] {len(scan)} scan names + trend list + {len(detail)} recent reports -> {len(symbols)} symbols to load")
            bars: dict[str, list[dict]] = {}
            for i in range(0, len(symbols), 10):
                chunk = symbols[i:i + 10]
                try:
                    p = await f.call("get_equity_historicals", {"symbols": chunk, "start_time": f.start400, "interval": "day"})
                    for s in chunk:
                        try:
                            bars[s] = drop_partial(parse_bars(p, s), now)
                        except Exception:
                            pass
                except Exception as e:
                    log(f"[data] WARNING history batch failed ({type(e).__name__})")
    except BaseException as e:
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        for x in leaf_errors(e):
            log(f"[FAIL] {type(x).__name__}: {str(x)[:300]}")
        log("\nRESULT: PAPER RUN FAILED. Nothing was changed.")
        records.record_job(j, "paper-run", False, "data step failed", now)
        notify(cfg, "AI-Trader: paper run FAILED", "Data step failed; no paper trades were made.", "high")
        return 1
    if bars and now.astimezone(ET).weekday() < 5 and (now.astimezone(ET).hour, now.astimezone(ET).minute) < (16, 15):
        log("[data] note: US market is still open, so today's unfinished bar was ignored (decisions use the last completed session)")
    data = {"bars": bars, "scan_symbols": [s for s in scan if s in bars], "earn_days": earn_days, "earn_detail": detail}
    summary = run_books(cfg, j, now, data, log, only)
    try:
        if bars.get("SPY"):
            records.store_spy(j, bars["SPY"])
        records.record_job(j, "paper-run", True, f"{len(summary)} books, {len(bars)} symbols", now)
    except Exception as e:
        log(f"[data] WARNING could not store records ({type(e).__name__})")
    parts = [f"{b}: {v['entries']} buy/{v['exits']} sell, equity {v['equity']:,.0f}" for b, v in summary.items()]
    log("\nRESULT: PAPER RUN complete. " + ("; ".join(parts) if parts else "nothing ran") + ". No real orders exist in this command.")
    if any(v["entries"] or v["exits"] for v in summary.values()):
        notify(cfg, "AI-Trader paper trades", "\n".join(parts))
    return 0
