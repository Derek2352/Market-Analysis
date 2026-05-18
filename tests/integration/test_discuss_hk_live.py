"""Live integration test for discuss_hk — hits the real site.

Gated on ``SCRAPE_LIVE_TESTS=1``. No ``ACCEPT_TOS_RISK`` gate because
discuss_hk's ToS stance is ``silent`` (no explicit prohibition).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

LIVE = os.environ.get("SCRAPE_LIVE_TESTS") == "1"


pytestmark = pytest.mark.skipif(
    not LIVE,
    reason="Set SCRAPE_LIVE_TESTS=1 to enable network-hitting tests.",
)


def test_discuss_hk_search_returns_at_least_one_post() -> None:
    from src.scrape.discuss_hk import DiscussHKScraper
    from src.scrape.base.http import PoliteClient
    from src.scrape.base.robots import RobotsCache

    since = datetime.now(timezone.utc) - timedelta(days=365 * 10)
    client = PoliteClient(
        robots_cache=RobotsCache(),
        respect_robots=False,
        rate=1.5,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with DiscussHKScraper(client=client) as s:
        posts = list(s.search("MTR", since=since, limit=2))
    assert len(posts) >= 1
    p = posts[0]
    assert p.source == "discuss_hk"
    assert p.region == "HK"
    assert p.body
    assert p.title
    assert p.engagement_metrics.get("views", 0) >= 0
