"""Portable per-strategy state so scheduled cloud runs (fresh containers) can resume where the last run stopped."""
from __future__ import annotations

from .journal import Journal

GLOBAL_META = ("market_regime", "market_regime_ts")
COLS = {
    "snapshots": ("ts", "book", "equity", "cash"),
    "decisions": ("id", "ts", "book", "symbol", "side", "quote", "req_qty", "qty", "approved", "reasons",
                  "warnings", "rationale", "signals", "stop_price", "target_price", "needs_approval",
                  "user_approved_at"),
    "fills": ("ts", "book", "symbol", "side", "qty", "price", "ref", "rationale", "decision_id"),
    "events": ("ts", "book", "kind", "detail"),
}


def export_state(j: Journal, book: str, max_events: int = 200, max_snapshots: int = 1500) -> dict:
    meta = {r["key"]: r["value"] for r in j.db.execute("SELECT key,value FROM meta")
            if r["key"].startswith(book + ":") or r["key"] in GLOBAL_META}
    out = {"book": book, "meta": meta}
    for t, cols in COLS.items():
        rows = [dict(r) for r in j.db.execute(f"SELECT {','.join(cols)} FROM {t} WHERE book=? ORDER BY ts, rowid", (book,))]
        if t == "events":
            rows = rows[-max_events:]
        if t == "snapshots":
            rows = rows[-max_snapshots:]
        out[t] = rows
    return out


def import_state(j: Journal, book: str, data: dict) -> dict:
    """Replace everything stored for `book` with `data`. Decision ids are kept unless another book owns them."""
    if not data or data.get("book") not in (None, book):
        return {"imported": False}
    d = j.db
    taken = {r["id"] for r in d.execute("SELECT id FROM decisions WHERE book<>?", (book,))}
    for t in COLS:
        d.execute(f"DELETE FROM {t} WHERE book=?", (book,))
    d.execute("DELETE FROM meta WHERE key LIKE ?", (book + ":%",))
    idmap: dict[int, int] = {}
    for r in data.get("decisions", []):
        old = r.get("id")
        cols = [c for c in COLS["decisions"] if c != "id" or (old is not None and old not in taken)]
        cur = d.execute(f"INSERT INTO decisions({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                        [r.get(c) for c in cols])
        if old is not None:
            idmap[old] = cur.lastrowid
    for t in ("snapshots", "fills", "events"):
        cols = COLS[t]
        for r in data.get(t, []):
            r = dict(r)
            if t == "fills" and r.get("decision_id") in idmap:
                r["decision_id"] = idmap[r["decision_id"]]
            d.execute(f"INSERT INTO {t}({','.join(cols)}) VALUES({','.join('?' * len(cols))})", [r.get(c) for c in cols])
    for k, v in data.get("meta", {}).items():
        if k in GLOBAL_META:
            if k == "market_regime_ts" and (j.get(k) or "") > v:
                continue  # keep the newer regime reading
            if k == "market_regime" and (j.get("market_regime_ts") or "") > data["meta"].get("market_regime_ts", ""):
                continue
        d.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))
    d.commit()
    return {"imported": True, "decisions": len(data.get("decisions", [])), "fills": len(data.get("fills", []))}
