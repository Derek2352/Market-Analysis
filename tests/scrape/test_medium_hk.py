"""medium_hk parser tests — offline against the saved JSON fixture.

The fixture is a hand-crafted-but-realistic Medium ``?format=json``
response. Its shape matches Medium's documented endpoint exactly
(XSS prefix + payload.value + references.User) so parsers that pass here
should also parse a real article on first hit; if Medium changes the
shape, the parser fails offline and the live test surfaces it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.scrape.base.protocol import SourceError
from src.scrape.medium_hk import (
    MediumHKScraper,
    _format_json_url,
    _is_medium_article_url,
    _strip_xss_prefix,
    parse_medium_response,
)
from src.schemas.enums import SignalType, SourceCategory

FIXTURE = (
    Path(__file__).resolve().parent.parent
    / "fixtures" / "medium_hk" / "article.json"
)


# ---------------------------------------------------------------------------
# XSS prefix + URL helpers
# ---------------------------------------------------------------------------


def test_strips_xss_prefix() -> None:
    body = "])}while(1);</x>\n{\"a\": 1}"
    assert _strip_xss_prefix(body) == '{"a": 1}'


def test_strip_passes_through_unprefixed() -> None:
    assert _strip_xss_prefix('{"a": 1}') == '{"a": 1}'


def test_format_json_url_appends_query() -> None:
    assert _format_json_url("https://medium.com/@x/abc-123") == (
        "https://medium.com/@x/abc-123?format=json"
    )
    assert _format_json_url("https://medium.com/@x/abc-123?utm=foo") == (
        "https://medium.com/@x/abc-123?utm=foo&format=json"
    )


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://medium.com/@user/some-article-slug-abc123def456", True),
        ("https://user.medium.com/long-titled-article-slug-12af", True),
        ("https://medium.com/@user", False),                # user root
        ("https://medium.com/tag/hong-kong", False),        # tag
        ("https://example.com/article", False),             # not medium
        ("not a url", False),
    ],
)
def test_is_medium_article_url(url: str, expected: bool) -> None:
    assert _is_medium_article_url(url) is expected


# ---------------------------------------------------------------------------
# parse_medium_response on the saved fixture
# ---------------------------------------------------------------------------


def _fixture_body() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def test_parse_extracts_core_fields() -> None:
    post = parse_medium_response(
        _fixture_body(),
        source_url="https://medium.com/@jane-tang/why-octopus-still-beats-apple-pay-abc123def456",
    )
    assert post.source == "medium_hk"
    assert post.source_category is SourceCategory.BLOGS
    assert post.signal_type is SignalType.RECOMMENDATION
    assert post.region == "HK"
    assert post.title and post.title.startswith("Why Hong Kong's Octopus")
    assert "MTR" in post.body or "Octopus" in post.body
    assert "every workday for 14 years" in post.body   # body paragraph 1 content
    assert post.posted_at.year == 2024


def test_parse_aggregates_paragraphs_into_body() -> None:
    post = parse_medium_response(_fixture_body(), source_url="x")
    # Body should contain 4 paragraphs joined by double newline; subtitle and
    # title (paragraph types 3 + 4) are stripped to avoid duplication.
    paragraph_count = len(post.body.split("\n\n"))
    assert paragraph_count == 4


def test_parse_engagement_metrics() -> None:
    post = parse_medium_response(_fixture_body(), source_url="x")
    assert post.engagement_metrics == {
        "claps_count": 412,
        "response_count": 17,
        "word_count": 187,
    }


def test_parse_raw_metadata_includes_paywall_flag() -> None:
    post = parse_medium_response(_fixture_body(), source_url="x")
    assert post.raw_metadata["post_id"] == "abc123def456"
    assert post.raw_metadata["author_username"] == "jane-tang"
    assert post.raw_metadata["is_locked"] is False
    assert post.raw_metadata["reading_time_minutes"] == 1.2


def test_parse_hashes_author_name() -> None:
    post = parse_medium_response(_fixture_body(), source_url="x")
    assert post.author_hash != ""
    # Raw author name must not leak into serialization.
    assert "Jane Tang" not in post.model_dump_json()


def test_parse_uses_medium_url_from_payload() -> None:
    post = parse_medium_response(_fixture_body(), source_url="https://override")
    # The fixture has mediumUrl in payload.value — it wins over source_url.
    assert str(post.url).startswith("https://medium.com/@jane-tang/")


def test_parse_falls_back_to_source_url_when_medium_url_missing() -> None:
    body = '])}while(1);</x>\n{"success": true, "payload": {"value": {' \
           '"id": "id1", "title": "T", "content": {"bodyModel": {"paragraphs": [' \
           '{"type": 1, "text": "para text here"}]}}, "firstPublishedAt": 0,' \
           '"creatorId": "u1", "virtuals": {}}, "references": {"User": {"u1": {' \
           '"name": "Alice", "username": "alice"}}}}}'
    post = parse_medium_response(body, source_url="https://medium.com/@alice/t-xyz")
    assert str(post.url) == "https://medium.com/@alice/t-xyz"


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_parse_rejects_non_json_body() -> None:
    with pytest.raises(SourceError):
        parse_medium_response("not json at all", source_url="x")


def test_parse_rejects_success_false() -> None:
    body = '])}while(1);</x>\n{"success": false, "payload": {}}'
    with pytest.raises(SourceError):
        parse_medium_response(body, source_url="x")


def test_parse_rejects_empty_body_paragraphs() -> None:
    body = (
        '])}while(1);</x>\n'
        '{"success": true, "payload": {"value": {'
        '"id": "x", "title": "t", "content": {"bodyModel": {"paragraphs": []}},'
        '"virtuals": {}, "references": {}, "creatorId": ""}, "references": {}}}'
    )
    with pytest.raises(SourceError):
        parse_medium_response(body, source_url="x")


# ---------------------------------------------------------------------------
# Scraper wiring — search() via a fake DDG + a fake fetch client
# ---------------------------------------------------------------------------


def test_scraper_search_through_fake_client(monkeypatch) -> None:
    """End-to-end: DDG returns one hit → scraper fetches ?format=json → parse."""
    import src.scrape.medium_hk as mod
    from src.scrape.utils.ddg_search import DDGResult

    monkeypatch.setenv("AUTHOR_HASH_SALT", "test")

    # Stub DDG with one Medium article hit + one non-article hit.
    hits = [
        DDGResult(
            url="https://medium.com/@jane-tang/why-octopus-still-beats-apple-pay-abc123def456",
            title="Why Octopus",
            snippet="…",
        ),
        DDGResult(url="https://medium.com/tag/hong-kong", title="HK tag", snippet="…"),
    ]
    monkeypatch.setattr(mod, "ddg_search", lambda q, max_results=15: hits)

    body = FIXTURE.read_text(encoding="utf-8")

    class _FakeClient:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def get_html(self, url: str) -> str:
            self.calls.append(url)
            return body

        def close(self) -> None: ...

    fake = _FakeClient()
    scraper = MediumHKScraper(client=fake)
    posts = list(
        scraper.search(
            "Octopus",
            since=datetime(2000, 1, 1, tzinfo=timezone.utc),
            limit=10,
        )
    )
    scraper.close()

    assert len(posts) == 1                          # tag URL was filtered out
    assert posts[0].engagement_metrics["claps_count"] == 412
    assert fake.calls[0].endswith("?format=json")
