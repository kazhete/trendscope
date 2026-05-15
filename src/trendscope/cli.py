"""Trendscope CLI entrypoint."""

from __future__ import annotations

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
) -> None:
    """Run collectors and write results to ``data/*.json``."""
    if source:
        typer.echo(f"[stub] collect: would run collector '{source}'")
    else:
        typer.echo("[stub] collect: would run all registered collectors")


@app.command()
def render() -> None:
    """Render templates from ``data/`` to ``dist/``."""
    typer.echo("[stub] render: would render templates to dist/")


@app.command()
def build() -> None:
    """Full pipeline: collect + render."""
    typer.echo("[stub] build: would run collect then render")


if __name__ == "__main__":
    app()
