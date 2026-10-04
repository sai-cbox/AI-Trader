"""Deterministic facts for the momentum-quality strategy (your robinhood-stock-trader skill).

Everything here is plain arithmetic on market data. The analyst (Claude) is GIVEN these facts and cannot restate
them differently: the guard also relies on the regime computed here, not on anything the model says.
"""
from __future__ import annotations

from dataclasses import dataclass, field


def macd_state(hist: list[float]) -> str:
    """Skill Phase 5: POSITIVE/NEGATIVE + RISING/FALLING from the last two MACD histogram values."""
    if len(hist) < 2:
        return "UNKNOWN"
    sign = "POSITIVE" if hist[-1] > 0 else "NEGATIVE"
    return f"{sign}_{'RISING' if hist[-1] > hist[-2] else 'FALLING'}"


def extension(price: float, ema21: float, atr14: float) -> tuple[float, str]:
    """Skill Phase 3 (MARS): distance from the 21 EMA in ATR units, with the skill's labels."""
    ext = (price - ema21) / atr14 if atr14 else float("inf")
    if ext < 0:
        label = "BELOW_21EMA"
    elif ext <= 0.5:
        label = "CLEAN"
    elif ext <= 1.5:
        label = "ACCEPTABLE"
    elif ext <= 2.0:
        label = "LATE"
    else:
        label = "TAKE_PROFITS"
    return round(ext, 2), label


def trend_template(price: float, sma50: float, sma150: float, sma200: float, sma200_month_ago: float,
                   high52: float, low52: float) -> dict:
    """Skill Phase 3: all seven must be true."""
    checks = {
        "price_above_50": price > sma50,
        "price_above_150": price > sma150,
        "price_above_200": price > sma200,
        "sma200_rising": sma200 > sma200_month_ago,
        "ma_stack_50_150_200": sma50 > sma150 > sma200,
        "within_25pct_of_52w_high": price >= high52 * 0.75,
        "at_least_30pct_above_52w_low": price >= low52 * 1.30,
    }
    return {"checks": checks, "pass": all(checks.values()),
            "pct_below_52w_high": round((1 - price / high52) * 100, 1) if high52 else None,
            "pct_above_52w_low": round((price / low52 - 1) * 100, 1) if low52 else None}


def distribution_days(bars: list[dict], lookback: int = 20) -> int:
    """Down days (>=0.2% lower close) on higher volume than the prior session, over the last `lookback` sessions."""
    seg = bars[-(lookback + 1):]
    n = 0
    for prev, cur in zip(seg, seg[1:]):
        if cur["close"] <= prev["close"] * 0.998 and cur["volume"] > prev["volume"]:
            n += 1
    return n


def distribution_dates(bars: list[dict], lookback: int = 20) -> list[str | None]:
    seg = bars[-(lookback + 1):]
    return [cur.get("t") for prev, cur in zip(seg, seg[1:])
            if cur["close"] <= prev["close"] * 0.998 and cur["volume"] > prev["volume"]]


def new_4w_low_recent(bars: list[dict]) -> bool:
    """True if the lowest low of the last 5 sessions undercuts the lowest low of the 15 sessions before them."""
    if len(bars) < 20:
        return False
    last5 = min(b["low"] for b in bars[-5:])
    prior = min(b["low"] for b in bars[-20:-5])
    return last5 < prior


def pct_return(bars: list[dict], days: int) -> float | None:
    if len(bars) <= days:
        return None
    return round((bars[-1]["close"] / bars[-1 - days]["close"] - 1) * 100, 2)


@dataclass
class RegimeResult:
    state: str  # "RISK-ON" | "RISK-OFF"
    reasons: list[str] = field(default_factory=list)
    qqq_distribution: bool = False
    facts: dict = field(default_factory=dict)


def regime(spy: dict, qqq: dict) -> RegimeResult:
    """Skill Phase 1. `spy`/`qqq`: price, sma50, sma200, ema50, ema100, dist_days, new_low_5d."""
    off, why = [], []
    if spy["price"] < spy["sma200"]:
        off.append("SPY below 200-day MA")
    if spy["dist_days"] >= 4:
        off.append(f"SPY has {spy['dist_days']} distribution days in 4 weeks (>=4)")
    if spy["price"] <= spy["sma50"]:
        off.append("SPY not above 50-day MA")
    if spy["new_low_5d"]:
        off.append("SPY made a new 4-week low in the past 5 sessions")
    if not spy["ema50"] > spy["ema100"]:
        off.append("SPY 50d EMA not above 100d EMA")
    if not off:
        why.append("SPY above 50d and 200d MA, EMA50>EMA100, no new 4-week low, <4 distribution days")
    return RegimeResult("RISK-OFF" if off else "RISK-ON", off or why,
                        qqq_distribution=qqq.get("dist_days", 0) >= 4, facts={"SPY": spy, "QQQ": qqq})


def shares_for(position_size: float, ask: float) -> int:
    """Skill: shares = floor(position_size / ask); skip when 0."""
    return int(position_size // ask) if ask > 0 else 0
