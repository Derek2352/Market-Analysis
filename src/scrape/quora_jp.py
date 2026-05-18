"""Quora JP scraper — thin wrapper around the generalized quora.py.

Sets region='JP' and delegates all scraping logic to
``src.scrape.quora.QuoraScraper``.
"""
from __future__ import annotations

from src.scrape.quora import (
    QuoraScraper as _Base,
    is_cloudflare_page,
    parse_question_page,
    parse_search_results,
)


class QuoraJPScraper(_Base):
    """Quora JP scraper — thin wrapper with region='JP'."""

    source_id = "quora_jp"

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("region", "JP")
        super().__init__(**kwargs)
