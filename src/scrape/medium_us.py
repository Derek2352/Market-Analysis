"""Medium US scraper — thin wrapper around the generalized medium.py.

Sets region='US' and delegates all scraping logic to
``src.scrape.medium.MediumScraper``.
"""
from __future__ import annotations

from src.scrape.medium import (
    MediumScraper as _Base,
    parse_medium_response,
)


class MediumUSScraper(_Base):
    """Medium US scraper — thin wrapper with region='US'."""

    source_id = "medium_us"

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("region", "US")
        super().__init__(**kwargs)
