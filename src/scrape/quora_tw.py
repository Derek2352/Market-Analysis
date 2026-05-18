"""Quora TW scraper — thin wrapper around the generalized quora.py.

Sets region='TW' and delegates all scraping logic to
``src.scrape.quora.QuoraScraper``.
"""
from __future__ import annotations

from src.scrape.quora import (
    QuoraScraper as _Base,
    is_cloudflare_page,
    parse_question_page,
    parse_search_results,
)


class QuoraTWScraper(_Base):
    """Quora TW scraper — thin wrapper with region='TW'."""

    source_id = "quora_tw"

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("region", "TW")
        super().__init__(**kwargs)
