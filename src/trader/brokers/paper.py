"""Simulated account per strategy book. State lives in the journal (cash in meta, positions from fills)."""
from __future__ import annotations

from datetime import datetime

from ..config import Config
from ..journal import Journal
from ..models import Account, Position
from ..reporting import open_positions


class PaperBroker:
    def __init__(self, cfg: Config, journal: Journal, book: str):
        if cfg.kind(book) != "paper":
            raise ValueError(f"{book} is not a paper strategy")
        self.cfg, self.j, self.book = cfg, journal, book
        if self.j.get(f"{book}:cash") is None:
            self.j.set(f"{book}:cash", str(cfg.start_cash))

    @property
    def cash(self) -> float:
        return float(self.j.get(f"{self.book}:cash"))

    def account(self) -> Account:
        positions = [
            Position(sym, v["qty"], self.j.mark(self.book, sym) or v["avg_cost"])
            for sym, v in open_positions(self.j.fills(self.book)).items()
        ]
        equity = self.cash + sum(p.qty * p.price for p in positions)
        return Account(f"paper:{self.book}", equity, self.cash, positions)

    def mark(self, prices: dict[str, float]) -> None:
        for sym, px in prices.items():
            self.j.set(f"{self.book}:mark:{sym.upper()}", str(px))

    def fill(self, symbol, side, qty, price, rationale="", decision_id=None,
             now: datetime | None = None) -> float:
        slip = self.cfg.slippage_bps / 10_000
        px = round(price * (1 + slip) if side == "buy" else price * (1 - slip), 4)
        held = open_positions(self.j.fills(self.book)).get(symbol, {}).get("qty", 0.0)
        if side == "sell" and qty > held + 1e-9:
            raise ValueError("paper short selling not allowed")
        cash = self.cash + (-qty * px if side == "buy" else qty * px)
        if cash < -1e-6:
            raise ValueError("insufficient paper cash")
        self.j.set(f"{self.book}:cash", str(cash))
        self.j.fill(self.book, symbol, side, qty, px, rationale=rationale,
                    decision_id=decision_id, now=now)
        return px
