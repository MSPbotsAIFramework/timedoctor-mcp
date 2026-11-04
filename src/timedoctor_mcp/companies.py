"""Company-scoping helper (AC8).

Most Time Doctor accounts manage a single company. ``resolve_company_id``
lets every tool take an optional ``company_id`` and fall back to the sole
company visible to the caller's token -- or fail loudly, listing the
candidates, rather than silently guessing one (which would be a correctness
bug for multi-company accounts).

Resolution is never cached: it runs fresh on every call, scoped to that
call's own ``TimeDoctorClient`` (SOP SS3.3/SS3.4 -- no cross-request state).
"""

from __future__ import annotations

from typing import Any

from .client import TimeDoctorClient
from .errors import InvalidArgument


async def resolve_company_id(client: TimeDoctorClient, explicit_company_id: str | None) -> str:
    if explicit_company_id:
        return explicit_company_id

    payload = await client.get("/companies")
    companies: list[dict[str, Any]] = _extract_companies(payload)

    if len(companies) == 1:
        return str(companies[0]["id"])

    candidates = ", ".join(f"{c.get('id')} ({c.get('name')})" for c in companies) or "none"
    if not companies:
        raise InvalidArgument(
            "company_id was not given and the token has access to no companies"
        )
    raise InvalidArgument(
        "company_id was not given and the token has access to more than one company; "
        f"pass one of: {candidates}"
    )


def _extract_companies(payload: Any) -> list[dict[str, Any]]:
    """Time Doctor list endpoints commonly wrap the array in a ``data`` key."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        data = payload.get("data", payload.get("companies"))
        if isinstance(data, list):
            return data
    return []
