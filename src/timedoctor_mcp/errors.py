"""Structured error envelope for tool results (SOP SS4).

Tools never raise exceptions for business/upstream failures -- they return a
JSON string with this envelope so the calling Agent can branch on ``code``
and decide whether to retry.
"""

from __future__ import annotations

import json
from typing import Literal

ErrorCode = Literal[
    "not_configured",
    "unauthorized",
    "not_found",
    "invalid_argument",
    "rate_limited",
    "upstream_error",
]

# HTTP status -> error code, per SOP SS4.2's fixed vocabulary.
_STATUS_TO_CODE: dict[int, ErrorCode] = {
    401: "unauthorized",
    403: "unauthorized",
    404: "not_found",
    400: "invalid_argument",
    422: "invalid_argument",
    429: "rate_limited",
}


class UpstreamError(Exception):
    """Raised by client.py for any Time Doctor HTTP/network failure.

    Carries just enough to map to an error envelope -- never the raw
    response body, which could contain account data.
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class InvalidArgument(Exception):
    """Raised for bad/ambiguous tool arguments (e.g. AC8's ambiguous company)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def code_for_status(status_code: int) -> ErrorCode:
    """Map an upstream HTTP status to the fixed error-code vocabulary.

    Anything not explicitly listed (5xx, or an unexpected 4xx) is
    ``upstream_error`` / falls through to the 5xx bucket below.
    """
    if status_code in _STATUS_TO_CODE:
        return _STATUS_TO_CODE[status_code]
    if 500 <= status_code < 600:
        return "upstream_error"
    # Unknown 4xx we didn't anticipate: still the caller's fault, closest to
    # invalid_argument, but conservatively treated as upstream_error since we
    # cannot be sure it is not a transient vendor-side quirk.
    return "upstream_error"


def envelope(code: ErrorCode, message: str, *, retryable: bool) -> str:
    """Serialize the error envelope (SOP SS4.2). Never ``indent=2`` (SS2.5)."""
    return json.dumps(
        {"error": {"code": code, "message": message, "retryable": retryable}},
        separators=(",", ":"),
        ensure_ascii=False,
    )


_RETRYABLE: frozenset[ErrorCode] = frozenset({"rate_limited", "upstream_error"})


def envelope_for_upstream_error(exc: UpstreamError) -> str:
    if exc.status_code is None:
        # Connection error / timeout -- no status code at all.
        return envelope("upstream_error", exc.message, retryable=True)
    code = code_for_status(exc.status_code)
    return envelope(code, exc.message, retryable=code in _RETRYABLE)


def envelope_for_invalid_argument(exc: InvalidArgument) -> str:
    return envelope("invalid_argument", exc.message, retryable=False)
