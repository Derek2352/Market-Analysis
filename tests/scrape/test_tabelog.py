"""tabelog parser tests."""
from __future__ import annotations

import os
from pathlib import Path

from src.scrape.tabelog import _parse_search_results, _parse_reviews

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "html" / "tabelog"


class TestSearch:
    def test_parses_search_results(self):
        html = (FIXTURES / "search.html").read_text(encoding="utf-8")
        urls = _parse_search_results(html)
        assert len(urls) >= 1
        assert any("/rst/" in u for u in urls)


class TestParse:
    def test_parses_restaurant_reviews(self, monkeypatch):
        monkeypatch.setenv("AUTHOR_HASH_SALT", "test")
        html = (FIXTURES / "restaurant.html").read_text(encoding="utf-8")
        posts = list(_parse_reviews(html, restaurant_url="https://tabelog.com/rst/abc123/"))
        assert len(posts) >= 1
        post = posts[0]
        assert post.source == "tabelog"
        assert post.region == "JP"
        assert post.language == "ja"
        assert "すきやばし次郎" in post.title
        assert len(post.body) > 10
        assert post.engagement_metrics["rating"] == 4  # round(4.5) = 4 (banker's rounding)
