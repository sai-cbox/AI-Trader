"""Plain indicator math on daily bars (oldest -> newest). Wilder smoothing for RSI / ATR / ADX."""
from __future__ import annotations


def sma(vals: list[float], n: int) -> float | None:
    return sum(vals[-n:]) / n if len(vals) >= n else None


def rsi(closes: list[float], n: int = 14) -> float | None:
    if len(closes) < n + 1:
        return None
    gains = [max(closes[i] - closes[i - 1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0) for i in range(1, len(closes))]
    ag, al = sum(gains[:n]) / n, sum(losses[:n]) / n
    for g, l in zip(gains[n:], losses[n:]):
        ag, al = (ag * (n - 1) + g) / n, (al * (n - 1) + l) / n
    return 100.0 if al == 0 else 100 - 100 / (1 + ag / al)


def _true_ranges(bars: list[dict]) -> list[float]:
    return [max(b["high"] - b["low"], abs(b["high"] - p["close"]), abs(b["low"] - p["close"]))
            for p, b in zip(bars, bars[1:])]


def atr(bars: list[dict], n: int = 14) -> float | None:
    tr = _true_ranges(bars)
    if len(tr) < n:
        return None
    a = sum(tr[:n]) / n
    for t in tr[n:]:
        a = (a * (n - 1) + t) / n
    return a


def bollinger(closes: list[float], n: int = 20, k: float = 2.0) -> tuple[float, float, float] | None:
    if len(closes) < n:
        return None
    seg = closes[-n:]
    mid = sum(seg) / n
    sd = (sum((x - mid) ** 2 for x in seg) / n) ** 0.5
    return mid - k * sd, mid, mid + k * sd


def adx(bars: list[dict], n: int = 14) -> float | None:
    if len(bars) < 2 * n + 1:
        return None
    pdm, mdm, tr = [], [], _true_ranges(bars)
    for p, b in zip(bars, bars[1:]):
        up, dn = b["high"] - p["high"], p["low"] - b["low"]
        pdm.append(up if up > dn and up > 0 else 0.0)
        mdm.append(dn if dn > up and dn > 0 else 0.0)

    def smooth(xs):
        s = sum(xs[:n])
        out = [s]
        for x in xs[n:]:
            s = s - s / n + x
            out.append(s)
        return out

    st, sp, sm = smooth(tr), smooth(pdm), smooth(mdm)
    dx = []
    for t, p, m in zip(st, sp, sm):
        pi, mi = (100 * p / t if t else 0), (100 * m / t if t else 0)
        dx.append(100 * abs(pi - mi) / (pi + mi) if (pi + mi) else 0.0)
    a = sum(dx[:n]) / n
    for d in dx[n:]:
        a = (a * (n - 1) + d) / n
    return a


def consecutive_down(closes: list[float]) -> int:
    n = 0
    for i in range(len(closes) - 1, 0, -1):
        if closes[i] < closes[i - 1]:
            n += 1
        else:
            break
    return n
