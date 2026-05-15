"""Pipeline orchestration: collector registry, runner, and topic metadata."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Literal, TypedDict

from pydantic import TypeAdapter

from trendscope.collectors.ai_news import AINewsCollector
from trendscope.collectors.base import Collector
from trendscope.collectors.ecommerce_news import EcommerceNewsCollector
from trendscope.collectors.github_ai import GitHubAICollector
from trendscope.collectors.github_general import GitHubGeneralCollector
from trendscope.config import settings
from trendscope.models import Item, Topic

logger = logging.getLogger(__name__)

GroupBy = Literal["period", "date", "flat"]


class TopicMeta(TypedDict):
    display: str
    slug: str
    group_by: GroupBy


TOPICS: dict[Topic, TopicMeta] = {
    "ai_repos": {"display": "AI / ML Repos", "slug": "ai-repos", "group_by": "period"},
    "general_repos": {"display": "Trending Repos", "slug": "repos", "group_by": "period"},
    "ai_news": {"display": "AI News", "slug": "ai-news", "group_by": "date"},
    "ecommerce": {"display": "E-commerce", "slug": "ecommerce", "group_by": "date"},
    "odoo_apps": {"display": "Odoo Apps", "slug": "odoo-apps", "group_by": "flat"},
}


COLLECTORS: dict[str, type[Collector]] = {
    "github_ai": GitHubAICollector,
    "github_general": GitHubGeneralCollector,
    "ai_news": AINewsCollector,
    "ecommerce_news": EcommerceNewsCollector,
}

_ITEMS_ADAPTER = TypeAdapter(list[Item])


async def run_collectors(
    *,
    source: str | None = None,
    data_dir: Path | None = None,
) -> dict[str, int | str]:
    """Run registered collectors concurrently, write each to ``data/<name>.json``.

    Returns a per-collector summary: item count on success, ``"error: <msg>"``
    on failure. A single collector's failure does not break the rest.
    """
    data_dir = data_dir or settings.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)

    if source is not None:
        if source not in COLLECTORS:
            raise ValueError(f"unknown collector {source!r}; known: {sorted(COLLECTORS)}")
        targets = {source: COLLECTORS[source]}
    else:
        targets = dict(COLLECTORS)

    outcomes = await asyncio.gather(*(_run_one(name, cls) for name, cls in targets.items()))

    summary: dict[str, int | str] = {}
    for name, result in outcomes:
        if isinstance(result, Exception):
            summary[name] = f"error: {result}"
            logger.error("collector %s failed", name, exc_info=result)
            continue
        path = data_dir / f"{name}.json"
        path.write_bytes(_ITEMS_ADAPTER.dump_json(result, indent=2))
        summary[name] = len(result)
    return summary


async def _run_one(name: str, cls: type[Collector]) -> tuple[str, list[Item] | Exception]:
    try:
        items = await cls().fetch()
    except Exception as e:
        return name, e
    return name, items
