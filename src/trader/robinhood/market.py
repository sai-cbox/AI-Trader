"""Market data step: read-only calls to Robinhood's official MCP server, parsed defensively.

Parsers raise ShapeError that lists the field names actually found, so a changed/unexpected reply is diagnosed in one run.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from ..config import Config
from ..strategy import momentum as m
from .client import agentic_accounts

ACTIONABLE = ("BELOW_21EMA", "CLEAN", "ACCEPTABLE")
TIME_NAMES = {"time", "timestamp", "date", "begins_at", "ts", "t", "start", "end"}


class ShapeError(RuntimeError):
    pass


def _timey(k: str) -> bool:
    k = k.lower()
    return k in TIME_NAMES or k.endswith(("_at", "_time", "_date"))


def num(x: Any) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def pick(d: dict, *names: str, what: str) -> Any:
    for n in names:
        if d.get(n) is not None:
            return d[n]
    raise ShapeError(f"{what}: expected one of {list(names)}; found keys {sorted(d)}")


def _chrono(items: list[dict]) -> list[dict]:
    """Oldest -> newest, judged by an ISO time field when one exists."""
    if len(items) < 2:
        return items
    tk = next((k for k, v in items[0].items() if _timey(k) and isinstance(v, str)), None)
    return items[::-1] if tk and items[0][tk] > items[-1][tk] else items


# ---------------- parsers ----------------
def parse_portfolio(p: dict) -> dict:
    d = p["data"]
    bp = d.get("buying_power") or {}
    return {"total": num(d["total_value"]), "cash": num(d.get("cash")),
            "buying_power": num(bp.get("unleveraged_buying_power") or bp.get("buying_power"))}


def parse_positions(p: dict) -> list[dict]:
    out = []
    for r in p["data"].get("positions", []):
        q = num(r.get("quantity"))
        if q and q > 0:
            out.append({"symbol": r["symbol"], "qty": q, "avg": num(r.get("average_buy_price"))})
    return out


def parse_quotes(p: dict) -> dict[str, dict]:
    out = {}
    for r in p["data"]["results"]:
        q = r["quote"]
        out[q["symbol"]] = {"last": num(q.get("last_trade_price")), "bid": num(q.get("bid_price")),
                            "ask": num(q.get("ask_price")), "prev_close": num(q.get("previous_close"))}
    return out


def series_points(p: dict) -> list[dict[str, float]]:
    ind = p["data"]["indicators"][0]
    rows = _chrono(list(ind["series"]))
    pts = []
    for it in rows:
        vals = {k: num(v) for k, v in it.items() if not _timey(k) and num(v) is not None}
        if not vals:
            raise ShapeError(f"indicator point has no numeric field; keys {sorted(it)}")
        pts.append(vals)
    if not pts:
        raise ShapeError("indicator series is empty")
    return pts


def _first(pt: dict[str, float]) -> float:
    return pt["value"] if "value" in pt else next(iter(pt.values()))


def latest_value(p: dict, field_hint: str | None = None) -> float:
    pt = series_points(p)[-1]
    if field_hint:
        k = next((k for k in pt if field_hint in k.lower()), None)
        if k:
            return pt[k]
    if "value" in pt:
        return pt["value"]
    if len(pt) == 1:
        return next(iter(pt.values()))
    raise ShapeError(f"cannot pick a value from indicator point; fields {sorted(pt)}")


def parse_bars(p: dict, symbol: str | None = None) -> list[dict]:
    results = p["data"]["results"]
    res = next((r for r in results if symbol is None or r.get("symbol") == symbol), None)
    if res is None:
        raise ShapeError(f"no bars for {symbol}; symbols {[r.get('symbol') for r in results]}")
    bars = []
    for b in _chrono(list(res["bars"])):
        tkey = next((k for k, v in b.items() if _timey(k) and isinstance(v, str)), None)
        bars.append({"t": (b[tkey][:10] if tkey else None), "close": num(pick(b, "close_price", "close", "c", what="bar close")),
                     "high": num(pick(b, "high_price", "high", "h", what="bar high")),
                     "low": num(pick(b, "low_price", "low", "l", what="bar low")),
                     "volume": num(pick(b, "volume", "v", what="bar volume"))})
    return bars


def parse_fundamentals(p: dict) -> dict[str, dict]:
    out = {}
    for r in p["data"]["results"]:
        out[r["symbol"]] = {"market_cap": num(r.get("market_cap")), "high52": num(r.get("high_52_weeks")),
                            "low52": num(r.get("low_52_weeks")), "avg_vol_30d": num(r.get("average_volume_30_days")),
                            "volume": num(r.get("volume")), "pe": num(r.get("pe_ratio")),
                            "sector": r.get("sector"), "industry": r.get("industry")}
    return out


def parse_scan(p: dict) -> list[dict]:
    return [{"symbol": x["ticker"], "columns": x.get("columns", {})} for x in p["data"]["result"]["results"]]


def parse_earnings(p: dict) -> dict[str, str]:
    """symbol -> nearest report date (YYYY-MM-DD)."""
    out: dict[str, str] = {}
    for r in p["data"]["results"]:
        d = (r.get("report") or {}).get("date")
        if d and (r["symbol"] not in out or d < out[r["symbol"]]):
            out[r["symbol"]] = d
    return out


# ---------------- fetching ----------------
def _iso(d: datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


class Fetcher:
    def __init__(self, rh, now: datetime, limit: int = 4):
        self.rh, self.now, self.sem = rh, now, asyncio.Semaphore(limit)
        self.start400, self.start120 = _iso(now - timedelta(days=400)), _iso(now - timedelta(days=120))

    async def call(self, tool: str, args: dict | None = None) -> Any:
        async with self.sem:
            return await self.rh.call(tool, args or {})

    async def ind(self, sym: str, typ: str, period: int | None = None, output: str = "latest") -> dict:
        a = {"symbol": sym, "type": typ, "interval": "day", "start_time": self.start400, "output": output}
        if period:
            a["period"] = period
        return await self.call("get_equity_technical_indicators", a)

    async def bars(self, symbols: list[str]) -> dict[str, list[dict]]:
        p = await self.call("get_equity_historicals", {"symbols": symbols, "start_time": self.start120, "interval": "day"})
        return {s: parse_bars(p, s) for s in symbols}


async def index_facts(f: Fetcher, sym: str, price: float) -> dict:
    sma50, sma200, ema50, ema100, bars = await asyncio.gather(
        f.ind(sym, "sma", 50), f.ind(sym, "sma", 200), f.ind(sym, "ema", 50), f.ind(sym, "ema", 100), f.bars([sym]))
    b = bars[sym]
    return {"price": price, "sma50": latest_value(sma50), "sma200": latest_value(sma200),
            "ema50": latest_value(ema50), "ema100": latest_value(ema100),
            "dist_days": m.distribution_days(b), "dist_dates": m.distribution_dates(b),
            "last_bar": b[-1].get("t"), "new_low_5d": m.new_4w_low_recent(b),
            "ret_30d": m.pct_return(b, 21)}


async def symbol_facts(f: Fetcher, sym: str, price: float, fund: dict, spy_ret30: float | None) -> dict:
    sma50, sma150, sma200s, ema21, atr14, macd, rsi = await asyncio.gather(
        f.ind(sym, "sma", 50), f.ind(sym, "sma", 150), f.ind(sym, "sma", 200, "last:25"),
        f.ind(sym, "ema", 21), f.ind(sym, "atr", 14), f.ind(sym, "macd", None, "last:3"), f.ind(sym, "rsi", 14))
    s200 = series_points(sma200s)
    s200_now, s200_prev = _first(s200[-1]), _first(s200[0])
    hist = [next((v for k, v in pt.items() if "hist" in k.lower()), None) for pt in series_points(macd)]
    hist = [h for h in hist if h is not None]
    e21, a14 = latest_value(ema21), latest_value(atr14)
    ext, label = m.extension(price, e21, a14)
    tt = m.trend_template(price, latest_value(sma50), latest_value(sma150), s200_now, s200_prev,
                          fund.get("high52") or 0, fund.get("low52") or 0)
    return {"symbol": sym, "price": price, "trend_template": tt, "extension_atr": ext, "extension_label": label,
            "macd": m.macd_state(hist), "rsi14": round(latest_value(rsi), 1), "atr14": round(a14, 2),
            "ema21": round(e21, 2), "sma50": round(latest_value(sma50), 2),
            "volume_vs_avg30": round(fund["volume"] / fund["avg_vol_30d"], 2) if fund.get("volume") and fund.get("avg_vol_30d") else None,
            "sector": fund.get("sector"), "industry": fund.get("industry"), "market_cap": fund.get("market_cap"),
            "high52": fund.get("high52"), "low52": fund.get("low52")}


async def build_context(rh, cfg: Config, book: str = "momentum-quality", log=print, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    f = Fetcher(rh, now)
    acct = next(iter(agentic_accounts(await f.call("get_accounts"))), None)
    if acct is None:
        raise ShapeError("no agentic account found")
    acct_no = acct["account_number"]
    pf = parse_portfolio(await f.call("get_portfolio", {"account_number": acct_no}))
    positions = parse_positions(await f.call("get_equity_positions", {"account_number": acct_no}))
    equity = pf["total"]
    pos_pct = cfg.opt(book, "max_position_pct", cfg.max_position_pct)
    position_size = equity * pos_pct / 100
    stop_pct = m.stop_pct_for(cfg.opt(book, "risk_per_trade_pct", 1.0) or 1.0, pos_pct)
    log(f"[data] account ok; {len(positions)} open position(s); position size = {pos_pct}% of equity")

    quotes = parse_quotes(await f.call("get_equity_quotes", {"symbols": ["SPY", "QQQ"]}))
    spy, qqq = await asyncio.gather(index_facts(f, "SPY", quotes["SPY"]["last"]), index_facts(f, "QQQ", quotes["QQQ"]["last"]))
    reg = m.regime(spy, qqq)
    log(f"[data] market regime: {reg.state}  ({'; '.join(reg.reasons)})")

    scan = parse_scan(await f.call("run_scan", {"scan_id": cfg.scan_id}))
    funnel = {"scan_matches": len(scan)}
    syms = [s["symbol"] for s in scan]
    quotes.update({k: v for chunk in [syms[i:i + 20] for i in range(0, len(syms), 20)]
                   for k, v in parse_quotes(await f.call("get_equity_quotes", {"symbols": chunk})).items()})
    ask = lambda s: (quotes.get(s) or {}).get("ask") or (quotes.get(s) or {}).get("last") or 0
    afford = [s for s in syms if m.shares_for(position_size, ask(s)) >= 1 and ask(s) >= 10]
    funnel["affordable"] = len(afford)

    fund: dict[str, dict] = {}
    for i in range(0, len(afford), 10):
        fund.update(parse_fundamentals(await f.call("get_equity_fundamentals", {"symbols": afford[i:i + 10]})))
    try:
        earn = parse_earnings(await f.call("get_earnings_calendar", {"days": 31, "filter": "high_market_cap"}))
    except Exception as e:  # earnings data is a filter, not a requirement; say so and continue
        log(f"[data] WARNING earnings calendar unavailable ({type(e).__name__}); earnings filter skipped")
        earn = {}
    today = now.date()
    days_to = lambda s: (datetime.strptime(earn[s], "%Y-%m-%d").date() - today).days if s in earn else None
    keep = [s for s in afford if s in fund and (days_to(s) is None or days_to(s) > 10)]
    funnel["after_earnings_filter"] = len(keep)
    # Stage A: strength ranking from price history (cheap), sector-capped, then an extension filter (2 calls per name),
    # so the analyst only sees setups the skill could actually buy.
    spy_ret = spy.get("ret_30d")
    bars: dict[str, list[dict]] = {}
    for i in range(0, len(keep), 10):
        try:
            bars.update(await f.bars(keep[i:i + 10]))
        except Exception as e:
            log(f"[data] WARNING history unavailable for a batch ({type(e).__name__})")
    ret30 = {s: m.pct_return(bars[s], 21) for s in keep if s in bars}
    rs_of = lambda s: (ret30[s] - spy_ret) if ret30.get(s) is not None and spy_ret is not None else -999.0
    stage_a, per_sector = [], {}
    for s in sorted([s for s in keep if s in ret30], key=lambda s: -rs_of(s)):
        sec = fund[s].get("sector")
        if per_sector.get(sec, 0) >= cfg.max_per_sector_candidates * 3:   # loose cap here; the strict cap comes after the extension filter
            continue
        per_sector[sec] = per_sector.get(sec, 0) + 1
        stage_a.append(s)
        if len(stage_a) >= 2 * cfg.max_candidates:
            break

    async def quick_ext(s: str):
        e21, a14 = await asyncio.gather(f.ind(s, "ema", 21), f.ind(s, "atr", 14))
        return m.extension(ask(s), latest_value(e21), latest_value(a14))

    quick = await asyncio.gather(*[quick_ext(s) for s in stage_a], return_exceptions=True)
    errors: list[str] = []
    actionable, extended = [], []
    for s, r in zip(stage_a, quick):
        if isinstance(r, BaseException):
            errors.append(f"{s}: {type(r).__name__}: {str(r)[:160]}")
        elif r[1] in ACTIONABLE:
            actionable.append(s)
        else:
            extended.append({"symbol": s, "extension_atr": r[0], "label": r[1], "rs_vs_spy_30d": round(rs_of(s), 1)})
    ranked, final_sector = [], {}
    for s in actionable:                                     # strict per-sector cap, applied to actionable names only
        sec = fund[s].get("sector")
        if final_sector.get(sec, 0) < cfg.max_per_sector_candidates and len(ranked) < cfg.max_candidates:
            final_sector[sec] = final_sector.get(sec, 0) + 1
            ranked.append(s)
    funnel.update({"strength_ranked": len(stage_a), "extension_ok": len(actionable), "finalists": len(ranked)})
    log(f"[data] funnel: scan {funnel['scan_matches']} -> affordable {funnel['affordable']} -> after earnings "
        f"{funnel['after_earnings_filter']} -> strongest/sector-capped {len(stage_a)} -> not extended {len(actionable)} "
        f"-> finalists {len(ranked)}")

    cands = []
    results = await asyncio.gather(*[symbol_facts(f, s, ask(s), fund[s], spy_ret) for s in ranked], return_exceptions=True)
    for s, r in zip(ranked, results):
        if isinstance(r, BaseException):
            errors.append(f"{s}: {type(r).__name__}: {str(r)[:160]}")
            continue
        r.update({"ret_30d": ret30.get(s), "rs_vs_spy_30d": round(rs_of(s), 2), "shares_at_position_size": m.shares_for(position_size, ask(s)),
                  "ask": ask(s), "earnings_in_days": days_to(s),
                  "scan_columns": next((x["columns"] for x in scan if x["symbol"] == s), {})})
        cands.append(r)
    for e in errors[:5]:
        log(f"[data] candidate skipped: {e}")

    holdings = []
    for pos in positions:
        s = pos["symbol"]
        try:
            q = parse_quotes(await f.call("get_equity_quotes", {"symbols": [s]}))[s]
            fd = parse_fundamentals(await f.call("get_equity_fundamentals", {"symbols": [s]}))[s]
            h = await symbol_facts(f, s, q["last"], fd, spy_ret)
            h.update({"qty": pos["qty"], "avg_cost": pos["avg"], "pnl_pct": round((q["last"] / pos["avg"] - 1) * 100, 2) if pos["avg"] else None,
                      "bid": q["bid"], "earnings_in_days": days_to(s)})
            h["exit_flags"] = m.exit_flags(q["last"], pos["avg"], h["sma50"], h["macd"], h["extension_label"], stop_pct, days_to(s))
            holdings.append(h)
        except Exception as e:
            holdings.append({"symbol": s, "qty": pos["qty"], "error": f"{type(e).__name__}: {str(e)[:160]}"})
    return {"asof": _iso(now), "account_number": acct_no,
            "account": {"equity": equity, "cash": pf["cash"], "buying_power": pf["buying_power"], "position_size": position_size,
                        "stop_pct": stop_pct, "positions": positions},
            "regime": {"state": reg.state, "reasons": reg.reasons, "qqq_distribution": reg.qqq_distribution, "facts": reg.facts},
            "funnel": funnel, "candidates": cands, "extended_skipped": extended[:8], "holdings": holdings, "candidate_errors": errors}
