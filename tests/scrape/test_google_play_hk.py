"""Fixture-based parser tests for GooglePlayHKScraper.

The scraper uses the `google-play-scraper` library which abstracts the HTTP
layer; the unit it produces is a Python dict per review. We capture a
realistic review-dict here and feed it through the parser. To exercise the
search path end-to-end without network, we monkey-patch
``google_play_scraper.search`` and ``google_play_scraper.reviews``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.scrape.google_play_hk import GooglePlayHKScraper


# A realistic Google Play review dict, captured from the live API shape.
_FIXTURE_REVIEW = {
    "reviewId": "deadbeef-1234-5678-90ab-cdef00000001",
    "userName": "Mary Cheung",
    "userImage": "https://example.com/avatar.jpg",
    "content": "用咗呢個 app 好多年, 介面好難用, 經常閃退. 但係冇得選, 因為港鐵就只有呢個 app.",
    "score": 2,
    "thumbsUpCount": 14,
    "reviewCreatedVersion": "23.4.0",
    "at": "2025-09-10 08:30:00",
    "replyContent": "Thanks for your feedback. We are working on improvements.",
    "repliedAt": "2025-09-12 10:00:00",
    "appVersion": "23.4.0",
}

_FIXTURE_REVIEW_EN = {
    "reviewId": "deadbeef-1234-5678-90ab-cdef00000002",
    "userName": "John Smith",
    "userImage": "https://example.com/avatar2.jpg",
    "content": "App keeps crashing after iOS 18 update. Please fix.",
    "score": 1,
    "thumbsUpCount": 3,
    "reviewCreatedVersion": "23.4.0",
    "at": "2025-09-15 14:20:00",
    "replyContent": None,
    "repliedAt": None,
    "appVersion": "23.4.0",
}


def test_review_to_post_extracts_all_fields() -> None:
    scraper = GooglePlayHKScraper()
    post = scraper._review_to_post(_FIXTURE_REVIEW, app_id="hk.com.mtr.mtrmobile")

    assert post is not None
    assert post.id == "gp_deadbeef-1234-5678-90ab-cdef00000001"
    assert post.source == "google_play_hk"
    assert post.source_category.value == "reviews"
    assert post.signal_type.value == "experience"
    assert post.region == "HK"
    assert post.engagement_metrics == {"rating": 2, "thumbs_up": 14}
    assert post.raw_metadata["app_id"] == "hk.com.mtr.mtrmobile"
    assert post.raw_metadata["has_reply"] is True
    # Developer reply appended to body.
    assert "[Developer Reply]" in post.body
    assert "working on improvements" in post.body
    # Author name MUST NOT leak — only the hash is persisted.
    serialized = post.model_dump_json()
    assert "Mary Cheung" not in serialized
    assert len(post.author_hash) == 64


def test_review_to_post_handles_no_reply() -> None:
    scraper = GooglePlayHKScraper()
    post = scraper._review_to_post(_FIXTURE_REVIEW_EN, app_id="hk.com.mtr.mtrmobile")

    assert post is not None
    assert post.raw_metadata["has_reply"] is False
    assert "[Developer Reply]" not in post.body


def test_review_to_post_returns_none_for_missing_review_id() -> None:
    scraper = GooglePlayHKScraper()
    bad = dict(_FIXTURE_REVIEW)
    bad["reviewId"] = ""
    assert scraper._review_to_post(bad, app_id="x") is None


def test_review_to_post_falls_back_when_timestamp_invalid() -> None:
    scraper = GooglePlayHKScraper()
    bad = dict(_FIXTURE_REVIEW)
    bad["at"] = "not-a-timestamp"
    post = scraper._review_to_post(bad, app_id="x")
    # Falls back to "now" rather than dropping the post.
    assert post is not None
    assert (datetime.now(timezone.utc) - post.posted_at).total_seconds() < 60


def test_search_via_monkeypatched_library(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end search() through a fake `google_play_scraper` module."""
    import google_play_scraper as gps

    def fake_search(term, lang, country, n_hits):
        return [
            {"appId": "hk.com.mtr.mtrmobile"},
            {"appId": "com.example.other"},
        ]

    def fake_reviews(app_id, lang, country, count, continuation_token):
        if app_id == "hk.com.mtr.mtrmobile" and continuation_token is None:
            return [_FIXTURE_REVIEW, _FIXTURE_REVIEW_EN], None  # no more pages
        return [], None

    monkeypatch.setattr(gps, "search", fake_search)
    monkeypatch.setattr(gps, "reviews", fake_reviews)

    scraper = GooglePlayHKScraper(max_apps_per_search=1)
    since = datetime.now(timezone.utc) - timedelta(days=365)
    posts = list(scraper.search("MTR Mobile", since=since, limit=10))

    assert len(posts) == 2
    assert {p.id for p in posts} == {
        "gp_deadbeef-1234-5678-90ab-cdef00000001",
        "gp_deadbeef-1234-5678-90ab-cdef00000002",
    }


def test_registry_lists_google_play_hk_in_hk() -> None:
    """Regression: ensure google_play_hk is wired into the HK region."""
    from src.regions.registry import get_region

    hk = get_region("HK")
    ids = [s.source_id for s in hk.sources]
    assert "google_play_hk" in ids

    gp = next(s for s in hk.sources if s.source_id == "google_play_hk")
    assert gp.category.value == "reviews"
    assert gp.tos_scraping_stance.value == "prohibited"
    assert gp.last_verified_working is not None  # set during the audit
