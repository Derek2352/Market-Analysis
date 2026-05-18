from __future__ import annotations

from collections.abc import Callable
from typing import Any

from src.scrape.app_store_hk import AppStoreHKScraper
from src.scrape.app_store_jp import AppStoreJPScraper
from src.scrape.app_store_tw import AppStoreTWScraper
from src.scrape.app_store_us import AppStoreUSScraper
from src.scrape.base import SourceScraper
from src.scrape.cosme import CosmeScraper
from src.scrape.dcard import DcardScraper
from src.scrape.discuss_hk import DiscussHKScraper
from src.scrape.five_ch import FiveChScraper
from src.scrape.google_play_hk import GooglePlayHKScraper
from src.scrape.google_play_jp import GooglePlayJPScraper
from src.scrape.google_play_tw import GooglePlayTWScraper
from src.scrape.google_play_us import GooglePlayUSScraper
from src.scrape.hk01 import HK01Scraper
from src.scrape.lihkg import LIHKGScraper
from src.scrape.medium import MediumScraper
from src.scrape.medium_hk import MediumHKScraper
from src.scrape.medium_jp import MediumJPScraper
from src.scrape.medium_tw import MediumTWScraper
from src.scrape.medium_us import MediumUSScraper
from src.scrape.mobile01 import Mobile01Scraper
from src.scrape.openrice import OpenriceScraper
from src.scrape.ptt import PTTScraper
from src.scrape.quora import QuoraScraper
from src.scrape.quora_hk import QuoraHKScraper
from src.scrape.quora_jp import QuoraJPScraper
from src.scrape.quora_tw import QuoraTWScraper
from src.scrape.quora_us import QuoraUSScraper
from src.scrape.reddit_old import RedditOldScraper
from src.scrape.tabelog import TabelogScraper
from src.scrape.trustpilot import TrustpilotScraper
from src.scrape.yahoo_japan_reviews import YahooJapanReviewsScraper
from src.scrape.yahoo_news_tw import YahooNewsTWScraper
from src.scrape.yelp_html import YelpHtmlScraper
from src.scrape.youtube_html import YoutubeHTMLScraper

# Each source has one entry here and one file under src/scrape/.
_FACTORIES: dict[str, Callable[[], SourceScraper]] = {
    "app_store_hk": AppStoreHKScraper,
    "app_store_jp": AppStoreJPScraper,
    "app_store_tw": AppStoreTWScraper,
    "app_store_us": AppStoreUSScraper,
    "cosme": CosmeScraper,
    "dcard": DcardScraper,
    "discuss_hk": DiscussHKScraper,
    "five_ch": FiveChScraper,
    "google_play_hk": GooglePlayHKScraper,
    "google_play_jp": GooglePlayJPScraper,
    "google_play_tw": GooglePlayTWScraper,
    "google_play_us": GooglePlayUSScraper,
    "hk01": HK01Scraper,
    "lihkg": LIHKGScraper,
    "medium": MediumScraper,
    "medium_hk": MediumHKScraper,
    "medium_jp": MediumJPScraper,
    "medium_tw": MediumTWScraper,
    "medium_us": MediumUSScraper,
    "mobile01": Mobile01Scraper,
    "openrice": OpenriceScraper,
    "ptt": PTTScraper,
    "quora": QuoraScraper,
    "quora_hk": QuoraHKScraper,
    "quora_jp": QuoraJPScraper,
    "quora_tw": QuoraTWScraper,
    "quora_us": QuoraUSScraper,
    "reddit_old": RedditOldScraper,
    "tabelog": TabelogScraper,
    "trustpilot": TrustpilotScraper,
    "yahoo_japan_reviews": YahooJapanReviewsScraper,
    "yahoo_news_tw": YahooNewsTWScraper,
    "yelp_html": YelpHtmlScraper,
    "youtube_html": YoutubeHTMLScraper,
}


def available_sources() -> list[str]:
    return sorted(_FACTORIES)


def get_scraper(source_id: str, **kwargs: Any) -> SourceScraper:
    if source_id not in _FACTORIES:
        raise KeyError(
            f"Unknown source_id: {source_id!r}. "
            f"Available: {available_sources()}"
        )
    return _FACTORIES[source_id](**kwargs)
