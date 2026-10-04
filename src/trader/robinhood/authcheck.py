"""Phase 0: prove a script can sign in to Robinhood's official MCP server and read the Agentic account. No orders."""
from __future__ import annotations

from ..config import Config
from .client import SERVER_URL, WriteBlocked, agentic_accounts, connect_read_only, is_read_only, mask


def leaf_errors(e: BaseException) -> list[BaseException]:
    """Flatten ExceptionGroups (anyio task groups) so the real cause is visible."""
    if isinstance(e, BaseExceptionGroup):
        return [x for sub in e.exceptions for x in leaf_errors(sub)]
    return [e]


async def run_auth_check(cfg: Config, url: str | None = None, port: int = 8765, token_file: str | None = None) -> int:
    ok = True

    def line(status: str, msg: str) -> None:
        print(f"[{status}] {msg}")

    try:
        async with connect_read_only(url or SERVER_URL, port, token_file) as rh:
            line("PASS", "signed in to the official Robinhood Trading MCP server")
            tools = await rh.tools()
            reads = [t for t in tools if is_read_only(t)]
            writes = [t for t in tools if not is_read_only(t)]
            line("INFO", f"{len(tools)} tools: {len(reads)} read-only, {len(writes)} others (this check cannot call them)")
            try:  # prove the guard works on the real tool list
                if writes:
                    await rh.call(writes[0])
                line("FAIL", "read-only guard did not block a non-read tool"); ok = False
            except WriteBlocked:
                line("PASS", f"read-only guard blocked {writes[0] if writes else 'n/a'}")

            accounts = await rh.call("get_accounts")
            agentic = agentic_accounts(accounts)
            if len(agentic) != 1:
                line("FAIL", f"expected exactly 1 agentic account, found {len(agentic)}"); ok = False
            else:
                a = agentic[0]
                line("PASS", f"agentic account {mask(a.get('account_number'))} nickname={a.get('nickname')!r} "
                             f"type={a.get('type')}")
                want = cfg.allowed_account_id
                if not want:
                    line("WARN", "allowed_account_id is empty in config")
                elif str(a.get("account_number")) == str(want):
                    line("PASS", "matches allowed_account_id in config (account lock OK)")
                else:
                    line("FAIL", f"agentic account differs from config allowed_account_id ({mask(want)})"); ok = False
                pf = await rh.call("get_portfolio", {"account_number": a.get("account_number")})
                d = pf.get("data", pf) if isinstance(pf, dict) else {}
                bp = (d.get("buying_power") or {}).get("buying_power") if isinstance(d, dict) else None
                line("PASS", f"portfolio read: total_value={d.get('total_value')} cash={d.get('cash')} buying_power={bp}")
    except BaseException as e:  # report, never trade
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        for x in leaf_errors(e):
            line("FAIL", f"{type(x).__name__}: {str(x)[:300]}")
        ok = False
    print("\nRESULT:", "ALL CHECKS PASSED. No orders were placed and no write tool was called." if ok else
          "NOT READY. Nothing was traded. Send me the lines above.")
    return 0 if ok else 1
