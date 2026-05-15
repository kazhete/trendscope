"""GitHub AI/ML repos collector. Uses the GitHub Search API."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from math import log
from typing import Any, Literal

from trendscope.collectors.base import Collector, client, with_retries
from trendscope.config import settings
from trendscope.models import Item, Topic

logger = logging.getLogger(__name__)

Period = Literal["day", "week", "month"]

_PERIOD_DAYS: dict[Period, int] = {"day": 1, "week": 7, "month": 30}

DEFAULT_AI_TOPICS: tuple[str, ...] = (
    "machine-learning",
    "deep-learning",
    "artificial-intelligence",
    "llm",
    "generative-ai",
)

GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"


class GitHubAICollector(Collector):
    """Collect trending AI/ML repositories from the GitHub Search API.

    Queries repos created within the configured ``period`` (day/week/month)
    that carry one of ``topics``, sorted by stars descending. The score is
    log-normalized against the top result so that 1.0 is the most-starred
    repo in the response and weaker results fall off smoothly.
    """

    name: str = "github_ai"
    topic: Topic = "ai_repos"

    def __init__(
        self,
        *,
        period: Period = "week",
        topics: Sequence[str] | None = None,
        per_page: int = 30,
        min_stars: int = 5,
    ) -> None:
        if period not in _PERIOD_DAYS:
            raise ValueError(f"unknown period: {period!r}")
        if not 1 <= per_page <= 100:
            raise ValueError(f"per_page must be in 1..100, got {per_page}")
        self.period: Period = period
        self.topics: tuple[str, ...] = tuple(topics) if topics else DEFAULT_AI_TOPICS
        self.per_page = per_page
        self.min_stars = min_stars

    async def fetch(self) -> list[Item]:
        """Return trending AI/ML repos: one Search query per topic, deduped by URL.

        GitHub Search does not support boolean OR across qualifiers, so we issue
        N parallel queries (one per topic) and merge. Results are deduplicated
        by repo URL, sorted by stars desc, and capped at ``per_page``.
        """
        now = datetime.now(UTC)
        results = await asyncio.gather(
            *(self._search(self._build_query(t, now=now)) for t in self.topics),
            return_exceptions=True,
        )

        seen: set[str] = set()
        repos: list[dict[str, Any]] = []
        for r in results:
            if isinstance(r, BaseException):
                logger.warning("github_ai topic search failed: %s", r)
                continue
            for repo in r.get("items") or []:
                url = repo.get("html_url")
                if not url or url in seen:
                    continue
                seen.add(url)
                repos.append(repo)

        if not repos:
            return []
        repos.sort(key=lambda r: int(r.get("stargazers_count") or 0), reverse=True)
        repos = repos[: self.per_page]
        max_stars = max(int(r.get("stargazers_count") or 0) for r in repos) or 1
        return [self._to_item(r, max_stars) for r in repos]

    def _build_query(self, topic: str, *, now: datetime | None = None) -> str:
        now = now or datetime.now(UTC)
        cutoff = (now - timedelta(days=_PERIOD_DAYS[self.period])).date().isoformat()
        return f"topic:{topic} created:>{cutoff} stars:>={self.min_stars}"

    @with_retries()
    async def _search(self, query: str) -> dict[str, Any]:
        headers: dict[str, str] = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if settings.github_token:
            headers["Authorization"] = f"Bearer {settings.github_token}"
        async with client() as c:
            r = await c.get(
                GITHUB_SEARCH_URL,
                params={
                    "q": query,
                    "sort": "stars",
                    "order": "desc",
                    "per_page": self.per_page,
                },
                headers=headers,
            )
            r.raise_for_status()
            data: dict[str, Any] = r.json()
            return data

    def _to_item(self, repo: dict[str, Any], max_stars: int) -> Item:
        stars = int(repo.get("stargazers_count", 0))
        url = repo["html_url"]
        return Item(
            id=Item.make_id(self.name, url),
            source=self.name,
            title=repo["full_name"],
            url=url,
            summary=repo.get("description"),
            score=_normalize_score(stars, max_stars),
            published_at=_parse_dt(repo["created_at"]),
            topic=self.topic,
            meta={
                "stars": stars,
                "forks": int(repo.get("forks_count", 0)),
                "language": repo.get("language"),
                "topics": list(repo.get("topics") or []),
                "owner": (repo.get("owner") or {}).get("login"),
                "pushed_at": repo.get("pushed_at"),
            },
        )


def _normalize_score(stars: int, max_stars: int) -> float:
    if max_stars <= 0:
        return 0.0
    return min(1.0, log(stars + 1) / log(max_stars + 1))


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
