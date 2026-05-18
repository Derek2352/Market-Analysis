"""Yelp scraper — business review pages via Playwright.

Yelp serves JS-rendered pages with aggressive anti-bot protection.
Requires Playwright for rendering. High risk of blocking.

**ToS:** Yelp prohibits automated access. Flagged, opt-in only.
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import structlog
from bs4 import BeautifulSoup

from src.scrape.base import PlaywrightManager, RobotsCache, SourceError
from src.scrape.utils.hashing import hash_author
from src.scrape.utils.lang import detect_language
from src.schemas.enums import SignalType, SourceCategory
from src.schemas.raw import RawPost

_log = structlog.get_logger(__name__)

BASE_URL = "https://www.yelp.com"
SEARCH_URL = BASE_URL + "/search?find_desc={query}"
YELP_RATE = 5.0
MAX_PAGES = 3


class YelpHtmlScraper:
    """Yelp business review scraper via Playwright."""

    source_id = "yelp_html"
    region = "US"
    language = "en"
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
                robots_cache=self._robots_cache, rate=YELP_RATE,
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
            _log.warning("yelp.search_failed", topic=topic, error=str(e))
            return

        biz_urls = _parse_search_results(html)[:5]
        emitted = 0

        for biz_url in biz_urls:
            if emitted >= limit:
                break
            try:
                with self._playwright.get_page(biz_url) as page:
                    page.goto(biz_url, wait_until="domcontentloaded", timeout=45000)
                    page.wait_for_timeout(3000)
                    self._playwright.scroll_until_stable(page, max_scrolls=4, settle_ms=2000)
                    html = page.content()
                for post in _parse_business_page(html, biz_url=biz_url):
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
            page.wait_for_timeout(3000)
            html = page.content()
        reviews = list(_parse_business_page(html, biz_url=thread_id))
        return reviews[0] if reviews else None


def _parse_search_results(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    for link in soup.select("a[href*='/biz/']"):
        href = link.get("href", "")
        if "/biz/" in href and "?" not in href:
            full = BASE_URL + href if href.startswith("/") else href
            if full not in urls:
                urls.append(full)
    return urls[:10]


def _parse_business_page(html: str, *, biz_url: str) -> Iterator[RawPost]:
    soup = BeautifulSoup(html, "html.parser")

    biz_name_el = soup.select_one("h1")
    biz_name = biz_name_el.get_text(strip=True) if biz_name_el else ""

    for review_card in soup.select("[class*='review'], .review__"):
        try:
            rp = _parse_review_card(review_card, biz_url=biz_url, biz_name=biz_name)
            if rp:
                yield rp
        except Exception:
            continue


def _parse_review_card(card, *, biz_url: str, biz_name: str) -> RawPost | None:
    body_el = card.select_one("[class*='comment'], [class*='review-content'] p, [lang]")
    body = body_el.get_text(strip=True) if body_el else ""
    if len(body) < 10:
        return None

    author_el = card.select_one("[class*='user-name'], [class*='author']")
    author = author_el.get_text(strip=True) if author_el else ""

    rating = 0
    rating_el = card.select_one("[class*='star-rating'], [aria-label*='star']")
    if rating_el:
        aria = rating_el.get("aria-label", "")
        m = re.search(r"(\d+)", aria)
        rating = int(m.group(1)) if m else 0

    date_el = card.select_one("[class*='date'], time")
    posted_at = datetime.now(timezone.utc)
    if date_el:
        dt = date_el.get("datetime", "")
        if dt:
            try:
                posted_at = datetime.fromisoformat(dt.replace("Z", "+00:00"))
            except ValueError:
                pass

    full_text = f"{body}"
    return RawPost(
        id=f"yelp:{abs(hash(full_text))}",
        source="yelp_html",
        source_category=SourceCategory.REVIEWS,
        region="US",
        language="en",
        language_detected=detect_language(full_text),
        url=biz_url,
        author_hash=hash_author(author),
        title=f"Yelp review for {biz_name}" if biz_name else None,
        body=body,
        posted_at=posted_at,
        signal_type=SignalType.EXPERIENCE,
        engagement_metrics={"rating": rating},
        replies=[],
        raw_metadata={"business_name": biz_name},
    )
