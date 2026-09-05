"""Device-code client that gets an Entra token *for the MCP app*, then calls tools.

This is the teaching counterpart to AUTH_MODE=bearer:

    user --device code--> Entra  (scope = api://<mcp-app>/access_as_user)
    client --Bearer MCP token--> FastMCP
    FastMCP --OBO--> Entra      (scope = https://graph.microsoft.com/...)
    FastMCP --Graph token--> Microsoft Graph
"""

from __future__ import annotations

import asyncio
import json
import sys

import msal
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from config import load_settings


def acquire_mcp_token(settings) -> str:
    app = msal.PublicClientApplication(
        client_id=settings.client_id,
        authority=settings.authority,
    )
    accounts = app.get_accounts()
    silent = app.acquire_token_silent([settings.mcp_scope_full], account=accounts[0]) if accounts else None
    if silent and "access_token" in silent:
        return silent["access_token"]

    flow = app.initiate_device_flow(scopes=[settings.mcp_scope_full])
    if "user_code" not in flow:
        raise RuntimeError(f"Device flow failed: {flow}")
    print(flow["message"], flush=True)
    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        raise RuntimeError(f"Could not acquire MCP-audience token: {result}")
    return result["access_token"]


async def main() -> None:
    settings = load_settings()
    token = acquire_mcp_token(settings)
    url = f"{settings.base_url}/mcp"
    transport = StreamableHttpTransport(
        url,
        headers={"Authorization": f"Bearer {token}"},
    )
    async with Client(transport) as client:
        tools = await client.list_tools()
        print("Tools:", [tool.name for tool in tools])
        for name in ("explain_obo", "whoami_mcp", "whoami_graph", "list_recent_mail"):
            print(f"\n=== {name} ===")
            try:
                result = await client.call_tool(name)
                payload = result.data if hasattr(result, "data") else result
                print(json.dumps(payload, indent=2, default=str))
            except Exception as exc:  # noqa: BLE001
                print(f"{name} failed: {exc}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
