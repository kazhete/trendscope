"""Base interface and shared HTTP client for collectors."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx

from trendscope.config import settings
from trendscope.models import Item, Topic


@asynccontextmanager
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """Async context manager yielding a configured ``httpx.AsyncClient``."""
    async with httpx.AsyncClient(
        timeout=settings.http_timeout_seconds,
        follow_redirects=True,
        headers={"User-Agent": settings.user_agent},
    ) as c:
        yield c


class Collector(ABC):
    """Abstract base class for all collectors."""

    name: str
    topic: Topic

    @abstractmethod
    async def fetch(self) -> list[Item]:
        """Fetch items from the upstream source and return them normalized."""
