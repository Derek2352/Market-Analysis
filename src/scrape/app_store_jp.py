"""App Store Japan scraper — thin wrapper around app_store_hk.

Sets country='jp', lang='ja', and region='JP'.
"""
from __future__ import annotations

from src.scrape.app_store_hk import AppStoreHKScraper as _Base


class AppStoreJPScraper(_Base):
    """App Store Japan scraper — thin wrapper."""

    source_id = "app_store_jp"

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("country", "jp")
        kwargs.setdefault("lang", "ja")
        kwargs.setdefault("region", "JP")
        super().__init__(**kwargs)
