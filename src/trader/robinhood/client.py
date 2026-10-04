"""Client for Robinhood's OFFICIAL Trading MCP server (https://agent.robinhood.com/mcp/trading).

No unofficial/undocumented endpoints and no username/password login: authorization is the standard MCP OAuth flow,
approved once by the account owner in the browser. The agentic account is the only one the server lets us trade.

Phase 0 is READ-ONLY: `ReadOnlySession` refuses every tool that is not a `get_*` call.
"""
from __future__ import annotations

import asyncio
import json
import os
import webbrowser
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

SERVER_URL = "https://agent.robinhood.com/mcp/trading"
DEFAULT_TOKEN_FILE = Path.home() / ".config" / "ai-trader" / "robinhood_oauth.json"


class WriteBlocked(RuntimeError):
    """Raised when code tries to call a tool that could change the account."""


def is_read_only(tool: str) -> bool:
    return tool.startswith("get_") and not tool.startswith(("get_and_", "get_then_"))


def mask(account_number: str | None) -> str:
    s = str(account_number or "")
    return "••••" + s[-4:] if len(s) >= 4 else "••••"


def parse_tool_result(result: Any) -> Any:
    """MCP tool results arrive as text content; our tools return JSON text."""
    if getattr(result, "isError", False):
        text = " ".join(getattr(c, "text", "") for c in getattr(result, "content", []))
        raise RuntimeError(f"tool error: {text[:300]}")
    sc = getattr(result, "structuredContent", None)
    if sc:
        return sc
    text = "".join(getattr(c, "text", "") for c in getattr(result, "content", []))
    try:
        return json.loads(text)
    except ValueError:
        return text


class ReadOnlySession:
    """Wraps an MCP ClientSession; only read tools can be called."""

    def __init__(self, session: Any):
        self._s = session

    async def tools(self) -> list[str]:
        return [t.name for t in (await self._s.list_tools()).tools]

    async def call(self, tool: str, arguments: dict | None = None) -> Any:
        if not is_read_only(tool):
            raise WriteBlocked(f"{tool!r} is not a read-only tool; blocked in read-only mode")
        return parse_tool_result(await self._s.call_tool(tool, arguments or {}))


def agentic_accounts(accounts_payload: Any) -> list[dict]:
    data = accounts_payload.get("data", accounts_payload) if isinstance(accounts_payload, dict) else {}
    accounts = data.get("accounts", []) if isinstance(data, dict) else []
    return [a for a in accounts if a.get("agentic_allowed") is True]


class FileTokenStorage:
    """OAuth tokens + client registration in one 0600 file. Never printed or logged."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or DEFAULT_TOKEN_FILE)

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.replace(tmp, self.path)
        os.chmod(self.path, 0o600)

    async def get_tokens(self):
        from mcp.shared.auth import OAuthToken
        d = self._load().get("tokens")
        return OAuthToken(**d) if d else None

    async def set_tokens(self, tokens) -> None:
        d = self._load(); d["tokens"] = tokens.model_dump(mode="json", exclude_none=True); self._save(d)

    async def get_client_info(self):
        from mcp.shared.auth import OAuthClientInformationFull
        d = self._load().get("client_info")
        return OAuthClientInformationFull(**d) if d else None

    async def set_client_info(self, info) -> None:
        d = self._load(); d["client_info"] = info.model_dump(mode="json", exclude_none=True); self._save(d)


def _wait_for_callback(port: int, timeout: float) -> tuple[str, str | None]:
    result: dict = {}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            q = parse_qs(urlparse(self.path).query)
            result["code"] = (q.get("code") or [None])[0]
            result["state"] = (q.get("state") or [None])[0]
            result["error"] = (q.get("error") or [None])[0]
            self.send_response(200); self.send_header("Content-Type", "text/plain; charset=utf-8"); self.end_headers()
            self.wfile.write(b"AI-Trader: authorization received. You can close this tab.")

    srv = HTTPServer(("127.0.0.1", port), H)
    srv.timeout = timeout
    try:
        srv.handle_request()
    finally:
        srv.server_close()
    if result.get("error") or not result.get("code"):
        raise RuntimeError(f"authorization failed: {result.get('error') or 'no code received (timed out?)'}")
    return result["code"], result["state"]


@asynccontextmanager
async def connect_read_only(url: str = SERVER_URL, port: int = 8765, token_file: str | Path | None = None):
    """Open an authorized, READ-ONLY session. First run opens a browser for one-time approval."""
    from mcp import ClientSession
    from mcp.client.auth import OAuthClientProvider
    from mcp.client.streamable_http import streamablehttp_client
    from mcp.shared.auth import OAuthClientMetadata

    meta = OAuthClientMetadata(
        client_name="AI-Trader (read-only check)",
        redirect_uris=[f"http://localhost:{port}/callback"],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",
    )

    async def redirect_handler(auth_url: str) -> None:
        print("\nOpen this URL to approve access (a browser tab should open automatically):\n  " + auth_url + "\n")
        try:
            webbrowser.open(auth_url)
        except Exception:
            pass

    async def callback_handler() -> tuple[str, str | None]:
        return await asyncio.get_running_loop().run_in_executor(None, _wait_for_callback, port, 300.0)

    provider = OAuthClientProvider(server_url=url, client_metadata=meta, storage=FileTokenStorage(token_file),
                                   redirect_handler=redirect_handler, callback_handler=callback_handler)
    async with streamablehttp_client(url, auth=provider) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield ReadOnlySession(session)
