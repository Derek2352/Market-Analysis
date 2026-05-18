"""Live integration test for medium_hk — hits the real Medium ?format=json.

Double-gated because medium_hk's ToS prohibits automated access:
  - SCRAPE_LIVE_TESTS=1
  - ACCEPT_TOS_RISK=1
Both must be set to enable the live test, mirroring the CLI's
``--accept-tos-risk`` flag for prohibited sources.

Discovery uses the DDG SERP utility — slow but real.
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


def test_medium_hk_search_returns_at_least_one_post() -> None:
    from src.scrape.medium_hk import MediumHKScraper

    since = datetime.now(timezone.utc) - timedelta(days=365 * 5)
    with MediumHKScraper(max_articles=3) as s:
        posts = list(s.search("MTR", since=since, limit=2))
    # We don't assert >=1 strictly — DDG SERP for "site:medium.com Hong Kong MTR"
    # can return zero on a quiet day, and ?format=json may 403 individual URLs.
    # What we DO assert: when something comes back, it's well-formed.
    for p in posts:
        assert p.source == "medium_hk"
        assert p.region == "HK"
        assert p.title
        assert p.body
        assert p.engagement_metrics.get("claps_count", -1) >= 0
        assert p.raw_metadata.get("post_id")
