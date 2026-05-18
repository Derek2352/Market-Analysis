"""App Store US scraper — thin wrapper around app_store_hk.

Sets country='us', lang='en', and region='US'.
"""
from __future__ import annotations

from src.scrape.app_store_hk import AppStoreHKScraper as _Base


class AppStoreUSScraper(_Base):
    """App Store US scraper — thin wrapper."""

    source_id = "app_store_us"

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("country", "us")
        kwargs.setdefault("lang", "en")
        kwargs.setdefault("region", "US")
        super().__init__(**kwargs)
