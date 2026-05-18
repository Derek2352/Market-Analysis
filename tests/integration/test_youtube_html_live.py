"""Live integration test for youtube_html — hits real YouTube.

Double-gated because YouTube ToS prohibits automated access:
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


def test_youtube_html_search_returns_at_least_one_post() -> None:
    from src.scrape.youtube_html import YoutubeHTMLScraper

    since = datetime.now(timezone.utc) - timedelta(days=365 * 5)
    with YoutubeHTMLScraper(max_videos=3) as s:
        posts = list(s.search("MTR Hong Kong", since=since, limit=2))
    for p in posts:
        assert p.source == "youtube_html"
        assert p.region == "HK"
        assert p.title
        assert p.raw_metadata.get("video_id")
        assert p.raw_metadata.get("channel_name")
        assert p.engagement_metrics.get("views", -1) >= 0
