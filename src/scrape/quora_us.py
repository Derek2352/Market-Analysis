"""Quora US scraper — thin wrapper around the generalized quora.py.

Sets region='US' and delegates all scraping logic to
``src.scrape.quora.QuoraScraper``.
"""
from __future__ import annotations

from src.scrape.quora import (
    QuoraScraper as _Base,
    is_cloudflare_page,
    parse_question_page,
    parse_search_results,
)


class QuoraUSScraper(_Base):
    """Quora US scraper — thin wrapper with region='US'."""

    source_id = "quora_us"

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("region", "US")
        super().__init__(**kwargs)
