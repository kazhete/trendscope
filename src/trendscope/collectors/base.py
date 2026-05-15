"""Base interface, shared HTTP client, and retry decorator for collectors."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import ParamSpec, TypeVar

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from trendscope.config import settings
from trendscope.models import Item, Topic

P = ParamSpec("P")
T = TypeVar("T")

RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})


@asynccontextmanager
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """Yield a configured ``httpx.AsyncClient`` for use by collectors."""
    async with httpx.AsyncClient(
        timeout=settings.http_timeout_seconds,
        follow_redirects=True,
        headers={"User-Agent": settings.user_agent},
    ) as c:
        yield c


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRYABLE_STATUS_CODES
    return isinstance(exc, httpx.RequestError)


def with_retries(
    *,
    attempts: int = 3,
    initial_wait: float = 1.0,
    max_wait: float = 10.0,
) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]]:
    """Wrap an async function with exponential-backoff retries for transient HTTP errors.

    Retries on :class:`httpx.RequestError` (network / timeout) and on
    :class:`httpx.HTTPStatusError` where the status code is in
    :data:`RETRYABLE_STATUS_CODES` (429, 500, 502, 503, 504). All other
    exceptions propagate without retry. After ``attempts`` failures the last
    exception is re-raised.
    """
    return retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(attempts),
        wait=wait_exponential(multiplier=initial_wait, max=max_wait),
        reraise=True,
    )


class Collector(ABC):
    """Abstract base class for all collectors.

    Subclasses set ``name`` and ``topic`` as class attributes and implement
    :meth:`fetch`. Collectors return ``Item`` objects only — they must never
    write HTML or touch ``dist/``.
    """

    name: str
    topic: Topic

    @abstractmethod
    async def fetch(self) -> list[Item]:
        """Fetch items from the upstream source and return them normalized."""
