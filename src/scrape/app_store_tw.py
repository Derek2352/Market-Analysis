"""App Store Taiwan scraper — thin wrapper around app_store_hk.

Sets country='tw', lang='zh-Hant', and region='TW'.
"""
from __future__ import annotations

from src.scrape.app_store_hk import AppStoreHKScraper as _Base


class AppStoreTWScraper(_Base):
    """App Store Taiwan scraper — thin wrapper."""

    source_id = "app_store_tw"

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("country", "tw")
        kwargs.setdefault("lang", "zh-Hant")
        kwargs.setdefault("region", "TW")
        super().__init__(**kwargs)
