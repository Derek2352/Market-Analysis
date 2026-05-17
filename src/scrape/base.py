"""SourceScraper protocol — re-exported from ``src/scrape/base/protocol.py``.

Kept for backward compatibility.  New code should import directly from
``src.scrape.base`` (the package), which re-exports everything.
"""
from src.scrape.base.protocol import SourceError, SourceScraper

__all__ = ["SourceError", "SourceScraper"]
