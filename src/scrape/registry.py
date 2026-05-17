from __future__ import annotations

from collections.abc import Callable

from src.scrape.app_store_hk import AppStoreHKScraper
from src.scrape.base import SourceScraper
from src.scrape.lihkg import LIHKGScraper
from src.scrape.openrice import OpenriceScraper
from src.scrape.reddit_old import RedditOldScraper

# Each source has one entry here and one file under src/scrape/.
_FACTORIES: dict[str, Callable[[], SourceScraper]] = {
    "app_store_hk": AppStoreHKScraper,
    "lihkg": LIHKGScraper,
    "openrice": OpenriceScraper,
    "reddit_old": RedditOldScraper,
}


def available_sources() -> list[str]:
    return sorted(_FACTORIES)


def get_scraper(source_id: str) -> SourceScraper:
    if source_id not in _FACTORIES:
        raise KeyError(
            f"Unknown source_id: {source_id!r}. "
            f"Available: {available_sources()}"
        )
    return _FACTORIES[source_id]()
