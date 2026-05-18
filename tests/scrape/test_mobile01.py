"""mobile01 parser tests — offline against saved HTML fixtures."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.scrape.mobile01 import _parse_search_results, _parse_thread

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "html" / "mobile01"


class TestSearch:

    def test_parses_search_results(self):
        html = (FIXTURES / "search.html").read_text(encoding="utf-8")
        urls = _parse_search_results(html)
        assert len(urls) >= 1
        assert any("topicdetail" in u for u in urls)


class TestParse:

    def test_parses_thread(self):
        html = (FIXTURES / "thread.html").read_text(encoding="utf-8")
        post = _parse_thread(html, url="https://www.mobile01.com/topicdetail.php?f=383&t=1234567")
        assert post is not None
        assert post.source == "mobile01"
        assert post.region == "TW"
        assert "iPhone" in post.title
        assert len(post.body) > 10

