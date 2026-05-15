"""Tests for the shared HTTP client and retry decorator in collectors.base."""

from __future__ import annotations

import httpx
import pytest
import respx

from trendscope.collectors.base import (
    RETRYABLE_STATUS_CODES,
    client,
    with_retries,
)


async def test_client_yields_configured_async_client():
    async with client() as c:
        assert isinstance(c, httpx.AsyncClient)
        assert c.follow_redirects is True
        assert "trendscope" in c.headers["user-agent"].lower()


def test_retryable_status_codes_constant():
    assert RETRYABLE_STATUS_CODES == frozenset({429, 500, 502, 503, 504})


@respx.mock
async def test_with_retries_recovers_after_transient_5xx():
    url = "https://api.example.com/data"
    route = respx.get(url).mock(
        side_effect=[
            httpx.Response(500),
            httpx.Response(503),
            httpx.Response(200, json={"ok": True}),
        ]
    )

    @with_retries(attempts=3, initial_wait=0, max_wait=0)
    async def fetch() -> dict[str, bool]:
        async with client() as c:
            r = await c.get(url)
            r.raise_for_status()
            return r.json()

    result = await fetch()
    assert result == {"ok": True}
    assert route.call_count == 3


@respx.mock
async def test_with_retries_retries_on_429():
    url = "https://api.example.com/ratelimited"
    route = respx.get(url).mock(
        side_effect=[
            httpx.Response(429),
            httpx.Response(200, json={"ok": True}),
        ]
    )

    @with_retries(attempts=3, initial_wait=0, max_wait=0)
    async def fetch() -> dict[str, bool]:
        async with client() as c:
            r = await c.get(url)
            r.raise_for_status()
            return r.json()

    assert await fetch() == {"ok": True}
    assert route.call_count == 2


@respx.mock
async def test_with_retries_does_not_retry_4xx_other_than_429():
    url = "https://api.example.com/missing"
    route = respx.get(url).mock(return_value=httpx.Response(404))

    @with_retries(attempts=3, initial_wait=0, max_wait=0)
    async def fetch() -> None:
        async with client() as c:
            r = await c.get(url)
            r.raise_for_status()

    with pytest.raises(httpx.HTTPStatusError):
        await fetch()
    assert route.call_count == 1


@respx.mock
async def test_with_retries_gives_up_after_max_attempts():
    url = "https://api.example.com/broken"
    route = respx.get(url).mock(return_value=httpx.Response(500))

    @with_retries(attempts=3, initial_wait=0, max_wait=0)
    async def fetch() -> None:
        async with client() as c:
            r = await c.get(url)
            r.raise_for_status()

    with pytest.raises(httpx.HTTPStatusError):
        await fetch()
    assert route.call_count == 3


@respx.mock
async def test_with_retries_retries_on_request_error():
    url = "https://api.example.com/flaky"
    route = respx.get(url).mock(
        side_effect=[
            httpx.ConnectError("boom"),
            httpx.Response(200, json={"ok": True}),
        ]
    )

    @with_retries(attempts=3, initial_wait=0, max_wait=0)
    async def fetch() -> dict[str, bool]:
        async with client() as c:
            r = await c.get(url)
            r.raise_for_status()
            return r.json()

    assert await fetch() == {"ok": True}
    assert route.call_count == 2


@respx.mock
async def test_with_retries_propagates_non_retryable_exceptions_immediately():
    call_count = 0

    @with_retries(attempts=3, initial_wait=0, max_wait=0)
    async def fetch() -> None:
        nonlocal call_count
        call_count += 1
        raise ValueError("not an http error")

    with pytest.raises(ValueError, match="not an http error"):
        await fetch()
    assert call_count == 1
