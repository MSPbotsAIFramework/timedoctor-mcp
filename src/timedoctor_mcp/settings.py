"""Non-credential configuration.

Time Doctor credentials never live here -- they arrive per request as the
``X-Timedoctor-Token`` header and are read in ``middleware.py`` (SOP SS3.1).
This module only knows the HTTP bind address and the upstream API base URL.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process-wide, non-secret configuration.

    ``extra="ignore"`` is required: the runtime environment injects variables
    this project does not declare, and an unknown field would otherwise crash
    startup (SOP SS1.2).
    """

    model_config = SettingsConfigDict(extra="ignore")

    mcp_http_host: str = "0.0.0.0"
    mcp_http_port: int = 8080

    timedoctor_base_url: str = "https://api2.timedoctor.com/api/1.0"


def get_settings() -> Settings:
    return Settings()
