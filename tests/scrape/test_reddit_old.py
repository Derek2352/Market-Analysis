"""reddit_old parser tests — offline against the saved JSON fixture.

The fixture (tests/fixtures/html/reddit_old/search_mtr.json) is a
synthetic-but-shape-faithful Reddit search.json response. Its structure
matches Reddit's public JSON API exactly: ``Listing{children: [t3{data}]}``.
If Reddit changes the shape, the parser fails offline and the live test
surfaces it.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.scrape.reddit_old import (
    doctor_check,
    parse_reddit_json_item,
    parse_reddit_search_json,
)
from src.schemas.enums import SignalType, SourceCategory

FIXTURE = (
    Path(__file__).resolve().parent.parent
    / "fixtures" / "html" / "reddit_old" / "search_mtr.json"
)


def _payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _hash_salt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTHOR_HASH_SALT", "test-reddit-salt")


# ---------------------------------------------------------------------------
# parse_reddit_search_json — top-level
# ---------------------------------------------------------------------------


def test_parse_returns_only_t3_kinds() -> None:
    """Fixture has 4 t3 + 1 t1 + 1 t3-without-id; only 3 valid posts emerge."""
    posts = parse_reddit_search_json(
        _payload()["data"], subreddit="HongKong", region="HK",
    )
    # 4 t3 entries, one of which has no id → 3 valid posts.
    assert len(posts) == 4
    ids = {p.id for p in posts}
    # The malformed (empty-id) t3 should NOT be present
    assert "reddit_HongKong_" not in ids


def test_parse_extracts_canonical_fields() -> None:
    posts = parse_reddit_search_json(
        _payload()["data"], subreddit="HongKong", region="HK",
    )
    top = next(p for p in posts if "1a2b3c" in p.id)
    assert top.source == "reddit_old"
    assert top.source_category is SourceCategory.FORUMS
    assert top.signal_type is SignalType.OPINION
    assert top.region == "HK"
    assert top.title.startswith("MTR fare increase 2026")
    assert "Octopus is draining" in top.body
    assert top.engagement_metrics == {"score": 287, "comments": 142}
    assert top.posted_at.year == 2024


def test_parse_self_post_body_concats_title_and_selftext() -> None:
    posts = parse_reddit_search_json(
        _payload()["data"], subreddit="HongKong", region="HK",
    )
    top = next(p for p in posts if "1a2b3c" in p.id)
    # Body should start with the title (for clustering / embedding context)
    # and contain the selftext.
    assert top.body.startswith("MTR fare increase 2026")
    assert "Octopus" in top.body
    assert "\n\n" in top.body   # separator between title and selftext


def test_parse_external_link_post_has_url_in_metadata() -> None:
    """A link-type post (is_self=False) records the external URL."""
    posts = parse_reddit_search_json(
        _payload()["data"], subreddit="HongKong", region="HK",
    )
    link = next(p for p in posts if "3c4d5e" in p.id)
    assert link.raw_metadata["is_self"] is False
    assert link.raw_metadata["external_url"] == (
        "https://www.scmp.com/news/hong-kong/transport/article/3300000/mtr-record-ridership"
    )
    assert link.raw_metadata["domain"] == "scmp.com"


def test_parse_self_post_external_url_is_none() -> None:
    posts = parse_reddit_search_json(
        _payload()["data"], subreddit="HongKong", region="HK",
    )
    top = next(p for p in posts if "1a2b3c" in p.id)
    assert top.raw_metadata["external_url"] is None
    assert top.raw_metadata["is_self"] is True


def test_parse_flair_captured() -> None:
    posts = parse_reddit_search_json(
        _payload()["data"], subreddit="HongKong", region="HK",
    )
    flairs = {p.raw_metadata.get("flair") for p in posts}
    assert "Discussion" in flairs
    assert "News" in flairs
    assert None in flairs   # one post without flair


def test_parse_handles_deleted_author() -> None:
    """The [deleted] author should still hash, not crash."""
    posts = parse_reddit_search_json(
        _payload()["data"], subreddit="HongKong", region="HK",
    )
    deleted = next(p for p in posts if "2b3c4d" in p.id)
    assert deleted.author_hash != ""
    # Raw author placeholder must not leak.
    assert "[deleted]" not in deleted.model_dump_json()


def test_parse_hashes_real_author_no_plaintext_leak() -> None:
    posts = parse_reddit_search_json(
        _payload()["data"], subreddit="HongKong", region="HK",
    )
    top = next(p for p in posts if "1a2b3c" in p.id)
    serialized = top.model_dump_json()
    assert "hk_commuter_4242" not in serialized
    assert len(top.author_hash) == 64


def test_parse_canonical_url_prefers_permalink() -> None:
    posts = parse_reddit_search_json(
        _payload()["data"], subreddit="HongKong", region="HK",
    )
    top = next(p for p in posts if "1a2b3c" in p.id)
    assert str(top.url) == (
        "https://old.reddit.com/r/HongKong/comments/1a2b3c/mtr_fare_increase_2026/"
    )


def test_parse_per_region_language_assignment() -> None:
    """Region drives the default language; detection still runs per post."""
    posts_hk = parse_reddit_search_json(
        _payload()["data"], subreddit="HongKong", region="HK",
    )
    assert {p.language for p in posts_hk} == {"en"}

    posts_jp = parse_reddit_search_json(
        _payload()["data"], subreddit="newsokur", region="JP",
    )
    assert {p.language for p in posts_jp} == {"ja"}

    posts_tw = parse_reddit_search_json(
        _payload()["data"], subreddit="Taiwan", region="TW",
    )
    assert {p.language for p in posts_tw} == {"zh-TW"}


def test_parse_detects_cantonese_post_language() -> None:
    posts = parse_reddit_search_json(
        _payload()["data"], subreddit="HongKong", region="HK",
    )
    cantonese = next(p for p in posts if "5e6f7g" in p.id)
    # detect_language uses py3langid; Cantonese-in-Traditional gets 'zh'.
    assert cantonese.language_detected == "zh"


def test_parse_empty_payload_returns_empty_list() -> None:
    assert parse_reddit_search_json({}, subreddit="HongKong", region="HK") == []
    assert parse_reddit_search_json(
        {"children": []}, subreddit="HongKong", region="HK",
    ) == []


def test_parse_skips_t1_kinds() -> None:
    """Comment-kind entries must never produce RawPosts."""
    payload = {
        "children": [
            {"kind": "t1", "data": {"id": "x", "body": "a comment"}},
            {"kind": "t3", "data": {
                "id": "valid",
                "title": "valid post",
                "is_self": True,
                "permalink": "/r/X/comments/valid/",
                "created_utc": 1714000000.0,
            }},
        ],
    }
    posts = parse_reddit_search_json(payload, subreddit="X", region="HK")
    assert len(posts) == 1
    assert posts[0].title == "valid post"


# ---------------------------------------------------------------------------
# parse_reddit_json_item — single-item edge cases
# ---------------------------------------------------------------------------


def test_item_without_id_returns_none() -> None:
    assert parse_reddit_json_item(
        {"title": "no id here", "is_self": True},
        subreddit="HongKong", region="HK",
    ) is None


def test_item_without_permalink_falls_back_to_constructed_url() -> None:
    post = parse_reddit_json_item(
        {
            "id": "abc",
            "title": "no permalink",
            "is_self": True,
            "created_utc": 1714000000.0,
        },
        subreddit="HongKong", region="HK",
    )
    assert post is not None
    assert str(post.url) == "https://old.reddit.com/r/HongKong/comments/abc/"


# ---------------------------------------------------------------------------
# scrape-doctor check
# ---------------------------------------------------------------------------


def test_doctor_check_passes_on_well_formed_fixture() -> None:
    html = FIXTURE.read_text(encoding="utf-8")
    ok, detail = doctor_check("search_mtr.json", html, {"subreddit": "HongKong"})
    assert ok, detail
    assert "OK" in detail


def test_doctor_check_fails_on_non_json() -> None:
    ok, detail = doctor_check("x.json", "not even close to JSON", {})
    assert not ok
    assert "not valid JSON" in detail


def test_doctor_check_fails_when_no_t3_posts() -> None:
    body = json.dumps({"data": {"children": [
        {"kind": "t1", "data": {"id": "c", "body": "comment only"}},
    ]}})
    ok, detail = doctor_check("x.json", body, {})
    assert not ok
    assert "0 posts" in detail
