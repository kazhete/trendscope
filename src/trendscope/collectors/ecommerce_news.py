"""E-commerce news collector. Aggregates RSS feeds + Odoo Apps Store scrape.

Two source types run concurrently:

1. **RSS feeds** (Odoo blog, ecommerce platform blogs, etc.) -- emitted with
   ``topic="ecommerce"``. Feeds default to ``settings.ecommerce_rss_feeds``;
   constructor override is supported.
2. **Odoo Apps Store** (``apps.odoo.com``) -- HTML scrape, emitted with
   ``topic="odoo_apps"``. CLAUDE.md sanctions this as the only HTML scrape
   in the project; the store has no API.

A failure on one source is logged and skipped without breaking the rest.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
from collections.abc import Awaitable, Sequence
from datetime import UTC, datetime, timedelta
from math import log
from typing import Any, Literal
from urllib.parse import urljoin, urlparse

import feedparser
import httpx
from bs4 import BeautifulSoup, Tag

from trendscope.collectors.base import Collector, client, with_retries
from trendscope.config import settings
from trendscope.models import Item, Topic

Period = Literal["day", "week", "month"]
_PERIOD_DAYS: dict[Period, int] = {"day": 1, "week": 7, "month": 30}

ODOO_APPS_BASE_URL = "https://apps.odoo.com"
ODOO_APPS_BROWSE_URL = f"{ODOO_APPS_BASE_URL}/apps/modules/browse"

# CSS selectors target the current apps.odoo.com markup. If the site changes,
# update these constants and the corresponding tests in lockstep.
ODOO_CARD_SELECTOR = ".loempia_panel_summary"
ODOO_TITLE_SELECTOR = "h5 a, .loempia_panel_title a"
ODOO_AUTHOR_SELECTOR = ".loempia_panel_author"
ODOO_PRICE_SELECTOR = ".loempia_panel_price"
ODOO_DOWNLOADS_SELECTOR = ".loempia_panel_downloads"
ODOO_SUMMARY_SELECTOR = ".loempia_panel_summary_text, .oe_module_desc"

logger = logging.getLogger(__name__)


class EcommerceNewsCollector(Collector):
    """RSS-based e-commerce news + Odoo Apps Store HTML scrape."""

    name: str = "ecommerce_news"
    topic: Topic = "ecommerce"

    def __init__(
        self,
        *,
        period: Period = "week",
        rss_feeds: Sequence[str] | None = None,
        rss_per_feed_limit: int = 20,
        scrape_odoo_apps: bool = True,
        odoo_apps_url: str = ODOO_APPS_BROWSE_URL,
        apps_limit: int = 30,
    ) -> None:
        if period not in _PERIOD_DAYS:
            raise ValueError(f"unknown period: {period!r}")
        self.period: Period = period
        self.rss_feeds: tuple[str, ...] = (
            tuple(rss_feeds) if rss_feeds is not None else tuple(settings.ecommerce_rss_feeds)
        )
        self.rss_per_feed_limit = rss_per_feed_limit
        self.scrape_odoo_apps = scrape_odoo_apps
        self.odoo_apps_url = odoo_apps_url
        self.apps_limit = apps_limit

    async def fetch(self) -> list[Item]:
        """Return ecommerce RSS items + Odoo apps items (failures isolated)."""
        now = _now()
        async with client() as c:
            tasks: list[Awaitable[list[Item]]] = [
                self._fetch_rss(c, url, now=now) for url in self.rss_feeds
            ]
            if self.scrape_odoo_apps:
                tasks.append(self._fetch_odoo_apps(c, now=now))
            results = await asyncio.gather(*tasks, return_exceptions=True)
        items: list[Item] = []
        for r in results:
            if isinstance(r, BaseException):
                logger.warning("ecommerce_news subfetch failed: %s", r)
                continue
            items.extend(r)
        return items

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
                    topic="ecommerce",
                    meta={
                        "feed_title": feed_title,
                        "feed_url": feed_url,
                        "author": entry.get("author"),
                    },
                )
            )
        return items

    @with_retries()
    async def _fetch_odoo_apps(self, c: httpx.AsyncClient, *, now: datetime) -> list[Item]:
        r = await c.get(self.odoo_apps_url, params={"order": "Newest"})
        r.raise_for_status()
        soup = await asyncio.to_thread(BeautifulSoup, r.text, "html.parser")
        cards = soup.select(ODOO_CARD_SELECTOR)[: self.apps_limit]
        raws: list[dict[str, Any]] = []
        for card in cards:
            link_tag = card.select_one(ODOO_TITLE_SELECTOR)
            if not isinstance(link_tag, Tag):
                continue
            href = link_tag.get("href")
            if not href or not isinstance(href, str):
                continue
            title = link_tag.get_text(strip=True)
            if not title:
                continue
            author_tag = card.select_one(ODOO_AUTHOR_SELECTOR)
            price_tag = card.select_one(ODOO_PRICE_SELECTOR)
            downloads_tag = card.select_one(ODOO_DOWNLOADS_SELECTOR)
            summary_tag = card.select_one(ODOO_SUMMARY_SELECTOR)
            raws.append(
                {
                    "title": title,
                    "url": urljoin(ODOO_APPS_BASE_URL, href),
                    "author": author_tag.get_text(strip=True) if author_tag else None,
                    "price": price_tag.get_text(strip=True) if price_tag else None,
                    "downloads": _parse_int(downloads_tag.get_text() if downloads_tag else None),
                    "summary": _clean_summary(summary_tag.get_text() if summary_tag else None),
                }
            )
        if not raws:
            return []
        max_downloads = max((r["downloads"] or 0) for r in raws) or 1
        return [
            Item(
                id=Item.make_id("odoo_apps", r["url"]),
                source="odoo_apps",
                title=r["title"],
                url=r["url"],
                summary=r["summary"],
                score=_log_normalize(r["downloads"] or 0, max_downloads),
                published_at=now,
                topic="odoo_apps",
                meta={
                    "author": r["author"],
                    "price": r["price"],
                    "downloads": r["downloads"],
                },
            )
            for r in raws
        ]


def _now() -> datetime:
    return datetime.now(UTC)


def _log_normalize(value: int, max_value: int) -> float:
    if max_value <= 0:
        return 0.0
    return min(1.0, log(value + 1) / log(max_value + 1))


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
_INT_RE = re.compile(r"(\d[\d,]*)")


def _parse_int(text: str | None) -> int | None:
    if not text:
        return None
    m = _INT_RE.search(text)
    if not m:
        return None
    return int(m.group(1).replace(",", ""))


def _clean_summary(value: str | None) -> str | None:
    if value is None:
        return None
    text = html.unescape(_TAG_RE.sub("", value)).strip()
    if not text:
        return None
    if len(text) > 500:
        text = text[:497].rstrip() + "..."
    return text
