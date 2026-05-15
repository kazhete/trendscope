"""Renderer: reads ``data/*.json`` and writes static HTML to ``dist/``."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import TypeAdapter

from trendscope.config import settings
from trendscope.models import Item, Topic
from trendscope.pipeline import TOPICS, GroupBy

logger = logging.getLogger(__name__)

_ITEMS_ADAPTER = TypeAdapter(list[Item])


def render_site(
    *,
    data_dir: Path | None = None,
    dist_dir: Path | None = None,
    now: datetime | None = None,
) -> Path:
    """Read JSON from ``data_dir``, render templates, write site to ``dist_dir``.

    Returns the resolved ``dist_dir`` path.
    """
    data_dir = data_dir or settings.data_dir
    dist_dir = dist_dir or settings.dist_dir
    dist_dir.mkdir(parents=True, exist_ok=True)
    now = now or datetime.now(UTC)

    items = _load_items(data_dir)
    by_topic: dict[Topic, list[Item]] = defaultdict(list)
    for item in items:
        by_topic[item.topic].append(item)

    env = Environment(
        loader=FileSystemLoader(settings.templates_dir),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )

    counts = {t: len(by_topic.get(t, [])) for t in TOPICS}
    common_ctx = {
        "topics": TOPICS,
        "generated_at": now.strftime("%Y-%m-%d %H:%M UTC"),
    }

    index_tpl = env.get_template("index.html")
    (dist_dir / "index.html").write_text(
        index_tpl.render(counts=counts, **common_ctx),
        encoding="utf-8",
    )

    topic_tpl = env.get_template("topic.html")
    for topic, meta in TOPICS.items():
        topic_items = sorted(
            by_topic.get(topic, []),
            key=lambda i: i.score,
            reverse=True,
        )
        groups = _group_items(topic_items, meta["group_by"], now=now)
        (dist_dir / f"{meta['slug']}.html").write_text(
            topic_tpl.render(
                topic=topic,
                topic_meta=meta,
                groups=groups,
                total_count=len(topic_items),
                **common_ctx,
            ),
            encoding="utf-8",
        )

    logger.info("Rendered %d topic pages + index to %s", len(TOPICS), dist_dir)
    return dist_dir


def _load_items(data_dir: Path) -> list[Item]:
    if not data_dir.exists():
        logger.warning("data_dir %s does not exist; rendering with no items", data_dir)
        return []
    items: list[Item] = []
    for path in sorted(data_dir.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            items.extend(_ITEMS_ADAPTER.validate_python(raw))
        except Exception:
            logger.exception("Failed to load %s; skipping", path)
    return items


def _group_items(
    items: list[Item],
    group_by: GroupBy,
    *,
    now: datetime,
) -> list[dict[str, Any]]:
    if group_by == "period":
        return _group_by_period(items, now=now)
    if group_by == "date":
        return _group_by_date(items)
    return [{"label": "All", "entries": items}] if items else []


def _group_by_period(items: list[Item], *, now: datetime) -> list[dict[str, Any]]:
    buckets: dict[str, list[Item]] = {
        "Today": [],
        "This week": [],
        "This month": [],
        "Older": [],
    }
    for item in items:
        age_days = (now - item.published_at).total_seconds() / 86400.0
        if age_days < 1:
            buckets["Today"].append(item)
        elif age_days < 7:
            buckets["This week"].append(item)
        elif age_days < 30:
            buckets["This month"].append(item)
        else:
            buckets["Older"].append(item)
    return [{"label": label, "entries": bucket} for label, bucket in buckets.items() if bucket]


def _group_by_date(items: list[Item]) -> list[dict[str, Any]]:
    sorted_items = sorted(items, key=lambda i: i.published_at, reverse=True)
    by_date: dict[str, list[Item]] = defaultdict(list)
    for item in sorted_items:
        by_date[item.published_at.strftime("%Y-%m-%d")].append(item)
    return [
        {"label": _format_date_label(date_str), "entries": bucket}
        for date_str, bucket in by_date.items()
    ]


def _format_date_label(date_str: str) -> str:
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=UTC)
    return f"{dt.strftime('%A')}, {dt.strftime('%B')} {dt.day}, {dt.year}"
