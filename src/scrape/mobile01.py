"""Mobile01 scraper — Taiwan's largest tech/consumer forum via static HTML.

Mobile01 (mobile01.com) is a server-rendered forum covering everything from
smartphones to cars, home appliances, and lifestyle products. Search is a
GET form submission; thread pages are standard HTML.

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

BASE_URL = "https://www.mobile01.com"
SEARCH_URL = BASE_URL + "/topicsearch.php?f=all&s={query}"
MOBILE01_RATE = 2.0
MAX_THREADS = 25


class Mobile01Scraper:
    """Mobile01 forum scraper."""

    source_id = "mobile01"
    region = "TW"
    language = "zh-TW"
    category = SourceCategory.FORUMS
    signal_type = SignalType.COMPARISON

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
                robots_cache=self._robots_cache, rate=MOBILE01_RATE,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
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
            _log.warning("mobile01.search_failed", topic=topic, error=str(e))
            return

        thread_urls = _parse_search_results(html)[:self._max_threads]
        emitted = 0
        seen: set[str] = set()

        for turl in thread_urls:
            if emitted >= limit:
                break
            if turl in seen:
                continue
            seen.add(turl)
            try:
                html = self._client.get_html(turl)
                post = _parse_thread(html, url=turl)
                if post and post.posted_at >= since:
                    yield post
                    emitted += 1
            except SourceError:
                continue

    def fetch_thread(self, thread_id: str) -> Any:
        url = f"{BASE_URL}/topicdetail.php?f=all&t={thread_id}"
        html = self._client.get_html(url)
        return _parse_thread(html, url=url)


def _parse_search_results(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    for link in soup.select("a[href*='topicdetail']"):
        href = link.get("href", "")
        full = urljoin(BASE_URL, href)
        if full not in urls:
            urls.append(full)
    return urls


def _parse_thread(html: str, *, url: str) -> RawPost | None:
    soup = BeautifulSoup(html, "html.parser")

    title_el = soup.select_one("h1") or soup.select_one(".topic-title") or soup.find("title")
    title = title_el.get_text(strip=True) if title_el else ""

    author_el = soup.select_one(".username") or soup.select_one("[class*='author']")
    author = author_el.get_text(strip=True) if author_el else ""

    body_parts = []
    content_el = soup.select_one(".single-post-content") or soup.select_one("[class*='content']")
    if content_el:
        body_parts.append(content_el.get_text(chr(10), strip=True))

    NL = chr(10)
    body = (NL * 2).join(body_parts).strip()
    if len(body) < 10:
        return None

    # Date
    date_match = re.search(r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})", html)
    if date_match:
        try:
            posted_at = datetime.strptime(date_match.group(1), "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        except ValueError:
            posted_at = datetime.now(timezone.utc)
    else:
        posted_at = datetime.now(timezone.utc)

    reply_count = 0
    reply_el = soup.select_one("[class*='reply-count']") or soup.select_one("[class*='reply']")
    if reply_el:
        m = re.search(r"(\d+)", reply_el.get_text(strip=True))
        if m:
            reply_count = int(m.group(1))

    thread_id = url.split("t=")[-1].split("&")[0] if "t=" in url else ""
    full_text = f"{title}\n\n{body}"

    return RawPost(
        id=f"mobile01:{thread_id}",
        source="mobile01",
        source_category=SourceCategory.FORUMS,
        region="TW",
        language="zh-TW",
        language_detected=detect_language(full_text),
        url=url,
        author_hash=hash_author(author),
        title=title or None,
        body=body,
        posted_at=posted_at,
        signal_type=SignalType.COMPARISON,
        engagement_metrics={"replies": reply_count},
        replies=[],
        raw_metadata={"thread_id": thread_id},
    )
