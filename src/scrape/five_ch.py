"""5ch (5channel) scraper — Japan's largest anonymous BBS via itest mirror.

5ch (formerly 2ch) is Japan's largest anonymous textboard. The official site
is heavily rate-limited, but open read-only mirrors exist:
- itest.5ch.net — stable mirror with clean HTML
- agree.5ch.net — alternative mirror

Each thread is a flat list of numbered posts with dates and post bodies.
No auth required on mirrors.

**ToS:** 5ch ToS is silent on read-only mirror access.
Rate limit: 1 req / 3 s.
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import structlog
from bs4 import BeautifulSoup

from src.scrape.base import RobotsCache, SourceError
from src.scrape.base.http import PoliteClient
from src.scrape.utils.hashing import hash_author
from src.scrape.utils.lang import detect_language
from src.schemas.enums import SignalType, SourceCategory
from src.schemas.raw import RawPost

_log = structlog.get_logger(__name__)

BASE_URL = "https://itest.5ch.net"
SEARCH_URL = BASE_URL + "/search?q={query}"
FIVECH_RATE = 3.0
MAX_THREADS = 25


class FiveChScraper:
    """5ch itest mirror scraper."""

    source_id = "five_ch"
    region = "JP"
    language = "ja"
    category = SourceCategory.FORUMS
    signal_type = SignalType.OPINION

    def __init__(
        self,
        *,
        client: PoliteClient | None = None,
        robots_cache: RobotsCache | None = None,
        max_threads: int = MAX_THREADS,
    ) -> None:
        self._max_threads = max_threads
        self._owns_client = client is None
        if client is None:
            self._robots_cache = robots_cache or RobotsCache()
            self._client = PoliteClient(
                robots_cache=self._robots_cache, rate=FIVECH_RATE,
                respect_robots=False,
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
        except SourceError as e:
            _log.warning("five_ch.search_failed", topic=topic, error=str(e))
            return

        thread_urls = _parse_search_results(html)[:self._max_threads]
        emitted = 0

        for turl in thread_urls:
            if emitted >= limit:
                break
            try:
                html = self._client.get_html(turl)
                for post in _parse_thread(html, url=turl):
                    if emitted >= limit:
                        break
                    if post.posted_at >= since:
                        yield post
                        emitted += 1
            except SourceError:
                continue

    def fetch_thread(self, thread_id: str) -> Any:
        html = self._client.get_html(thread_id)
        posts = list(_parse_thread(html, url=thread_id))
        return posts[0] if posts else None


def _parse_search_results(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    for link in soup.select("a[href*='/test/read']"):
        href = link.get("href", "")
        if href.startswith("/"):
            href = BASE_URL + href
        if href not in urls:
            urls.append(href)
    return urls


def _parse_thread(html: str, *, url: str) -> Iterator[RawPost]:
    soup = BeautifulSoup(html, "html.parser")

    title_el = soup.select_one("title") or soup.select_one("h1")
    thread_title = title_el.get_text(strip=True) if title_el else ""

    # 5ch posts are in <div class="post"> or <div class="message"> or <dt>/<dd> pairs
    for post_div in soup.select(".post, .message, .res"):
        try:
            body_el = post_div.select_one(".message, .escaped, dd")
            body = body_el.get_text(strip=True) if body_el else ""

            if len(body) < 10:
                continue

            # Date from post header
            posted_at = datetime.now(timezone.utc)
            date_el = post_div.select_one(".date, dt")
            if date_el:
                date_text = date_el.get_text(strip=True)
                m = re.search(r"(\d{4}/\d{2}/\d{2}|\d{4}-\d{2}-\d{2})\s*[\(（]?\w+[\)）]?\s*(\d{2}:\d{2}:\d{2}|\d{2}:\d{2})", date_text)
                if m:
                    try:
                        dt_str = m.group(0).replace("/", "-").replace("\u3000", " ")[:19]
                        posted_at = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                    except ValueError:
                        pass

            full_text = f"{thread_title}\n\n{body}"
            yield RawPost(
                id=f"five_ch:{abs(hash(full_text))}",
                source="five_ch",
                source_category=SourceCategory.FORUMS,
                region="JP",
                language="ja",
                language_detected=detect_language(full_text),
                url=url,
                author_hash=hash_author("anonymous"),
                title=thread_title or None,
                body=body,
                posted_at=posted_at,
                signal_type=SignalType.OPINION,
                engagement_metrics={},
                replies=[],
                raw_metadata={"thread_url": url},
            )
        except Exception:
            continue
