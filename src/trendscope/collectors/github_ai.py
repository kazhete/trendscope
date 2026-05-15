"""GitHub AI/ML repos collector. Uses the GitHub Search API."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from math import log
from typing import Any, Literal

from trendscope.collectors.base import Collector, client, with_retries
from trendscope.config import settings
from trendscope.models import Item, Topic

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
        """Return a list of trending AI/ML repos for the configured period."""
        data = await self._search(self._build_query())
        repos = data.get("items") or []
        if not repos:
            return []
        max_stars = max(int(r.get("stargazers_count", 0)) for r in repos) or 1
        return [self._to_item(r, max_stars) for r in repos]

    def _build_query(self, *, now: datetime | None = None) -> str:
        now = now or datetime.now(UTC)
        cutoff = (now - timedelta(days=_PERIOD_DAYS[self.period])).date().isoformat()
        topics_clause = " OR ".join(f"topic:{t}" for t in self.topics)
        return f"({topics_clause}) created:>{cutoff} stars:>={self.min_stars}"

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
