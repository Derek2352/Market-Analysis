"""Openrice scraper — Playwright-based HTML scraper for HK restaurant reviews.

Openrice (https://www.openrice.com) is the dominant F&B review platform in
Hong Kong.  The site is JS-rendered (React), so we use Playwright through
``PlaywrightManager``.

**ToS note:** Openrice's ToS prohibit automated access.  This scraper is
registered with ``tos_scraping_stance=PROHIBITED`` — it won't run by default.
The user must explicitly opt in via ``--sources openrice``.

Scraping approach:
- Search: ``https://www.openrice.com/en/hongkong/restaurants?what={keyword}``
- Restaurant detail: ``.../r-{slug}-r{id}`` → reviews tab
- Plays nice: 1 req / 3 s (slower than httpx sources), Playwright stealth
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, quote

import structlog

from src.scrape.base import (
    FixtureStore,
    PlaywrightManager,
    RobotsCache,
    SourceError,
)
from src.scrape.base.protocol import SourceScraper
from src.scrape.utils.hashing import hash_author
from src.scrape.utils.lang import detect_language
from src.schemas.enums import SignalType, SourceCategory
from src.schemas.raw import RawPost

BASE_URL = "https://www.openrice.com"
SEARCH_URL = f"{BASE_URL}/en/hongkong/restaurants"

# Configurable max restaurants to scrape per topic
MAX_RESTAURANTS = 5
# Configurable max review pages per restaurant
MAX_REVIEW_PAGES = 5
# Rate: Playwright is heavy — 1 req / 3 s
OPENRICE_RATE = 3.0


class OpenriceScraper:
    """Scrape Openrice restaurant reviews."""

    source_id = "openrice"
    region = "HK"
    language = "zh-HK"

    def __init__(
        self,
        *,
        max_restaurants: int = MAX_RESTAURANTS,
        max_review_pages: int = MAX_REVIEW_PAGES,
    ) -> None:
        self._log = structlog.get_logger().bind(scraper="openrice")
        self._robots = RobotsCache()
        self._pw = PlaywrightManager(
            robots_cache=self._robots,
            rate=OPENRICE_RATE,
            headless=True,
        )
        self._fixtures = FixtureStore("openrice")
        self._max_restaurants = max_restaurants
        self._max_review_pages = max_review_pages

    # ------------------------------------------------------------------
    # SourceScraper protocol
    # ------------------------------------------------------------------

    def search(
        self,
        topic: str,
        since: datetime,
        limit: int,
    ) -> Iterator[RawPost]:
        """Search for restaurants matching *topic*, then scrape reviews."""
        self._log.info("openrice.search.start", topic=topic, limit=limit)

        restaurant_urls = self._search_restaurants(topic)
        if not restaurant_urls:
            self._log.warning("openrice.no_restaurants_found", topic=topic)
            return

        emitted = 0
        for rest_url in restaurant_urls:
            if emitted >= limit:
                break
            for review in self._scrape_reviews(rest_url, since=since):
                yield review
                emitted += 1
                if emitted >= limit:
                    break

        self._log.info("openrice.search.done", emitted=emitted)

    def fetch_thread(self, thread_id: str) -> Any:
        """Fetch a single review page.  thread_id = restaurant_url."""
        raise NotImplementedError("Openrice reviews are flat — use search()")

    def close(self) -> None:
        self._pw.close()
        self._robots.close()

    # ------------------------------------------------------------------
    # Internal — search
    # ------------------------------------------------------------------

    def _search_restaurants(self, keyword: str) -> list[str]:
        """Search Openrice and return restaurant detail URLs."""
        search_url = f"{SEARCH_URL}?what={quote(keyword)}"
        urls: list[str] = []

        with self._pw.get_page(search_url) as page:
            page.goto(search_url, wait_until="networkidle", timeout=30000)

            # Save fixture for scrape-doctor
            try:
                html = page.content()
                self._fixtures.save(
                    f"search_{self._slug(keyword)}",
                    html,
                    metadata={"url": search_url, "topic": keyword},
                )
            except Exception as e:
                self._log.warning("openrice.fixture_save_failed", error=str(e))

            # Find restaurant cards — Openrice renders restaurant links
            # in elements like: a[href*="/r-"] containing the restaurant slug
            links = page.query_selector_all('a[href*="/r-"]')
            for link in links[:self._max_restaurants * 3]:
                href = link.get_attribute("href")
                if href and "/r-" in href:
                    # Filter out non-restaurant links (e.g., /review/r-...)
                    parts = href.split("/")
                    if any(p.startswith("r-") and not p.startswith("review") for p in parts):
                        full_url = urljoin(BASE_URL, href)
                        # Deduplicate by base restaurant ID
                        if full_url not in urls:
                            urls.append(full_url)
                        if len(urls) >= self._max_restaurants:
                            break

        self._log.info("openrice.restaurants_found", count=len(urls))
        return urls

    def _scrape_reviews(
        self, rest_url: str, since: datetime
    ) -> Iterator[RawPost]:
        """Scrape reviews for a single restaurant."""
        rest_id = self._extract_rest_id(rest_url)

        for page_num in range(1, self._max_review_pages + 1):
            review_url = f"{rest_url}/reviews?page={page_num}"

            with self._pw.get_page(review_url) as page:
                page.goto(review_url, wait_until="networkidle", timeout=30000)

                # Save fixture
                try:
                    html = page.content()
                    self._fixtures.save(
                        f"reviews_{rest_id}_p{page_num}",
                        html,
                        metadata={"url": review_url, "rest_id": rest_id},
                    )
                except Exception:
                    pass

                # Find review containers
                reviews = page.query_selector_all(
                    '.review-container, [class*="review-item"], '
                    'article[class*="review"]'
                )
                if not reviews:
                    reviews = page.query_selector_all(
                        'div[id*="review"], div[class*="comment"]'
                    )

                if not reviews:
                    self._log.info("openrice.no_reviews", rest_id=rest_id, page=page_num)
                    break

                for review_el in reviews:
                    post = self._parse_review(review_el, rest_url, rest_id)
                    if post is None:
                        continue
                    if post.posted_at < since:
                        return
                    yield post

    # ------------------------------------------------------------------
    # Internal — parsing
    # ------------------------------------------------------------------

    def _parse_review(
        self, el: Any, rest_url: str, rest_id: str
    ) -> RawPost | None:
        """Parse a single review DOM element → RawPost."""
        try:
            # Author
            author_el = el.query_selector(
                '.reviewer-name, [class*="user-name"], [class*="author"]'
            )
            author_raw = author_el.inner_text().strip() if author_el else "anonymous"

            # Rating
            rating_el = el.query_selector(
                '.review-score, [class*="rating"], [class*="score"]'
            )
            rating_text = rating_el.inner_text().strip() if rating_el else ""
            rating = self._parse_rating(rating_text)

            # Date
            date_el = el.query_selector(
                '.review-date, time, [class*="date"]'
            )
            date_text = date_el.inner_text().strip() if date_el else ""
            posted_at = self._parse_date(date_text)

            # Body
            body_el = el.querySelector(
                '.review-content, [class*="review-text"], '
                '[class*="comment-text"], .review-body'
            )
            body = body_el.inner_text().strip() if body_el else ""

            # Title
            title_el = el.querySelector(
                '.review-title, [class*="review-heading"], h3, h4'
            )
            title = title_el.inner_text().strip() if title_el else None

            if not body:
                return None
        except Exception as e:
            self._log.warning("openrice.parse_failed", error=str(e))
            return None

        review_id = f"openrice_{rest_id}_{hash(body[:50])}"
        full_text = f"{title}\n\n{body}" if title else body

        return RawPost(
            id=review_id,
            source="openrice",
            source_category=SourceCategory.REVIEWS,
            region="HK",
            language="zh-HK",
            language_detected=detect_language(full_text),
            url=rest_url,
            author_hash=hash_author(author_raw),
            title=title,
            body=body,
            posted_at=posted_at,
            signal_type=SignalType.EXPERIENCE,
            engagement_metrics={"rating": rating} if rating else {},
            replies=[],
            raw_metadata={
                "rest_id": rest_id,
                "rest_url": rest_url,
                "rating_raw": rating_text,
            },
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_rest_id(url: str) -> str:
        """Extract numeric restaurant ID from URL like .../r-abc-r123."""
        import re
        match = re.search(r"-r(\d+)(?:/|$)", url)
        return match.group(1) if match else url

    @staticmethod
    def _parse_rating(text: str) -> int | None:
        """Parse rating from text like '4.5' or '3/5'."""
        import re
        if not text:
            return None
        # Try "X / 5" format
        m = re.search(r"(\d+(?:\.\d+)?)\s*/\s*5", text)
        if m:
            return int(float(m.group(1)))
        # Try bare number (assume out of 5)
        m = re.search(r"(\d+(?:\.\d+)?)", text)
        if m:
            return int(float(m.group(1)))
        return None

    @staticmethod
    def _parse_date(text: str) -> datetime:
        """Parse Openrice date strings."""
        import re
        from datetime import timedelta

        text = text.lower().strip()
        now = datetime.now(timezone.utc)

        # Relative: "3 days ago", "yesterday", "2 hours ago"
        m = re.search(r"(\d+)\s*(day|hour|week|month)s?\s*ago", text)
        if m:
            n = int(m.group(1))
            unit = m.group(2)
            if unit == "hour":
                return now - timedelta(hours=n)
            elif unit == "day":
                return now - timedelta(days=n)
            elif unit == "week":
                return now - timedelta(weeks=n)
            elif unit == "month":
                return now - timedelta(days=n * 30)

        if text == "yesterday":
            return now - timedelta(days=1)
        if text == "today":
            return now

        # Absolute dates: "2024-01-15", "15 Jan 2024"
        for fmt in ("%Y-%m-%d", "%d %b %Y", "%d %B %Y", "%b %d, %Y"):
            try:
                return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue

        return now

    @staticmethod
    def _slug(s: str) -> str:
        import re
        s = s.lower().strip()
        s = re.sub(r"[^a-z0-9]+", "_", s)
        return s.strip("_")[:50] or "untitled"
