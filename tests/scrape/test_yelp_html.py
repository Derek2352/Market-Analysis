"""yelp_html parser tests — offline against saved HTML fixtures."""
from __future__ import annotations

from pathlib import Path

from src.scrape.yelp_html import _parse_search_results, _parse_business_page, _parse_review_card

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "html" / "yelp_html"


class TestSearch:
    html_path = FIXTURES / "search.html"

    def test_parses_search_results(self):
        html = self.html_path.read_text(encoding="utf-8")
        urls = _parse_search_results(html)
        assert len(urls) >= 1

    def test_handles_empty(self):
        assert _parse_search_results("") == []


class TestParse:
    html_path = FIXTURES / "business.html"

    def test_parses_business_page(self):
        html = self.html_path.read_text(encoding="utf-8")
        posts = list(_parse_business_page(html, biz_url="https://www.yelp.com/biz/test"))
        assert len(posts) >= 1
        post = posts[0]
        assert post.source == "yelp_html"
        assert post.region == "US"
        assert len(post.body) > 10

    def test_review_card_rejects_short_body(self):
        from bs4 import BeautifulSoup
        card = BeautifulSoup("<div><p class='comment__'>ok</p></div>", "html.parser")
        result = _parse_review_card(card, biz_url="x", biz_name="Test")
        assert result is None
