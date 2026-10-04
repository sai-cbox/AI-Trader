"""Deterministic risk guard. Every order must pass check(); the LLM cannot bypass it. One book per strategy."""
from __future__ import annotations

import json
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
        today = now.astimezone(ET).date()
        if d.approved and controls.confirm_active(self.j, book, self.cfg.opt(book, "confirm_days", 0), today):
            d.needs_user_approval = True
            d.warnings.append("confirm period: ask the user, then `trader approve --decision-id`")
        d.id = self.j.decision(book, p, quote, d.qty, d.approved, d.reasons, d.warnings, now,
                               d.needs_user_approval)
        return d

    def _held_meta(self, book: str, a: Account) -> list[dict]:
        """Sector / AI flags of current holdings, from the latest approved buy decision of each symbol."""
        out = []
        for pos in a.positions:
            row = self.j.db.execute(
                "SELECT signals FROM decisions WHERE book=? AND symbol=? AND side='buy' AND approved=1 "
                "ORDER BY id DESC LIMIT 1", (book, pos.symbol)).fetchone()
            sig = json.loads(row["signals"]) if row else {}
            out.append({"symbol": pos.symbol, "sector": sig.get("sector") or pos.sector,
                        "is_ai": bool(sig.get("is_ai")) or pos.is_ai})
        return out

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

        if c.opt(book, "limit_only", False) and p.order_type != "limit":
            return reject("limit orders only for this strategy")

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

        # ---- strategy rules (per-book overrides; encode the strategy's hard rules)
        if c.opt(book, "require_regime", False):
            regime, rts = self.j.get("market_regime"), self.j.get("market_regime_ts")
            fresh = bool(rts) and (now - datetime.fromisoformat(rts)).total_seconds() < 36 * 3600
            if regime != "RISK-ON" or not fresh:
                return reject(f"market regime is {regime if fresh else 'unset/stale'}; buys need fresh RISK-ON")
        phases = p.signals.get("phases", {})
        failing = [n for n in c.opt(book, "required_phases", []) or []
                   if not (isinstance(phases.get(n), dict) and phases[n].get("pass") is True)]
        if failing:
            return reject("phases not passed/missing: " + ", ".join(failing))
        held = self._held_meta(book, a)
        if c.opt(book, "no_add", False) and pos:
            return reject(f"already holding {p.symbol}; no averaging down / adding")
        mp = c.opt(book, "max_positions")
        if mp and not pos and len(a.positions) >= mp:
            return reject(f"max positions ({mp}) reached")
        if c.opt(book, "one_per_sector", False):
            sector = p.signals.get("sector")
            if not sector:
                return reject("signals.sector required (one position per sector)")
            if any(h["sector"] == sector for h in held):
                return reject(f"sector {sector} already held")
        mai = c.opt(book, "max_ai_names")
        if mai is not None and p.signals.get("is_ai") and sum(h["is_ai"] for h in held) >= mai:
            return reject(f"max AI names ({mai}) reached")

        warn = []
        pos_cap = c.opt(book, "max_position_pct", c.max_position_pct)
        ed = p.signals.get("earnings_days")
        if ed is not None and float(ed) <= 10:
            pos_cap /= 2
            warn.append(f"earnings in {ed} days: position cap halved to {pos_cap:.1f}%")
        held_value = (pos.qty * quote) if pos else 0.0
        room_pos = pos_cap / 100 * a.equity - held_value
        room_inv = c.opt(book, "max_invested_pct", c.max_invested_pct) / 100 * a.equity - a.invested
        allowed = math.floor(min(room_pos, room_inv, a.cash) / price) if price else 0
        rpt = c.opt(book, "risk_per_trade_pct")
        if c.opt(book, "require_stop", False) or (rpt and p.stop_price):
            if p.stop_price is None or not (0 < p.stop_price < price):
                return reject("valid stop_price below entry price required")
            if rpt:
                risk_qty = math.floor(rpt / 100 * a.equity / (price - p.stop_price))
                if risk_qty < allowed:
                    warn.append(f"qty limited to {risk_qty} by {rpt}% risk-per-trade at stop {p.stop_price}")
                allowed = min(allowed, risk_qty)
        qty = min(p.qty, allowed)
        if qty <= 0:
            return reject("no room: position cap, invested cap, risk-per-trade or cash exhausted")
        if qty < p.qty and not any(w.startswith("qty") for w in warn):
            warn.append(f"qty reduced {p.qty}->{qty} by position/invested/cash limits")
        return Decision(True, qty, [], warn)
