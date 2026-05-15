"""Pipeline coordination: collect -> normalize -> dedupe -> rank -> render."""

from __future__ import annotations

from typing import TypedDict


class TopicMeta(TypedDict):
    display: str
    slug: str


TOPICS: dict[str, TopicMeta] = {
    "ai_repos": {"display": "AI / ML Repos", "slug": "ai-repos"},
    "general_repos": {"display": "Trending Repos", "slug": "repos"},
    "ai_news": {"display": "AI News", "slug": "ai-news"},
    "ecommerce": {"display": "E-commerce", "slug": "ecommerce"},
    "odoo_apps": {"display": "Odoo Apps", "slug": "odoo-apps"},
}
