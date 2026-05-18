"""discuss_hk parser tests — run against the three saved HTML fixtures.

Each test exercises the pure parser functions ``parse_search_results`` and
``parse_thread`` so they're offline and deterministic. A live integration
test (gated by ``SCRAPE_LIVE_TESTS=1``) is provided separately.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from src.scrape.discuss_hk import (
    DiscussHKScraper,
    parse_search_results,
    parse_thread,
)
from src.schemas.enums import SignalType, SourceCategory

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "html" / "discuss_hk"


# ---------------------------------------------------------------------------
# Search-results parser
# ---------------------------------------------------------------------------


def test_parse_search_results_returns_thread_ids() -> None:
    html = (FIXTURES / "search_mtr.html").read_text(encoding="utf-8")
    tids = parse_search_results(html)
    # The fixture captured 10 unique results.
    assert len(tids) >= 5
    # Known tids from the saved fixture.
    assert "20861215" in tids
    assert "28230494" in tids
    # tids are unique and look numeric.
    assert len(tids) == len(set(tids))
    assert all(t.isdigit() for t in tids)


def test_parse_search_results_handles_empty_or_garbage_html() -> None:
    assert parse_search_results("") == []
    assert parse_search_results("<html><body>no results</body></html>") == []


# ---------------------------------------------------------------------------
# Thread parser — small fixture (2 posts, 2009)
# ---------------------------------------------------------------------------


def test_parse_thread_small_ok() -> None:
    html = (FIXTURES / "thread_11221987.html").read_text(encoding="utf-8")
    post = parse_thread(html, thread_id="11221987")
    assert post.id == "discuss_hk:11221987"
    assert post.source == "discuss_hk"
    assert post.source_category is SourceCategory.FORUMS
    assert post.signal_type is SignalType.OPINION
    assert post.region == "HK"
    assert post.language == "zh-HK"
    assert post.title == "MTR一問..."
    assert "MTR" in post.body
    assert "今晚" in post.body
    # Counts pulled from "瀏覽: 1,221" + "回覆: 1"
    assert post.engagement_metrics["views"] == 1221
    assert post.engagement_metrics["replies"] == 1
    assert post.engagement_metrics["post_count_on_page"] == 2
    # Date: 2009-12-25 23:44 HKT
    assert post.posted_at.year == 2009
    assert post.posted_at.month == 12
    # Author hashed, not stored verbatim.
    assert post.author_hash
    assert "李奧曲佳" not in post.model_dump_json()
    assert post.raw_metadata["thread_id"] == "11221987"
    assert post.raw_metadata["post_number"] == "#1"


def test_parse_thread_large_ok() -> None:
    """A 13-post thread from 2019 — counts and date both larger."""
    html = (FIXTURES / "thread_28230494.html").read_text(encoding="utf-8")
    post = parse_thread(html, thread_id="28230494")
    assert post.title.startswith("MTR")
    assert post.engagement_metrics["views"] == 8515
    assert post.engagement_metrics["replies"] == 12
    assert post.engagement_metrics["post_count_on_page"] == 13
    assert post.posted_at.year == 2019


def test_parse_thread_url_is_canonical() -> None:
    html = (FIXTURES / "thread_11221987.html").read_text(encoding="utf-8")
    post = parse_thread(html, thread_id="11221987")
    assert str(post.url) == "https://www.discuss.com.hk/viewthread.php?tid=11221987"


def test_parse_thread_uses_provided_url_override() -> None:
    html = (FIXTURES / "thread_11221987.html").read_text(encoding="utf-8")
    post = parse_thread(html, thread_id="11221987", url="https://example/x")
    assert str(post.url) == "https://example/x"


def test_parse_thread_raises_when_no_posts_found() -> None:
    from src.scrape.base.protocol import SourceError

    with pytest.raises(SourceError):
        parse_thread("<html><body>no posts</body></html>", thread_id="x")


# ---------------------------------------------------------------------------
# Scraper wiring — search() drives the parser pair via PoliteClient.get_html
# ---------------------------------------------------------------------------


def test_scraper_search_with_fake_client(monkeypatch) -> None:
    """End-to-end search() with a stub client that returns our fixtures."""
    search_html = (FIXTURES / "search_mtr.html").read_text(encoding="utf-8")
    thread_html = (FIXTURES / "thread_11221987.html").read_text(encoding="utf-8")
    thread2_html = (FIXTURES / "thread_28230494.html").read_text(encoding="utf-8")

    class _FakeClient:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def get_html(self, url: str) -> str:
            self.calls.append(url)
            if "search.php" in url:
                return search_html
            if "tid=11221987" in url:
                return thread_html
            if "tid=28230494" in url:
                return thread2_html
            # Other tids in the search results we don't have fixtures for —
            # raise so the scraper falls through to the next thread cleanly.
            from src.scrape.base.protocol import SourceError
            raise SourceError(f"no fixture for {url}")

        def close(self) -> None: ...

    fake = _FakeClient()
    monkeypatch.setenv("AUTHOR_HASH_SALT", "test")
    scraper = DiscussHKScraper(client=fake)
    posts = list(
        scraper.search("MTR", since=datetime(2000, 1, 1, tzinfo=__import__("datetime").timezone.utc), limit=10),
    )
    scraper.close()

    # Two threads matched our fixtures; the rest raised SourceError and were
    # skipped (verified by the logged warnings, not asserted here).
    assert len(posts) == 2
    assert {p.raw_metadata["thread_id"] for p in posts} == {"11221987", "28230494"}
    # Search hit first, then thread pages, in order.
    assert "search.php" in fake.calls[0]
