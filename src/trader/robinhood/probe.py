"""Phase 1a: read-only probe. Calls a fixed set of READ tools and shows the *shape* of each reply (field names and
types, no balances or amounts) so the data step can be built against real responses. Full raw replies are saved
locally to data/probe/ (gitignored) and are never sent anywhere."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..config import Config
from .client import SERVER_URL, agentic_accounts, connect_read_only

SAFE_STR_KEYS = {"state", "side", "type", "order_type", "time_in_force", "status", "market_hours", "timing", "bounds",
                 "interval", "span", "trigger", "currency", "indicator", "adjustment_type", "title"}


def outline(obj: Any, key: str = "", depth: int = 0, max_depth: int = 5, lines: list[str] | None = None) -> list[str]:
    lines = [] if lines is None else lines
    pad = "  " * depth
    if isinstance(obj, dict):
        lines.append(f"{pad}{key}: object ({len(obj)} keys)" if key else f"{pad}object ({len(obj)} keys)")
        if depth < max_depth:
            for k, v in list(obj.items())[:40]:
                outline(v, str(k), depth + 1, max_depth, lines)
    elif isinstance(obj, list):
        lines.append(f"{pad}{key}: list[{len(obj)}]")
        if obj and depth < max_depth:
            outline(obj[0], "[0]", depth + 1, max_depth, lines)
    elif isinstance(obj, bool):
        lines.append(f"{pad}{key}: bool")
    elif isinstance(obj, (int, float)):
        lines.append(f"{pad}{key}: number")
    elif obj is None:
        lines.append(f"{pad}{key}: null")
    else:
        sample = f' e.g. "{obj[:24]}"' if (key in SAFE_STR_KEYS and len(obj) <= 24) else ""
        lines.append(f"{pad}{key}: string{sample}")
    return lines


async def run_probe(cfg: Config, url: str | None, port: int, token_file: str | None, out_dir: str = "data/probe",
                    scan_id: str = "ca8f132f-c07b-473f-9456-31e09b1e0d46") -> int:
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")
    start_400, start_60 = iso(now - timedelta(days=400)), iso(now - timedelta(days=60))
    failures = 0

    async with connect_read_only(url or SERVER_URL, port, token_file, extra_allowed={"run_scan"}) as rh:
        async def step(name: str, tool: str, args: dict) -> Any:
            nonlocal failures
            print(f"\n=== {name}  ({tool}) ===")
            try:
                res = await rh.call(tool, args)
            except Exception as e:  # keep going; report
                failures += 1
                print(f"ERROR {type(e).__name__}: {str(e)[:240]}")
                return None
            (out / f"{name}.json").write_text(json.dumps(res, indent=1, default=str))
            print("\n".join(outline(res)))
            return res

        accts = await step("accounts", "get_accounts", {})
        ag = agentic_accounts(accts) if accts else []
        if len(ag) != 1:
            print("\nNo single agentic account found; stopping."); return 1
        acct = ag[0]["account_number"]
        await step("portfolio", "get_portfolio", {"account_number": acct})
        await step("positions", "get_equity_positions", {"account_number": acct})
        await step("orders", "get_equity_orders", {"account_number": acct})
        await step("quotes", "get_equity_quotes", {"symbols": ["SPY", "QQQ", "NVDA"]})
        for name, args in {
            "ind_sma50": {"type": "sma", "period": 50}, "ind_sma200": {"type": "sma", "period": 200},
            "ind_ema21": {"type": "ema", "period": 21}, "ind_atr14": {"type": "atr", "period": 14},
            "ind_macd": {"type": "macd"}, "ind_rsi14": {"type": "rsi", "period": 14},
        }.items():
            await step(name, "get_equity_technical_indicators",
                       {"symbol": "SPY", "interval": "day", "start_time": start_400, "output": "latest", **args})
        await step("historicals", "get_equity_historicals", {"symbols": ["NVDA"], "start_time": start_60, "interval": "day"})
        await step("fundamentals", "get_equity_fundamentals", {"symbols": ["NVDA", "VRT"]})
        await step("earnings", "get_earnings_calendar", {"days": 14, "filter": "high_market_cap"})
        scans = await step("scans", "get_scans", {})
        if scans is not None and scan_id in json.dumps(scans):
            await step("scan_run", "run_scan", {"scan_id": scan_id})
        elif scans is not None:
            print(f"\n(scan {scan_id} not found in get_scans; skipping run_scan)")

    print(f"\nFull raw replies saved in {out.resolve()} (private, not in git). Steps with errors: {failures}")
    print("No orders were placed and no write tool was called.")
    return 0 if failures == 0 else 1
