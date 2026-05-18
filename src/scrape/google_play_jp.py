"""Google Play Japan scraper — thin wrapper around google_play_hk.

Sets country='jp', lang='ja_JP', and region='JP'.
"""
from __future__ import annotations

from src.scrape.google_play_hk import GooglePlayHKScraper as _Base


class GooglePlayJPScraper(_Base):
    """Google Play Japan scraper — thin wrapper."""

    source_id = "google_play_jp"

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("country", "jp")
        kwargs.setdefault("lang", "ja_JP")
        kwargs.setdefault("region", "JP")
        super().__init__(**kwargs)
