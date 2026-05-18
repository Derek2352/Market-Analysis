"""@cosme scraper — Japan's largest beauty/skincare review platform.

@cosme (cosme.net) is server-rendered HTML with product pages containing
user reviews. Each review has a rating, skin type, age, and detailed text.
Gold mine for beauty and skincare brand research.

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

BASE_URL = "https://www.cosme.net"
SEARCH_URL = BASE_URL + "/search?keyword={query}"
COSME_RATE = 2.0
MAX_PRODUCTS = 8


class CosmeScraper:
    """@cosme beauty review scraper."""

    source_id = "cosme"
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
                robots_cache=self._robots_cache, rate=COSME_RATE,
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

        product_urls = _parse_search_results(html)[:self._max_products]
        emitted = 0

        for purl in product_urls:
            if emitted >= limit:
                break
            try:
                html = self._client.get_html(purl)
                for post in _parse_product_reviews(html, product_url=purl):
                    if emitted >= limit:
                        break
                    if post.posted_at >= since:
                        yield post
                        emitted += 1
            except SourceError:
                continue

    def fetch_thread(self, thread_id: str) -> Any:
        html = self._client.get_html(thread_id)
        reviews = list(_parse_product_reviews(html, product_url=thread_id))
        return reviews[0] if reviews else None


def _parse_search_results(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    for link in soup.select("a[href*='/products/']"):
        href = link.get("href", "")
        full = urljoin(BASE_URL, href)
        if full not in urls:
            urls.append(full)
    return urls


def _parse_product_reviews(html: str, *, product_url: str) -> Iterator[RawPost]:
    soup = BeautifulSoup(html, "html.parser")

    product_name_el = soup.select_one("h1") or soup.select_one("[class*='product-name']")
    product_name = product_name_el.get_text(strip=True) if product_name_el else ""

    for card in soup.select("[class*='review-list'] > li, [class*='review-item'], [class*='ReviewItem']"):
        try:
            body_el = card.select_one("[class*='review-text'], [class*='comment'], [class*='body']")
            body = body_el.get_text(strip=True) if body_el else ""
            if len(body) < 20:
                continue

            rating = 0
            rating_el = card.select_one("[class*='rating'] img, [class*='star']")
            if rating_el:
                alt = rating_el.get("alt", "")
                m = re.search(r"(\d)", alt)
                rating = int(m.group(1)) if m else 0

            author_el = card.select_one("[class*='user-name'], [class*='reviewer']")
            author = author_el.get_text(strip=True) if author_el else ""

            yield RawPost(
                id=f"cosme:{abs(hash(body))}",
                source="cosme",
                source_category=SourceCategory.REVIEWS,
                region="JP",
                language="ja",
                language_detected=detect_language(body),
                url=product_url,
                author_hash=hash_author(author),
                title=product_name or None,
                body=body,
                posted_at=datetime.now(timezone.utc),
                signal_type=SignalType.EXPERIENCE,
                engagement_metrics={"rating": rating},
                replies=[],
                raw_metadata={"product_name": product_name},
            )
        except Exception:
            continue
