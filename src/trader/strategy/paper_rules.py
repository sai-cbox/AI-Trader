"""The four paper strategies as mechanical rules, exactly as written in strategies/*.md.

Each function takes a Ctx and returns (exits, entries). No LLM, no network: same inputs always give the same decisions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from . import indicators as ind

TREND_UNIVERSE = ("SPY", "QQQ", "IWM", "XLK", "XLF", "XLE", "XLV", "MSFT", "AAPL", "NVDA", "GOOGL", "AMZN", "META")


@dataclass
class Exit:
    symbol: str
    rule: str
    signals: dict = field(default_factory=dict)


@dataclass
class Entry:
    symbol: str
    price: float
    qty: int
    rationale: str
    signals: dict
    rank: float
    stop_price: float | None = None
    state: dict = field(default_factory=dict)


@dataclass
class Ctx:
    bars: dict[str, list[dict]]
    positions: dict[str, dict]            # symbol -> {"qty", "avg", "state": {entry_date, entry_px, ...}}
    equity: float
    universe: list[str]
    earn_days: dict[str, int | None] = field(default_factory=dict)
    earn_detail: dict[str, dict] = field(default_factory=dict)
    entries_allowed: bool = True          # trend-following evaluates entries once per week


def _c(b): return [x["close"] for x in b]
def _held_days(b, entry_date): return sum(1 for x in b if entry_date and x.get("t") and x["t"] > entry_date)
def _size(ctx, price, pct=10.0): return int(ctx.equity * pct / 100 // price) if price > 0 else 0
def _have(ctx, sym, n): return sym in ctx.bars and len(ctx.bars[sym]) >= n


def mean_reversion(ctx: Ctx):
    exits, cands = [], []
    for sym, pos in ctx.positions.items():
        if not _have(ctx, sym, 10):
            continue
        b = ctx.bars[sym]; c = _c(b); st = pos["state"]
        r2, s5, days = ind.rsi(c, 2), ind.sma(c, 5), _held_days(b, st.get("entry_date"))
        why = []
        if r2 is not None and r2 > 60: why.append(f"RSI2 {r2:.0f} > 60 (reverted)")
        if s5 and c[-1] > s5: why.append(f"close {c[-1]:.2f} above 5d SMA {s5:.2f}")
        if days >= 7: why.append(f"time stop: held {days} trading days")
        if st.get("entry_px") and c[-1] <= st["entry_px"] * 0.94: why.append(f"hard stop: 6% below entry {st['entry_px']:.2f}")
        if why:
            exits.append(Exit(sym, "; ".join(why), {"rsi2": round(r2, 1) if r2 is not None else None, "days_held": days}))
    for sym in ctx.universe:
        if sym in ctx.positions or not _have(ctx, sym, 205):
            continue
        c = _c(ctx.bars[sym]); s200, r2, bb, dd = ind.sma(c, 200), ind.rsi(c, 2), ind.bollinger(c), ind.consecutive_down(c)
        ed = ctx.earn_days.get(sym)
        if ed is not None and ed <= 3:
            continue
        oversold = (r2 is not None and r2 < 10) or (bb is not None and c[-1] < bb[0])
        if c[-1] > s200 and oversold and dd >= 3:
            cands.append(Entry(sym, c[-1], _size(ctx, c[-1]), f"Oversold pullback inside an uptrend: RSI2 {r2:.1f}, {dd} down days in a row, "
                               f"price {c[-1]:.2f} above 200d SMA {s200:.2f}",
                               {"rsi2": round(r2, 1), "down_days": dd, "price_vs_sma200_pct": round((c[-1] / s200 - 1) * 100, 1),
                                "below_lower_bollinger": bb is not None and c[-1] < bb[0]},
                               rank=r2, state={"entry_px": c[-1]}))
    cands.sort(key=lambda e: e.rank)
    return exits, cands[:max(0, 6 - (len(ctx.positions) - len(exits)))]


def breakout(ctx: Ctx):
    exits, cands = [], []
    for sym, pos in ctx.positions.items():
        if not _have(ctx, sym, 30):
            continue
        b = ctx.bars[sym]; c = _c(b); st = pos["state"]; a = ind.atr(b, 14)
        low20 = min(x["low"] for x in b[-21:-1])
        hc = max((x["close"] for x in b if st.get("entry_date") and x.get("t") and x["t"] >= st["entry_date"]), default=c[-1])
        why = []
        if c[-1] < low20: why.append(f"close {c[-1]:.2f} below prior 20-day low {low20:.2f}")
        if a and c[-1] <= hc - 2.5 * a: why.append(f"trailing stop: 2.5 ATR below high close {hc:.2f}")
        if st.get("entry_px") and c[-1] <= st["entry_px"] * 0.92: why.append("hard stop: 8% below entry")
        if why:
            exits.append(Exit(sym, "; ".join(why), {"high_close_since_entry": hc, "atr14": round(a, 2) if a else None}))
    for sym in ctx.universe:
        if sym in ctx.positions or not _have(ctx, sym, 60):
            continue
        b = ctx.bars[sym]; c = _c(b); a = ind.atr(b, 14)
        prior_high = max(x["high"] for x in b[-56:-1])
        vol_avg = sum(x["volume"] for x in b[-51:-1]) / 50
        if not a or vol_avg <= 0:
            continue
        vr = b[-1]["volume"] / vol_avg
        if c[-1] > prior_high and vr >= 1.5 and a / c[-1] < 0.04:
            stop_dist = 2.5 * a
            qty = int(min(0.01 * ctx.equity / stop_dist, 0.10 * ctx.equity / c[-1]))
            cands.append(Entry(sym, c[-1], qty, f"Closed at {c[-1]:.2f} above the prior 55-day high {prior_high:.2f} on {vr:.1f}x average volume "
                               f"after a tight base (ATR {a / c[-1] * 100:.1f}% of price)",
                               {"high55_break": True, "volume_ratio": round(vr, 2), "atr_pct": round(a / c[-1] * 100, 2),
                                "stop_price": round(c[-1] - stop_dist, 2)}, rank=-vr, stop_price=round(c[-1] - stop_dist, 2),
                               state={"entry_px": c[-1]}))
    cands.sort(key=lambda e: e.rank)
    return exits, cands[:max(0, 8 - (len(ctx.positions) - len(exits)))]


def trend_following(ctx: Ctx):
    exits, cands = [], []
    for sym, pos in ctx.positions.items():
        if not _have(ctx, sym, 205):
            continue
        c = _c(ctx.bars[sym]); s50, s200, s100, s100p = ind.sma(c, 50), ind.sma(c, 200), ind.sma(c, 100), ind.sma(c[:-1], 100)
        why = []
        if s50 < s200: why.append(f"50d SMA {s50:.2f} crossed below 200d {s200:.2f}")
        if c[-1] < s100 and c[-2] < s100p: why.append("two closes below the 100d SMA")
        if why:
            exits.append(Exit(sym, "; ".join(why), {"sma50": round(s50, 2), "sma200": round(s200, 2)}))
    if ctx.entries_allowed:
        for sym in ctx.universe:
            if sym in ctx.positions or not _have(ctx, sym, 215):
                continue
            b = ctx.bars[sym]; c = _c(b)
            s50, s200, s50p, s200p, ax = ind.sma(c, 50), ind.sma(c, 200), ind.sma(c[:-10], 50), ind.sma(c[:-10], 200), ind.adx(b, 14)
            crossed = s50p <= s200p and s50 > s200
            riding = c[-1] > s50 and c[-1] > s200 and s50 > s50p
            if ax is not None and ax > 20 and (crossed or riding):
                cands.append(Entry(sym, c[-1], _size(ctx, c[-1]), ("Fresh 50/200 golden cross" if crossed else "Established uptrend: price above 50d and 200d, "
                                   "50d rising") + f"; ADX {ax:.0f} > 20", {"sma50": round(s50, 2), "sma200": round(s200, 2), "adx14": round(ax, 1),
                                                                         "regime": "golden_cross" if crossed else "uptrend"}, rank=-ax,
                                   state={"entry_px": c[-1]}))
    cands.sort(key=lambda e: e.rank)
    return exits, cands[:max(0, 8 - (len(ctx.positions) - len(exits)))]


def earnings_drift(ctx: Ctx):
    exits, cands = [], []
    for sym, pos in ctx.positions.items():
        if not _have(ctx, sym, 10):
            continue
        b = ctx.bars[sym]; c = _c(b); st = pos["state"]; days = _held_days(b, st.get("entry_date"))
        hc = max((x["close"] for x in b if st.get("entry_date") and x.get("t") and x["t"] >= st["entry_date"]), default=c[-1])
        why = []
        if days >= 30: why.append(f"time stop: held {days} trading days")
        if st.get("ref_close") and c[-1] < st["ref_close"]: why.append(f"gap filled: close {c[-1]:.2f} below pre-earnings close {st['ref_close']:.2f}")
        if c[-1] <= hc * 0.92: why.append(f"trailing stop: 8% below high close {hc:.2f}")
        if why:
            exits.append(Exit(sym, "; ".join(why), {"days_held": days}))
    for sym, d in ctx.earn_detail.items():
        if sym in ctx.positions or not _have(ctx, sym, 30) or d.get("eps_actual") is None or not d.get("eps_est"):
            continue
        surprise = (d["eps_actual"] - d["eps_est"]) / abs(d["eps_est"])
        b = ctx.bars[sym]
        react = next((i for i, x in enumerate(b) if x.get("t") and (x["t"] > d["date"] if d.get("timing") == "pm" else x["t"] >= d["date"])), None)
        if react is None or react < 1 or len(b) - 1 - react > 2 or b[react].get("open") is None or b[-1]["close"] < 10:
            continue
        ref = b[react - 1]["close"]
        gap = b[react]["open"] / ref - 1
        if surprise > 0.05 and gap > 0.03 and b[-1]["close"] > b[react]["low"]:
            cands.append(Entry(sym, b[-1]["close"], _size(ctx, b[-1]["close"]), f"Beat estimates by {surprise * 100:.0f}% and gapped up {gap * 100:.1f}%, "
                               f"holding above the gap-day low", {"eps_surprise_pct": round(surprise * 100, 1), "gap_pct": round(gap * 100, 1),
                                                                   "days_since_report": len(b) - 1 - react}, rank=-surprise,
                               state={"entry_px": b[-1]["close"], "ref_close": ref}))
    cands.sort(key=lambda e: e.rank)
    return exits, cands[:max(0, 5 - (len(ctx.positions) - len(exits)))]


STRATEGIES = {"mean-reversion": mean_reversion, "breakout": breakout, "trend-following": trend_following,
              "earnings-drift": earnings_drift}
