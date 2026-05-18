"""cosme parser tests."""
from __future__ import annotations

from pathlib import Path

from src.scrape.cosme import _parse_search_results, _parse_product_reviews

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "html" / "cosme"


class TestSearch:
    def test_parses_search_results(self):
        html = (FIXTURES / "search.html").read_text(encoding="utf-8")
        urls = _parse_search_results(html)
        assert len(urls) >= 1
        assert any("/products/" in u for u in urls)


class TestParse:
    def test_parses_product_reviews(self):
        html = (FIXTURES / "reviews.html").read_text(encoding="utf-8")
        posts = list(_parse_product_reviews(html, product_url="https://www.cosme.net/products/12345/"))
        assert len(posts) >= 1
        post = posts[0]
        assert post.source == "cosme"
        assert post.region == "JP"
        assert post.language == "ja"
        assert "SK-II" in post.title
        assert len(post.body) > 10
