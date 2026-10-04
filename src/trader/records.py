"""What each run leaves behind in the journal so the dashboard can show it: account snapshot, latest live run,
SPY benchmark series and job health. All JSON in the `meta` table. No secrets, no full account numbers."""
from __future__ import annotations

import json
from datetime import datetime

from .journal import Journal


def mask(n: str | None) -> str:
    return "••••" + str(n or "")[-4:]


def account_snapshot(ctx: dict, now: datetime) -> dict:
    a = ctx["account"]
    stop_pct = a.get("stop_pct")
    holdings = []
    for h in ctx["holdings"]:
        if "price" not in h:
            holdings.append({"symbol": h["symbol"], "qty": h.get("qty"), "error": h.get("error")})
            continue
        avg = h.get("avg_cost")
        holdings.append({"symbol": h["symbol"], "qty": h["qty"], "avg_cost": avg, "price": h["price"], "pnl_pct": h.get("pnl_pct"),
                         "stop_price": round(avg * (1 - stop_pct / 100), 2) if avg and stop_pct else None,
                         "sector": h.get("sector"), "exit_flags": h.get("exit_flags") or {}, "sma50": h.get("sma50"), "macd": h.get("macd")})
    invested = sum(h["qty"] * h["price"] for h in holdings if "price" in h and h.get("qty"))
    orders = ctx.get("orders") or []
    open_states = {"new", "queued", "confirmed", "unconfirmed", "partially_filled"}
    return {"updated_at": now.isoformat(), "account": mask(ctx.get("account_number")), "total": a["equity"], "cash": a["cash"],
            "buying_power": a["buying_power"], "invested": round(invested, 2), "position_size": a["position_size"], "stop_pct": stop_pct,
            "holdings": holdings, "open_orders": [o for o in orders if o["state"] in open_states][:20], "recent_orders": orders[:15]}


def live_run_record(ctx: dict, now: datetime, summary: str | None = None, usage: dict | None = None,
                    decisions: list[dict] | None = None) -> dict:
    r = ctx["regime"]
    return {"ts": now.isoformat(), "regime": {"state": r["state"], "reasons": r["reasons"], "facts": r["facts"],
                                              "qqq_distribution": r["qqq_distribution"]},
            "funnel": ctx["funnel"], "extended_skipped": ctx.get("extended_skipped", []),
            "candidates": [{"symbol": c["symbol"], "sector": c.get("sector"), "price": c["price"], "trend_pass": c["trend_template"]["pass"],
                            "ext_atr": c["extension_atr"], "ext_label": c["extension_label"], "macd": c["macd"],
                            "rs30": c.get("rs_vs_spy_30d"), "shares": c.get("shares_at_position_size"),
                            "volume_vs_avg": c.get("volume_vs_avg30")} for c in ctx["candidates"]],
            "holdings": [{"symbol": h["symbol"], "pnl_pct": h.get("pnl_pct"), "flags": h.get("exit_flags"), "price": h.get("price"),
                          "sma50": h.get("sma50"), "macd": h.get("macd"), "error": h.get("error")} for h in ctx["holdings"]],
            "summary": summary, "analyst_usage": usage, "decisions": decisions or []}


def save(j: Journal, key: str, obj) -> None:
    j.set(key, json.dumps(obj, default=str))


def load(j: Journal, key: str, default=None):
    try:
        v = j.get(key)
        return json.loads(v) if v else default
    except ValueError:
        return default


def store_spy(j: Journal, series: list[dict]) -> None:
    """Merge SPY closes (date -> close) so the Compare page can draw the benchmark since your start date."""
    have = load(j, "spy:series", {}) or {}
    for b in series:
        if b.get("t") and b.get("close"):
            have[b["t"]] = b["close"]
    keep = dict(sorted(have.items())[-400:])
    save(j, "spy:series", keep)


def record_job(j: Journal, name: str, ok: bool, detail: str, now: datetime) -> None:
    save(j, f"job:{name}", {"ts": now.isoformat(), "ok": ok, "detail": detail})
