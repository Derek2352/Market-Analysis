"""Medium TW scraper — thin wrapper around the generalized medium.py.

Sets region='TW' and delegates all scraping logic to
``src.scrape.medium.MediumScraper``.
"""
from __future__ import annotations

from src.scrape.medium import (
    MediumScraper as _Base,
    parse_medium_response,
)


class MediumTWScraper(_Base):
    """Medium TW scraper — thin wrapper with region='TW'."""

    source_id = "medium_tw"

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("region", "TW")
        super().__init__(**kwargs)
