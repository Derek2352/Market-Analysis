from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from src.scrape.app_store_hk import AppStoreHKScraper

LIVE = os.environ.get("SCRAPE_LIVE_TESTS") == "1"

pytestmark = pytest.mark.skipif(
    not LIVE,
    reason="Set SCRAPE_LIVE_TESTS=1 to enable network-hitting tests.",
)


def test_whatsapp_hk_returns_real_reviews() -> None:
    since = datetime.now(timezone.utc) - timedelta(days=365 * 3)
    with AppStoreHKScraper(max_apps_per_search=1) as s:
        posts = list(s.search("WhatsApp", since=since, limit=5))
    assert len(posts) >= 1, "expected at least one HK App Store review"
    p = posts[0]
    assert p.source == "app_store_hk"
    assert p.region == "HK"
    assert 1 <= p.engagement_metrics["rating"] <= 5
    assert p.author_hash and len(p.author_hash) == 64
    assert p.body  # non-empty
