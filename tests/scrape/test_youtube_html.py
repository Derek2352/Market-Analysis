"""Unit tests for youtube_html — offline, against saved HTML fixtures."""
from __future__ import annotations

import os

import pytest

from src.scrape.youtube_html import parse_search_results, parse_video_page


@pytest.fixture
def fixtures_dir() -> str:
    return os.path.join(os.path.dirname(__file__), "..", "fixtures")


class TestParseSearchResults:
    def test_extracts_video_urls_from_search_page(self, fixtures_dir: str) -> None:
        path = os.path.join(fixtures_dir, "html", "youtube_html", "search.html")
        if not os.path.exists(path):
            pytest.skip("youtube_html/search.html fixture not available")
        html = open(path, encoding="utf-8").read()
        urls = parse_search_results(html)
        assert len(urls) >= 1
        for url in urls:
            assert url.startswith("https://www.youtube.com/watch?v=")


class TestParseVideoPage:
    def test_parses_video_page(self, fixtures_dir: str) -> None:
        path = os.path.join(fixtures_dir, "html", "youtube_html", "video.html")
        if not os.path.exists(path):
            pytest.skip("youtube_html/video.html fixture not available")
        html = open(path, encoding="utf-8").read()
        post = parse_video_page(html, video_url="https://www.youtube.com/watch?v=wB8uDGPcS1g")

        assert post.source == "youtube_html"
        assert post.region == "HK"
        assert post.title
        assert len(post.title) > 5
        assert post.engagement_metrics.get("views", -1) >= 0
        assert post.raw_metadata.get("video_id")
        assert post.raw_metadata.get("channel_name")
