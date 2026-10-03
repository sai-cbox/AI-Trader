from __future__ import annotations

from typing import Protocol

from ..models import Account


class Broker(Protocol):
    def account(self) -> Account: ...
    def fill(self, symbol: str, side: str, qty: int, price: float, rationale: str = "") -> float: ...
