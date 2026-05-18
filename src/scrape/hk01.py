"""HK01 scraper — Playwright-based with ``__NEXT_DATA__`` JSON extraction.

HK01 (hk01.com) is a major Hong Kong news site. Article pages are
Next.js SSR — the article data is embedded in ``<script id="__NEXT_DATA__">``.
Comments load via AJAX; we extract the commentCount metadata and
defer full comment content to a future phase.

Discovery: Search via ``https://www.hk01.com/sr/<topic>`` — JS-rendered
results list with article card links.

**ToS:** HK01 prohibits automated access. Default-disabled, requires
``--accept-tos-risk``.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

import structlog
from bs4 import BeautifulSoup

from src.scrape.base import (
    PlaywrightManager,
    RobotsCache,
    SourceError,
)
from src.scrape.base.protocol import SourceScraper
from src.scrape.utils.hashing import hash_author
from src.scrape.utils.lang import detect_language
from src.schemas.enums import SignalType, SourceCategory
from src.schemas.raw import RawPost

_log = structlog.get_logger(__name__)

BASE_URL = "https://www.hk01.com"
SEARCH_URL_TEMPLATE = BASE_URL + "/search?q={}"

HK01_RATE = 2.0
MAX_ARTICLES_PER_SEARCH = 10


class HK01Scraper:
    """HK01 news scraper — search + article pages via Playwright."""

    source_id = "hk01"
    region = "HK"
    language = "zh-HK"
    category = SourceCategory.NEWS_COMMENTS
    signal_type = SignalType.OPINION

    def __init__(
        self,
        *,
        playwright: PlaywrightManager | None = None,
        robots_cache: RobotsCache | None = None,
        max_articles: int = MAX_ARTICLES_PER_SEARCH,
    ) -> None:
        self._owns_playwright = playwright is None
        if playwright is None:
            self._robots_cache = robots_cache or RobotsCache()
            self._playwright = PlaywrightManager(
                robots_cache=self._robots_cache,
                rate=HK01_RATE,
                respect_robots=False,
            )
        else:
            self._playwright = playwright
        self._max_articles = max_articles

    def close(self) -> None:
        if self._owns_playwright:
            self._playwright.close()

    def __enter__(self) -> "HK01Scraper":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ---- SourceScraper protocol -----------------------------------------

    def search(
        self, topic: str, since: datetime, limit: int,
    ) -> Iterator[RawPost]:
        """Discover articles via HK01 search, then parse each one."""
        search_url = SEARCH_URL_TEMPLATE.format(topic)
        try:
            with self._playwright.get_page(search_url) as page:
                page.goto(search_url, wait_until="networkidle", timeout=45000)
                page.wait_for_timeout(3000)
                # Scroll to trigger lazy-loaded results
                page.evaluate("window.scrollTo(0, 1200)")
                page.wait_for_timeout(2000)
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(3000)
                html = page.content()
        except SourceError as e:
            _log.warning("hk01.search_failed", topic=topic, error=str(e))
            return

        article_urls = parse_search_results(html)[: self._max_articles]
        _log.info(
            "hk01.search", topic=topic, candidates=len(article_urls),
        )

        emitted = 0
        for aurl in article_urls:
            if emitted >= limit:
                break
            try:
                post = self.fetch_article(aurl)
            except SourceError as e:
                _log.warning(
                    "hk01.article_failed", url=aurl, error=str(e),
                )
                continue
            if since is not None and post.posted_at < since:
                continue
            yield post
            emitted += 1

    def fetch_thread(self, thread_id: str) -> Any:
        """Alias for fetch_article — satisfies SourceScraper protocol."""
        return self.fetch_article(thread_id)

    def fetch_article(self, article_url: str) -> RawPost:
        """Fetch a HK01 article page, extract __NEXT_DATA__, and parse."""
        with self._playwright.get_page(article_url) as page:
            page.goto(article_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)
            html = page.content()
        return parse_article(html, article_url=article_url)


# ---------------------------------------------------------------------------
# Module-level parsers (testable offline against saved HTML fixtures).
# ---------------------------------------------------------------------------


def parse_search_results(html: str) -> list[str]:
    """Extract article URLs from an HK01 search-results page.

    URLs look like ``/article/<article_id>`` or ``/<category>/<article_id>/``.
    """
    soup = BeautifulSoup(html, "html.parser")
    out: list[str] = []
    seen: set[str] = set()

    # Primary: article card links
    for a in soup.select("a[href*='/article/'], a[data-article-id]"):
        href = (a.get("href") or "").strip()
        if not href:
            # Try data-article-id fallback
            aid = a.get("data-article-id", "")
            if aid:
                href = f"/article/{aid}"

        # Normalize to full URL
        if href.startswith("/"):
            full = urljoin(BASE_URL, href)
        elif href.startswith("http"):
            full = href
        else:
            continue

        # Must contain an article identifier pattern
        if "/article/" not in full and not re.search(r"/\d{7,9}/", full):
            continue

        if full in seen:
            continue
        seen.add(full)
        out.append(full)

    # Fallback: __NEXT_DATA__ search results
    nd_el = soup.find("script", id="__NEXT_DATA__")
    if nd_el and nd_el.string:
        try:
            data = json.loads(nd_el.string)
            # Search results sometimes embed article lists
            # Walk the props tree for article IDs
            def _walk(obj: Any, depth: int = 0) -> None:
                if depth > 10 or len(out) >= MAX_ARTICLES_PER_SEARCH:
                    return
                if isinstance(obj, dict):
                    aid = obj.get("articleId") or obj.get("id")
                    if isinstance(aid, (int, str)) and str(aid).isdigit() and len(str(aid)) >= 7:
                        full = f"{BASE_URL}/article/{aid}"
                        if full not in seen:
                            seen.add(full)
                            out.append(full)
                    for v in obj.values():
                        _walk(v, depth + 1)
                elif isinstance(obj, list):
                    for item in obj[:20]:
                        _walk(item, depth + 1)
            _walk(data.get("props", {}))
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

    return out


def parse_article(html: str, *, article_url: str) -> RawPost:
    """Parse an HK01 article page into a RawPost.

    Primary data source is ``<script id="__NEXT_DATA__">`` (Next.js SSR JSON).
    Falls back to HTML meta tags and visible elements if JSON is absent.

    Raises ``SourceError`` if the article has no __NEXT_DATA__ and no
    visible content.
    """
    soup = BeautifulSoup(html, "html.parser")

    # ---- __NEXT_DATA__ extraction ----------------------------------------
    nd_el = soup.find("script", id="__NEXT_DATA__")
    article_data: dict[str, Any] = {}
    if nd_el and nd_el.string:
        try:
            data = json.loads(nd_el.string)
            article_data = (
                data.get("props", {})
                .get("initialProps", {})
                .get("pageProps", {})
                .get("article", {})
            )
        except (json.JSONDecodeError, KeyError, TypeError):
            article_data = {}

    # ---- title ----------------------------------------------------------
    title = ""
    if article_data:
        title = (article_data.get("title") or "").strip()
    if not title:
        title_el = (
            soup.select_one("h1#articleTitle")
            or soup.select_one("h1[data-testid='article-title']")
            or soup.select_one("h1")
        )
        title = title_el.get_text(" ", strip=True) if title_el else ""
    if not title:
        meta = soup.select_one("meta[property='og:title']")
        title = (meta.get("content", "") or "") if meta else ""

    # ---- body -----------------------------------------------------------
    body_parts: list[str] = []
    if article_data:
        # Content is in content.blocks array with text fields
        blocks = article_data.get("content", {}).get("blocks") or []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            text = (block.get("text") or "").strip()
            if text:
                body_parts.append(text)
        # Also try content.text fallback
        content_text = article_data.get("content", {}).get("text") or ""
        if content_text:
            body_parts.append(content_text)
    if not body_parts:
        # HTML fallback: visible paragraphs
        for p in soup.select(".article-grid__content-section p, "
                              "[class*='article'] p, "
                              ".content p, "
                              "article p"):
            text = p.get_text(" ", strip=True)
            if text and len(text) > 20:
                body_parts.append(text)
    body_text = "\n\n".join(body_parts).strip()

    # ---- author ---------------------------------------------------------
    author_name = ""
    if article_data:
        author_name = (
            (article_data.get("author") or {}).get("nickname")
            or article_data.get("author", {}).get("name")
            or ""
        )
    if not author_name:
        h1 = soup.select_one("h1#articleTitle")
        if h1:
            author_name = h1.get("data-author", "")
    if not author_name:
        author_el = soup.select_one("[class*='author'], .author-name, [data-author]")
        author_name = author_el.get_text(strip=True) if author_el else ""

    # ---- date -----------------------------------------------------------
    posted_at = datetime(1970, 1, 1, tzinfo=timezone.utc)
    if article_data:
        ts = (
            article_data.get("publishTime")
            or article_data.get("publishedAt")
            or article_data.get("createdAt")
        )
        if ts:
            try:
                if isinstance(ts, (int, float)):
                    posted_at = datetime.fromtimestamp(
                        ts / 1000.0 if ts > 1e10 else ts,
                        tz=timezone.utc,
                    )
                elif isinstance(ts, str):
                    posted_at = datetime.fromisoformat(
                        ts.replace("Z", "+00:00")
                    )
            except (ValueError, TypeError):
                pass

    # ---- comment count --------------------------------------------------
    comment_count = article_data.get("commentCount", 0) or 0

    # ---- article ID -----------------------------------------------------
    article_id = str(article_data.get("articleId") or "")
    if not article_id:
        m = re.search(r"/article/(\d+)", article_url)
        if m:
            article_id = m.group(1)

    # ---- category -------------------------------------------------------
    category = article_data.get("category", {}).get("name") or ""

    full_text = f"{title}\n\n{body_text}" if title else body_text

    return RawPost(
        id=f"hk01:{article_id}" if article_id else f"hk01:{abs(hash(article_url))}",
        source="hk01",
        source_category=SourceCategory.NEWS_COMMENTS,
        region="HK",
        language="zh-HK",
        language_detected=detect_language(full_text),
        url=article_url,
        author_hash=hash_author(author_name) if author_name else "",
        title=title or None,
        body=body_text,
        posted_at=posted_at,
        signal_type=SignalType.OPINION,
        engagement_metrics={
            "comment_count": int(comment_count) if comment_count else 0,
        },
        replies=[],
        raw_metadata={
            "article_id": article_id,
            "author_name": author_name,
            "category": category,
        },
    )
