"""Credential header handling and per-request isolation (SOP SS1.3, SS3, SS10).

Covers TC-TD-007 and TC-TD-009 from docs/tickets/PRD-21319.ac.json.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
import respx

from .conftest import TIMEDOCTOR_BASE, MCP_HEADERS, call_tool, tool_result_text


# ---------------------------------------------------------------------------
# TC-TD-007 (AC7)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_credential_header_returns_401_without_calling_upstream(
    app_client: httpx.AsyncClient,
) -> None:
    with respx.mock(assert_all_called=False) as mock:
        companies_route = mock.get(f"{TIMEDOCTOR_BASE}/companies").respond(json={"data": []})

        response = await app_client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "timedoctor_list_companies", "arguments": {}},
            },
            headers=MCP_HEADERS,  # no X-Timedoctor-Token
        )

        assert response.status_code == 401
        assert "X-Timedoctor-Token" in response.json()["error"]
        assert companies_route.call_count == 0


@pytest.mark.asyncio
async def test_missing_credential_header_rejects_tools_list_too(app_client: httpx.AsyncClient) -> None:
    """The gate is on the transport, not per-tool: tools/list also needs the header."""
    response = await app_client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers=MCP_HEADERS,
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_health_does_not_require_credential(app_client: httpx.AsyncClient) -> None:
    """`/health` must stay a pure local probe, independent of the credential (SOP SS1.1)."""
    response = await app_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# TC-TD-009 (AC9) -- per-request isolation under real concurrency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_requests_do_not_leak_credentials_across_tenants(
    app_client: httpx.AsyncClient,
) -> None:
    token_a, token_b = "tenant-a-token", "tenant-b-token"
    seen_auth_headers: list[str] = []

    async def slow_company_a(request: httpx.Request) -> httpx.Response:
        # Delay tenant A's upstream call so tenant B's request is guaranteed to
        # start, and run its own context-var read, while A is still in flight.
        seen_auth_headers.append(request.headers["authorization"])
        await asyncio.sleep(0.05)
        return httpx.Response(200, json={"data": [{"id": "company-a", "name": "Tenant A Co"}]})

    def fast_company_b(request: httpx.Request) -> httpx.Response:
        seen_auth_headers.append(request.headers["authorization"])
        return httpx.Response(200, json={"data": [{"id": "company-b", "name": "Tenant B Co"}]})

    with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{TIMEDOCTOR_BASE}/companies", headers={"authorization": f"JWT {token_a}"}).mock(
            side_effect=slow_company_a
        )
        mock.get(f"{TIMEDOCTOR_BASE}/companies", headers={"authorization": f"JWT {token_b}"}).mock(
            side_effect=fast_company_b
        )

        result_a, result_b = await asyncio.gather(
            call_tool(app_client, token_a, "timedoctor_list_companies"),
            call_tool(app_client, token_b, "timedoctor_list_companies"),
        )

    companies_a = json.loads(tool_result_text(result_a))
    companies_b = json.loads(tool_result_text(result_b))

    assert companies_a == [{"id": "company-a", "name": "Tenant A Co"}]
    assert companies_b == [{"id": "company-b", "name": "Tenant B Co"}]

    # Each upstream call carried exactly its own tenant's token -- never the other's.
    assert seen_auth_headers.count(f"JWT {token_a}") == 1
    assert seen_auth_headers.count(f"JWT {token_b}") == 1


@pytest.mark.asyncio
async def test_concurrent_requests_resolve_their_own_company_not_the_others(
    app_client: httpx.AsyncClient,
) -> None:
    """Same isolation property, exercised through the company-resolution path (AC8+AC9
    together): tenant A has two companies (ambiguous -> error), tenant B has one
    (auto-resolves) -- running concurrently must not mix up which is which."""
    token_a, token_b = "multi-co-token", "single-co-token"

    async def companies_for_a(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.05)
        return httpx.Response(
            200, json={"data": [{"id": "a1", "name": "A One"}, {"id": "a2", "name": "A Two"}]}
        )

    def companies_for_b(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "b1", "name": "B Co"}]})

    with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{TIMEDOCTOR_BASE}/companies", headers={"authorization": f"JWT {token_a}"}).mock(
            side_effect=companies_for_a
        )
        mock.get(f"{TIMEDOCTOR_BASE}/companies", headers={"authorization": f"JWT {token_b}"}).mock(
            side_effect=companies_for_b
        )
        mock.get(f"{TIMEDOCTOR_BASE}/projects", headers={"authorization": f"JWT {token_b}"}).respond(
            json={"data": [{"id": "p1", "name": "B Project", "status": "active"}]}
        )

        result_a, result_b = await asyncio.gather(
            call_tool(app_client, token_a, "timedoctor_list_projects"),
            call_tool(app_client, token_b, "timedoctor_list_projects"),
        )

    error_a = json.loads(tool_result_text(result_a))["error"]
    assert error_a["code"] == "invalid_argument"
    assert "a1" in error_a["message"] and "a2" in error_a["message"]

    payload_b = json.loads(tool_result_text(result_b))
    assert payload_b["items"] == [{"id": "p1", "name": "B Project", "status": "active"}]
