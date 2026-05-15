"""Tests for collectors.ecommerce_news (RSS + Odoo Apps Store scrape)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx

from trendscope.collectors import ecommerce_news as mod
from trendscope.collectors.ecommerce_news import (
    ODOO_APPS_BROWSE_URL,
    EcommerceNewsCollector,
)

FIXED_NOW = datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _freeze_now(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(mod, "_now", lambda: FIXED_NOW)


# ---------- helpers ----------


def _rss_xml(items: list[dict[str, str]], channel_title: str = "Ecom Blog") -> bytes:
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


def _odoo_apps_html(cards: list[dict[str, Any]]) -> str:
    """Mirror the real apps.odoo.com markup: each card wraps an <a href>
    that contains a summary <p>, an <h5> title, and an author/price row."""
    card_blocks = []
    for c in cards:
        summary_block = (
            f'<p class="loempia_panel_summary">{c["summary"]}</p>' if c.get("summary") else ""
        )
        author_block = (
            f'<div class="loempia_panel_author"><b>{c["author"]}</b></div>'
            if c.get("author")
            else ""
        )
        price_block = (
            f'<div class="loempia_panel_price"><b>{c["price"]}</b></div>' if c.get("price") else ""
        )
        card_blocks.append(f"""
        <div class="loempia_app_entry loempia_app_card">
            <a href="{c["href"]}">
                {summary_block}
                <h5><b>{c["title"]}</b></h5>
                {author_block}
                {price_block}
            </a>
        </div>
        """)
    return "<html><body>" + "".join(card_blocks) + "</body></html>"


# ---------- construction / validation ----------


def test_invalid_period_raises():
    with pytest.raises(ValueError, match="unknown period"):
        EcommerceNewsCollector(period="year")  # type: ignore[arg-type]


def test_defaults_pull_rss_feeds_from_settings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        mod.settings,
        "ecommerce_rss_feeds",
        ["https://blog.odoo.example/feed", "https://shopify.example/feed"],
    )
    c = EcommerceNewsCollector()
    assert c.rss_feeds == (
        "https://blog.odoo.example/feed",
        "https://shopify.example/feed",
    )
    assert c.period == "week"
    assert c.name == "ecommerce_news"
    assert c.topic == "ecommerce"
    assert c.scrape_odoo_apps is True


def test_constructor_overrides_settings_rss(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(mod.settings, "ecommerce_rss_feeds", ["https://ignored.example/feed"])
    c = EcommerceNewsCollector(rss_feeds=["https://override.example/feed"])
    assert c.rss_feeds == ("https://override.example/feed",)


# ---------- RSS integration ----------


@respx.mock
async def test_rss_items_emitted_with_ecommerce_topic():
    feed_url = "https://blog.odoo.example/feed"
    xml = _rss_xml(
        [
            {
                "title": "Odoo 19 release notes",
                "link": "https://blog.odoo.example/p1",
                "description": "We released <b>Odoo 19</b>",
                "pub_date": "Tue, 12 May 2026 10:00:00 GMT",
            },
            {
                "title": "Old post",
                "link": "https://blog.odoo.example/p0",
                "description": "old",
                "pub_date": "Mon, 01 Jan 2024 10:00:00 GMT",
            },
        ]
    )
    respx.get(feed_url).mock(return_value=httpx.Response(200, content=xml))

    items = await EcommerceNewsCollector(
        rss_feeds=[feed_url],
        scrape_odoo_apps=False,
    ).fetch()

    assert len(items) == 1
    item = items[0]
    assert item.title == "Odoo 19 release notes"
    assert item.source == "rss:blog.odoo.example"
    assert item.topic == "ecommerce"
    assert item.summary == "We released Odoo 19"
    assert item.meta["feed_title"] == "Ecom Blog"
    assert 0.05 <= item.score <= 1.0


@respx.mock
async def test_rss_per_feed_limit_applied():
    feed_url = "https://blog.odoo.example/feed"
    xml = _rss_xml(
        [
            {
                "title": f"post {n}",
                "link": f"https://blog.odoo.example/p{n}",
                "description": "x",
                "pub_date": "Tue, 12 May 2026 10:00:00 GMT",
            }
            for n in range(10)
        ]
    )
    respx.get(feed_url).mock(return_value=httpx.Response(200, content=xml))

    items = await EcommerceNewsCollector(
        rss_feeds=[feed_url],
        scrape_odoo_apps=False,
        rss_per_feed_limit=3,
    ).fetch()
    assert len(items) == 3


# ---------- Odoo Apps Store integration ----------


@respx.mock
async def test_odoo_apps_scrape_returns_items_with_odoo_apps_topic():
    html_body = _odoo_apps_html(
        [
            {
                "href": "/apps/modules/19.0/account_easy",
                "title": "Easy Accounting",
                "author": "Acme Inc",
                "price": "€49",
                "summary": "Simplify your accounting",
            },
            {
                "href": "/apps/modules/19.0/stock_helper",
                "title": "Stock Helper",
                "author": "Bob",
                "price": "Free",
                "summary": "Stock utilities",
            },
        ]
    )
    respx.get(ODOO_APPS_BROWSE_URL).mock(return_value=httpx.Response(200, text=html_body))

    items = await EcommerceNewsCollector(rss_feeds=[]).fetch()

    assert len(items) == 2
    by_title = {i.title: i for i in items}

    # Score is by list position: first card lands at 1.0.
    top = by_title["Easy Accounting"]
    assert top.source == "odoo_apps"
    assert top.topic == "odoo_apps"
    assert str(top.url) == "https://apps.odoo.com/apps/modules/19.0/account_easy"
    assert top.score == pytest.approx(1.0)
    assert top.summary == "Simplify your accounting"
    assert top.meta["author"] == "Acme Inc"
    assert top.meta["price"] == "€49"
    assert "downloads" not in top.meta

    lesser = by_title["Stock Helper"]
    assert lesser.score < top.score
    assert lesser.score >= 0.05


@respx.mock
async def test_odoo_apps_request_uses_newest_order():
    respx.get(ODOO_APPS_BROWSE_URL).mock(return_value=httpx.Response(200, text=_odoo_apps_html([])))
    await EcommerceNewsCollector(rss_feeds=[]).fetch()
    call = respx.calls.last
    assert call.request.url.params["order"] == "Newest"


@respx.mock
async def test_odoo_apps_empty_page_returns_no_items():
    respx.get(ODOO_APPS_BROWSE_URL).mock(
        return_value=httpx.Response(200, text="<html><body></body></html>")
    )
    items = await EcommerceNewsCollector(rss_feeds=[]).fetch()
    assert items == []


@respx.mock
async def test_odoo_apps_skips_cards_without_link_or_title():
    html_body = """<html><body>
        <div class="loempia_app_card"><a><h5>no href</h5></a></div>
        <div class="loempia_app_card"><a href="/apps/modules/19.0/empty"></a></div>
        <div class="loempia_app_card">
            <a href="/apps/modules/19.0/ok"><h5>OK Module</h5></a>
        </div>
    </body></html>"""
    respx.get(ODOO_APPS_BROWSE_URL).mock(return_value=httpx.Response(200, text=html_body))
    items = await EcommerceNewsCollector(rss_feeds=[]).fetch()
    assert [i.title for i in items] == ["OK Module"]


@respx.mock
async def test_apps_limit_caps_result_count():
    html_body = _odoo_apps_html(
        [{"href": f"/apps/modules/19.0/m{n}", "title": f"Module {n}"} for n in range(50)]
    )
    respx.get(ODOO_APPS_BROWSE_URL).mock(return_value=httpx.Response(200, text=html_body))
    items = await EcommerceNewsCollector(rss_feeds=[], apps_limit=5).fetch()
    assert len(items) == 5


@respx.mock
async def test_scrape_odoo_apps_false_skips_apps_request():
    feed_url = "https://blog.odoo.example/feed"
    respx.get(feed_url).mock(return_value=httpx.Response(200, content=_rss_xml([])))
    apps_route = respx.get(ODOO_APPS_BROWSE_URL).mock(
        return_value=httpx.Response(200, text=_odoo_apps_html([]))
    )

    await EcommerceNewsCollector(rss_feeds=[feed_url], scrape_odoo_apps=False).fetch()
    assert not apps_route.called


# ---------- combined ----------


@respx.mock
async def test_combines_rss_and_odoo_apps():
    feed_url = "https://blog.odoo.example/feed"
    respx.get(feed_url).mock(
        return_value=httpx.Response(
            200,
            content=_rss_xml(
                [
                    {
                        "title": "Odoo 19 launches",
                        "link": "https://blog.odoo.example/p1",
                        "description": "Hooray",
                        "pub_date": "Tue, 12 May 2026 10:00:00 GMT",
                    }
                ]
            ),
        )
    )
    respx.get(ODOO_APPS_BROWSE_URL).mock(
        return_value=httpx.Response(
            200,
            text=_odoo_apps_html([{"href": "/apps/modules/19.0/m1", "title": "Cool Module"}]),
        )
    )
    items = await EcommerceNewsCollector(rss_feeds=[feed_url]).fetch()
    sources = {i.source for i in items}
    topics = {i.topic for i in items}
    assert sources == {"rss:blog.odoo.example", "odoo_apps"}
    assert topics == {"ecommerce", "odoo_apps"}


@respx.mock
async def test_failed_source_does_not_break_others():
    feed_url = "https://blog.odoo.example/feed"
    respx.get(feed_url).mock(
        return_value=httpx.Response(
            200,
            content=_rss_xml(
                [
                    {
                        "title": "Good post",
                        "link": "https://blog.odoo.example/p1",
                        "description": "x",
                        "pub_date": "Tue, 12 May 2026 10:00:00 GMT",
                    }
                ]
            ),
        )
    )
    # 404 is non-retryable, fails immediately
    respx.get(ODOO_APPS_BROWSE_URL).mock(return_value=httpx.Response(404))

    items = await EcommerceNewsCollector(rss_feeds=[feed_url]).fetch()
    sources = {i.source for i in items}
    assert sources == {"rss:blog.odoo.example"}
