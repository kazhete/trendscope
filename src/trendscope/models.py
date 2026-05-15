"""Pydantic data models shared across collectors and the renderer."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

Topic = Literal[
    "ai_repos",
    "general_repos",
    "ai_news",
    "ecommerce",
    "odoo_apps",
]


class Item(BaseModel):
    """A single normalized item produced by a collector."""

    model_config = ConfigDict(frozen=False, extra="forbid")

    id: str
    source: str
    title: str
    url: HttpUrl
    summary: str | None = None
    score: float = Field(ge=0.0, le=1.0)
    published_at: datetime
    topic: Topic
    meta: dict[str, Any] = Field(default_factory=dict)

    @staticmethod
    def make_id(source: str, url: str) -> str:
        """Compute the deterministic id for an item given its source and url."""
        digest = hashlib.sha256(f"{source}|{url}".encode()).hexdigest()
        return digest[:16]
