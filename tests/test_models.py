"""Tests for the Item model and id helper."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from trendscope.models import Item


def _kwargs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": Item.make_id("hn", "https://news.ycombinator.com/item?id=1"),
        "source": "hn",
        "title": "Show HN: cool thing",
        "url": "https://news.ycombinator.com/item?id=1",
        "score": 0.5,
        "published_at": datetime(2026, 5, 15, tzinfo=UTC),
        "topic": "ai_news",
    }
    base.update(overrides)
    return base


def test_item_minimal_construction():
    item = Item(**_kwargs())
    assert item.source == "hn"
    assert str(item.url).startswith("https://")
    assert item.summary is None
    assert item.meta == {}


def test_item_accepts_arbitrary_meta():
    item = Item(**_kwargs(meta={"stars": 1234, "language": "Python"}))
    assert item.meta["stars"] == 1234


@pytest.mark.parametrize("bad_score", [-0.1, 1.5, 2.0])
def test_item_rejects_score_outside_unit_interval(bad_score: float):
    with pytest.raises(ValidationError):
        Item(**_kwargs(score=bad_score))


def test_item_rejects_unknown_topic():
    with pytest.raises(ValidationError):
        Item(**_kwargs(topic="not_a_real_topic"))


def test_item_rejects_extra_fields():
    with pytest.raises(ValidationError):
        Item(**_kwargs(bogus="x"))


def test_item_rejects_invalid_url():
    with pytest.raises(ValidationError):
        Item(**_kwargs(url="not-a-url"))


def test_make_id_is_deterministic_and_short():
    a = Item.make_id("github_ai", "https://github.com/foo/bar")
    b = Item.make_id("github_ai", "https://github.com/foo/bar")
    assert a == b
    assert len(a) == 16


def test_make_id_differs_by_source():
    a = Item.make_id("github_ai", "https://example.com")
    b = Item.make_id("hn", "https://example.com")
    assert a != b


def test_make_id_differs_by_url():
    a = Item.make_id("hn", "https://example.com/a")
    b = Item.make_id("hn", "https://example.com/b")
    assert a != b
