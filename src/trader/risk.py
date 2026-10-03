"""Deterministic risk guard. Every order must pass check(); the LLM cannot bypass it. One book per strategy."""
from __future__ import annotations

import math
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from . import controls
from .config import Config
from .journal import Journal, utcnow
from .models import Account, Decision, Proposal

ET = ZoneInfo("America/New_York")
SYMBOL_RE = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")


class RiskGuard:
    def __init__(self, cfg: Config, journal: Journal):
        self.cfg, self.j = cfg, journal

    # ---- equity tracking: day start, peak, drawdown trip
    def refresh(self, book: str, account: Account, now: datetime | None = None) -> None:
        now = now or utcnow()
        day = now.astimezone(ET).date().isoformat()
        if self.j.get(f"{book}:day") != day:
            self.j.set(f"{book}:day", day)
            self.j.set(f"{book}:day_start_equity", str(account.equity))
        peak = max(float(self.j.get(f"{book}:peak_equity", "0")), account.equity)
        self.j.set(f"{book}:peak_equity", str(peak))
        self.j.snapshot(book, account.equity, account.cash, now)

        dd = (peak - account.equity) / peak * 100 if peak else 0.0
        if dd >= self.cfg.drawdown_stop_pct and controls.get_state(self.j, book) == controls.RUNNING:
            controls.set_state(self.j, book, controls.PAUSED,
                               f"drawdown {dd:.1f}% >= {self.cfg.drawdown_stop_pct}% from peak")
        elif dd >= self.cfg.drawdown_warn_pct and self.j.get(f"{book}:warned") != "1":
            self.j.set(f"{book}:warned", "1")
            self.j.event(book, "warning", f"drawdown {dd:.1f}% >= {self.cfg.drawdown_warn_pct}%")
        elif dd < self.cfg.drawdown_warn_pct:
            self.j.set(f"{book}:warned", "0")

    def daily_pnl_pct(self, book: str, account: Account) -> float:
        start = float(self.j.get(f"{book}:day_start_equity", str(account.equity)))
        return (account.equity - start) / start * 100 if start else 0.0

    # ---- the gate
    def check(self, book: str, p: Proposal, account: Account, quote: float,
              now: datetime | None = None) -> Decision:
        now = now or utcnow()
        self.refresh(book, account, now)
        d = self._evaluate(book, p, account, quote, now)
        d.id = self.j.decision(book, p, quote, d.qty, d.approved, d.reasons, d.warnings, now)
        return d

    def _evaluate(self, book, p: Proposal, a: Account, quote: float, now: datetime) -> Decision:
        c, rej = self.cfg, []

        def reject(msg):
            return Decision(False, 0, [msg])

        kind = c.kind(book)
        if kind is None:
            return reject(f"unknown strategy {book}")
        if p.side not in ("buy", "sell"):
            return reject(f"bad side {p.side}")
        if p.qty <= 0:
            return reject("qty must be positive")
        if quote <= 0 or not math.isfinite(quote):
            return reject("invalid quote (stale/missing data)")
        if p.asset_type != "equity":
            return reject(f"asset type {p.asset_type} not allowed (equities only)")
        if not SYMBOL_RE.match(p.symbol):
            return reject(f"symbol {p.symbol} not a plain equity ticker")
        if p.symbol in c.symbol_denylist:
            return reject(f"{p.symbol} is denylisted")
        if p.order_type not in ("market", "limit"):
            return reject(f"order type {p.order_type} not allowed")
        if p.order_type == "limit":
            if p.limit_price is None or p.limit_price <= 0:
                return reject("limit order needs limit_price")
            if abs(p.limit_price - quote) / quote * 100 > c.price_sanity_pct:
                return reject(f"limit {p.limit_price} more than {c.price_sanity_pct}% from quote {quote}")

        if kind == "live":
            if not c.allowed_account_id:
                return reject("allowed_account_id not configured; live trading blocked")
            if a.account_id != c.allowed_account_id:
                return reject("account mismatch; live trading restricted to the Agentic account")

        if controls.kill_file_present(c.db_path):
            return reject("STOP file present (kill switch)")
        state = controls.get_state(self.j, book)
        if state == controls.STOPPED:
            return reject("trading is stopped (run `trader start`)")

        today = now.astimezone(ET).date()
        day = today.isoformat()
        todays = [r for r in self.j.approved_decisions(book)
                  if datetime.fromisoformat(r["ts"]).astimezone(ET).date().isoformat() == day]
        if len(todays) >= c.max_orders_per_day:
            return reject(f"max orders per day ({c.max_orders_per_day}) reached")
        for r in self.j.approved_decisions(book, p.symbol, p.side):
            age = (now - datetime.fromisoformat(r["ts"])).total_seconds()
            if 0 <= age < c.duplicate_window_sec:
                return reject(f"duplicate {p.side} {p.symbol} within {c.duplicate_window_sec}s")

        price = p.limit_price if p.order_type == "limit" else quote
        pos = a.position(p.symbol)

        if p.side == "sell":  # exits are always allowed while not STOPPED, never short
            held = int(pos.qty) if pos else 0
            if held <= 0:
                return reject(f"no {p.symbol} position to sell")
            qty = min(p.qty, held)
            warn = [f"qty reduced to held {held}"] if qty < p.qty else []
            return Decision(True, qty, [], warn)

        # ---- buys
        if state == controls.PAUSED:
            return reject("trading is paused (exits only)")
        if controls.window_ended(self.j, book, today):
            return reject("evaluation window ended (exits only)")
        pnl = self.daily_pnl_pct(book, a)
        if pnl <= -c.daily_loss_limit_pct:
            return reject(f"daily loss {pnl:.2f}% hit limit -{c.daily_loss_limit_pct}%")

        held_value = (pos.qty * quote) if pos else 0.0
        room_pos = c.max_position_pct / 100 * a.equity - held_value
        room_inv = c.max_invested_pct / 100 * a.equity - a.invested
        allowed = math.floor(min(room_pos, room_inv, a.cash) / price) if price else 0
        qty = min(p.qty, allowed)
        if qty <= 0:
            return reject("no room: position cap, invested cap or cash exhausted")
        warn = []
        if qty < p.qty:
            warn.append(f"qty reduced {p.qty}->{qty} by position/invested/cash limits")
        return Decision(True, qty, [], warn)
