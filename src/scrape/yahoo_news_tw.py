"""Yahoo News Taiwan scraper — news articles with comment sections.

Yahoo News Taiwan (tw.news.yahoo.com) publishes local news articles with
embedded comment sections. Articles are static HTML. Comments load via
a separate API endpoint that we can hit directly.

**ToS:** Silent. Rate limit: 1 req / 3 s.
"""
from __future__ import annotations

import json
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

BASE_URL = "https://tw.news.yahoo.com"
SEARCH_URL = BASE_URL + "/search?p={query}"
YAHOO_RATE = 3.0
MAX_ARTICLES = 20


class YahooNewsTWScraper:
    """Yahoo News Taiwan scraper."""

    source_id = "yahoo_news_tw"
    region = "TW"
    language = "zh-TW"
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
            _log.warning("yahoo_news_tw.search_failed", topic=topic, error=str(e))
            return

        article_urls = _parse_search_results(html)[:self._max_articles]
        emitted = 0

        for aurl in article_urls:
            if emitted >= limit:
                break
            try:
                html = self._client.get_html(aurl)
                post = _parse_article(html, url=aurl)
                if post and post.posted_at >= since:
                    yield post
                    emitted += 1
            except SourceError:
                continue

    def fetch_thread(self, thread_id: str) -> Any:
        html = self._client.get_html(thread_id)
        return _parse_article(html, url=thread_id)


def _parse_search_results(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    seen: set[str] = set()
    for link in soup.select("a[href]"):
        href = link.get("href", "")
        if "tw.news.yahoo.com/" in href and "/" not in href.split("tw.news.yahoo.com/")[-1][:50]:
            continue
        if "tw.news.yahoo.com/" in href and href not in seen:
            seen.add(href)
            urls.append(href)
    return urls


def _parse_article(html: str, *, url: str) -> RawPost | None:
    soup = BeautifulSoup(html, "html.parser")

    # Title
    title_el = soup.select_one("h1") or soup.select_one("[data-test-locator='headline']") or soup.find("title")
    title = title_el.get_text(strip=True) if title_el else ""

    # Body
    body_parts = []
    for p in soup.select("article p, [data-test-locator='body'] p, .caas-body p"):
        text = p.get_text(strip=True)
        if text and len(text) > 20:
            body_parts.append(text)
    body = "\n\n".join(body_parts).strip()

    # Author
    author_el = soup.select_one("[data-test-locator='author']") or soup.select_one(".caas-attr-item-author")
    author = author_el.get_text(strip=True) if author_el else ""

    # Date
    posted_at = datetime.now(timezone.utc)
    time_el = soup.select_one("time[datetime]") or soup.select_one("[data-test-locator='publish-date']")
    if time_el:
        dt = time_el.get("datetime", "")
        if dt:
            try:
                posted_at = datetime.fromisoformat(dt.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass

    comment_count = 0
    comment_el = soup.select_one("[data-test-locator='comment-count']") or soup.select_one(".comment-count")
    if comment_el:
        m = re.search(r"(\d+)", comment_el.get_text(strip=True))
        if m:
            comment_count = int(m.group(1))

    full_text = f"{title}\n\n{body}"
    if len(full_text) < 20:
        return None

    return RawPost(
        id=f"yahoo_news_tw:{abs(hash(url))}",
        source="yahoo_news_tw",
        source_category=SourceCategory.NEWS_COMMENTS,
        region="TW",
        language="zh-TW",
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
