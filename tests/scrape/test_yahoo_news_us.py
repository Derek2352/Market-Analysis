"""yahoo_news_us parser tests — offline against saved fixtures.

Yahoo News US uses the same caas-* HTML platform as TW. Selectors and
parser behaviour should mirror yahoo_news_tw; only domain / region /
language differ.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.scrape.yahoo_news_us import (
    _parse_article,
    _parse_search_results,
    doctor_check,
)
from src.schemas.enums import SignalType, SourceCategory

FIXTURES = (
    Path(__file__).resolve().parent.parent / "fixtures" / "html" / "yahoo_news_us"
)


@pytest.fixture(autouse=True)
def _hash_salt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTHOR_HASH_SALT", "test-yahoo-us-salt")


# ---------------------------------------------------------------------------
# Search-results parser
# ---------------------------------------------------------------------------


def test_parses_us_article_urls() -> None:
    html = (FIXTURES / "search.html").read_text(encoding="utf-8")
    urls = _parse_search_results(html)
    assert len(urls) >= 2
    # Category/landing URLs must NOT come through as articles.
    assert not any(u.endswith("news.yahoo.com/") for u in urls)
    assert not any(u.endswith("/category/world/") for u in urls)
    # Known article URLs from the fixture.
    assert any("mtr-fare-increase-2026" in u for u in urls)
    assert any("apple-pay-transit-asia" in u for u in urls)


def test_search_dedupes() -> None:
    urls = _parse_search_results(
        '<a href="https://news.yahoo.com/x-aaa.html">a</a>'
        '<a href="https://news.yahoo.com/x-aaa.html">again</a>',
    )
    assert urls == ["https://news.yahoo.com/x-aaa.html"]


# ---------------------------------------------------------------------------
# Article parser
# ---------------------------------------------------------------------------


def test_parses_us_article_core_fields() -> None:
    html = (FIXTURES / "article.html").read_text(encoding="utf-8")
    post = _parse_article(
        html, url="https://news.yahoo.com/mtr-fare-increase-2026-082300123.html",
    )
    assert post is not None
    assert post.source == "yahoo_news_us"
    assert post.source_category is SourceCategory.NEWS_COMMENTS
    assert post.signal_type is SignalType.OPINION
    assert post.region == "US"
    assert post.language == "en"
    assert post.title and post.title.startswith("MTR Fare Increase")
    assert "Hong Kong's MTR Corporation" in post.body
    assert "monthly-pass holders" in post.body
    assert post.engagement_metrics["comments"] == 312
    assert post.posted_at.year == 2025


def test_us_author_hashed_no_plaintext_leak() -> None:
    html = (FIXTURES / "article.html").read_text(encoding="utf-8")
    post = _parse_article(html, url="https://news.yahoo.com/x.html")
    assert post is not None
    serialized = post.model_dump_json()
    assert "Alex Tan" not in serialized
    assert len(post.author_hash) == 64


def test_us_returns_none_on_thin_html() -> None:
    assert _parse_article("<html><body></body></html>", url="x") is None
    assert _parse_article("<html><body><h1>hi</h1></body></html>", url="x") is None


# ---------------------------------------------------------------------------
# doctor_check
# ---------------------------------------------------------------------------


def test_doctor_check_search_pass() -> None:
    html = (FIXTURES / "search.html").read_text(encoding="utf-8")
    ok, detail = doctor_check("search.html", html, {})
    assert ok, detail
    assert "URLs" in detail


def test_doctor_check_article_pass() -> None:
    html = (FIXTURES / "article.html").read_text(encoding="utf-8")
    ok, detail = doctor_check("article.html", html, {})
    assert ok, detail
    assert "MTR" in detail or "title" in detail


def test_doctor_check_article_fail_on_empty() -> None:
    ok, detail = doctor_check("article.html", "<html></html>", {})
    assert not ok
    assert "None" in detail or "< 20" in detail
