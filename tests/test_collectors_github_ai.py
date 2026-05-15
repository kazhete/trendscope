"""Tests for collectors.github_ai."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx

from trendscope.collectors import github_ai as gh
from trendscope.collectors.github_ai import (
    DEFAULT_AI_TOPICS,
    GITHUB_SEARCH_URL,
    GitHubAICollector,
)


def _repo(
    full_name: str,
    *,
    stars: int,
    created: str = "2026-05-10T12:00:00Z",
    description: str | None = None,
    language: str = "Python",
    topics: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": abs(hash(full_name)) % 10_000_000,
        "name": full_name.split("/")[-1],
        "full_name": full_name,
        "html_url": f"https://github.com/{full_name}",
        "description": description,
        "stargazers_count": stars,
        "forks_count": stars // 10,
        "language": language,
        "topics": topics or ["machine-learning"],
        "created_at": created,
        "pushed_at": "2026-05-12T00:00:00Z",
        "owner": {"login": full_name.split("/")[0]},
    }


def _ok(items: list[dict[str, Any]]) -> httpx.Response:
    return httpx.Response(
        200,
        json={"total_count": len(items), "incomplete_results": False, "items": items},
    )


# ---------- construction / validation ----------


def test_invalid_period_raises():
    with pytest.raises(ValueError, match="unknown period"):
        GitHubAICollector(period="year")  # type: ignore[arg-type]


def test_per_page_out_of_range_raises():
    with pytest.raises(ValueError, match="per_page"):
        GitHubAICollector(per_page=0)
    with pytest.raises(ValueError, match="per_page"):
        GitHubAICollector(per_page=101)


def test_defaults():
    c = GitHubAICollector()
    assert c.period == "week"
    assert c.topics == DEFAULT_AI_TOPICS
    assert c.per_page == 30
    assert c.name == "github_ai"
    assert c.topic == "ai_repos"


# ---------- query construction ----------


def test_build_query_period_day_uses_one_day_cutoff():
    c = GitHubAICollector(period="day")
    now = datetime(2026, 5, 15, tzinfo=UTC)
    q = c._build_query(now=now)
    assert "created:>2026-05-14" in q


def test_build_query_period_week_uses_seven_day_cutoff():
    c = GitHubAICollector(period="week")
    now = datetime(2026, 5, 15, tzinfo=UTC)
    assert "created:>2026-05-08" in c._build_query(now=now)


def test_build_query_period_month_uses_thirty_day_cutoff():
    c = GitHubAICollector(period="month")
    now = datetime(2026, 5, 15, tzinfo=UTC)
    assert "created:>2026-04-15" in c._build_query(now=now)


def test_build_query_ors_topics():
    c = GitHubAICollector(topics=["llm", "generative-ai"])
    q = c._build_query(now=datetime(2026, 5, 15, tzinfo=UTC))
    assert "topic:llm" in q
    assert "topic:generative-ai" in q
    assert " OR " in q


def test_build_query_includes_min_stars():
    c = GitHubAICollector(min_stars=42)
    assert "stars:>=42" in c._build_query(now=datetime(2026, 5, 15, tzinfo=UTC))


# ---------- HTTP integration ----------


@respx.mock
async def test_fetch_returns_normalized_items():
    repos = [
        _repo("alice/llm-thing", stars=1000, description="cool"),
        _repo("bob/ml-thing", stars=100, description=None),
    ]
    route = respx.get(GITHUB_SEARCH_URL).mock(return_value=_ok(repos))

    items = await GitHubAICollector(period="week").fetch()

    assert route.called
    assert {i.title for i in items} == {"alice/llm-thing", "bob/ml-thing"}

    top = next(i for i in items if i.title == "alice/llm-thing")
    assert top.source == "github_ai"
    assert top.topic == "ai_repos"
    assert str(top.url) == "https://github.com/alice/llm-thing"
    assert top.score == pytest.approx(1.0)
    assert top.summary == "cool"
    assert top.meta["stars"] == 1000
    assert top.meta["language"] == "Python"
    assert top.meta["owner"] == "alice"

    weaker = next(i for i in items if i.title == "bob/ml-thing")
    assert 0 < weaker.score < 1


@respx.mock
async def test_fetch_handles_empty_results():
    respx.get(GITHUB_SEARCH_URL).mock(return_value=_ok([]))
    assert await GitHubAICollector().fetch() == []


@respx.mock
async def test_sort_order_and_per_page_params():
    route = respx.get(GITHUB_SEARCH_URL).mock(return_value=_ok([]))
    await GitHubAICollector(per_page=42).fetch()
    params = route.calls.last.request.url.params
    assert params["sort"] == "stars"
    assert params["order"] == "desc"
    assert params["per_page"] == "42"


@respx.mock
async def test_authorization_header_sent_when_token_set(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(gh.settings, "github_token", "tok_abc")
    route = respx.get(GITHUB_SEARCH_URL).mock(return_value=_ok([]))
    await GitHubAICollector().fetch()
    assert route.calls.last.request.headers["authorization"] == "Bearer tok_abc"


@respx.mock
async def test_no_authorization_header_when_token_missing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(gh.settings, "github_token", None)
    route = respx.get(GITHUB_SEARCH_URL).mock(return_value=_ok([]))
    await GitHubAICollector().fetch()
    assert "authorization" not in route.calls.last.request.headers


@respx.mock
async def test_api_version_and_accept_headers():
    route = respx.get(GITHUB_SEARCH_URL).mock(return_value=_ok([]))
    await GitHubAICollector().fetch()
    headers = route.calls.last.request.headers
    assert headers["accept"] == "application/vnd.github+json"
    assert headers["x-github-api-version"] == "2022-11-28"


@respx.mock
async def test_query_param_carries_topic_and_period(monkeypatch: pytest.MonkeyPatch):
    route = respx.get(GITHUB_SEARCH_URL).mock(return_value=_ok([]))
    await GitHubAICollector(period="day", topics=["llm"]).fetch()
    q = route.calls.last.request.url.params["q"]
    assert "topic:llm" in q
    assert "created:>" in q
    assert "stars:>=5" in q


@respx.mock
async def test_score_is_log_normalized_against_top_result():
    repos = [
        _repo("a/top", stars=10_000),
        _repo("b/mid", stars=1_000),
        _repo("c/low", stars=10),
    ]
    respx.get(GITHUB_SEARCH_URL).mock(return_value=_ok(repos))
    items = {i.title: i for i in await GitHubAICollector().fetch()}
    assert items["a/top"].score == pytest.approx(1.0)
    assert 0.5 < items["b/mid"].score < 1.0
    assert 0 < items["c/low"].score < items["b/mid"].score
