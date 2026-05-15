"""Trendscope CLI entrypoint."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

app = typer.Typer(
    help="Trendscope: collect trending repos and news, then render a static site.",
    no_args_is_help=True,
    add_completion=False,
)


@app.command()
def collect(
    source: str | None = typer.Option(
        None,
        "--source",
        "-s",
        help="Run a single collector by name (e.g. 'github_ai'). Omit to run all.",
    ),
    data_dir: Path | None = typer.Option(
        None, "--data-dir", help="Override data directory (default: settings.data_dir)."
    ),
) -> None:
    """Run collectors and write results to ``data/<name>.json``."""
    from trendscope.pipeline import run_collectors

    summary = asyncio.run(run_collectors(source=source, data_dir=data_dir))
    for name, info in summary.items():
        typer.echo(f"  {name}: {info}")


@app.command()
def render(
    data_dir: Path | None = typer.Option(
        None, "--data-dir", help="Override data directory (default: settings.data_dir)."
    ),
    dist_dir: Path | None = typer.Option(
        None, "--dist-dir", help="Override dist directory (default: settings.dist_dir)."
    ),
) -> None:
    """Render templates from ``data/`` to ``dist/``."""
    from trendscope.render import render_site

    out = render_site(data_dir=data_dir, dist_dir=dist_dir)
    typer.echo(f"Rendered site to {out}")


@app.command()
def build(
    data_dir: Path | None = typer.Option(
        None, "--data-dir", help="Override data directory (default: settings.data_dir)."
    ),
    dist_dir: Path | None = typer.Option(
        None, "--dist-dir", help="Override dist directory (default: settings.dist_dir)."
    ),
) -> None:
    """Full pipeline: collect all sources + render."""
    from trendscope.pipeline import run_collectors
    from trendscope.render import render_site

    summary = asyncio.run(run_collectors(data_dir=data_dir))
    for name, info in summary.items():
        typer.echo(f"  {name}: {info}")
    out = render_site(data_dir=data_dir, dist_dir=dist_dir)
    typer.echo(f"Rendered site to {out}")


if __name__ == "__main__":
    app()
