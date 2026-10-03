from __future__ import annotations

from collections import defaultdict, deque

from .journal import Journal


def _lots(fills):
    lots: dict[str, deque] = defaultdict(deque)
    closed = []
    for f in fills:
        sym, qty, px = f["symbol"], f["qty"], f["price"]
        if f["side"] == "buy":
            lots[sym].append([qty, px, f["ts"]])
            continue
        remaining = qty
        while remaining > 1e-9 and lots[sym]:
            lot = lots[sym][0]
            take = min(remaining, lot[0])
            closed.append({"symbol": sym, "qty": take, "pnl": (px - lot[1]) * take,
                           "ret_pct": (px / lot[1] - 1) * 100, "opened": lot[2],
                           "closed": f["ts"], "entry": lot[1], "exit": px})
            lot[0] -= take
            remaining -= take
            if lot[0] <= 1e-9:
                lots[sym].popleft()
    return lots, closed


def realized_trades(fills) -> list[dict]:
    """FIFO-match sells against buys. One record per closed lot."""
    return _lots(fills)[1]


def open_positions(fills) -> dict[str, dict]:
    out = {}
    for sym, lots in _lots(fills)[0].items():
        qty = sum(l[0] for l in lots)
        if qty > 1e-9:
            out[sym] = {"qty": qty, "avg_cost": sum(l[0] * l[1] for l in lots) / qty}
    return out


def max_drawdown_pct(equities: list[float]) -> float:
    peak, worst = 0.0, 0.0
    for e in equities:
        peak = max(peak, e)
        if peak:
            worst = max(worst, (peak - e) / peak * 100)
    return worst


def build_report(j: Journal, book: str, bench_start: float | None = None,
                 bench_end: float | None = None) -> dict:
    snaps = j.snapshots(book)
    eq = [s["equity"] for s in snaps]
    closed = realized_trades(j.fills(book))
    wins = [t for t in closed if t["pnl"] > 0]
    losses = [t for t in closed if t["pnl"] <= 0]
    by_symbol: dict[str, float] = defaultdict(float)
    for t in closed:
        by_symbol[t["symbol"]] += t["pnl"]
    rep = {
        "book": book,
        "window": [j.get(f"{book}:window_start"), j.get(f"{book}:window_end")],
        "state": j.get(f"{book}:state", "stopped"),
        "start_equity": eq[0] if eq else None,
        "end_equity": eq[-1] if eq else None,
        "return_pct": (eq[-1] / eq[0] - 1) * 100 if len(eq) > 1 and eq[0] else None,
        "max_drawdown_pct": max_drawdown_pct(eq),
        "closed_lots": len(closed),
        "win_rate_pct": len(wins) / len(closed) * 100 if closed else None,
        "avg_win": sum(t["pnl"] for t in wins) / len(wins) if wins else None,
        "avg_loss": sum(t["pnl"] for t in losses) / len(losses) if losses else None,
        "realized_pnl": sum(t["pnl"] for t in closed),
        "pnl_by_symbol": dict(sorted(by_symbol.items(), key=lambda kv: kv[1])),
        "decisions_approved": len(j.approved_decisions(book)),
        "decisions_rejected": j.db.execute(
            "SELECT COUNT(*) c FROM decisions WHERE book=? AND approved=0", (book,)).fetchone()["c"],
    }
    if bench_start and bench_end:
        rep["benchmark_return_pct"] = (bench_end / bench_start - 1) * 100
    return rep
