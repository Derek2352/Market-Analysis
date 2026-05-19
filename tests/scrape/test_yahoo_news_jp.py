"""yahoo_news_jp parser tests — offline against saved fixtures.

news.yahoo.co.jp runs a separate platform from Yahoo's caas-* CMS, so
the parser uses different selectors (.article_body, .source, .commentCount).
These tests pin the JP-specific selectors against a synthetic but
representative fixture; the live integration test surfaces real drift.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.scrape.yahoo_news_jp import (
    _parse_article,
    _parse_search_results,
    doctor_check,
)
from src.schemas.enums import SignalType, SourceCategory

FIXTURES = (
    Path(__file__).resolve().parent.parent / "fixtures" / "html" / "yahoo_news_jp"
)


@pytest.fixture(autouse=True)
def _hash_salt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTHOR_HASH_SALT", "test-yahoo-jp-salt")


# ---------------------------------------------------------------------------
# Search-results parser
# ---------------------------------------------------------------------------


def test_parses_jp_article_urls() -> None:
    html = (FIXTURES / "search.html").read_text(encoding="utf-8")
    urls = _parse_search_results(html)
    assert len(urls) >= 2
    # All matches must be either /articles/<hash> or /pickup/<id>.
    for u in urls:
        assert "/articles/" in u or "/pickup/" in u
    # Category and root URLs must NOT appear.
    assert not any(u.endswith("/categories/domestic") for u in urls)
    assert not any(u == "https://news.yahoo.co.jp/" for u in urls)


def test_search_normalizes_relative_urls_to_absolute() -> None:
    """Relative links should be promoted to absolute https://news.yahoo.co.jp/ URLs."""
    html = (FIXTURES / "search.html").read_text(encoding="utf-8")
    urls = _parse_search_results(html)
    # The fixture has one relative href; verify it surfaced as absolute.
    assert any(u.startswith("https://news.yahoo.co.jp/articles/abc123") for u in urls)


# ---------------------------------------------------------------------------
# Article parser
# ---------------------------------------------------------------------------


def test_parses_jp_article_core_fields() -> None:
    html = (FIXTURES / "article.html").read_text(encoding="utf-8")
    post = _parse_article(
        html, url="https://news.yahoo.co.jp/articles/abc123def456",
    )
    assert post is not None
    assert post.source == "yahoo_news_jp"
    assert post.source_category is SourceCategory.NEWS_COMMENTS
    assert post.signal_type is SignalType.OPINION
    assert post.region == "JP"
    assert post.language == "ja"
    assert post.title and post.title.startswith("JR東日本")
    assert "山手線" in post.body
    assert post.posted_at.year == 2025


def test_parses_jp_comment_count_with_japanese_label() -> None:
    """commentCount on JP looks like 'コメント 547件' — count is the digits."""
    html = (FIXTURES / "article.html").read_text(encoding="utf-8")
    post = _parse_article(html, url="https://news.yahoo.co.jp/articles/x")
    assert post is not None
    assert post.engagement_metrics["comments"] == 547


def test_jp_language_detected() -> None:
    html = (FIXTURES / "article.html").read_text(encoding="utf-8")
    post = _parse_article(html, url="https://news.yahoo.co.jp/articles/x")
    assert post is not None
    # py3langid returns 'ja' for Japanese text.
    assert post.language_detected == "ja"


def test_jp_author_field_uses_publication_credit() -> None:
    """Japanese news commonly credits the publisher (e.g. '読売新聞オンライン')
    in .source rather than an individual byline."""
    html = (FIXTURES / "article.html").read_text(encoding="utf-8")
    post = _parse_article(html, url="https://news.yahoo.co.jp/articles/x")
    assert post is not None
    assert post.author_hash != ""
    serialized = post.model_dump_json()
    # Publisher name should be hashed, not leaked.
    assert "読売新聞オンライン" not in serialized


def test_jp_returns_none_on_thin_html() -> None:
    assert _parse_article("<html></html>", url="x") is None


# ---------------------------------------------------------------------------
# doctor_check
# ---------------------------------------------------------------------------


def test_doctor_check_search_pass() -> None:
    html = (FIXTURES / "search.html").read_text(encoding="utf-8")
    ok, detail = doctor_check("search.html", html, {})
    assert ok, detail


def test_doctor_check_article_pass() -> None:
    html = (FIXTURES / "article.html").read_text(encoding="utf-8")
    ok, detail = doctor_check("article.html", html, {})
    assert ok, detail
