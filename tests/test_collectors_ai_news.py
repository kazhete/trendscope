"""Tests for collectors.ai_news (HN Algolia + RSS aggregator)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx

from trendscope.collectors import ai_news as mod
from trendscope.collectors.ai_news import (
    HN_SEARCH_URL,
    AINewsCollector,
    _clean_summary,
    _feed_source_key,
)

FIXED_NOW = datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _freeze_now(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(mod, "_now", lambda: FIXED_NOW)


# ---------- helpers ----------


def _hn_hit(
    *,
    object_id: str,
    title: str,
    url: str | None,
    points: int,
    created: str = "2026-05-12T10:00:00Z",
    num_comments: int = 0,
    author: str = "alice",
) -> dict[str, Any]:
    return {
        "objectID": object_id,
        "title": title,
        "url": url,
        "points": points,
        "num_comments": num_comments,
        "author": author,
        "created_at": created,
        "story_text": None,
    }


def _hn_ok(hits: list[dict[str, Any]]) -> httpx.Response:
    return httpx.Response(200, json={"hits": hits, "nbHits": len(hits)})


def _rss_xml(items: list[dict[str, str]], channel_title: str = "Cool AI Blog") -> bytes:
    item_xml = "".join(
        f"""
        <item>
            <title>{i["title"]}</title>
            <link>{i["link"]}</link>
            <description>{i.get("description", "")}</description>
            <pubDate>{i["pub_date"]}</pubDate>
        </item>"""
        for i in items
    )
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
    <channel>
        <title>{channel_title}</title>
        <link>https://example.com</link>
        <description>desc</description>{item_xml}
    </channel>
</rss>"""
    return xml.encode("utf-8")


# ---------- construction / validation ----------


def test_invalid_period_raises():
    with pytest.raises(ValueError, match="unknown period"):
        AINewsCollector(period="year")  # type: ignore[arg-type]


def test_defaults_pull_rss_feeds_from_settings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        mod.settings,
        "ai_news_rss_feeds",
        ["https://blog.example.com/feed", "https://other.example.com/atom"],
    )
    c = AINewsCollector()
    assert c.rss_feeds == (
        "https://blog.example.com/feed",
        "https://other.example.com/atom",
    )
    assert c.period == "week"
    assert c.name == "ai_news"
    assert c.topic == "ai_news"


def test_constructor_overrides_settings_rss(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(mod.settings, "ai_news_rss_feeds", ["https://ignored.example/feed"])
    c = AINewsCollector(rss_feeds=["https://override.example/feed"])
    assert c.rss_feeds == ("https://override.example/feed",)


# ---------- HN integration ----------


@respx.mock
async def test_hn_filters_by_keyword_in_title():
    hits = [
        _hn_hit(
            object_id="1", title="Anthropic releases Claude", url="https://a.example/x", points=400
        ),
        _hn_hit(
            object_id="2", title="Best coffee maker review", url="https://c.example/x", points=300
        ),
        _hn_hit(object_id="3", title="A new LLM benchmark", url="https://b.example/x", points=200),
    ]
    respx.get(HN_SEARCH_URL).mock(return_value=_hn_ok(hits))

    items = await AINewsCollector(rss_feeds=[]).fetch()
    titles = {i.title for i in items}
    assert "Anthropic releases Claude" in titles
    assert "A new LLM benchmark" in titles
    assert "Best coffee maker review" not in titles


@respx.mock
async def test_hn_request_uses_min_points_and_period_cutoff():
    route = respx.get(HN_SEARCH_URL).mock(return_value=_hn_ok([]))
    await AINewsCollector(period="day", hn_min_points=120, rss_feeds=[]).fetch()
    params = route.calls.last.request.url.params
    nf = params["numericFilters"]
    assert "points>=120" in nf
    assert "created_at_i>" in nf
    # cutoff is now - 1 day = 2026-05-14 12:00 UTC = unix 1779278400
    cutoff_ts = int(datetime(2026, 5, 14, 12, 0, 0, tzinfo=UTC).timestamp())
    assert f"created_at_i>{cutoff_ts}" in nf
    assert params["tags"] == "story"
    assert params["hitsPerPage"] == "50"


@respx.mock
async def test_hn_item_normalization_and_score():
    hits = [
        _hn_hit(
            object_id="100", title="GPT-5 details", url="https://news.example/gpt5", points=1000
        ),
        _hn_hit(object_id="101", title="LLM tips", url=None, points=100),
    ]
    respx.get(HN_SEARCH_URL).mock(return_value=_hn_ok(hits))

    items = await AINewsCollector(rss_feeds=[]).fetch()
    by_title = {i.title: i for i in items}

    top = by_title["GPT-5 details"]
    assert top.source == "hn"
    assert top.topic == "ai_news"
    assert str(top.url) == "https://news.example/gpt5"
    assert top.score == pytest.approx(1.0)
    assert top.meta["points"] == 1000
    assert top.meta["hn_url"] == "https://news.ycombinator.com/item?id=100"

    no_url = by_title["LLM tips"]
    assert str(no_url.url) == "https://news.ycombinator.com/item?id=101"
    assert 0 < no_url.score < 1


@respx.mock
async def test_hn_no_matching_hits_returns_empty():
    hits = [
        _hn_hit(
            object_id="1", title="Pottery wheel maintenance", url="https://p.example", points=50
        )
    ]
    respx.get(HN_SEARCH_URL).mock(return_value=_hn_ok(hits))
    assert await AINewsCollector(rss_feeds=[]).fetch() == []


# ---------- RSS integration ----------


@respx.mock
async def test_rss_returns_items_within_period():
    feed_url = "https://blog.example.com/feed"
    xml = _rss_xml(
        [
            {
                "title": "New LLM model",
                "link": "https://blog.example.com/p1",
                "description": "About <b>AI</b> &amp; ML",
                "pub_date": "Tue, 12 May 2026 10:00:00 GMT",
            },
            {
                "title": "Ancient post",
                "link": "https://blog.example.com/p0",
                "description": "old",
                "pub_date": "Mon, 01 Jan 2024 10:00:00 GMT",
            },
        ]
    )
    respx.get(HN_SEARCH_URL).mock(return_value=_hn_ok([]))
    respx.get(feed_url).mock(return_value=httpx.Response(200, content=xml))

    items = await AINewsCollector(rss_feeds=[feed_url]).fetch()

    assert len(items) == 1
    item = items[0]
    assert item.title == "New LLM model"
    assert str(item.url) == "https://blog.example.com/p1"
    assert item.source == "rss:blog.example.com"
    assert item.topic == "ai_news"
    assert item.summary == "About AI & ML"
    assert item.meta["feed_title"] == "Cool AI Blog"
    assert item.meta["feed_url"] == feed_url
    assert 0.05 <= item.score <= 1.0


@respx.mock
async def test_rss_skips_entries_without_link():
    feed_url = "https://blog.example.com/feed"
    # Manually crafted XML — one entry missing <link>
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>X</title>
  <link>https://x.example</link>
  <description>d</description>
  <item>
    <title>no link</title>
    <pubDate>Tue, 12 May 2026 10:00:00 GMT</pubDate>
  </item>
  <item>
    <title>with link</title>
    <link>https://blog.example.com/p1</link>
    <pubDate>Tue, 12 May 2026 10:00:00 GMT</pubDate>
  </item>
</channel></rss>"""
    respx.get(HN_SEARCH_URL).mock(return_value=_hn_ok([]))
    respx.get(feed_url).mock(return_value=httpx.Response(200, content=xml))

    items = await AINewsCollector(rss_feeds=[feed_url]).fetch()
    assert [i.title for i in items] == ["with link"]


@respx.mock
async def test_rss_per_feed_limit_applied():
    feed_url = "https://blog.example.com/feed"
    xml = _rss_xml(
        [
            {
                "title": f"post {n}",
                "link": f"https://blog.example.com/p{n}",
                "description": "x",
                "pub_date": "Tue, 12 May 2026 10:00:00 GMT",
            }
            for n in range(10)
        ]
    )
    respx.get(HN_SEARCH_URL).mock(return_value=_hn_ok([]))
    respx.get(feed_url).mock(return_value=httpx.Response(200, content=xml))

    items = await AINewsCollector(
        rss_feeds=[feed_url],
        rss_per_feed_limit=3,
    ).fetch()
    assert len(items) == 3


# ---------- combined ----------


@respx.mock
async def test_combines_hn_and_rss():
    feed_url = "https://blog.example.com/feed"
    respx.get(HN_SEARCH_URL).mock(
        return_value=_hn_ok(
            [_hn_hit(object_id="1", title="LLM weekly", url="https://hn.example/x", points=200)]
        )
    )
    respx.get(feed_url).mock(
        return_value=httpx.Response(
            200,
            content=_rss_xml(
                [
                    {
                        "title": "Blog post about AI",
                        "link": "https://blog.example.com/p1",
                        "description": "content",
                        "pub_date": "Tue, 12 May 2026 10:00:00 GMT",
                    }
                ]
            ),
        )
    )
    items = await AINewsCollector(rss_feeds=[feed_url]).fetch()
    sources = {i.source for i in items}
    assert sources == {"hn", "rss:blog.example.com"}
    assert len(items) == 2


@respx.mock
async def test_failed_rss_feed_does_not_break_others():
    good = "https://good.example/feed"
    bad = "https://bad.example/feed"
    respx.get(HN_SEARCH_URL).mock(
        return_value=_hn_ok(
            [_hn_hit(object_id="1", title="AI piece", url="https://hn.example/x", points=200)]
        )
    )
    respx.get(good).mock(
        return_value=httpx.Response(
            200,
            content=_rss_xml(
                [
                    {
                        "title": "Good entry on AI",
                        "link": "https://good.example/p1",
                        "description": "x",
                        "pub_date": "Tue, 12 May 2026 10:00:00 GMT",
                    }
                ]
            ),
        )
    )
    # 404 is non-retryable, so this fails immediately rather than burning attempts
    respx.get(bad).mock(return_value=httpx.Response(404))

    items = await AINewsCollector(rss_feeds=[good, bad]).fetch()
    sources = {i.source for i in items}
    assert sources == {"hn", "rss:good.example"}


# ---------- helper unit tests ----------


def test_feed_source_key_strips_www():
    assert _feed_source_key("https://www.example.com/feed") == "rss:example.com"


def test_feed_source_key_uses_hostname():
    assert _feed_source_key("https://blog.example.com/atom") == "rss:blog.example.com"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ("", None),
        ("<p>Hello <b>world</b></p>", "Hello world"),
        ("Tom &amp; Jerry", "Tom & Jerry"),
    ],
)
def test_clean_summary(raw: str | None, expected: str | None):
    assert _clean_summary(raw) == expected


def test_clean_summary_truncates_long_text():
    out = _clean_summary("x" * 1000)
    assert out is not None
    assert len(out) <= 500
    assert out.endswith("...")
