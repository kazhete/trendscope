"""Pipeline coordination: collect -> normalize -> dedupe -> rank -> render."""

from __future__ import annotations

from typing import Literal, TypedDict

from trendscope.models import Topic

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
