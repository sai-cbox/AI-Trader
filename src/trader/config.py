from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

DEFAULT_STRATEGIES = {
    "momentum-quality": "live",
    "mean-reversion": "paper",
    "breakout": "paper",
    "trend-following": "paper",
    "earnings-drift": "paper",
}


@dataclass(frozen=True)
class Config:
    allowed_account_id: str = ""
    start_cash: float = 100_000.0
    daily_loss_limit_pct: float = 3.0
    drawdown_stop_pct: float = 12.0
    drawdown_warn_pct: float = 6.0
    max_position_pct: float = 10.0
    max_invested_pct: float = 85.0
    max_orders_per_day: int = 20
    price_sanity_pct: float = 5.0
    duplicate_window_sec: int = 300
    slippage_bps: float = 5.0
    window_trading_days: int = 30
    symbol_denylist: tuple[str, ...] = ()
    db_path: str = "data/trader.db"
    ai_symbols: tuple[str, ...] = ("NVDA", "AMD", "AVGO", "ARM", "SMCI", "MRVL", "ANET", "VRT", "NBIS", "CIEN", "NOW", "SNOW",
                                   "PLTR", "APP", "CRM", "DDOG", "MDB", "IREN", "CORZ", "HUT", "NNE", "CCJ", "BWXT", "CEG", "VST")
    ntfy_topic: str = ""       # phone alerts via ntfy.sh; empty = off
    scan_id: str = "ca8f132f-c07b-473f-9456-31e09b1e0d46"   # saved Robinhood scan used by the momentum-quality skill
    max_candidates: int = 20
    max_per_sector_candidates: int = 4
    analyst_model: str = "claude-opus-5-5"
    analyst_effort: str = "high"
    analyst_max_proposals: int = 3
    analyst_daily_cap_usd: float = 5.0
    analyst_price_in_per_mtok: float = 4.0
    analyst_price_out_per_mtok: float = 20.0
    strategies: dict = field(default_factory=lambda: dict(DEFAULT_STRATEGIES))
    # per-strategy rule overrides, e.g. {"momentum-quality": {"max_position_pct": 16.0, ...}}
    overrides: dict = field(default_factory=dict)

    def __post_init__(self):
        bad = {k: v for k, v in self.strategies.items() if v not in ("live", "paper")}
        if bad:
            raise ValueError(f"strategy kind must be 'live' or 'paper': {bad}")
        if sum(v == "live" for v in self.strategies.values()) > 1:
            raise ValueError("at most one strategy may be live")

    def opt(self, book: str, key: str, default=None):
        """Per-strategy override, falling back to the global value of the same name, then `default`."""
        ov = self.overrides.get(book, {})
        if key in ov:
            return ov[key]
        return getattr(self, key, default)

    def kind(self, book: str) -> str | None:
        return self.strategies.get(book)

    @property
    def books(self) -> list[str]:
        return list(self.strategies)

    @property
    def live_book(self) -> str | None:
        return next((k for k, v in self.strategies.items() if v == "live"), None)


def load_config(path: str | Path | None = None) -> Config:
    path = Path(path or "config/default.toml")
    if not path.exists():
        return Config()
    raw = tomllib.loads(path.read_text())
    unknown = set(raw) - {f.name for f in fields(Config)}
    if unknown:
        raise ValueError(f"unknown config keys: {sorted(unknown)}")
    if "ai_symbols" in raw:
        raw["ai_symbols"] = tuple(s.upper() for s in raw["ai_symbols"])
    if "symbol_denylist" in raw:
        raw["symbol_denylist"] = tuple(s.upper() for s in raw["symbol_denylist"])
    return Config(**raw)
