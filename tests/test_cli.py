"""Smoke tests for the trendscope CLI."""

from __future__ import annotations

from typer.testing import CliRunner

from trendscope.cli import app

runner = CliRunner()


def test_root_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "collect" in result.stdout
    assert "render" in result.stdout
    assert "build" in result.stdout


def test_collect_all():
    result = runner.invoke(app, ["collect"])
    assert result.exit_code == 0
    assert "all registered collectors" in result.stdout


def test_collect_single_source():
    result = runner.invoke(app, ["collect", "--source", "github_ai"])
    assert result.exit_code == 0
    assert "github_ai" in result.stdout


def test_render():
    result = runner.invoke(app, ["render"])
    assert result.exit_code == 0


def test_build():
    result = runner.invoke(app, ["build"])
    assert result.exit_code == 0
