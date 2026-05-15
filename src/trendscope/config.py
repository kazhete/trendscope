"""Runtime configuration. API keys and paths loaded from environment variables."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings sourced from environment (prefix ``TRENDSCOPE_``) and ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="TRENDSCOPE_",
        extra="ignore",
    )

    github_token: str | None = None
    data_dir: Path = Path("data")
    dist_dir: Path = Path("dist")
    templates_dir: Path = Path("src/trendscope/templates")

    http_timeout_seconds: float = 30.0
    user_agent: str = "trendscope/0.1 (+https://github.com/)"


settings = Settings()
