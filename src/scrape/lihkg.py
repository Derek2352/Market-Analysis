"""LIHKG forum scraper via Playwright (headless browser).

LIHKG is behind Cloudflare anti-bot protection, blocking direct HTTP/JSON
requests. This scraper uses Playwright to bypass Cloudflare by running a
real Chromium browser that handles the JavaScript challenge automatically.

Scrapes HTML listing pages (category browse + search) and parses thread
data from the rendered DOM.

Usage::

    mkt scrape --topic "MTR" --region HK --sources lihkg --limit 100

Rate limit: 1 req / 3 s (Playwright is slower than httpx).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone, timedelta
from typing import Any
from urllib.parse import quote

import structlog
from bs4 import BeautifulSoup

from src.scrape.base import RobotsCache, PlaywrightManager
from src.scrape.utils.hashing import hash_author
from src.scrape.utils.lang import detect_language
from src.schemas.enums import SignalType, SourceCategory
from src.schemas.raw import RawPost

BASE_URL = "https://lihkg.com"
LIHKG_RATE = 3.0  # 1 req / 3 s (Playwright is heavy)
MAX_PAGES = 5
CATEGORY_ID = 1  # "all" category


class LIHKGScraper:
    """Scrape LIHKG forum threads via Playwright browser."""

    source_id = "lihkg"
    region = "HK"
    language = "zh-HK"

    def __init__(self) -> None:
        self._log = structlog.get_logger().bind(scraper="lihkg")
        self._robots = RobotsCache()
        self._pw = PlaywrightManager(
            robots_cache=self._robots,
            rate=LIHKG_RATE,
            headless=True,
            respect_robots=False,
        )

    # ------------------------------------------------------------------
    # SourceScraper protocol
    # ------------------------------------------------------------------

    def search(
        self,
        topic: str,
        since: datetime,
        limit: int,
    ) -> Iterator[RawPost]:
        """Search LIHKG for *topic*.

        Strategy: try hot category first (finds trending topics quickly),
        then fall back to latest category for niche topics.
        """
        self._log.info("lihkg.search.start", topic=topic, limit=limit)

        # Try hot category first
        emitted = list(self._search_category(topic, since, limit, "now"))
        if emitted:
            yield from emitted
            self._log.info("lihkg.search.done", emitted=len(emitted))
            return

        # Fallback: latest category (catches niche topics)
        self._log.info("lihkg.search.fallback_latest", topic=topic)
        emitted2 = list(self._search_category(topic, since, limit, "latest"))
        yield from emitted2
        self._log.info("lihkg.search.done", emitted=len(emitted2))

    def _search_category(
        self, topic: str, since: datetime, limit: int, sort: str
    ) -> Iterator[RawPost]:
        """Browse a LIHKG category listing and filter by keyword."""
        emitted = 0
        seen_ids: set[str] = set()
        topic_lower = topic.lower()

        for page in range(1, MAX_PAGES + 1):
            if emitted >= limit:
                break

            url = (
                f"{BASE_URL}/category/{CATEGORY_ID}"
                f"?page={page}&type={sort}"
            )

            try:
                html = self._fetch_page(url)
                posts = self._parse_thread_list(html, topic_lower)
            except Exception:
                self._log.warning(
                    "lihkg.page_failed", page=page, sort=sort, exc_info=True
                )
                continue

            if not posts:
                break

            for post in posts:
                if post.id in seen_ids:
                    continue
                seen_ids.add(post.id)

                if post.posted_at < since:
                    continue

                yield post
                emitted += 1
                if emitted >= limit:
                    break

    def fetch_thread(self, thread_id: str) -> Any:
        """Fetch a full thread with replies."""
        url = f"{BASE_URL}/thread/{thread_id}"
        return self._fetch_page(url)

    def close(self) -> None:
        self._pw.close()
        self._robots.close()

    # ------------------------------------------------------------------
    # Internal — page fetching
    # ------------------------------------------------------------------

    def _fetch_page(self, url: str) -> str:
        """Fetch rendered HTML for *url* via Playwright."""
        with self._pw.get_page(url) as page:
            page.goto(url, wait_until="networkidle", timeout=30000)
            # Extra wait for Cloudflare challenge + JS rendering
            page.wait_for_timeout(2000)
            return page.content()

    # ------------------------------------------------------------------
    # Internal — parsing
    # ------------------------------------------------------------------

    def _parse_thread_list(
        self, html: str, topic_lower: str
    ) -> list[RawPost]:
        """Parse LIHKG category page HTML → RawPost list.

        LIHKG uses minimal HTML: each thread is a div containing an
        ``<a href="/thread/ID/page/1">Title [reply_count]</a>`` link.
        We find page-1 links, then extract text from their parent div.
        """
        import re
        soup = BeautifulSoup(html, "html.parser")
        results: list[RawPost] = []

        # Find unique thread page-1 links
        seen_tids: set[str] = set()

        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            m = re.search(r"/thread/(\d+)/page/1", href)
            if not m:
                continue
            tid = m.group(1)
            if tid in seen_tids:
                continue
            seen_tids.add(tid)

            # Title from the link text
            raw_title = a.get_text(strip=True)

            # LIHKG titles often have [tag] prefix and [reply_count] suffix
            # Strip the trailing [N] reply count
            title = re.sub(r"\s*\[\d+\]\s*$", "", raw_title).strip()
            # Also strip leading [tag]
            title = re.sub(r"^\[.*?\]\s*", "", title).strip()

            if topic_lower not in title.lower() and topic_lower not in raw_title.lower():
                continue

            # Parent container has all metadata
            container = a.find_parent("div")
            container_text = container.get_text(" ", strip=True) if container else ""

            # Try to extract author from container text
            # LIHKG format: "AuthorName · 2025-05-17 · 123 replies · 45 likes"
            author_raw = "anonymous"
            author_match = re.search(
                r"([^\s·]+?)\s*·\s*\d{4}-\d{2}-\d{2}", container_text
            )
            if author_match:
                author_raw = author_match.group(1).strip()

            # Reply count from container or title suffix
            reply_count = 0
            reply_match = re.search(r"\[(\d+)\]\s*$", raw_title)
            if reply_match:
                reply_count = int(reply_match.group(1))
            else:
                reply_match = re.search(r"(\d+)\s*repl", container_text, re.IGNORECASE)
                if reply_match:
                    reply_count = int(reply_match.group(1))

            # Like count
            like_count = 0
            like_match = re.search(r"(\d+)\s*lik", container_text, re.IGNORECASE)
            if like_match:
                like_count = int(reply_match.group(1))

            # Body = title (no excerpt available in listing)
            body = title

            # Timestamp
            posted_at = datetime.now(timezone.utc)
            time_match = re.search(r"(\d{4}-\d{2}-\d{2}\s*\d{2}:\d{2})", container_text)
            if time_match:
                try:
                    posted_at = datetime.strptime(
                        time_match.group(1), "%Y-%m-%d %H:%M"
                    ).replace(tzinfo=timezone.utc)
                except ValueError:
                    pass

            # Language
            lang = detect_language(body)
            author_hash_val = hash_author(author_raw)

            url = f"{BASE_URL}/thread/{tid}"

            results.append(RawPost(
                id=f"lihkg_{tid}",
                source="lihkg",
                source_category=SourceCategory.FORUMS,
                region="HK",
                language="zh-HK",
                language_detected=lang,
                url=url,
                author_hash=author_hash_val,
                title=title,
                body=body,
                posted_at=posted_at,
                signal_type=SignalType.OPINION,
                engagement_metrics={
                    "reply_count": reply_count,
                    "likes": like_count,
                },
                raw_metadata={
                    "thread_id": tid,
                    "raw_title": raw_title,
                },
            ))

        return results
