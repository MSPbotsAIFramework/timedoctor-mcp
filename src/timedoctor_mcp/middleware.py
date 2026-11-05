"""Per-request credential handling (SOP SS3).

One container serves many tenants concurrently. The Time Doctor JWT lives
only in a ``contextvars.ContextVar`` for the lifetime of a single request and
is reset in a ``finally`` block -- never a module-level variable or
singleton, which would leak one tenant's token into another tenant's
concurrent request (SOP SS3.3/SS3.4).
"""

from __future__ import annotations

import json
from contextvars import ContextVar
from typing import Awaitable, Callable

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

CREDENTIAL_HEADER = "X-Timedoctor-Token"

_token: ContextVar[str | None] = ContextVar("timedoctor_token", default=None)


def current_token() -> str | None:
    """The current request's Time Doctor JWT, or ``None`` outside a request."""
    return _token.get()


def _missing_header_response() -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"error": f"{CREDENTIAL_HEADER} header is required"},
    )


class CredentialMiddleware:
    """Starlette ASGI middleware: read the credential header, scope it per request.

    Only applies to the ``/mcp`` path -- ``/health`` must stay a pure local
    probe independent of any credential (SOP SS1.1).
    """

    def __init__(self, app: ASGIApp, *, protected_path: str = "/mcp") -> None:
        self.app = app
        self.protected_path = protected_path

    async def __call__(self, scope, receive, send) -> None:  # type: ignore[no-untyped-def]
        if scope["type"] != "http" or not scope["path"].startswith(self.protected_path):
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        token = request.headers.get(CREDENTIAL_HEADER)
        if not token:
            response: Response = _missing_header_response()
            await response(scope, receive, send)
            return

        reset = _token.set(token)
        try:
            await self.app(scope, receive, send)
        finally:
            _token.reset(reset)
