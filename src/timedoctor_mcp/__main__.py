"""Entry point: run the Time Doctor MCP server over Streamable HTTP."""

from __future__ import annotations

import uvicorn

from .server import build_asgi_app
from .settings import get_settings


def main() -> None:
    settings = get_settings()
    app = build_asgi_app(settings)
    uvicorn.run(app, host=settings.mcp_http_host, port=settings.mcp_http_port)


if __name__ == "__main__":
    main()
