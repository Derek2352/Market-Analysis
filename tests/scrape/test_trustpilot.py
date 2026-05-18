"""trustpilot parser tests — offline against saved HTML fixtures."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.scrape.trustpilot import _parse_search_results, _parse_reviews

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "html" / "trustpilot"


class TestSearch:

    def test_parses_search_results(self):
        html = (FIXTURES / "search.html").read_text(encoding="utf-8")
        urls = _parse_search_results(html)
        assert len(urls) >= 1


class TestParse:

    def test_parses_reviews_from_jsonld(self):
        html = (FIXTURES / "reviews.html").read_text(encoding="utf-8")
        posts = list(_parse_reviews(html, company_url="https://www.trustpilot.com/review/www.apple.com"))
        assert len(posts) >= 1
        post = posts[0]
        assert post.source == "trustpilot"
        assert post.region == "US"
        assert post.engagement_metrics["rating"] == 5
        assert len(post.body) > 20

