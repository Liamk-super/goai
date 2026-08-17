from __future__ import annotations

from datetime import UTC, datetime

from launchscope_api.modules.evidence.source_locator import (
    browser_source_locator,
    canonicalize_url,
    internal_material_source_locator,
    search_source_locators,
)


def test_browser_source_stores_human_locator_and_screenshot_hash() -> None:
    fetched_at = datetime(2026, 8, 13, tzinfo=UTC)
    locator = browser_source_locator(
        final_url="HTTPS://Example.COM:443/report/?utm_source=feed&b=2&a=1#summary",
        title="Market Report",
        fetched_at=fetched_at,
        region="HK",
        screenshot_sha256="a" * 64,
    )
    assert locator.canonical_url == "https://example.com/report?a=1&b=2"
    assert locator.title == "Market Report"
    assert locator.fetched_at == fetched_at
    assert locator.region == "HK"
    assert locator.screenshot_sha256 == "a" * 64
    assert locator.independence_group == "example.com:market-report"


def test_each_search_result_gets_a_locator_and_syndications_count_once() -> None:
    fetched_at = datetime(2026, 8, 13, tzinfo=UTC)
    locators = search_source_locators(
        (
            {
                "url": "https://news.example.com/reports/retention?utm_campaign=a",
                "title": "Retention Benchmarks 2026",
                "publisher": "Example Institute",
                "content": "First syndication",
                "published_date": "2026-01-01T00:00:00Z",
            },
            {
                "url": "https://mirror.example.net/item/42",
                "title": "Retention Benchmarks 2026",
                "publisher": "Example Institute",
                "content": "Second syndication",
                "published_date": "2026-01-01T00:00:00Z",
            },
        ),
        fetched_at=fetched_at,
        region="HK",
    )
    assert len(locators) == 2
    assert len({locator.canonical_url for locator in locators}) == 2
    assert {locator.independence_group for locator in locators} == {
        "example-institute:retention-benchmarks-2026"
    }
    assert all(locator.content_sha256 for locator in locators)


def test_internal_material_has_display_name_and_locator_without_fake_url() -> None:
    locator = internal_material_source_locator(
        display_name="访谈纪要.pdf",
        fetched_at=datetime(2026, 8, 13, tzinfo=UTC),
        content_sha256="b" * 64,
        locator={"page": 7, "section": "续费"},
    )
    assert locator.source_kind == "INTERNAL_MATERIAL"
    assert locator.canonical_url is None
    assert locator.title == "访谈纪要.pdf"
    assert locator.locator == {"page": 7, "section": "续费"}


def test_url_canonicalization_removes_tracking_fragment_and_default_port() -> None:
    assert canonicalize_url("https://EXAMPLE.com:443/a/?z=2&utm_medium=x&z=1#frag") == (
        "https://example.com/a?z=1&z=2"
    )
