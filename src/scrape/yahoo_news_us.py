"""Yahoo News US scraper — news articles with comment-count metadata.

news.yahoo.com runs on the same Yahoo "caas" CMS as tw.news.yahoo.com, so
the parser is the same shape as ``yahoo_news_tw``; only the domain,
``region``, ``language``, and ``source_id`` differ.

**ToS:** Silent. Rate limit: 1 req / 3 s. Honors robots.txt.
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

BASE_URL = "https://news.yahoo.com"
SEARCH_URL = BASE_URL + "/search?p={query}"
YAHOO_RATE = 3.0
MAX_ARTICLES = 20


class YahooNewsUSScraper:
    """Yahoo News US scraper."""

    source_id = "yahoo_news_us"
    region = "US"
    language = "en"
    category = SourceCategory.NEWS_COMMENTS
    signal_type = SignalType.OPINION

    def __init__(
        self,
        *,
        client: PoliteClient | None = None,
        robots_cache: RobotsCache | None = None,
        max_articles: int = MAX_ARTICLES,
    ) -> None:
        self._max_articles = max_articles
        self._owns_client = client is None
        if client is None:
            self._robots_cache = robots_cache or RobotsCache()
            self._client = PoliteClient(
                robots_cache=self._robots_cache, rate=YAHOO_RATE,
                headers={"User-Agent": "Mozilla/5.0"},
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
            _log.warning("yahoo_news_us.search_failed", topic=topic, error=str(e))
            return

        article_urls = _parse_search_results(html)[:self._max_articles]
        emitted = 0

        for aurl in article_urls:
            if emitted >= limit:
                break
            try:
                page_html = self._client.get_html(aurl)
                post = _parse_article(page_html, url=aurl)
                if post and post.posted_at >= since:
                    yield post
                    emitted += 1
            except SourceError:
                continue

    def fetch_thread(self, thread_id: str) -> Any:
        html = self._client.get_html(thread_id)
        return _parse_article(html, url=thread_id)


# ---------------------------------------------------------------------------
# Module-level parsers (testable offline against the saved HTML fixtures).
# ---------------------------------------------------------------------------


def _parse_search_results(html: str) -> list[str]:
    """Extract Yahoo News US article URLs from a search-results page.

    Yahoo News US articles live at hyphenated-slug paths ending in ``.html``
    (e.g. ``/apple-pay-transit-asia-110200789.html``) or under ``/article/``.
    Category and section pages (``/category/world/``) and the host root must
    be filtered out.
    """
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    seen: set[str] = set()
    for link in soup.select("a[href]"):
        href = link.get("href", "")
        if "news.yahoo.com/" not in href:
            continue
        if not (href.endswith(".html") or "/article/" in href):
            continue
        if href in seen:
            continue
        seen.add(href)
        urls.append(href)
    return urls


def _parse_article(html: str, *, url: str) -> RawPost | None:
    """Parse a Yahoo News US article page → RawPost."""
    soup = BeautifulSoup(html, "html.parser")

    # Title
    title_el = (
        soup.select_one("h1")
        or soup.select_one("[data-test-locator='headline']")
        or soup.find("title")
    )
    title = title_el.get_text(strip=True) if title_el else ""

    # Body
    body_parts: list[str] = []
    for p in soup.select("article p, [data-test-locator='body'] p, .caas-body p"):
        text = p.get_text(strip=True)
        if text and len(text) > 20:
            body_parts.append(text)
    body = "\n\n".join(body_parts).strip()

    # Author
    author_el = (
        soup.select_one("[data-test-locator='author']")
        or soup.select_one(".caas-attr-item-author")
    )
    author = author_el.get_text(strip=True) if author_el else ""

    # Date
    posted_at = datetime.now(timezone.utc)
    time_el = soup.select_one("time[datetime]") or soup.select_one(
        "[data-test-locator='publish-date']"
    )
    if time_el:
        dt = time_el.get("datetime", "")
        if dt:
            try:
                posted_at = datetime.fromisoformat(dt.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass

    comment_count = 0
    comment_el = soup.select_one("[data-test-locator='comment-count']") or soup.select_one(
        ".comment-count"
    )
    if comment_el:
        m = re.search(r"(\d+)", comment_el.get_text(strip=True))
        if m:
            comment_count = int(m.group(1))

    full_text = f"{title}\n\n{body}"
    if len(full_text) < 20:
        return None

    return RawPost(
        id=f"yahoo_news_us:{abs(hash(url))}",
        source="yahoo_news_us",
        source_category=SourceCategory.NEWS_COMMENTS,
        region="US",
        language="en",
        language_detected=detect_language(full_text),
        url=url,
        author_hash=hash_author(author),
        title=title or None,
        body=body or full_text,
        posted_at=posted_at,
        signal_type=SignalType.OPINION,
        engagement_metrics={"comments": comment_count},
        replies=[],
        raw_metadata={},
    )


# ---------------------------------------------------------------------------
# scrape-doctor check — same convention as Phase 8 scrapers.
# ---------------------------------------------------------------------------


def doctor_check(name: str, html: str, meta: dict) -> tuple[bool, str]:
    """Doctor hook: dispatch on fixture filename to the right parser."""
    if "search" in name:
        urls = _parse_search_results(html)
        if not urls:
            return False, "_parse_search_results returned 0 article URLs"
        return True, f"_parse_search_results OK ({len(urls)} URLs)"
    if "article" in name:
        post = _parse_article(html, url="https://news.yahoo.com/test")
        if post is None:
            return False, "_parse_article returned None (title+body < 20 chars)"
        return True, (
            f"_parse_article OK (title={(post.title or '')[:40]!r}, "
            f"{len(post.body)} chars body)"
        )
    return True, f"{len(html)} bytes (no specific check for {name})"
