"""Tabelog scraper — Japan's #1 restaurant review platform via Playwright.

Tabelog (tabelog.com) is JS-rendered with aggressive anti-bot measures.
Requires Playwright for page rendering. Each restaurant has review pages
with user ratings, visit dates, and detailed reviews.

**ToS:** Tabelog prohibits automated access. Flagged, opt-in only.
**Risk:** High — aggressive anti-bot, may block headless browsers quickly.
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urljoin

import structlog
from bs4 import BeautifulSoup

from src.scrape.base import PlaywrightManager, RobotsCache, SourceError
from src.scrape.utils.hashing import hash_author
from src.scrape.utils.lang import detect_language
from src.schemas.enums import SignalType, SourceCategory
from src.schemas.raw import RawPost

_log = structlog.get_logger(__name__)

BASE_URL = "https://tabelog.com"
SEARCH_URL = BASE_URL + "/rstLst/?sk={query}&SrtT=rvcn"
TABELOG_RATE = 5.0
MAX_RESTAURANTS = 5


class TabelogScraper:
    """Tabelog restaurant review scraper via Playwright."""

    source_id = "tabelog"
    region = "JP"
    language = "ja"
    category = SourceCategory.REVIEWS
    signal_type = SignalType.EXPERIENCE

    def __init__(
        self,
        *,
        playwright: PlaywrightManager | None = None,
        robots_cache: RobotsCache | None = None,
    ) -> None:
        self._owns_playwright = playwright is None
        if playwright is None:
            self._robots_cache = robots_cache or RobotsCache()
            self._playwright = PlaywrightManager(
                robots_cache=self._robots_cache, rate=TABELOG_RATE,
                respect_robots=False,
            )
        else:
            self._playwright = playwright

    def close(self) -> None:
        if self._owns_playwright:
            self._playwright.close()

    def search(self, topic: str, since: datetime, limit: int) -> Iterator[RawPost]:
        url = SEARCH_URL.format(query=quote(topic))
        try:
            with self._playwright.get_page(url) as page:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(5000)
                html = page.content()
        except SourceError as e:
            _log.warning("tabelog.search_failed", topic=topic, error=str(e))
            return

        restaurant_urls = _parse_search_results(html)[:MAX_RESTAURANTS]
        emitted = 0

        for rurl in restaurant_urls:
            if emitted >= limit:
                break
            try:
                with self._playwright.get_page(rurl) as page:
                    page.goto(rurl, wait_until="domcontentloaded", timeout=45000)
                    page.wait_for_timeout(4000)
                    self._playwright.scroll_until_stable(page, max_scrolls=4, settle_ms=2000)
                    html = page.content()
                for post in _parse_reviews(html, restaurant_url=rurl):
                    if emitted >= limit:
                        break
                    if post.posted_at >= since:
                        yield post
                        emitted += 1
            except SourceError:
                continue

    def fetch_thread(self, thread_id: str) -> Any:
        with self._playwright.get_page(thread_id) as page:
            page.goto(thread_id, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(4000)
            html = page.content()
        reviews = list(_parse_reviews(html, restaurant_url=thread_id))
        return reviews[0] if reviews else None


def _parse_search_results(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    seen: set[str] = set()
    for link in soup.select("a[href*='/rst/']"):
        href = link.get("href", "")
        if "/rst/" in href and "rvwPart" not in href:
            full = urljoin(BASE_URL, href.split("?")[0])
            if full not in seen:
                seen.add(full)
                urls.append(full)
    return urls


def _parse_reviews(html: str, *, restaurant_url: str) -> Iterator[RawPost]:
    soup = BeautifulSoup(html, "html.parser")

    rest_name_el = soup.select_one("h1") or soup.select_one("[class*='rst-name']")
    rest_name = rest_name_el.get_text(strip=True) if rest_name_el else ""

    for card in soup.select("[class*='rvw-item'], [class*='review-item'], .rvw-item__"):
        try:
            body_el = card.select_one("[class*='rvw-item__comment'], [class*='comment'], [class*='body']")
            body = body_el.get_text(strip=True) if body_el else ""
            if len(body) < 20:
                continue

            # Tabelog uses 1-5 scale (often with 0.5 increments)
            rating = 0.0
            rating_el = card.select_one("[class*='rvw-item__rating'], [class*='rating']")
            if rating_el:
                rating_text = rating_el.get_text(strip=True)
                try:
                    rating = float(rating_text)
                except ValueError:
                    m = re.search(r"(\d+\.?\d*)", rating_text)
                    rating = float(m.group(1)) if m else 0.0

            author_el = card.select_one("[class*='rvw-item__user'], [class*='user-name']")
            author = author_el.get_text(strip=True) if author_el else ""

            yield RawPost(
                id=f"tabelog:{abs(hash(body))}",
                source="tabelog",
                source_category=SourceCategory.REVIEWS,
                region="JP",
                language="ja",
                language_detected=detect_language(body),
                url=restaurant_url,
                author_hash=hash_author(author),
                title=rest_name or None,
                body=body,
                posted_at=datetime.now(timezone.utc),
                signal_type=SignalType.EXPERIENCE,
                engagement_metrics={"rating": int(round(rating))},
                replies=[],
                raw_metadata={"restaurant_name": rest_name, "restaurant_url": restaurant_url},
            )
        except Exception:
            continue
