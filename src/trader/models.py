from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Proposal:
    symbol: str
    side: str  # "buy" | "sell"
    qty: int
    order_type: str = "market"
    limit_price: float | None = None
    rationale: str = ""
    asset_type: str = "equity"
    signals: dict = field(default_factory=dict)  # indicators / rule hits behind the idea

    @classmethod
    def from_dict(cls, d: dict) -> "Proposal":
        return cls(
            symbol=str(d["symbol"]).upper(),
            side=str(d["side"]).lower(),
            qty=int(d["qty"]),
            order_type=str(d.get("order_type", "market")).lower(),
            limit_price=None if d.get("limit_price") is None else float(d["limit_price"]),
            rationale=str(d.get("rationale", "")),
            asset_type=str(d.get("asset_type", "equity")).lower(),
            signals=dict(d.get("signals", {})),
        )


@dataclass
class Position:
    symbol: str
    qty: float
    price: float  # last mark


@dataclass
class Account:
    account_id: str
    equity: float
    cash: float
    positions: list[Position] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "Account":
        return cls(
            account_id=str(d.get("account_id", "")),
            equity=float(d["equity"]),
            cash=float(d["cash"]),
            positions=[
                Position(str(p["symbol"]).upper(), float(p["qty"]), float(p["price"]))
                for p in d.get("positions", [])
            ],
        )

    def position(self, symbol: str) -> Position | None:
        return next((p for p in self.positions if p.symbol == symbol), None)

    @property
    def invested(self) -> float:
        return sum(p.qty * p.price for p in self.positions)


@dataclass
class Decision:
    approved: bool
    qty: int
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    id: int | None = None

    def to_dict(self) -> dict:
        return {
            "decision_id": self.id,
            "approved": self.approved,
            "qty": self.qty,
            "reasons": self.reasons,
            "warnings": self.warnings,
        }
