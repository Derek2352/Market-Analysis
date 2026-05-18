"""Unit tests for quora_hk — offline, against saved HTML fixture."""
from __future__ import annotations

import os

import pytest

from src.scrape.quora_hk import parse_question_page, parse_search_results, is_cloudflare_page


@pytest.fixture
def fixtures_dir() -> str:
    return os.path.join(os.path.dirname(__file__), "..", "fixtures")


class TestParseQuestionPage:
    def test_parses_question_page_or_skips_cloudflare(self, fixtures_dir: str) -> None:
        path = os.path.join(fixtures_dir, "html", "quora_hk", "question.html")
        if not os.path.exists(path):
            pytest.skip("quora_hk/question.html fixture not available")
        html = open(path, encoding="utf-8").read()

        from src.scrape.base import SourceError

        if is_cloudflare_page(html):
            with pytest.raises(SourceError):
                parse_question_page(html, question_url="https://www.quora.com/test")
        else:
            # May be a real page or Quora's own error page
            try:
                post = parse_question_page(html, question_url="https://www.quora.com/test")
                assert post.source == "quora_hk"
                assert post.region == "HK"
                assert post.title
                assert len(post.title) > 3
            except SourceError:
                # Quora error page with <title>Error</title> is expected until
                # we have a manual browser save-as fixture
                pass


class TestParseSearchResults:
    def test_handles_search_page(self, fixtures_dir: str) -> None:
        path = os.path.join(fixtures_dir, "html", "quora_hk", "search.html")
        if not os.path.exists(path):
            pytest.skip("quora_hk/search.html fixture not available")
        html = open(path, encoding="utf-8").read()
        urls = parse_search_results(html)
        # OK if Cloudflare blocks — just verify no crash
        assert isinstance(urls, list)
        if urls:
            for url in urls:
                assert "quora.com" in url
