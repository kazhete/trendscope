# Trendscope

Static-site aggregator of trending GitHub repos and curated news across AI/ML, e-commerce platforms, DevOps, and self-hosting. Updated on a schedule, served as static HTML from nginx.

## Stack

- Python 3.12, managed with `uv`
- Pydantic v2 for data models
- httpx (async) for HTTP, with `tenacity` for retries
- Jinja2 for templating; Tailwind CSS (CDN build) for styling
- pytest for tests
- Docker + nginx for serving
- Ansible for deployment (see `deploy/ansible/`)

## Commands

- `uv run trendscope collect` — run all collectors, write to `data/*.json`
- `uv run trendscope collect --source github_ai` — run one collector
- `uv run trendscope render` — render templates to `dist/`
- `uv run trendscope build` — full pipeline (collect + render)
- `uv run pytest` — run tests
- `uv run ruff check . && uv run ruff format .` — lint + format
- `docker compose up --build` — local nginx on :8080

## Architecture

The pipeline is `collect → normalize → dedupe → rank → render`. Every collector inherits from `collectors.base.Collector` and produces a list of `Item` (pydantic) objects. Collectors NEVER write HTML or touch `dist/`. Rendering reads JSON from `data/` only.

`Item` shape:
- `id: str` (deterministic hash of source+url)
- `source: str` ("github_ai", "hn", "odoo_blog", ...)
- `title: str`
- `url: HttpUrl`
- `summary: str | None`
- `score: float` (collector-normalized, 0–1)
- `published_at: datetime`
- `topic: Literal["ai_repos", "general_repos", "ai_news", "ecommerce", "odoo_apps", ...]`
- `meta: dict` (source-specific extras: stars, language, etc.)

## Conventions

- All I/O is async. No `requests` library — use `httpx.AsyncClient` from `collectors.base.client()`.
- API keys via env vars, loaded in `config.py` with pydantic-settings. Never hardcode.
- Each collector handles its own rate limiting via `tenacity` decorators on `base`.
- Tests use `respx` to mock HTTP — no live calls in CI.
- Type hints everywhere. `mypy --strict` should pass.
- Public functions get docstrings; private helpers (`_foo`) don't need them.
- Templates extend `base.html`. Topic pages use `topic.html` with a `{% block content %}` override.

## Don't

- Don't add a database. JSON files in `data/` are the source of truth between runs.
- Don't add a runtime backend (FastAPI, Flask). The site is static.
- Don't scrape GitHub HTML — use the Search API. The only HTML scrape is `apps.odoo.com` (it has no API).
- Don't commit anything in `data/` or `dist/`.

## Adding a new topic

1. Add a collector in `src/trendscope/collectors/<name>.py` inheriting `Collector`.
2. Register the topic literal in `models.Item.topic`.
3. Add the topic to `pipeline.TOPICS` with display name and icon.
4. Templates auto-pick it up via `topics` in context.
5. Add at least one unit test with `respx`.
