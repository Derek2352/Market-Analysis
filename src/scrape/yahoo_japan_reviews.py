"""Yahoo Japan Reviews scraper.

Yahoo Japan (shopping.yahoo.co.jp) has product review pages accessible
via static HTML. Each product has a /review/ endpoint with paginated reviews.

**ToS:** Silent. Rate limit: 1 req / 2 s.
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urljoin

import structlog
from bs4 import BeautifulSoup

from src.scrape.base import RobotsCache, SourceError
from src.scrape.base.http import PoliteClient
from src.scrape.utils.hashing import hash_author
from src.scrape.utils.lang import detect_language
from src.schemas.enums import SignalType, SourceCategory
from src.schemas.raw import RawPost

_log = structlog.get_logger(__name__)

BASE_URL = "https://shopping.yahoo.co.jp"
SEARCH_URL = BASE_URL + "/search?p={query}"
REVIEW_URL = BASE_URL + "/product/{product_id}/review/"
YAHOO_JP_RATE = 2.0
MAX_PRODUCTS = 8


class YahooJapanReviewsScraper:
    """Yahoo Japan shopping reviews scraper."""

    source_id = "yahoo_japan_reviews"
    region = "JP"
    language = "ja"
    category = SourceCategory.REVIEWS
    signal_type = SignalType.EXPERIENCE

    def __init__(
        self,
        *,
        client: PoliteClient | None = None,
        robots_cache: RobotsCache | None = None,
        max_products: int = MAX_PRODUCTS,
    ) -> None:
        self._max_products = max_products
        self._owns_client = client is None
        if client is None:
            self._robots_cache = robots_cache or RobotsCache()
            self._client = PoliteClient(
                robots_cache=self._robots_cache, rate=YAHOO_JP_RATE,
            )
        else:
            self._client = client

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def search(self, topic: str, since: datetime, limit: int) -> Iterator[RawPost]:
        url = SEARCH_URL.format(query=quote(topic))
        try:
            html = self._client.get_html(url)
        except SourceError:
            return

        product_ids = _parse_search_results(html)[:self._max_products]
        emitted = 0

        for pid in product_ids:
            if emitted >= limit:
                break
            try:
                review_url = REVIEW_URL.format(product_id=pid)
                html = self._client.get_html(review_url)
                for post in _parse_reviews(html, product_id=pid):
                    if emitted >= limit:
                        break
                    if post.posted_at >= since:
                        yield post
                        emitted += 1
            except SourceError:
                continue

    def fetch_thread(self, thread_id: str) -> Any:
        review_url = REVIEW_URL.format(product_id=thread_id)
        html = self._client.get_html(review_url)
        reviews = list(_parse_reviews(html, product_id=thread_id))
        return reviews[0] if reviews else None


def _parse_search_results(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    ids: list[str] = []
    for link in soup.select("a[href*='/product/']"):
        href = link.get("href", "")
        m = re.search(r"/product/([a-zA-Z0-9]+)", href)
        if m:
            pid = m.group(1)
            if pid not in ids:
                ids.append(pid)
    return ids


def _parse_reviews(html: str, *, product_id: str) -> Iterator[RawPost]:
    soup = BeautifulSoup(html, "html.parser")

    for card in soup.select("[class*='reviewItem'], .review-item, [class*='Review']"):
        try:
            body_el = card.select_one("[class*='reviewText'], [class*='comment'], [class*='body']")
            body = body_el.get_text(strip=True) if body_el else ""
            if len(body) < 10:
                continue

            rating = 0
            rating_el = card.select_one("[class*='rating'], [class*='star']")
            if rating_el:
                stars = rating_el.get_text(strip=True)
                m = re.search(r"(\d)", stars)
                rating = int(m.group(1)) if m else 0

            author_el = card.select_one("[class*='reviewer'], [class*='userName']")
            author = author_el.get_text(strip=True) if author_el else ""

            yield RawPost(
                id=f"yahoo_jp:{product_id}:{abs(hash(body))}",
                source="yahoo_japan_reviews",
                source_category=SourceCategory.REVIEWS,
                region="JP",
                language="ja",
                language_detected=detect_language(body),
                url=REVIEW_URL.format(product_id=product_id),
                author_hash=hash_author(author),
                title=None,
                body=body,
                posted_at=datetime.now(timezone.utc),
                signal_type=SignalType.EXPERIENCE,
                engagement_metrics={"rating": rating},
                replies=[],
                raw_metadata={"product_id": product_id},
            )
        except Exception:
            continue
