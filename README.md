# Trendscope

Static-site aggregator of trending GitHub repos and curated news across AI/ML,
e-commerce platforms, DevOps, and self-hosting. Collectors run on a schedule,
results are rendered to static HTML and served from nginx.

## Stack

- Python 3.12, managed with [uv](https://github.com/astral-sh/uv)
- Pydantic v2 + pydantic-settings
- httpx (async) with tenacity for retries
- Jinja2 + Tailwind (CDN) for the UI
- pytest + respx for tests
- Docker + nginx for serving; Ansible for deployment

## Quickstart

```bash
# 1. Install uv (one-time)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Sync dependencies into .venv
uv sync

# 3. Run the pipeline
uv run trendscope build
```

## Commands

| Command | Description |
|---|---|
| `uv run trendscope collect` | Run all registered collectors, write `data/<name>.json` per collector |
| `uv run trendscope collect --source <name>` | Run a single collector (`github_ai`, `github_general`, `ai_news`, `ecommerce_news`) |
| `uv run trendscope render` | Render templates from `data/` to `dist/` |
| `uv run trendscope build` | Full pipeline: collect all + render |
| `uv run pytest` | Run the test suite |
| `uv run ruff check . && uv run ruff format .` | Lint + format |
| `uv run mypy` | Type-check (strict mode) |
| `docker compose up nginx` | Local nginx on `:8080` (serves `dist/`) |
| `docker compose --profile pipeline run --rm runner` | Run the full pipeline in Docker |

A single collector failure does not stop the others — its slot in the summary reads `error: <msg>` and that source's JSON file is left untouched.

## Layout

```
src/trendscope/
  cli.py            Typer entrypoint (collect / render / build)
  config.py         Settings via pydantic-settings (TRENDSCOPE_* env vars)
  models.py         Item model + Topic literal
  pipeline.py       Topic registry; collect -> dedupe -> rank -> render
  collectors/       One file per source; all inherit collectors.base.Collector
  templates/        Jinja2 templates (base.html, topic.html)
data/               Collector output (JSON, gitignored)
dist/               Rendered static site (gitignored)
tests/              pytest + respx (no live HTTP)
```

## Pipeline

`collect -> normalize -> dedupe -> rank -> render`

Collectors produce `Item` objects and never touch HTML or `dist/`. The renderer
reads only from `data/`. JSON files in `data/` are the source of truth between
runs — there is no database.

## Adding a new topic

1. Add a collector in `src/trendscope/collectors/<name>.py` inheriting `Collector`.
2. Register the topic literal in `models.Item.topic`.
3. Add the topic to `pipeline.TOPICS` with a display name.
4. Add at least one unit test using `respx` to mock the upstream HTTP.

## Configuration

Environment variables (loaded from `.env` or the shell, prefix `TRENDSCOPE_`):

| Variable | Purpose |
|---|---|
| `TRENDSCOPE_GITHUB_TOKEN` | GitHub API token for higher rate limits |
| `TRENDSCOPE_DATA_DIR` | Override `data/` location |
| `TRENDSCOPE_DIST_DIR` | Override `dist/` location |
| `TRENDSCOPE_HTTP_TIMEOUT_SECONDS` | Per-request timeout (default 30s) |

See `CLAUDE.md` for project conventions and constraints.
