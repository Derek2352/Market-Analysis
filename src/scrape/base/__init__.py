"""Shared scraper base infrastructure.

Phase 2 introduces a `src/scrape/base/` package that every scraper (LIHKG,
Openrice, Reddit old-reddit, etc.) builds on. It provides:

- Polite HTTP client with configurable rate limiting, retries, and honest User-Agent
- robots.txt checking with per-host caching
- Playwright session manager (stealth defaults, reusable browser)
- HTML fixture system for tests and scrape-doctor
- SourceScraper protocol (moved from parent package)

Import paths are stable — existing `from src.scrape.base import SourceScraper` still works.
"""

from src.scrape.base.protocol import SourceError, SourceScraper
from src.scrape.base.http import PoliteClient
from src.scrape.base.robots import RobotsCache
from src.scrape.base.fixtures import FixtureStore
from src.scrape.base.playwright import PlaywrightManager

__all__ = [
    "FixtureStore",
    "PlaywrightManager",
    "PoliteClient",
    "RobotsCache",
    "SourceError",
    "SourceScraper",
]
