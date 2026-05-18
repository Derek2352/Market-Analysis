"""yahoo_news_tw parser tests — offline against saved HTML fixtures."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.scrape.yahoo_news_tw import _parse_search_results, _parse_article

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "html" / "yahoo_news_tw"


class TestSearch:

    def test_parses_search_results(self):
        html = (FIXTURES / "search.html").read_text(encoding="utf-8")
        urls = _parse_search_results(html)
        assert len(urls) >= 1
        assert any("tw.news.yahoo.com" in u for u in urls)


class TestParse:

    def test_parses_article(self):
        html = (FIXTURES / "article.html").read_text(encoding="utf-8")
        post = _parse_article(html, url="https://tw.news.yahoo.com/test")
        assert post is not None
        assert post.source == "yahoo_news_tw"
        assert post.region == "TW"
        assert "iPhone" in post.title
        assert len(post.body) > 10

