"""Retry/backoff call-budget tests for TimeDoctorClient (PRD-21319 review round 1, finding 1).

SOP SS14's checklist requires a single tool call (including all retries) to
stay under 120s. These drive ``TimeDoctorClient.get`` directly against a fake
clock, so the budget math is exact instead of depending on real sleeps.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from timedoctor_mcp import client as client_module
from timedoctor_mcp.client import TimeDoctorClient
from timedoctor_mcp.errors import UpstreamError

BASE = "https://api2.timedoctor.com/api/1.0"


class FakeClock:
    """A monotonic clock driven only by explicit ``advance()`` calls."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def fake_clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    clock = FakeClock()
    monkeypatch.setattr(client_module.time, "monotonic", clock.monotonic)

    async def fake_sleep(delay: float) -> None:
        clock.advance(delay)

    monkeypatch.setattr(client_module.asyncio, "sleep", fake_sleep)
    return clock


@pytest.mark.asyncio
@respx.mock
async def test_retry_delay_is_clamped_even_for_a_huge_retry_after(fake_clock: FakeClock) -> None:
    """A vendor Retry-After of 150s must not be slept for verbatim."""
    route = respx.get(f"{BASE}/thing").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "150"}, json={"message": "slow down"}),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    async with TimeDoctorClient("tok", BASE) as client:
        result = await client.get("/thing")

    assert result == {"ok": True}
    assert route.call_count == 2
    # The only sleep that happened is clamped, not the raw 150s header value.
    assert fake_clock.now <= client_module.MAX_RETRY_DELAY_S


@pytest.mark.asyncio
@respx.mock
async def test_retry_stops_once_the_call_budget_would_be_exceeded(fake_clock: FakeClock) -> None:
    """Slow upstream responses plus repeated large Retry-After must not cross ~120s total.

    Each simulated response "takes" 40s of wall-clock time before replying
    429 with Retry-After: 150 (clamped to 30s). After the first retry the
    client has already burned 70s; the second response pushes elapsed to
    110s, past CALL_BUDGET_S, so the client must give up before sleeping
    the clamped 30s again rather than silently running past the SOP's 120s
    hard limit.
    """

    def slow_rate_limited(_: httpx.Request) -> httpx.Response:
        fake_clock.advance(40.0)
        return httpx.Response(429, headers={"Retry-After": "150"}, json={"message": "slow down"})

    respx.get(f"{BASE}/thing").mock(side_effect=slow_rate_limited)

    async with TimeDoctorClient("tok", BASE) as client:
        with pytest.raises(UpstreamError) as excinfo:
            await client.get("/thing")

    assert excinfo.value.status_code == 429
    assert fake_clock.now < 120.0


@pytest.mark.asyncio
@respx.mock
async def test_retry_after_zero_is_unaffected_by_the_budget(fake_clock: FakeClock) -> None:
    """Regression guard: the existing fast-path tests rely on Retry-After: 0 still working."""
    route = respx.get(f"{BASE}/thing").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}, json={"message": "slow down"}),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    async with TimeDoctorClient("tok", BASE) as client:
        result = await client.get("/thing")

    assert result == {"ok": True}
    assert route.call_count == 2
    assert fake_clock.now == 0.0
