"""LIHKG forum scraper — public JSON API.

LIHKG (https://lihkg.com) exposes a mobile-app JSON API that requires no
authentication.  This scraper uses the thread listing and thread-detail
endpoints to search for topics and return ``RawPost`` records.

API structure::

    GET /api/v2/thread/category?cat_id=1&page=1&count=30&type=now
      → response.items[]  — thread previews

    GET /api/v2/thread/{thread_id}/page/1
      → response.items[]  — first post + replies

    GET /api/v2/search/threads?q={query}&page=1&count=30
      → response.items[]  — search results (if available)

Rate limit: 1 req / 2 s (conservative — registry notes say "Keep <1 req/2s").
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urljoin

import structlog

from src.scrape.base import PoliteClient, RobotsCache, SourceError
from src.scrape.base.protocol import SourceScraper
from src.scrape.utils.hashing import hash_author
from src.scrape.utils.lang import detect_language
from src.schemas.raw import RawPost

BASE_URL = "https://lihkg.com"
API_BASE = f"{BASE_URL}/api/v2"

# LIHKG "all categories" — cat_id=1 returns threads from all categories
# when combined with type=now (hot).
ALL_CATEGORY_ID = 1

# Conservative rate: 1 req / 2 s
LIHKG_RATE = 2.0


class LIHKGScraper:
    """Scrape LIHKG forum threads for a given topic."""

    source_id = "lihkg"
    region = "HK"
    language = "zh-HK"

    def __init__(self) -> None:
        self._log = structlog.get_logger().bind(scraper="lihkg")
        self._robots = RobotsCache()
        self._client = PoliteClient(robots_cache=self._robots, rate=LIHKG_RATE)
        self._salt = None  # Initialised lazily from env

    # ------------------------------------------------------------------
    # SourceScraper protocol
    # ------------------------------------------------------------------

    def search(
        self,
        topic: str,
        since: datetime,
        limit: int,
    ) -> Iterator[RawPost]:
        """Yield ``RawPost`` records for threads matching *topic* since *since*.

        Uses LIHKG's category listing endpoint, filtered client-side by keyword
        and date.  If LIHKG has a search endpoint, we try that first.
        """
        emitted = 0
        self._log.info("lihkg.search.start", topic=topic, limit=limit)

        # Try keyword search endpoint first
        try:
            posts, reached_end = self._search_by_keyword(topic, since, limit)
            for p in posts:
                if emitted >= limit:
                    break
                yield p
                emitted += 1
            if emitted >= limit or reached_end:
                self._log.info("lihkg.search.done", emitted=emitted)
                return
        except Exception:
            self._log.warning("lihkg.search.keyword_failed", exc_info=True)

        # Fall back to category browsing + client-side keyword filter
        yield from self._search_by_category(topic, since, limit - emitted)

    def fetch_thread(self, thread_id: str) -> Any:
        """Fetch a full thread with replies."""
        resp = self._client.get_json(
            f"{API_BASE}/thread/{thread_id}/page/1"
        )
        return resp.get("response", {})

    def close(self) -> None:
        self._client.close()
        self._robots.close()

    # ------------------------------------------------------------------
    # Internal — search
    # ------------------------------------------------------------------

    def _search_by_keyword(
        self, topic: str, since: datetime, limit: int
    ) -> tuple[list[RawPost], bool]:
        """Try the search endpoint. Returns (posts, reached_end)."""
        results: list[RawPost] = []

        for page in range(1, 11):  # Max 10 pages
            url = f"{API_BASE}/search/threads?q={quote(topic)}&page={page}&count=30"
            data = self._client.get_json(url)
            items = data.get("response", {}).get("items", [])

            if not items:
                break

            for item in items:
                post = self._thread_item_to_post(item)
                if post is None:
                    continue
                if post.created_at and post.created_at < since:
                    return results, True  # Reached the time cutoff
                results.append(post)
                if len(results) >= limit:
                    return results, True

        return results, len(results) < limit

    def _search_by_category(
        self, topic: str, since: datetime, limit: int
    ) -> Iterator[RawPost]:
        """Browse category listings and filter by keyword client-side."""
        topic_lower = topic.lower()
        emitted = 0

        for page in range(1, 21):  # Max 20 pages
            url = (
                f"{API_BASE}/thread/category"
                f"?cat_id={ALL_CATEGORY_ID}&page={page}&count=30&type=now"
            )
            data = self._client.get_json(url)
            items = data.get("response", {}).get("items", [])

            if not items:
                break

            for item in items:
                title = (item.get("title") or "").lower()
                excerpt = (item.get("excerpt") or "").lower()
                if topic_lower not in title and topic_lower not in excerpt:
                    continue

                post = self._thread_item_to_post(item)
                if post is None:
                    continue
                if post.created_at and post.created_at < since:
                    return  # Time cutoff reached
                yield post
                emitted += 1
                if emitted >= limit:
                    return

    # ------------------------------------------------------------------
    # Internal — mapping
    # ------------------------------------------------------------------

    def _thread_item_to_post(self, item: dict) -> RawPost | None:
        """Convert a LIHKG thread item dict → RawPost."""
        thread_id = str(item.get("thread_id", ""))
        if not thread_id:
            return None

        title = item.get("title") or ""
        excerpt = item.get("excerpt") or ""

        # Combine title + excerpt as the post body
        body = f"{title}\n\n{excerpt}" if excerpt else title

        created_ts = item.get("create_time")
        created_at = (
            datetime.fromtimestamp(created_ts, tz=timezone.utc)
            if created_ts
            else None
        )

        # Author
        author_raw = str(item.get("user_nickname") or item.get("user_id") or "anonymous")
        author_hash = hash_author(author_raw, self._salt)

        # Language detection on the combined body
        lang = detect_language(body)

        # Category
        cat = item.get("category", {}) or {}
        cat_name = cat.get("name")

        # URL
        url = f"{BASE_URL}/thread/{thread_id}"

        return RawPost(
            id=f"lihkg_{thread_id}",
            source="lihkg",
            source_category="forums",
            region="HK",
            language="zh-HK",
            language_detected=lang,
            url=url,
            title=title,
            body=body,
            author_hash=author_hash,
            posted_at=created_at or datetime.now(timezone.utc),
            signal_type="opinion",
            engagement_metrics={
                "reply_count": item.get("total_replies", 0),
                "likes": item.get("like_count", 0),
            },
            raw_metadata={
                "thread_id": thread_id,
                "cat_id": item.get("cat_id"),
                "cat_name": cat_name,
                "sub_cat_id": item.get("sub_cat_id"),
                "pin": item.get("pin", 0),
            },
        )
