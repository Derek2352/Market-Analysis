"""Quora HK scraper — thin wrapper around the generalized quora.py.

Kept for backward compatibility. Sets region='HK' and delegates all
scraping logic to ``src.scrape.quora.QuoraScraper``.
"""
from __future__ import annotations

from src.scrape.quora import (
    QuoraScraper as _Base,
    is_cloudflare_page,
    parse_question_page,
    parse_search_results,
)


class QuoraHKScraper(_Base):
    """Quora HK scraper — thin wrapper with region='HK'."""

    source_id = "quora_hk"

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("region", "HK")
        super().__init__(**kwargs)


from src.scrape.quora import doctor_check  # noqa: E402,F401
