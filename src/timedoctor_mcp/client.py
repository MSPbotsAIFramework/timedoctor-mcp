"""Thin async wrapper around the Time Doctor v2 API (SOP SS5).

One ``TimeDoctorClient`` instance is created per tool call (via
``client_factory``) and discarded afterwards -- never cached across requests,
since it is constructed with one tenant's token (SOP SS3.4, no cross-request
caching of credentials or derived data).
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .errors import UpstreamError

CONNECT_TIMEOUT_S = 5.0
READ_TIMEOUT_S = 30.0
MAX_RETRIES = 3
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
# Module-level so tests can monkeypatch it to 0 and keep the retry path fast.
DEFAULT_BACKOFF_S = 0.5


class TimeDoctorClient:
    """Calls ``base_url`` with ``Authorization: JWT <token>``.

    Used as an async context manager so the underlying connection pool is
    always closed, and reused for every call made within one tool
    invocation (SOP SS5 "connection reuse within one request").
    """

    def __init__(self, token: str, base_url: str) -> None:
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "TimeDoctorClient":
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Authorization": f"JWT {self._token}"},
            timeout=httpx.Timeout(connect=CONNECT_TIMEOUT_S, read=READ_TIMEOUT_S, write=READ_TIMEOUT_S, pool=READ_TIMEOUT_S),
        )
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET ``path`` with bounded retry+backoff on 429/5xx. Returns parsed JSON."""
        assert self._client is not None, "TimeDoctorClient must be used as `async with`"

        clean_params = {k: v for k, v in (params or {}).items() if v is not None}

        last_exc: UpstreamError | None = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = await self._client.get(path, params=clean_params)
            except httpx.TimeoutException as exc:
                last_exc = UpstreamError(f"Time Doctor request timed out: {exc}")
                await self._sleep_before_retry(attempt, None)
                continue
            except httpx.HTTPError as exc:
                last_exc = UpstreamError(f"Time Doctor network error: {exc}")
                await self._sleep_before_retry(attempt, None)
                continue

            if response.status_code < 400:
                return response.json()

            if response.status_code in RETRYABLE_STATUS and attempt < MAX_RETRIES:
                last_exc = UpstreamError(
                    f"Time Doctor returned {response.status_code} for {path}",
                    status_code=response.status_code,
                )
                await self._sleep_before_retry(attempt, response.headers.get("Retry-After"))
                continue

            raise UpstreamError(
                f"Time Doctor returned {response.status_code} for {path}",
                status_code=response.status_code,
            )

        assert last_exc is not None
        raise last_exc

    @staticmethod
    async def _sleep_before_retry(attempt: int, retry_after: str | None) -> None:
        if retry_after is not None:
            try:
                delay = float(retry_after)
            except ValueError:
                delay = DEFAULT_BACKOFF_S * (2**attempt)
        else:
            delay = DEFAULT_BACKOFF_S * (2**attempt)
        await asyncio.sleep(delay)
