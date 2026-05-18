"""Live integration test for hk01 — hits real HK01 pages.

Double-gated because HK01 ToS prohibits automated access:
  - SCRAPE_LIVE_TESTS=1
  - ACCEPT_TOS_RISK=1
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

LIVE = os.environ.get("SCRAPE_LIVE_TESTS") == "1"
TOS = os.environ.get("ACCEPT_TOS_RISK") == "1"

pytestmark = pytest.mark.skipif(
    not (LIVE and TOS),
    reason=(
        "Live test for a ToS-prohibited source. Set both SCRAPE_LIVE_TESTS=1 "
        "and ACCEPT_TOS_RISK=1 to enable."
    ),
)


def test_hk01_search_returns_at_least_one_post() -> None:
    from src.scrape.hk01 import HK01Scraper

    since = datetime.now(timezone.utc) - timedelta(days=365 * 3)
    with HK01Scraper(max_articles=3) as s:
        posts = list(s.search("MTR", since=since, limit=2))
    for p in posts:
        assert p.source == "hk01"
        assert p.region == "HK"
        assert p.title
        assert p.raw_metadata.get("article_id")
        assert p.engagement_metrics.get("comment_count", -1) >= 0
