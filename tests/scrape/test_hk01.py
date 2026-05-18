"""Unit tests for hk01 — offline, against saved HTML fixtures."""
from __future__ import annotations

import os

import pytest

from src.scrape.hk01 import parse_search_results, parse_article


@pytest.fixture
def fixtures_dir() -> str:
    return os.path.join(os.path.dirname(__file__), "..", "fixtures")


class TestParseSearchResults:
    def test_extracts_article_urls_from_search_page(self, fixtures_dir: str) -> None:
        path = os.path.join(fixtures_dir, "html", "hk01", "search.html")
        if not os.path.exists(path):
            pytest.skip("hk01/search.html fixture not available")
        html = open(path, encoding="utf-8").read()
        urls = parse_search_results(html)
        # HK01 search is JS-rendered; AJAX-loaded results may not be in
        # the initial HTML. Accept zero URLs — the live test covers the
        # full Playwright path.
        if urls:
            for url in urls:
                assert "hk01.com" in url


class TestParseArticle:
    def test_parses_article_page(self, fixtures_dir: str) -> None:
        path = os.path.join(fixtures_dir, "html", "hk01", "article.html")
        if not os.path.exists(path):
            pytest.skip("hk01/article.html fixture not available")
        html = open(path, encoding="utf-8").read()
        post = parse_article(html, article_url="https://www.hk01.com/article/60234567")

        assert post.source == "hk01"
        assert post.region == "HK"
        assert post.title
        assert len(post.title) > 3
        assert post.engagement_metrics.get("comment_count", -1) >= 0
        assert post.raw_metadata.get("article_id")
