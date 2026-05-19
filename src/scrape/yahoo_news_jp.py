"""Yahoo News Japan scraper — news.yahoo.co.jp.

Note: news.yahoo.co.jp runs on a separate platform from Yahoo's caas-*
CMS used by TW / US / HK. Selectors here are tuned for ``news.yahoo.co.jp``
article pages (article-body, pickup paths) but they may need adjustment
when run against a live capture — flagged for first-run iteration.

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

BASE_URL = "https://news.yahoo.co.jp"
SEARCH_URL = BASE_URL + "/search?p={query}&ei=UTF-8"
YAHOO_JP_RATE = 3.0
MAX_ARTICLES = 20

# news.yahoo.co.jp article paths:
#   /articles/<hash>             — primary article page
#   /pickup/<id>                  — picked-up / aggregated article
# Both are treated as articles. Topic / category landing pages have
# distinct shapes and are filtered out.
_ARTICLE_PATH_RE = re.compile(r"/(articles|pickup)/[A-Za-z0-9_-]+/?$")


class YahooNewsJPScraper:
    """Yahoo News Japan scraper."""

    source_id = "yahoo_news_jp"
    region = "JP"
    language = "ja"
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
                robots_cache=self._robots_cache, rate=YAHOO_JP_RATE,
                headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "ja"},
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
            _log.warning("yahoo_news_jp.search_failed", topic=topic, error=str(e))
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
    """Extract Yahoo News JP article URLs from a search-results page."""
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    seen: set[str] = set()
    for link in soup.select("a[href]"):
        href = link.get("href", "")
        if not href:
            continue
        # Normalize relative URLs.
        if href.startswith("/"):
            href = BASE_URL + href
        if BASE_URL not in href:
            continue
        path = href.split(BASE_URL, 1)[-1].split("?", 1)[0]
        if not _ARTICLE_PATH_RE.search(path):
            continue
        if href in seen:
            continue
        seen.add(href)
        urls.append(href)
    return urls


def _parse_article(html: str, *, url: str) -> RawPost | None:
    """Parse a Yahoo News JP article page → RawPost."""
    soup = BeautifulSoup(html, "html.parser")

    # Title — news.yahoo.co.jp wraps in h1 inside an article element.
    title_el = (
        soup.select_one("article h1")
        or soup.select_one("h1")
        or soup.find("title")
    )
    title = title_el.get_text(strip=True) if title_el else ""

    # Body — JP page uses .article_body, .articleBody, or paragraphs in <article>.
    body_parts: list[str] = []
    selectors = (
        ".article_body p",
        ".articleBody p",
        "[data-component='ArticleBody'] p",
        "article p",
    )
    for sel in selectors:
        nodes = soup.select(sel)
        if nodes:
            for p in nodes:
                text = p.get_text(strip=True)
                if text and len(text) > 10:
                    body_parts.append(text)
            break
    body = "\n\n".join(body_parts).strip()

    # Author / source publication (Japanese news sites often credit the
    # publication, not an author).
    author_el = (
        soup.select_one(".source")
        or soup.select_one("[class*='source']")
        or soup.select_one("[class*='credit']")
    )
    author = author_el.get_text(strip=True) if author_el else ""

    # Date
    posted_at = datetime.now(timezone.utc)
    time_el = soup.select_one("time[datetime]")
    if time_el:
        dt = time_el.get("datetime", "")
        if dt:
            try:
                posted_at = datetime.fromisoformat(dt.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass

    comment_count = 0
    # JP page has コメント count under .commentCount / [class*='comment'].
    for sel in (".commentCount", "[class*='CommentCount']", "[class*='comment']"):
        el = soup.select_one(sel)
        if el:
            m = re.search(r"(\d[\d,]*)", el.get_text(strip=True))
            if m:
                try:
                    comment_count = int(m.group(1).replace(",", ""))
                    break
                except ValueError:
                    continue

    full_text = f"{title}\n\n{body}"
    if len(full_text) < 20:
        return None

    return RawPost(
        id=f"yahoo_news_jp:{abs(hash(url))}",
        source="yahoo_news_jp",
        source_category=SourceCategory.NEWS_COMMENTS,
        region="JP",
        language="ja",
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
# scrape-doctor check.
# ---------------------------------------------------------------------------


def doctor_check(name: str, html: str, meta: dict) -> tuple[bool, str]:
    if "search" in name:
        urls = _parse_search_results(html)
        if not urls:
            return False, "_parse_search_results returned 0 article URLs"
        return True, f"_parse_search_results OK ({len(urls)} URLs)"
    if "article" in name:
        post = _parse_article(html, url="https://news.yahoo.co.jp/articles/test")
        if post is None:
            return False, "_parse_article returned None (title+body < 20 chars)"
        return True, (
            f"_parse_article OK (title={(post.title or '')[:40]!r}, "
            f"{len(post.body)} chars body)"
        )
    return True, f"{len(html)} bytes (no specific check for {name})"
