"""Tests for the renderer."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from trendscope.models import Item
from trendscope.pipeline import TOPICS
from trendscope.render import (
    _format_date_label,
    _group_by_date,
    _group_by_period,
    _load_items,
    render_site,
)

FIXED_NOW = datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC)


def _make_item(
    *,
    source: str,
    title: str,
    url: str,
    topic: str,
    published_at: datetime,
    score: float = 0.5,
    meta: dict[str, Any] | None = None,
) -> Item:
    return Item(
        id=Item.make_id(source, url),
        source=source,
        title=title,
        url=url,
        score=score,
        published_at=published_at,
        topic=topic,  # type: ignore[arg-type]
        meta=meta or {},
    )


def _write_data(data_dir: Path, filename: str, items: list[Item]) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    payload = [json.loads(i.model_dump_json()) for i in items]
    (data_dir / filename).write_text(json.dumps(payload), encoding="utf-8")


# ---------- _load_items ----------


def test_load_items_returns_empty_when_dir_missing(tmp_path: Path):
    assert _load_items(tmp_path / "does-not-exist") == []


def test_load_items_reads_multiple_files(tmp_path: Path):
    data_dir = tmp_path / "data"
    _write_data(
        data_dir,
        "github_ai.json",
        [
            _make_item(
                source="github_ai",
                title="cool/repo",
                url="https://github.com/cool/repo",
                topic="ai_repos",
                published_at=FIXED_NOW - timedelta(days=2),
            )
        ],
    )
    _write_data(
        data_dir,
        "ai_news.json",
        [
            _make_item(
                source="hn",
                title="news 1",
                url="https://example.com/n1",
                topic="ai_news",
                published_at=FIXED_NOW - timedelta(hours=3),
            )
        ],
    )
    items = _load_items(data_dir)
    assert {i.title for i in items} == {"cool/repo", "news 1"}


def test_load_items_skips_corrupt_files(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "bad.json").write_text("not json", encoding="utf-8")
    _write_data(
        data_dir,
        "good.json",
        [
            _make_item(
                source="hn",
                title="ok",
                url="https://example.com/ok",
                topic="ai_news",
                published_at=FIXED_NOW,
            )
        ],
    )
    items = _load_items(data_dir)
    assert [i.title for i in items] == ["ok"]
    assert "Failed to load" in caplog.text


# ---------- grouping ----------


def test_group_by_period_buckets_by_age():
    items = [
        _make_item(
            source="x",
            title="today",
            url="https://e.example/a",
            topic="ai_repos",
            published_at=FIXED_NOW - timedelta(hours=3),
        ),
        _make_item(
            source="x",
            title="this-week",
            url="https://e.example/b",
            topic="ai_repos",
            published_at=FIXED_NOW - timedelta(days=3),
        ),
        _make_item(
            source="x",
            title="this-month",
            url="https://e.example/c",
            topic="ai_repos",
            published_at=FIXED_NOW - timedelta(days=15),
        ),
        _make_item(
            source="x",
            title="older",
            url="https://e.example/d",
            topic="ai_repos",
            published_at=FIXED_NOW - timedelta(days=60),
        ),
    ]
    groups = _group_by_period(items, now=FIXED_NOW)
    labels = [g["label"] for g in groups]
    assert labels == ["Today", "This week", "This month", "Older"]
    titles_by_label = {g["label"]: [i.title for i in g["entries"]] for g in groups}
    assert titles_by_label["Today"] == ["today"]
    assert titles_by_label["This week"] == ["this-week"]
    assert titles_by_label["This month"] == ["this-month"]
    assert titles_by_label["Older"] == ["older"]


def test_group_by_period_omits_empty_buckets():
    items = [
        _make_item(
            source="x",
            title="t1",
            url="https://e.example/a",
            topic="ai_repos",
            published_at=FIXED_NOW - timedelta(hours=2),
        )
    ]
    groups = _group_by_period(items, now=FIXED_NOW)
    assert [g["label"] for g in groups] == ["Today"]


def test_group_by_date_groups_by_calendar_day_descending():
    items = [
        _make_item(
            source="hn",
            title="d2",
            url="https://e.example/2",
            topic="ai_news",
            published_at=datetime(2026, 5, 13, 9, 0, tzinfo=UTC),
        ),
        _make_item(
            source="hn",
            title="d1-late",
            url="https://e.example/1b",
            topic="ai_news",
            published_at=datetime(2026, 5, 15, 11, 0, tzinfo=UTC),
        ),
        _make_item(
            source="hn",
            title="d1-early",
            url="https://e.example/1a",
            topic="ai_news",
            published_at=datetime(2026, 5, 15, 8, 0, tzinfo=UTC),
        ),
    ]
    groups = _group_by_date(items)
    # Two date groups, newest day first
    assert len(groups) == 2
    assert "May 15" in groups[0]["label"]
    assert "May 13" in groups[1]["label"]
    # Within the newest day, items sorted desc by published_at
    assert [i.title for i in groups[0]["entries"]] == ["d1-late", "d1-early"]


def test_format_date_label():
    label = _format_date_label("2026-05-15")
    assert "Friday" in label
    assert "May" in label
    assert "15" in label
    assert "2026" in label


# ---------- render_site end-to-end ----------


def test_render_site_writes_index_and_topic_pages(tmp_path: Path):
    data_dir = tmp_path / "data"
    dist_dir = tmp_path / "dist"
    _write_data(
        data_dir,
        "github_ai.json",
        [
            _make_item(
                source="github_ai",
                title="alice/llm",
                url="https://github.com/alice/llm",
                topic="ai_repos",
                published_at=FIXED_NOW - timedelta(hours=2),
                score=0.9,
                meta={"stars": 1234, "language": "Python"},
            ),
        ],
    )
    _write_data(
        data_dir,
        "ai_news.json",
        [
            _make_item(
                source="hn",
                title="Claude releases",
                url="https://news.example/claude",
                topic="ai_news",
                published_at=FIXED_NOW - timedelta(days=1),
                score=0.8,
                meta={"points": 200, "num_comments": 50},
            ),
        ],
    )

    out = render_site(data_dir=data_dir, dist_dir=dist_dir, now=FIXED_NOW)
    assert out == dist_dir

    # Index
    index = (dist_dir / "index.html").read_text()
    assert "Topics" in index
    for meta in TOPICS.values():
        assert meta["display"] in index

    # Topic pages
    ai_repos = (dist_dir / "ai-repos.html").read_text()
    assert "alice/llm" in ai_repos
    assert "1234 stars" in ai_repos
    assert "Python" in ai_repos
    assert "Today" in ai_repos  # period bucket

    ai_news = (dist_dir / "ai-news.html").read_text()
    assert "Claude releases" in ai_news
    assert "200 points" in ai_news
    assert "Thursday" in ai_news  # 2026-05-14 was a Thursday


def test_render_site_creates_dist_dir_if_missing(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    dist_dir = tmp_path / "new_dist"
    assert not dist_dir.exists()
    render_site(data_dir=data_dir, dist_dir=dist_dir, now=FIXED_NOW)
    assert dist_dir.exists()
    assert (dist_dir / "index.html").exists()


def test_render_site_with_no_items_still_writes_pages(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    dist_dir = tmp_path / "dist"
    render_site(data_dir=data_dir, dist_dir=dist_dir, now=FIXED_NOW)
    for meta in TOPICS.values():
        assert (dist_dir / f"{meta['slug']}.html").exists()
    index = (dist_dir / "index.html").read_text()
    assert "0 items" in index


def test_index_shows_counts_per_topic(tmp_path: Path):
    data_dir = tmp_path / "data"
    dist_dir = tmp_path / "dist"
    _write_data(
        data_dir,
        "github_ai.json",
        [
            _make_item(
                source="github_ai",
                title=f"r{n}",
                url=f"https://github.com/x/r{n}",
                topic="ai_repos",
                published_at=FIXED_NOW - timedelta(days=1),
            )
            for n in range(3)
        ],
    )
    render_site(data_dir=data_dir, dist_dir=dist_dir, now=FIXED_NOW)
    index = (dist_dir / "index.html").read_text()
    assert "3 items" in index
