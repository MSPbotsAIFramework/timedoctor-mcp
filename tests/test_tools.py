"""tools/list snapshot, tool happy paths, and error-code mapping (SOP SS10, SS14).

Covers TC-TD-001..006, TC-TD-008, TC-TD-010 from docs/tickets/PRD-21319.ac.json.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from timedoctor_mcp import client as client_module
from .conftest import TIMEDOCTOR_BASE, call_tool, list_tools, tool_result_text

TOKEN = "test-jwt-token"


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the retry/backoff tests fast and deterministic."""
    monkeypatch.setattr(client_module, "DEFAULT_BACKOFF_S", 0)


# ---------------------------------------------------------------------------
# tools/list snapshot (SOP SS10, SS13: a changed snapshot flags a contract change)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tools_list_snapshot(app_client: httpx.AsyncClient) -> None:
    rpc = await list_tools(app_client, TOKEN)
    tools = {t["name"]: t for t in rpc["result"]["tools"]}

    assert set(tools) == {
        "timedoctor_list_companies",
        "timedoctor_list_users",
        "timedoctor_list_projects",
        "timedoctor_list_worklogs",
        "timedoctor_get_activity_summary",
        "timedoctor_get_activity_by_category",
    }

    assert set(tools["timedoctor_list_companies"]["inputSchema"]["properties"]) == set()
    assert set(tools["timedoctor_list_users"]["inputSchema"]["properties"]) == {
        "company_id",
        "filter",
        "page",
        "limit",
    }
    assert set(tools["timedoctor_list_projects"]["inputSchema"]["properties"]) == {
        "company_id",
        "page",
        "limit",
    }
    assert set(tools["timedoctor_list_worklogs"]["inputSchema"]["properties"]) == {
        "company_id",
        "from_date",
        "to_date",
        "user_id",
        "page",
        "limit",
    }
    for name in ("timedoctor_get_activity_summary", "timedoctor_get_activity_by_category"):
        assert set(tools[name]["inputSchema"]["properties"]) == {
            "company_id",
            "from_date",
            "to_date",
            "user_id",
        }

    for tool in tools.values():
        annotations = tool["annotations"]
        assert annotations["readOnlyHint"] is True
        assert annotations["idempotentHint"] is True
        assert len(tool["description"]) <= 500
        assert len(tool["description"].splitlines()[0]) <= 100


# ---------------------------------------------------------------------------
# TC-TD-001
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_companies_returns_company_list(app_client: httpx.AsyncClient) -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{TIMEDOCTOR_BASE}/companies").respond(
            json={"data": [{"id": "c1", "name": "Acme"}, {"id": "c2", "name": "Acme EU"}]}
        )
        rpc = await call_tool(app_client, TOKEN, "timedoctor_list_companies")

    companies = json.loads(tool_result_text(rpc))
    assert companies == [{"id": "c1", "name": "Acme"}, {"id": "c2", "name": "Acme EU"}]


# ---------------------------------------------------------------------------
# TC-TD-002
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_users_returns_paginated_roster(app_client: httpx.AsyncClient) -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{TIMEDOCTOR_BASE}/companies").respond(json={"data": [{"id": "c1", "name": "Acme"}]})
        mock.get(f"{TIMEDOCTOR_BASE}/users").respond(
            json={"data": [{"id": "u1", "name": "Alice", "role": "member"}]}
        )
        rpc = await call_tool(app_client, TOKEN, "timedoctor_list_users", {"page": 1, "limit": 10})

    payload = json.loads(tool_result_text(rpc))
    assert payload["items"] == [{"id": "u1", "name": "Alice", "role": "member"}]
    assert payload["page"] == 1
    assert payload["limit"] == 10
    assert payload["has_more"] is False


@pytest.mark.asyncio
async def test_list_users_caps_limit_at_200(app_client: httpx.AsyncClient) -> None:
    captured: dict[str, str] = {}

    def record_and_respond(request: httpx.Request) -> httpx.Response:
        captured.update(request.url.params)
        return httpx.Response(200, json={"data": []})

    with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{TIMEDOCTOR_BASE}/companies").respond(json={"data": [{"id": "c1", "name": "Acme"}]})
        mock.get(f"{TIMEDOCTOR_BASE}/users").mock(side_effect=record_and_respond)
        rpc = await call_tool(app_client, TOKEN, "timedoctor_list_users", {"limit": 5000})

    payload = json.loads(tool_result_text(rpc))
    assert payload["limit"] == 200
    assert captured["limit"] == "200"


# ---------------------------------------------------------------------------
# TC-TD-003
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_projects_returns_project_list(app_client: httpx.AsyncClient) -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{TIMEDOCTOR_BASE}/companies").respond(json={"data": [{"id": "c1", "name": "Acme"}]})
        mock.get(f"{TIMEDOCTOR_BASE}/projects").respond(
            json={"data": [{"id": "p1", "name": "Migration", "status": "active"}]}
        )
        rpc = await call_tool(app_client, TOKEN, "timedoctor_list_projects")

    payload = json.loads(tool_result_text(rpc))
    assert payload["items"] == [{"id": "p1", "name": "Migration", "status": "active"}]


# ---------------------------------------------------------------------------
# TC-TD-004
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_worklogs_filters_by_user_and_date_range(app_client: httpx.AsyncClient) -> None:
    captured: dict[str, str] = {}

    def record_and_respond(request: httpx.Request) -> httpx.Response:
        captured.update(request.url.params)
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "user_id": "u1",
                        "task_id": "t1",
                        "project_id": "p1",
                        "start": "2026-01-01T09:00:00Z",
                        "end": "2026-01-01T09:30:00Z",
                        "duration_s": 1800,
                    },
                    {
                        "user_id": "u2",
                        "task_id": "t2",
                        "project_id": "p1",
                        "start": "2026-01-01T10:00:00Z",
                        "end": "2026-01-01T10:30:00Z",
                        "duration_s": 1800,
                    },
                ]
            },
        )

    with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{TIMEDOCTOR_BASE}/companies").respond(json={"data": [{"id": "c1", "name": "Acme"}]})
        mock.get(f"{TIMEDOCTOR_BASE}/activity/worklog").mock(side_effect=record_and_respond)
        rpc = await call_tool(
            app_client,
            TOKEN,
            "timedoctor_list_worklogs",
            {"from_date": "2026-01-01", "to_date": "2026-01-02", "user_id": "u1"},
        )

    payload = json.loads(tool_result_text(rpc))
    assert captured["from"] == "2026-01-01"
    assert captured["to"] == "2026-01-02"
    assert captured["user"] == "u1"
    assert [item["user_id"] for item in payload["items"]] == ["u1"]


@pytest.mark.asyncio
async def test_list_worklogs_has_more_counts_raw_page_not_filtered_items(
    app_client: httpx.AsyncClient,
) -> None:
    """Review round 1, finding 2.

    A full-size upstream page (== limit) that the client-side user_id filter
    then shrinks must still report has_more: true -- the next upstream page
    can hold more of that user's worklogs. Computing has_more from the
    post-filter list silently truncates pagination (undermines AC4).
    """
    with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{TIMEDOCTOR_BASE}/companies").respond(json={"data": [{"id": "c1", "name": "Acme"}]})
        # No "total"/"total_count" in the payload, so _has_more falls back to
        # len(items) == limit -- the raw page is exactly `limit` items, but
        # only one of them belongs to the requested user.
        mock.get(f"{TIMEDOCTOR_BASE}/activity/worklog").respond(
            json={
                "data": [
                    {
                        "user_id": "u1",
                        "task_id": "t1",
                        "project_id": "p1",
                        "start": "2026-01-01T09:00:00Z",
                        "end": "2026-01-01T09:30:00Z",
                        "duration_s": 1800,
                    },
                    {
                        "user_id": "u2",
                        "task_id": "t2",
                        "project_id": "p1",
                        "start": "2026-01-01T10:00:00Z",
                        "end": "2026-01-01T10:30:00Z",
                        "duration_s": 1800,
                    },
                ]
            }
        )
        rpc = await call_tool(
            app_client,
            TOKEN,
            "timedoctor_list_worklogs",
            {"from_date": "2026-01-01", "to_date": "2026-01-02", "user_id": "u1", "limit": 2},
        )

    payload = json.loads(tool_result_text(rpc))
    assert [item["user_id"] for item in payload["items"]] == ["u1"]
    assert payload["has_more"] is True


# ---------------------------------------------------------------------------
# TC-TD-005
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_activity_summary_returns_aggregated_totals(app_client: httpx.AsyncClient) -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{TIMEDOCTOR_BASE}/companies").respond(json={"data": [{"id": "c1", "name": "Acme"}]})
        mock.get(f"{TIMEDOCTOR_BASE}/activity/stats/summary").respond(
            json={"data": {"worked_s": 28800, "manual_s": 300, "productive_s": 25000, "unproductive_s": 3500}}
        )
        rpc = await call_tool(
            app_client,
            TOKEN,
            "timedoctor_get_activity_summary",
            {"from_date": "2026-01-01", "to_date": "2026-01-07"},
        )

    payload = json.loads(tool_result_text(rpc))
    assert payload == {
        "from": "2026-01-01",
        "to": "2026-01-07",
        "worked_s": 28800,
        "manual_s": 300,
        "productive_s": 25000,
        "unproductive_s": 3500,
    }


# ---------------------------------------------------------------------------
# TC-TD-006
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_activity_by_category_returns_breakdown(app_client: httpx.AsyncClient) -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{TIMEDOCTOR_BASE}/companies").respond(json={"data": [{"id": "c1", "name": "Acme"}]})
        mock.get(f"{TIMEDOCTOR_BASE}/activity/stats/category").respond(
            json={"data": {"categories": [{"name": "Development", "seconds": 18000}, {"name": "Email", "seconds": 3600}]}}
        )
        rpc = await call_tool(
            app_client,
            TOKEN,
            "timedoctor_get_activity_by_category",
            {"from_date": "2026-01-01", "to_date": "2026-01-07"},
        )

    payload = json.loads(tool_result_text(rpc))
    assert payload["categories"] == [
        {"name": "Development", "seconds": 18000},
        {"name": "Email", "seconds": 3600},
    ]


# ---------------------------------------------------------------------------
# TC-TD-008 (AC8)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ambiguous_company_without_company_id_returns_invalid_argument(
    app_client: httpx.AsyncClient,
) -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{TIMEDOCTOR_BASE}/companies").respond(
            json={"data": [{"id": "c1", "name": "Acme"}, {"id": "c2", "name": "Acme EU"}]}
        )
        rpc = await call_tool(app_client, TOKEN, "timedoctor_list_users")

    error = json.loads(tool_result_text(rpc))["error"]
    assert error["code"] == "invalid_argument"
    assert error["retryable"] is False
    assert "c1" in error["message"] and "c2" in error["message"]


@pytest.mark.asyncio
async def test_single_company_auto_resolves_without_company_id(app_client: httpx.AsyncClient) -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{TIMEDOCTOR_BASE}/companies").respond(json={"data": [{"id": "c1", "name": "Acme"}]})
        mock.get(f"{TIMEDOCTOR_BASE}/projects").respond(json={"data": []})
        rpc = await call_tool(app_client, TOKEN, "timedoctor_list_projects")

    payload = json.loads(tool_result_text(rpc))
    assert payload["items"] == []


# ---------------------------------------------------------------------------
# TC-TD-010 (AC10)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upstream_429_maps_to_rate_limited(app_client: httpx.AsyncClient) -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{TIMEDOCTOR_BASE}/companies").respond(
            429, headers={"Retry-After": "0"}, json={"message": "rate limited"}
        )
        rpc = await call_tool(app_client, TOKEN, "timedoctor_list_companies")

    error = json.loads(tool_result_text(rpc))["error"]
    assert error["code"] == "rate_limited"
    assert error["retryable"] is True


@pytest.mark.asyncio
async def test_upstream_5xx_maps_to_upstream_error(app_client: httpx.AsyncClient) -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{TIMEDOCTOR_BASE}/companies").respond(503, json={"message": "down for maintenance"})
        rpc = await call_tool(app_client, TOKEN, "timedoctor_list_companies")

    error = json.loads(tool_result_text(rpc))["error"]
    assert error["code"] == "upstream_error"
    assert error["retryable"] is True


@pytest.mark.asyncio
async def test_upstream_401_maps_to_unauthorized_not_configured(app_client: httpx.AsyncClient) -> None:
    """A bad/expired token: Time Doctor itself returns 401, mapped to `unauthorized`
    (not the middleware's `not_configured`, which never fires once a header is present)."""
    with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{TIMEDOCTOR_BASE}/companies").respond(401, json={"message": "invalid token"})
        rpc = await call_tool(app_client, TOKEN, "timedoctor_list_companies")

    error = json.loads(tool_result_text(rpc))["error"]
    assert error["code"] == "unauthorized"
    assert error["retryable"] is False
