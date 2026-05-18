"""Medium JP scraper — thin wrapper around the generalized medium.py.

Sets region='JP' and delegates all scraping logic to
``src.scrape.medium.MediumScraper``.
"""
from __future__ import annotations

from src.scrape.medium import (
    MediumScraper as _Base,
    parse_medium_response,
)


class MediumJPScraper(_Base):
    """Medium JP scraper — thin wrapper with region='JP'."""

    source_id = "medium_jp"

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("region", "JP")
        super().__init__(**kwargs)
