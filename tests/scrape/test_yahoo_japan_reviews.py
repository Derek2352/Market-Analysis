"""yahoo_japan_reviews parser tests."""
from __future__ import annotations

from pathlib import Path

from src.scrape.yahoo_japan_reviews import _parse_search_results, _parse_reviews

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "html" / "yahoo_japan_reviews"


class TestSearch:
    def test_parses_search_results(self):
        html = (FIXTURES / "search.html").read_text(encoding="utf-8")
        ids = _parse_search_results(html)
        assert "iphone16" in ids


class TestParse:
    def test_parses_reviews(self):
        html = (FIXTURES / "reviews.html").read_text(encoding="utf-8")
        posts = list(_parse_reviews(html, product_id="iphone16"))
        assert len(posts) >= 1
        post = posts[0]
        assert post.source == "yahoo_japan_reviews"
        assert post.region == "JP"
        assert post.language == "ja"
        assert len(post.body) > 10
        assert post.engagement_metrics["rating"] == 5
