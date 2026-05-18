"""Google Play Taiwan scraper — thin wrapper around google_play_hk.

Sets country='tw', lang='zh_TW', and region='TW'.
"""
from __future__ import annotations

from src.scrape.google_play_hk import GooglePlayHKScraper as _Base


class GooglePlayTWScraper(_Base):
    """Google Play Taiwan scraper — thin wrapper."""

    source_id = "google_play_tw"

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("country", "tw")
        kwargs.setdefault("lang", "zh_TW")
        kwargs.setdefault("region", "TW")
        super().__init__(**kwargs)
