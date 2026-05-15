"""Tests for the pipeline runner (collector registry + run_collectors)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from trendscope import pipeline as pipeline_mod
from trendscope.collectors.base import Collector
from trendscope.collectors.github_ai import GitHubAICollector
from trendscope.models import Item, Topic
from trendscope.pipeline import COLLECTORS, run_collectors


def _make_item(*, source: str = "stub", topic: Topic = "ai_news") -> Item:
    return Item(
        id=Item.make_id(source, f"https://example.com/{source}"),
        source=source,
        title="stub item",
        url=f"https://example.com/{source}",
        score=0.5,
        published_at=datetime(2026, 5, 15, tzinfo=UTC),
        topic=topic,
        meta={},
    )


def _make_stub(
    name_value: str,
    *,
    items: list[Item] | None = None,
    error: Exception | None = None,
) -> type[Collector]:
    class _Stub(Collector):
        name: str = name_value
        topic: Topic = "ai_news"

        async def fetch(self) -> list[Item]:
            if error is not None:
                raise error
            return items or []

    return _Stub


# ---------- registry ----------


def test_registry_includes_all_four_collectors():
    assert set(COLLECTORS) == {
        "github_ai",
        "github_general",
        "ai_news",
        "ecommerce_news",
    }
    assert COLLECTORS["github_ai"] is GitHubAICollector


# ---------- run_collectors ----------


def test_run_collectors_writes_one_json_per_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    items = [_make_item(source="a"), _make_item(source="a")]
    monkeypatch.setattr(
        pipeline_mod,
        "COLLECTORS",
        {
            "a": _make_stub("a", items=items),
            "b": _make_stub("b", items=[_make_item(source="b")]),
        },
    )
    summary = asyncio.run(run_collectors(data_dir=tmp_path))

    assert summary == {"a": 2, "b": 1}
    assert (tmp_path / "a.json").exists()
    assert (tmp_path / "b.json").exists()

    # JSON round-trips back to Items
    adapter = TypeAdapter(list[Item])
    loaded = adapter.validate_python(json.loads((tmp_path / "a.json").read_text()))
    assert len(loaded) == 2
    assert loaded[0].source == "a"


def test_run_collectors_filters_by_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        pipeline_mod,
        "COLLECTORS",
        {
            "a": _make_stub("a", items=[_make_item(source="a")]),
            "b": _make_stub("b", items=[_make_item(source="b")]),
        },
    )
    summary = asyncio.run(run_collectors(source="a", data_dir=tmp_path))
    assert summary == {"a": 1}
    assert (tmp_path / "a.json").exists()
    assert not (tmp_path / "b.json").exists()


def test_run_collectors_unknown_source_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(pipeline_mod, "COLLECTORS", {"a": _make_stub("a")})
    with pytest.raises(ValueError, match="unknown collector"):
        asyncio.run(run_collectors(source="ghost", data_dir=tmp_path))


def test_run_collectors_isolates_per_source_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    monkeypatch.setattr(
        pipeline_mod,
        "COLLECTORS",
        {
            "ok": _make_stub("ok", items=[_make_item(source="ok")]),
            "bad": _make_stub("bad", error=RuntimeError("boom")),
        },
    )
    summary = asyncio.run(run_collectors(data_dir=tmp_path))
    assert summary["ok"] == 1
    assert isinstance(summary["bad"], str)
    assert "boom" in summary["bad"]

    # ok file written, bad file NOT written
    assert (tmp_path / "ok.json").exists()
    assert not (tmp_path / "bad.json").exists()
    assert "collector bad failed" in caplog.text


def test_run_collectors_creates_data_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(pipeline_mod, "COLLECTORS", {"a": _make_stub("a")})
    target = tmp_path / "nested" / "data"
    assert not target.exists()
    asyncio.run(run_collectors(data_dir=target))
    assert target.exists()
    assert (target / "a.json").exists()


def test_run_collectors_empty_items_writes_empty_array(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(pipeline_mod, "COLLECTORS", {"a": _make_stub("a", items=[])})
    asyncio.run(run_collectors(data_dir=tmp_path))
    contents = json.loads((tmp_path / "a.json").read_text())
    assert contents == []
