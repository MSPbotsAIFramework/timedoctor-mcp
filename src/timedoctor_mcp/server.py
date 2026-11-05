"""FastMCP app factory and HTTP wiring.

Uses the MCP Python SDK's bundled ``mcp.server.fastmcp.FastMCP`` (not the
third-party ``fastmcp`` package), because only this implementation exposes
``transport_security`` directly on the constructor -- required to turn off
DNS-rebinding protection (SOP SS7): this service sits behind the Gateway on
an internal network, so the inbound ``Host`` header is never ``localhost``.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .client import TimeDoctorClient
from .middleware import CredentialMiddleware, current_token
from .settings import Settings

INSTRUCTIONS = (
    "Time Doctor is a time-tracking and productivity tool. This service reports on "
    "an organization's Time Doctor data: who is tracked (companies, users, projects) "
    "and how time is spent (worklogs, productivity summaries, per-app/website "
    "category breakdowns) over a date range. All tools are read-only. Most accounts "
    "have exactly one company; omit company_id and it resolves automatically. "
    "A typical flow: call timedoctor_list_companies (or let company_id auto-resolve) "
    "to find the company, timedoctor_list_users to find a person of interest, then "
    "timedoctor_list_worklogs or timedoctor_get_activity_summary/_by_category for "
    "that person's time window."
)


def client_factory(settings: Settings):
    """Build a ``TimeDoctorClient`` for the current request's token.

    Returns ``None`` when no token is present on the context (should not
    normally happen -- the credential middleware rejects such requests with
    401 before a tool ever runs -- but tools must still treat this as
    ``not_configured`` rather than crash; SOP SS9's ``client_factory``
    contract).
    """
    token = current_token()
    if not token:
        return None
    return TimeDoctorClient(token, settings.timedoctor_base_url)


def build_mcp(settings: Settings) -> FastMCP:
    mcp = FastMCP(
        name="timedoctor-mcp",
        instructions=INSTRUCTIONS,
        host=settings.mcp_http_host,
        port=settings.mcp_http_port,
        stateless_http=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_request: Request) -> Response:
        # Pure local probe -- must never depend on Time Doctor's availability (SOP SS1.1).
        return JSONResponse({"status": "ok"})

    from .tools import register

    register(mcp, lambda: client_factory(settings))

    return mcp


def build_asgi_app(settings: Settings | None = None) -> ASGIApp:
    """The full ASGI app: credential middleware wrapping the MCP Starlette app.

    Wrapped as a plain ASGI callable (not a Starlette ``Mount``), so lifespan
    scope messages pass straight through to the inner app unchanged -- a
    Starlette ``Mount`` would silently swallow them and break ``/mcp`` with
    "Task group is not initialized" (SOP SS7).
    """
    settings = settings or Settings()
    mcp = build_mcp(settings)
    inner_app = mcp.streamable_http_app()
    return CredentialMiddleware(inner_app)
