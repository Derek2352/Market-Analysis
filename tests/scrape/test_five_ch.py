"""five_ch parser tests — offline against saved HTML fixtures."""
from __future__ import annotations

from pathlib import Path

from src.scrape.five_ch import _parse_search_results, _parse_thread

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "html" / "five_ch"


class TestSearch:
    def test_parses_search_results(self):
        html = (FIXTURES / "search.html").read_text(encoding="utf-8")
        urls = _parse_search_results(html)
        assert len(urls) >= 2
        assert any("/test/read" in u for u in urls)

    def test_handles_empty_html(self):
        assert _parse_search_results("") == []


class TestParse:
    def test_parses_thread(self):
        html = (FIXTURES / "thread.html").read_text(encoding="utf-8")
        posts = list(_parse_thread(html, url="https://itest.5ch.net/test/read.cgi/iphone/1715990400/"))
        assert len(posts) >= 2
        post = posts[0]
        assert post.source == "five_ch"
        assert post.region == "JP"
        assert post.language == "ja"
        assert len(post.body) > 5
