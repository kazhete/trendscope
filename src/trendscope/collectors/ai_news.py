"""AI news collector. Aggregates Hacker News stories (via Algolia) and RSS feeds."""

from __future__ import annotations

import asyncio
import html
import logging
import re
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from math import log
from typing import Any, Literal
from urllib.parse import urlparse

import feedparser
import httpx

from trendscope.collectors.base import Collector, client, with_retries
from trendscope.config import settings
from trendscope.models import Item, Topic

Period = Literal["day", "week", "month"]
_PERIOD_DAYS: dict[Period, int] = {"day": 1, "week": 7, "month": 30}

HN_SEARCH_URL = "https://hn.algolia.com/api/v1/search"

DEFAULT_HN_KEYWORDS: tuple[str, ...] = (
    "AI",
    "LLM",
    "GPT",
    "transformer",
    "machine learning",
    "deep learning",
    "neural network",
    "Anthropic",
    "OpenAI",
)

logger = logging.getLogger(__name__)


class AINewsCollector(Collector):
    """Aggregate AI news from Hacker News (via Algolia) and configured RSS feeds.

    HN stories are filtered by Algolia to ``points >= hn_min_points`` and
    further filtered in Python to titles matching one of ``hn_keywords``.
    RSS feeds default to ``settings.ai_news_rss_feeds`` and can be overridden
    per-instance. HN and RSS fetches run concurrently; a single feed failure
    does not break the rest.
    """

    name: str = "ai_news"
    topic: Topic = "ai_news"

    def __init__(
        self,
        *,
        period: Period = "week",
        hn_keywords: Sequence[str] | None = None,
        hn_min_points: int = 50,
        hn_hits_per_page: int = 50,
        rss_feeds: Sequence[str] | None = None,
        rss_per_feed_limit: int = 20,
    ) -> None:
        if period not in _PERIOD_DAYS:
            raise ValueError(f"unknown period: {period!r}")
        self.period: Period = period
        self.hn_keywords: tuple[str, ...] = (
            tuple(hn_keywords) if hn_keywords else DEFAULT_HN_KEYWORDS
        )
        self.hn_min_points = hn_min_points
        self.hn_hits_per_page = hn_hits_per_page
        self.rss_feeds: tuple[str, ...] = (
            tuple(rss_feeds) if rss_feeds is not None else tuple(settings.ai_news_rss_feeds)
        )
        self.rss_per_feed_limit = rss_per_feed_limit

    async def fetch(self) -> list[Item]:
        """Return HN + RSS items for the configured period (failures isolated)."""
        now = _now()
        async with client() as c:
            tasks: list[asyncio.Future[list[Item]] | Any] = [self._fetch_hn(c, now=now)]
            tasks.extend(self._fetch_rss(c, url, now=now) for url in self.rss_feeds)
            results = await asyncio.gather(*tasks, return_exceptions=True)
        items: list[Item] = []
        for r in results:
            if isinstance(r, BaseException):
                logger.warning("ai_news subfetch failed: %s", r)
                continue
            items.extend(r)
        return items

    @with_retries()
    async def _fetch_hn(self, c: httpx.AsyncClient, *, now: datetime) -> list[Item]:
        cutoff = int((now - timedelta(days=_PERIOD_DAYS[self.period])).timestamp())
        r = await c.get(
            HN_SEARCH_URL,
            params={
                "tags": "story",
                "numericFilters": f"points>={self.hn_min_points},created_at_i>{cutoff}",
                "hitsPerPage": self.hn_hits_per_page,
            },
        )
        r.raise_for_status()
        hits: list[dict[str, Any]] = r.json().get("hits") or []
        pattern = re.compile(
            r"\b(?:" + "|".join(re.escape(kw) for kw in self.hn_keywords) + r")\b",
            re.IGNORECASE,
        )
        filtered = [h for h in hits if pattern.search(h.get("title") or "")]
        if not filtered:
            return []
        max_points = max(int(h.get("points") or 0) for h in filtered) or 1
        return [self._hn_to_item(h, max_points) for h in filtered]

    @with_retries()
    async def _fetch_rss(self, c: httpx.AsyncClient, feed_url: str, *, now: datetime) -> list[Item]:
        r = await c.get(feed_url)
        r.raise_for_status()
        parsed = await asyncio.to_thread(feedparser.parse, r.content)
        cutoff = now - timedelta(days=_PERIOD_DAYS[self.period])
        period_seconds = float(_PERIOD_DAYS[self.period] * 86400)
        source_key = _feed_source_key(feed_url)
        feed_title = (parsed.get("feed") or {}).get("title") or source_key
        items: list[Item] = []
        for entry in parsed.entries[: self.rss_per_feed_limit]:
            link = entry.get("link")
            if not link:
                continue
            published = _entry_published(entry)
            if published is None or published < cutoff:
                continue
            age_s = (now - published).total_seconds()
            score = max(0.05, min(1.0, 1.0 - age_s / period_seconds))
            items.append(
                Item(
                    id=Item.make_id(source_key, link),
                    source=source_key,
                    title=(entry.get("title") or "(no title)").strip(),
                    url=link,
                    summary=_clean_summary(entry.get("summary") or entry.get("description")),
                    score=score,
                    published_at=published,
                    topic=self.topic,
                    meta={
                        "feed_title": feed_title,
                        "feed_url": feed_url,
                        "author": entry.get("author"),
                    },
                )
            )
        return items

    def _hn_to_item(self, hit: dict[str, Any], max_points: int) -> Item:
        object_id = str(hit["objectID"])
        external = hit.get("url")
        hn_url = f"https://news.ycombinator.com/item?id={object_id}"
        url = external or hn_url
        points = int(hit.get("points") or 0)
        return Item(
            id=Item.make_id("hn", url),
            source="hn",
            title=hit["title"],
            url=url,
            summary=hit.get("story_text") or None,
            score=_log_normalize(points, max_points),
            published_at=_parse_hn_dt(hit["created_at"]),
            topic=self.topic,
            meta={
                "points": points,
                "num_comments": int(hit.get("num_comments") or 0),
                "author": hit.get("author"),
                "hn_url": hn_url,
                "object_id": object_id,
            },
        )


def _now() -> datetime:
    return datetime.now(UTC)


def _log_normalize(value: int, max_value: int) -> float:
    if max_value <= 0:
        return 0.0
    return min(1.0, log(value + 1) / log(max_value + 1))


def _parse_hn_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _entry_published(entry: Any) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    y, mo, d, h, mi, s = parsed[:6]
    return datetime(y, mo, d, h, mi, s, tzinfo=UTC)


def _feed_source_key(feed_url: str) -> str:
    host = urlparse(feed_url).hostname or "rss"
    if host.startswith("www."):
        host = host[4:]
    return f"rss:{host}"


_TAG_RE = re.compile(r"<[^>]+>")


def _clean_summary(value: str | None) -> str | None:
    if value is None:
        return None
    text = html.unescape(_TAG_RE.sub("", value)).strip()
    if not text:
        return None
    if len(text) > 500:
        text = text[:497].rstrip() + "..."
    return text
