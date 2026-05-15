"""Tests for collectors.github_general."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx

from trendscope.collectors import github_general as gh
from trendscope.collectors.github_general import (
    GITHUB_SEARCH_URL,
    GitHubGeneralCollector,
)


def _repo(
    full_name: str,
    *,
    stars: int,
    created: str = "2026-05-10T12:00:00Z",
    description: str | None = None,
    language: str = "Rust",
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
        "topics": topics or [],
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
        GitHubGeneralCollector(period="year")  # type: ignore[arg-type]


def test_per_page_out_of_range_raises():
    with pytest.raises(ValueError, match="per_page"):
        GitHubGeneralCollector(per_page=0)
    with pytest.raises(ValueError, match="per_page"):
        GitHubGeneralCollector(per_page=101)


def test_defaults():
    c = GitHubGeneralCollector()
    assert c.period == "week"
    assert c.language is None
    assert c.exclude_topics == ()
    assert c.per_page == 30
    assert c.min_stars == 50
    assert c.name == "github_general"
    assert c.topic == "general_repos"


# ---------- query construction ----------


def test_build_query_period_day_uses_one_day_cutoff():
    q = GitHubGeneralCollector(period="day")._build_query(now=datetime(2026, 5, 15, tzinfo=UTC))
    assert "created:>2026-05-14" in q


def test_build_query_period_week_uses_seven_day_cutoff():
    q = GitHubGeneralCollector(period="week")._build_query(now=datetime(2026, 5, 15, tzinfo=UTC))
    assert "created:>2026-05-08" in q


def test_build_query_period_month_uses_thirty_day_cutoff():
    q = GitHubGeneralCollector(period="month")._build_query(now=datetime(2026, 5, 15, tzinfo=UTC))
    assert "created:>2026-04-15" in q


def test_build_query_default_has_no_topic_or_language_clause():
    q = GitHubGeneralCollector()._build_query(now=datetime(2026, 5, 15, tzinfo=UTC))
    assert "topic:" not in q
    assert "language:" not in q


def test_build_query_includes_language_filter():
    q = GitHubGeneralCollector(language="rust")._build_query(now=datetime(2026, 5, 15, tzinfo=UTC))
    assert "language:rust" in q


def test_build_query_excludes_topics_with_minus_prefix():
    q = GitHubGeneralCollector(
        exclude_topics=["machine-learning", "llm"],
    )._build_query(now=datetime(2026, 5, 15, tzinfo=UTC))
    assert "-topic:machine-learning" in q
    assert "-topic:llm" in q


def test_build_query_default_min_stars_is_fifty():
    q = GitHubGeneralCollector()._build_query(now=datetime(2026, 5, 15, tzinfo=UTC))
    assert "stars:>=50" in q


def test_build_query_custom_min_stars():
    q = GitHubGeneralCollector(min_stars=500)._build_query(now=datetime(2026, 5, 15, tzinfo=UTC))
    assert "stars:>=500" in q


# ---------- HTTP integration ----------


@respx.mock
async def test_fetch_returns_normalized_items():
    repos = [
        _repo("alice/cool", stars=2000, description="neat", language="Rust"),
        _repo("bob/util", stars=200, language="Go"),
    ]
    route = respx.get(GITHUB_SEARCH_URL).mock(return_value=_ok(repos))

    items = await GitHubGeneralCollector().fetch()

    assert route.called
    assert {i.title for i in items} == {"alice/cool", "bob/util"}

    top = next(i for i in items if i.title == "alice/cool")
    assert top.source == "github_general"
    assert top.topic == "general_repos"
    assert str(top.url) == "https://github.com/alice/cool"
    assert top.score == pytest.approx(1.0)
    assert top.meta["stars"] == 2000
    assert top.meta["language"] == "Rust"
    assert top.summary == "neat"


@respx.mock
async def test_fetch_handles_empty_results():
    respx.get(GITHUB_SEARCH_URL).mock(return_value=_ok([]))
    assert await GitHubGeneralCollector().fetch() == []


@respx.mock
async def test_sort_order_and_per_page_params():
    route = respx.get(GITHUB_SEARCH_URL).mock(return_value=_ok([]))
    await GitHubGeneralCollector(per_page=75).fetch()
    params = route.calls.last.request.url.params
    assert params["sort"] == "stars"
    assert params["order"] == "desc"
    assert params["per_page"] == "75"


@respx.mock
async def test_authorization_header_sent_when_token_set(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(gh.settings, "github_token", "tok_xyz")
    route = respx.get(GITHUB_SEARCH_URL).mock(return_value=_ok([]))
    await GitHubGeneralCollector().fetch()
    assert route.calls.last.request.headers["authorization"] == "Bearer tok_xyz"


@respx.mock
async def test_no_authorization_header_when_token_missing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(gh.settings, "github_token", None)
    route = respx.get(GITHUB_SEARCH_URL).mock(return_value=_ok([]))
    await GitHubGeneralCollector().fetch()
    assert "authorization" not in route.calls.last.request.headers


@respx.mock
async def test_api_version_and_accept_headers():
    route = respx.get(GITHUB_SEARCH_URL).mock(return_value=_ok([]))
    await GitHubGeneralCollector().fetch()
    headers = route.calls.last.request.headers
    assert headers["accept"] == "application/vnd.github+json"
    assert headers["x-github-api-version"] == "2022-11-28"


@respx.mock
async def test_query_param_carries_language_and_exclusions():
    route = respx.get(GITHUB_SEARCH_URL).mock(return_value=_ok([]))
    await GitHubGeneralCollector(
        period="day",
        language="python",
        exclude_topics=["machine-learning"],
    ).fetch()
    q = route.calls.last.request.url.params["q"]
    assert "language:python" in q
    assert "-topic:machine-learning" in q
    assert "created:>" in q


@respx.mock
async def test_score_is_log_normalized_against_top_result():
    repos = [
        _repo("a/top", stars=10_000),
        _repo("b/mid", stars=1_000),
        _repo("c/low", stars=100),
    ]
    respx.get(GITHUB_SEARCH_URL).mock(return_value=_ok(repos))
    items = {i.title: i for i in await GitHubGeneralCollector().fetch()}
    assert items["a/top"].score == pytest.approx(1.0)
    assert 0.5 < items["b/mid"].score < 1.0
    assert 0 < items["c/low"].score < items["b/mid"].score
