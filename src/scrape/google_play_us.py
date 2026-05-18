"""Google Play US scraper — thin wrapper around google_play_hk.

Sets country='us', lang='en_US', and region='US'.
"""
from __future__ import annotations

from src.scrape.google_play_hk import GooglePlayHKScraper as _Base


class GooglePlayUSScraper(_Base):
    """Google Play US scraper — thin wrapper."""

    source_id = "google_play_us"

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("country", "us")
        kwargs.setdefault("lang", "en_US")
        kwargs.setdefault("region", "US")
        super().__init__(**kwargs)
