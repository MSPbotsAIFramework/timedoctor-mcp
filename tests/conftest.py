"""Shared test fixtures.

Tests drive the real ASGI app (credential middleware + the MCP Streamable
HTTP app) end to end over HTTP via an in-process ASGI transport, with Time
Doctor's API mocked by ``respx`` -- this is the same request path a real
Gateway call takes, just without a socket (SOP SS12).
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx
import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager

from timedoctor_mcp.server import build_asgi_app
from timedoctor_mcp.settings import Settings

TIMEDOCTOR_BASE = "https://api2.timedoctor.com/api/1.0"
MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


@pytest_asyncio.fixture
async def app_client() -> AsyncIterator[httpx.AsyncClient]:
    app = build_asgi_app(Settings())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client


def auth_headers(token: str) -> dict[str, str]:
    return {**MCP_HEADERS, "X-Timedoctor-Token": token}


async def call_tool(
    client: httpx.AsyncClient, token: str, name: str, arguments: dict[str, Any] | None = None
) -> dict[str, Any]:
    """POST a stateless ``tools/call`` and return the parsed tool result payload."""
    response = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        },
        headers=auth_headers(token),
    )
    return _parse_sse_json_rpc(response)


async def list_tools(client: httpx.AsyncClient, token: str) -> dict[str, Any]:
    response = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers=auth_headers(token),
    )
    return _parse_sse_json_rpc(response)


def _parse_sse_json_rpc(response: httpx.Response) -> dict[str, Any]:
    """The Streamable HTTP transport replies with an SSE body wrapping one JSON-RPC message."""
    assert response.status_code == 200, response.text
    for line in response.text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[len("data:") :].strip())
    raise AssertionError(f"no SSE data line in response: {response.text!r}")


def tool_result_text(rpc_response: dict[str, Any]) -> str:
    """Pull the tool's string result out of a ``tools/call`` JSON-RPC response."""
    result = rpc_response["result"]
    assert result.get("isError") is not True, result
    return result["content"][0]["text"]
