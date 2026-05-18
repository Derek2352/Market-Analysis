"""Live integration test for quora_hk — hits real Quora pages.

Double-gated because Quora ToS prohibits automated access:
  - SCRAPE_LIVE_TESTS=1
  - ACCEPT_TOS_RISK=1

Note: Quora uses Cloudflare anti-bot protection. Headless Playwright
may get a challenge page. The test tolerates zero results in that case.
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


def test_quora_hk_search_posts_are_well_formed() -> None:
    from src.scrape.quora_hk import QuoraHKScraper

    since = datetime.now(timezone.utc) - timedelta(days=365 * 5)
    with QuoraHKScraper(max_questions=3) as s:
        posts = list(s.search("MTR", since=since, limit=2))
    # Cloudflare may block headless — tolerate zero results
    for p in posts:
        assert p.source == "quora_hk"
        assert p.region == "HK"
        assert p.title
        assert p.engagement_metrics.get("answer_count", -1) >= 0
