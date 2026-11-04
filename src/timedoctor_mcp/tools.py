"""The six Time Doctor tools (companies, users, projects, worklogs, activity stats).

Parameter naming note: the tech design's "Data & API contract" table names the
worklog/activity-stats date-range parameters ``from``/``to``. ``from`` is a
Python reserved keyword, and the installed MCP SDK (``mcp.server.fastmcp``)
dispatches validated tool arguments to the handler using the pydantic field's
*alias* as the Python keyword argument name (``ArgModelBase.model_dump_one_level``),
so a schema alias of ``"from"`` still requires a literally-named ``from``
parameter on the function -- which Python cannot express. Verified
experimentally (``mcp.call_tool`` raises ``TypeError: unexpected keyword
argument 'from'`` for an aliased ``from_`` parameter). The tools below use
``from_date``/``to_date`` instead; this is a wire-contract naming detail, not
a change to any AC or user-visible behavior (SOP SS13: this is not a tool
rename/removal, it is the first version's own contract).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .client import TimeDoctorClient
from .companies import resolve_company_id
from .errors import (
    InvalidArgument,
    UpstreamError,
    envelope,
    envelope_for_invalid_argument,
    envelope_for_upstream_error,
)

ClientFactory = Callable[[], "TimeDoctorClient | None"]

DEFAULT_LIMIT = 50
MAX_LIMIT = 200

_READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True)


def _not_configured() -> str:
    return envelope(
        "not_configured",
        "No Time Doctor credential is available for this request",
        retryable=False,
    )


def _cap_limit(limit: int) -> int:
    """SOP SS2.5: default <= 50, hard cap <= 200, never unbounded."""
    if limit is None or limit <= 0:
        return DEFAULT_LIMIT
    return min(limit, MAX_LIMIT)


def _default_date_range(from_date: str | None, to_date: str | None) -> tuple[str, str]:
    """Trailing 7 days (today-6..today, UTC) when omitted (design decision 5)."""
    if from_date and to_date:
        return from_date, to_date
    today = datetime.now(timezone.utc).date()
    resolved_to = to_date or today.isoformat()
    resolved_from = from_date or (today - timedelta(days=6)).isoformat()
    return resolved_from, resolved_to


def _extract_items(payload: Any, *keys: str) -> list[dict[str, Any]]:
    """Time Doctor list endpoints commonly wrap the array in a ``data`` key.

    Tolerant of either shape, since the exact envelope is inferred from docs,
    not a captured real response (see design "Risks & dependencies").
    """
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _has_more(items: list[Any], limit: int, payload: Any) -> bool:
    if isinstance(payload, dict):
        total = (
            payload.get("total")
            or payload.get("total_count")
            or payload.get("totalCount")
        )
        if isinstance(total, int):
            return len(items) < total
    return len(items) == limit and limit > 0


def _field(d: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in d and d[key] is not None:
            return d[key]
    return default


def _trim_company(c: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _field(c, "id", "company_id", "_id"),
        "name": _field(c, "name", "company_name"),
    }


def _trim_user(u: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _field(u, "id", "user_id", "_id"),
        "name": _field(u, "name", "full_name", "fullName"),
        "role": _field(u, "role", "userRole", "role_name"),
    }


def _trim_project(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _field(p, "id", "project_id", "_id"),
        "name": _field(p, "name", "project_name"),
        "status": _field(p, "status", "project_status"),
    }


def _trim_worklog(w: dict[str, Any]) -> dict[str, Any]:
    return {
        "user_id": _field(w, "user_id", "userId"),
        "task_id": _field(w, "task_id", "taskId"),
        "project_id": _field(w, "project_id", "projectId"),
        "start": _field(w, "start", "start_time", "startTime"),
        "end": _field(w, "end", "end_time", "endTime"),
        "duration_s": _field(w, "duration_s", "duration", "length"),
    }


def _dump(obj: Any) -> str:
    """SOP SS2.5: compact separators, no indent, ensure_ascii=False."""
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def register(mcp: FastMCP, client_factory: ClientFactory) -> None:
    """Register all six tools. ``client_factory()`` returns ``None`` when no
    credential is on the request context (SOP SS9's documented contract)."""

    @mcp.tool(
        name="timedoctor_list_companies",
        description="List the Time Doctor companies/workspaces visible to the caller's credential.",
        annotations=_READ_ONLY,
    )
    async def timedoctor_list_companies() -> str:
        client = client_factory()
        if client is None:
            return _not_configured()
        try:
            async with client as c:
                payload = await c.get("/companies")
        except UpstreamError as exc:
            return envelope_for_upstream_error(exc)
        companies = [_trim_company(co) for co in _extract_items(payload, "data", "companies")]
        return _dump(companies)

    @mcp.tool(
        name="timedoctor_list_users",
        description=(
            "List the users (team roster) in a Time Doctor company: id, name, role.\n"
            "Omit company_id when the account has exactly one company -- it resolves "
            "automatically, or returns an error listing the candidates."
        ),
        annotations=_READ_ONLY,
    )
    async def timedoctor_list_users(
        company_id: str | None = None,
        filter: str | None = None,
        page: int = 1,
        limit: int = DEFAULT_LIMIT,
    ) -> str:
        client = client_factory()
        if client is None:
            return _not_configured()
        limit = _cap_limit(limit)
        try:
            async with client as c:
                company = await resolve_company_id(c, company_id)
                payload = await c.get(
                    "/users",
                    {"company": company, "page": page, "limit": limit, "filter": filter},
                )
        except InvalidArgument as exc:
            return envelope_for_invalid_argument(exc)
        except UpstreamError as exc:
            return envelope_for_upstream_error(exc)
        items = [_trim_user(u) for u in _extract_items(payload, "data", "users")]
        return _dump({"items": items, "page": page, "limit": limit, "has_more": _has_more(items, limit, payload)})

    @mcp.tool(
        name="timedoctor_list_projects",
        description=(
            "List the projects in a Time Doctor company: id, name, status.\n"
            "Omit company_id when the account has exactly one company."
        ),
        annotations=_READ_ONLY,
    )
    async def timedoctor_list_projects(
        company_id: str | None = None,
        page: int = 1,
        limit: int = DEFAULT_LIMIT,
    ) -> str:
        client = client_factory()
        if client is None:
            return _not_configured()
        limit = _cap_limit(limit)
        try:
            async with client as c:
                company = await resolve_company_id(c, company_id)
                payload = await c.get("/projects", {"company": company, "page": page, "limit": limit})
        except InvalidArgument as exc:
            return envelope_for_invalid_argument(exc)
        except UpstreamError as exc:
            return envelope_for_upstream_error(exc)
        items = [_trim_project(p) for p in _extract_items(payload, "data", "projects")]
        return _dump({"items": items, "page": page, "limit": limit, "has_more": _has_more(items, limit, payload)})

    @mcp.tool(
        name="timedoctor_list_worklogs",
        description=(
            "List raw time-tracking entries (worklogs) for a date range, optionally for one user.\n"
            "from_date/to_date default to the trailing 7 days (UTC) when omitted. Omit company_id "
            "when the account has exactly one company."
        ),
        annotations=_READ_ONLY,
    )
    async def timedoctor_list_worklogs(
        company_id: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        user_id: str | None = None,
        page: int = 1,
        limit: int = DEFAULT_LIMIT,
    ) -> str:
        client = client_factory()
        if client is None:
            return _not_configured()
        limit = _cap_limit(limit)
        resolved_from, resolved_to = _default_date_range(from_date, to_date)
        try:
            async with client as c:
                company = await resolve_company_id(c, company_id)
                payload = await c.get(
                    "/activity/worklog",
                    {
                        "company": company,
                        "from": resolved_from,
                        "to": resolved_to,
                        "user": user_id,
                        "page": page,
                        "limit": limit,
                    },
                )
        except InvalidArgument as exc:
            return envelope_for_invalid_argument(exc)
        except UpstreamError as exc:
            return envelope_for_upstream_error(exc)
        all_items = [_trim_worklog(w) for w in _extract_items(payload, "data", "worklogs")]
        if user_id:
            all_items = [w for w in all_items if str(w.get("user_id")) == str(user_id)]
        return _dump(
            {"items": all_items, "page": page, "limit": limit, "has_more": _has_more(all_items, limit, payload)}
        )

    @mcp.tool(
        name="timedoctor_get_activity_summary",
        description=(
            "Get aggregated productivity totals for a date range, optionally for one user.\n"
            "Totals are worked/manual/productive/unproductive seconds. from_date/to_date default "
            "to the trailing 7 days (UTC)."
        ),
        annotations=_READ_ONLY,
    )
    async def timedoctor_get_activity_summary(
        company_id: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        user_id: str | None = None,
    ) -> str:
        client = client_factory()
        if client is None:
            return _not_configured()
        resolved_from, resolved_to = _default_date_range(from_date, to_date)
        try:
            async with client as c:
                company = await resolve_company_id(c, company_id)
                payload = await c.get(
                    "/activity/stats/summary",
                    {"company": company, "from": resolved_from, "to": resolved_to, "user": user_id},
                )
        except InvalidArgument as exc:
            return envelope_for_invalid_argument(exc)
        except UpstreamError as exc:
            return envelope_for_upstream_error(exc)
        data = payload.get("data", payload) if isinstance(payload, dict) else {}
        result = {
            "from": resolved_from,
            "to": resolved_to,
            "worked_s": _field(data, "worked_s", "workedTime", "worked"),
            "manual_s": _field(data, "manual_s", "manualTime", "manual"),
            "productive_s": _field(data, "productive_s", "productiveTime", "productive"),
            "unproductive_s": _field(data, "unproductive_s", "unproductiveTime", "unproductive"),
        }
        return _dump(result)

    @mcp.tool(
        name="timedoctor_get_activity_by_category",
        description=(
            "Get time spent per application/website category for a date range, optionally for one user.\n"
            "from_date/to_date default to the trailing 7 days (UTC)."
        ),
        annotations=_READ_ONLY,
    )
    async def timedoctor_get_activity_by_category(
        company_id: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        user_id: str | None = None,
    ) -> str:
        client = client_factory()
        if client is None:
            return _not_configured()
        resolved_from, resolved_to = _default_date_range(from_date, to_date)
        try:
            async with client as c:
                company = await resolve_company_id(c, company_id)
                payload = await c.get(
                    "/activity/stats/category",
                    {"company": company, "from": resolved_from, "to": resolved_to, "user": user_id},
                )
        except InvalidArgument as exc:
            return envelope_for_invalid_argument(exc)
        except UpstreamError as exc:
            return envelope_for_upstream_error(exc)
        data = payload.get("data", payload) if isinstance(payload, dict) else {}
        categories_raw = data.get("categories") if isinstance(data, dict) else None
        if not isinstance(categories_raw, list):
            categories_raw = []
        categories = [
            {"name": _field(cat, "name", "category"), "seconds": _field(cat, "seconds", "duration", "time")}
            for cat in categories_raw
        ]
        return _dump({"from": resolved_from, "to": resolved_to, "categories": categories})
