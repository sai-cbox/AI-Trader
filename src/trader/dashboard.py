"""Dashboard data + tiny local server. Binds to 127.0.0.1 only; web controls can STOP/PAUSE, never start live."""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import controls
from .config import Config
from .journal import Journal
from .reporting import build_report, open_positions, realized_trades

STRATEGY_DIR = Path(__file__).resolve().parents[2] / "strategies"
HTML_PATH = Path(__file__).with_name("dashboard.html")


def describe(book: str) -> str:
    f = STRATEGY_DIR / f"{book}.md"
    if not f.exists():
        return ""
    for line in f.read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            return line.strip()
    return ""


def _curve(j: Journal, book: str, limit: int = 300):
    snaps = j.snapshots(book)
    if not snaps:
        return []
    base = snaps[0]["equity"]
    step = max(1, len(snaps) // limit)
    pts = snaps[::step] + ([snaps[-1]] if (len(snaps) - 1) % step else [])
    return [{"ts": s["ts"], "equity": s["equity"], "ret_pct": (s["equity"] / base - 1) * 100} for s in pts]


def book_summary(j: Journal, cfg: Config, book: str) -> dict:
    rep = build_report(j, book)
    return {
        "book": book, "kind": cfg.kind(book), "description": describe(book),
        "state": controls.get_state(j, book), "reason": j.get(f"{book}:state_reason"),
        "window": rep["window"], "equity": rep["end_equity"], "return_pct": rep["return_pct"],
        "max_drawdown_pct": rep["max_drawdown_pct"], "win_rate_pct": rep["win_rate_pct"],
        "realized_pnl": rep["realized_pnl"], "trades": rep["closed_lots"],
        "approved": rep["decisions_approved"], "rejected": rep["decisions_rejected"],
        "open_positions": len(open_positions(j.fills(book))),
        "curve": _curve(j, book),
    }


def overview(j: Journal, cfg: Config) -> dict:
    events = [dict(r) for r in j.db.execute(
        "SELECT * FROM events WHERE kind='warning' OR kind LIKE 'state:%' ORDER BY ts DESC LIMIT 20")]
    pending = [dict(r) for r in j.db.execute(
        "SELECT id,book,symbol,side,qty,quote FROM decisions WHERE approved=1 AND needs_approval=1 "
        "AND user_approved_at IS NULL ORDER BY id DESC LIMIT 20")]
    return {"books": [book_summary(j, cfg, b) for b in cfg.books],
            "pending_approvals": pending, "regime": j.get("market_regime"), "regime_ts": j.get("market_regime_ts"),
"live_execution": cfg.live_execution, "events": events, "stop_file": controls.kill_file_present(cfg.db_path)}


def book_detail(j: Journal, cfg: Config, book: str, limit: int = 100) -> dict:
    fills = j.fills(book)
    fills_by_decision = {}
    for f in fills:
        if f["decision_id"] is not None:
            fills_by_decision.setdefault(f["decision_id"], []).append(
                {"qty": f["qty"], "price": f["price"], "ts": f["ts"]})
    decisions = []
    for r in j.db.execute("SELECT * FROM decisions WHERE book=? ORDER BY id DESC LIMIT ?", (book, limit)):
        decisions.append({
            "id": r["id"], "ts": r["ts"], "symbol": r["symbol"], "side": r["side"], "quote": r["quote"],
            "requested_qty": r["req_qty"], "approved_qty": r["qty"], "approved": bool(r["approved"]),
            "risk_reasons": json.loads(r["reasons"] or "[]"), "risk_warnings": json.loads(r["warnings"] or "[]"),
            "rationale": r["rationale"], "signals": json.loads(r["signals"] or "{}"),
            "stop_price": r["stop_price"], "target_price": r["target_price"],
            "needs_approval": bool(r["needs_approval"]), "user_approved_at": r["user_approved_at"],
            "fills": fills_by_decision.get(r["id"], []),
        })
    positions = []
    for sym, v in open_positions(fills).items():
        px = j.mark(book, sym) or v["avg_cost"]
        positions.append({"symbol": sym, "qty": v["qty"], "avg_cost": v["avg_cost"], "price": px,
                          "pnl": (px - v["avg_cost"]) * v["qty"]})
    return {"summary": book_summary(j, cfg, book), "report": build_report(j, book),
            "positions": positions, "decisions": decisions,
            "closed": realized_trades(fills)[-50:][::-1]}


def snapshot(j: Journal, cfg: Config) -> dict:
    return {"overview": overview(j, cfg), "books": {b: book_detail(j, cfg, b) for b in cfg.books}}


def render_html(snap: dict | None = None) -> str:
    html = HTML_PATH.read_text()
    inject = "null" if snap is None else json.dumps(snap, default=str).replace("</", "<\\/")
    return html.replace("/*__SNAPSHOT__*/null", inject)


def serve(cfg: Config, port: int = 8765) -> None:
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            u = urlparse(self.path)
            j = Journal(cfg.db_path)
            if u.path == "/":
                self._send(200, render_html(), "text/html; charset=utf-8")
            elif u.path == "/api/overview":
                self._send(200, json.dumps(overview(j, cfg), default=str))
            elif u.path == "/api/book":
                name = parse_qs(u.query).get("name", [""])[0]
                if name not in cfg.strategies:
                    self._send(404, '{"error":"unknown strategy"}')
                else:
                    self._send(200, json.dumps(book_detail(j, cfg, name), default=str))
            else:
                self._send(404, "{}")

        def do_POST(self):
            if urlparse(self.path).path != "/api/control":
                return self._send(404, "{}")
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            action, book = req.get("action"), req.get("book", "all")
            if action not in ("stop", "pause"):
                return self._send(400, '{"error":"web controls allow stop/pause only; resume via CLI"}')
            if book != "all" and book not in cfg.strategies:
                return self._send(404, '{"error":"unknown strategy"}')
            j = Journal(cfg.db_path)
            state = controls.STOPPED if action == "stop" else controls.PAUSED
            for b in (cfg.books if book == "all" else [book]):
                controls.set_state(j, b, state, f"dashboard {action}")
            self._send(200, json.dumps({"ok": True, "state": state, "book": book}))

    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    print(f"Dashboard: http://127.0.0.1:{port}  (Ctrl+C to quit)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
