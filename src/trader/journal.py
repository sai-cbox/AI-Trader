from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS events (ts TEXT, book TEXT, kind TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS snapshots (ts TEXT, book TEXT, equity REAL, cash REAL);
CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, book TEXT, symbol TEXT, side TEXT,
  quote REAL, req_qty INTEGER, qty INTEGER, approved INTEGER,
  reasons TEXT, warnings TEXT, rationale TEXT, signals TEXT,
  stop_price REAL, target_price REAL, needs_approval INTEGER DEFAULT 0, user_approved_at TEXT);
CREATE TABLE IF NOT EXISTS fills (
  ts TEXT, book TEXT, symbol TEXT, side TEXT, qty REAL, price REAL, ref TEXT,
  rationale TEXT, decision_id INTEGER);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Journal:
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # ---- meta / state
    def get(self, key: str, default: str | None = None) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set(self, key: str, value: str) -> None:
        self.db.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.db.commit()

    # ---- logging
    def event(self, book: str, kind: str, detail: str, now: datetime | None = None) -> None:
        self.db.execute("INSERT INTO events VALUES(?,?,?,?)",
                        ((now or utcnow()).isoformat(), book, kind, detail))
        self.db.commit()

    def snapshot(self, book: str, equity: float, cash: float, now: datetime | None = None) -> None:
        self.db.execute("INSERT INTO snapshots VALUES(?,?,?,?)",
                        ((now or utcnow()).isoformat(), book, equity, cash))
        self.db.commit()

    def decision(self, book, p, quote, qty, approved, reasons, warnings, now=None, needs_approval=False) -> int:
        cur = self.db.execute(
            "INSERT INTO decisions(ts,book,symbol,side,quote,req_qty,qty,approved,reasons,warnings,rationale,signals,stop_price,target_price,needs_approval)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ((now or utcnow()).isoformat(), book, p.symbol, p.side, quote, p.qty, qty, int(approved),
             json.dumps(reasons), json.dumps(warnings), p.rationale, json.dumps(p.signals, default=str),
             p.stop_price, p.target_price, int(needs_approval)))
        self.db.commit()
        return cur.lastrowid

    def get_decision(self, decision_id: int):
        return self.db.execute("SELECT * FROM decisions WHERE id=?", (decision_id,)).fetchone()

    def approve_decision(self, decision_id: int, now=None) -> None:
        self.db.execute("UPDATE decisions SET user_approved_at=? WHERE id=?",
                        ((now or utcnow()).isoformat(), decision_id))
        self.db.commit()

    def fill(self, book, symbol, side, qty, price, ref="", rationale="", decision_id=None, now=None) -> None:
        self.db.execute("INSERT INTO fills VALUES(?,?,?,?,?,?,?,?,?)",
                        ((now or utcnow()).isoformat(), book, symbol, side, qty, price, ref,
                         rationale, decision_id))
        self.set(f"{book}:mark:{symbol}", str(price))

    # ---- queries
    def approved_decisions(self, book: str, symbol: str | None = None, side: str | None = None):
        q, args = "SELECT * FROM decisions WHERE book=? AND approved=1", [book]
        if symbol:
            q += " AND symbol=?"; args.append(symbol)
        if side:
            q += " AND side=?"; args.append(side)
        return self.db.execute(q + " ORDER BY ts", args).fetchall()

    def fills(self, book: str):
        return self.db.execute("SELECT * FROM fills WHERE book=? ORDER BY ts, rowid", (book,)).fetchall()

    def snapshots(self, book: str):
        return self.db.execute("SELECT * FROM snapshots WHERE book=? ORDER BY ts", (book,)).fetchall()

    def mark(self, book: str, symbol: str) -> float | None:
        v = self.get(f"{book}:mark:{symbol}")
        return float(v) if v else None
