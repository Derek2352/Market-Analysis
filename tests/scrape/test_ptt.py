"""ptt parser tests — offline against saved HTML fixtures."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.scrape.ptt import _parse_search_results, _parse_article

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "html" / "ptt"


class TestSearch:

    def test_parses_search_results(self):
        html = (FIXTURES / "search.html").read_text(encoding="utf-8")
        urls = _parse_search_results(html)
        assert len(urls) >= 2
        assert any("/bbs/" in u for u in urls)

    def test_handles_empty_html(self):
        assert _parse_search_results("") == []


class TestParse:

    def test_parses_article(self):
        html = (FIXTURES / "article.html").read_text(encoding="utf-8")
        post = _parse_article(html, url="https://www.ptt.cc/bbs/Gossiping/M.1715990400.A.123.html", board="Gossiping")
        assert post is not None
        assert post.source == "ptt"
        assert post.region == "TW"
        assert post.language == "zh-TW"
        assert "iPhone" in post.title
        assert len(post.body) > 20
        assert post.engagement_metrics["push"] >= 2
        assert post.engagement_metrics["boo"] >= 1

    def test_rejects_short_body(self):
        result = _parse_article("<html><body>short</body></html>", url="x", board="test")
        assert result is None

