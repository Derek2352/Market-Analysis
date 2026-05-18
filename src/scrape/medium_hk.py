"""Medium HK scraper — thin wrapper around the generalized medium.py.

Kept for backward compatibility. Sets region='HK' and delegates all
scraping logic to ``src.scrape.medium.MediumScraper``.
"""
from __future__ import annotations

from src.scrape.medium import (
    MediumScraper as _Base,
    parse_medium_response,
)


class MediumHKScraper(_Base):
    """Medium HK scraper — thin wrapper with region='HK'."""

    source_id = "medium_hk"

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("region", "HK")
        super().__init__(**kwargs)
