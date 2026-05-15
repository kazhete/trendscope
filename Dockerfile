# syntax=docker/dockerfile:1
FROM python:3.12-slim

# Install uv from the official image (pinned to match dev environment)
COPY --from=ghcr.io/astral-sh/uv:0.11.14 /uv /usr/local/bin/uv

WORKDIR /app

# Copy lockfile + project metadata + source, then install production deps only.
# uv sync creates /app/.venv with the project installed editable.
COPY pyproject.toml uv.lock ./
COPY src/ ./src/
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH" \
    TRENDSCOPE_DATA_DIR=/data \
    TRENDSCOPE_DIST_DIR=/dist \
    PYTHONUNBUFFERED=1

VOLUME ["/data", "/dist"]

ENTRYPOINT ["trendscope"]
CMD ["build"]
