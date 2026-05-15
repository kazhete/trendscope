"""Smoke tests for the trendscope CLI."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from trendscope import pipeline as pipeline_mod
from trendscope.cli import app
from trendscope.collectors.base import Collector
from trendscope.models import Item, Topic

runner = CliRunner()


def _stub_item(source: str = "stub") -> Item:
    return Item(
        id=Item.make_id(source, f"https://example.com/{source}"),
        source=source,
        title="stub",
        url=f"https://example.com/{source}",
        score=0.5,
        published_at=datetime(2026, 5, 15, tzinfo=UTC),
        topic="ai_news",
        meta={},
    )


def _stub_collector_cls(name_value: str) -> type[Collector]:
    class _Stub(Collector):
        name: str = name_value
        topic: Topic = "ai_news"

        async def fetch(self) -> list[Item]:
            return [_stub_item(source=name_value)]

    return _Stub


def test_root_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "collect" in result.stdout
    assert "render" in result.stdout
    assert "build" in result.stdout


def test_collect_all(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        pipeline_mod,
        "COLLECTORS",
        {"a": _stub_collector_cls("a"), "b": _stub_collector_cls("b")},
    )
    data_dir = tmp_path / "data"
    result = runner.invoke(app, ["collect", "--data-dir", str(data_dir)])
    assert result.exit_code == 0, result.stdout
    assert (data_dir / "a.json").exists()
    assert (data_dir / "b.json").exists()
    assert "a: 1" in result.stdout
    assert "b: 1" in result.stdout


def test_collect_single_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        pipeline_mod,
        "COLLECTORS",
        {"github_ai": _stub_collector_cls("github_ai"), "ai_news": _stub_collector_cls("ai_news")},
    )
    data_dir = tmp_path / "data"
    result = runner.invoke(app, ["collect", "--source", "github_ai", "--data-dir", str(data_dir)])
    assert result.exit_code == 0, result.stdout
    assert (data_dir / "github_ai.json").exists()
    assert not (data_dir / "ai_news.json").exists()


def test_collect_unknown_source_exits_nonzero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(pipeline_mod, "COLLECTORS", {"a": _stub_collector_cls("a")})
    result = runner.invoke(app, ["collect", "--source", "ghost", "--data-dir", str(tmp_path)])
    assert result.exit_code != 0


def test_render(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    dist_dir = tmp_path / "dist"
    result = runner.invoke(
        app, ["render", "--data-dir", str(data_dir), "--dist-dir", str(dist_dir)]
    )
    assert result.exit_code == 0
    assert (dist_dir / "index.html").exists()


def test_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(pipeline_mod, "COLLECTORS", {"a": _stub_collector_cls("a")})
    data_dir = tmp_path / "data"
    dist_dir = tmp_path / "dist"
    result = runner.invoke(app, ["build", "--data-dir", str(data_dir), "--dist-dir", str(dist_dir)])
    assert result.exit_code == 0, result.stdout
    assert (data_dir / "a.json").exists()
    assert (dist_dir / "index.html").exists()
