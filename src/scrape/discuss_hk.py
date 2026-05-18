"""Discuss.com.hk scraper — static HTML over httpx + BeautifulSoup.

Discuss.com.hk is a long-running general Hong Kong forum. The site renders
on the server, so we can pull pages with the ``PoliteClient`` directly —
no Playwright. Phase 6 scope: each thread becomes one ``RawPost``
representing the original post (#1); replies are deferred to a later phase.

ToS stance is ``silent`` in the regional registry, so this scraper is
default-enabled and contributes to the HK default source list.
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlparse, parse_qs

import structlog
from bs4 import BeautifulSoup

from src.scrape.base import RobotsCache, SourceError
from src.scrape.base.http import PoliteClient
from src.scrape.base.protocol import SourceScraper
from src.scrape.utils.hashing import hash_author
from src.scrape.utils.lang import detect_language
from src.schemas.enums import SignalType, SourceCategory
from src.schemas.raw import RawPost

_log = structlog.get_logger(__name__)

BASE_URL = "https://www.discuss.com.hk"
SEARCH_URL_TEMPLATE = (
    BASE_URL + "/search.php?mod=forum&searchsubmit=yes&srchtxt={}"
)
THREAD_URL_TEMPLATE = BASE_URL + "/viewthread.php?tid={}"

DISCUSS_RATE = 1.5   # 1 req / 1.5 s — well under their "be polite" tolerance.
MAX_THREADS_PER_SEARCH = 30
MAX_SEARCH_PAGES = 1   # Phase 6: first page of results only.

# Posts surface their date inline with the post number, e.g.
# "#1 發表於 2019-5-20 18:29" or "#1發表於 2009-12-25 23:44".
_DATE_RE = re.compile(r"(\d{4}-\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?)")
# Search-result anchors look like /viewthread.php?tid=20861215&feedback=...
_TID_RE = re.compile(r"tid=(\d+)")
# View / reply counts come as "瀏覽: 1,221" and "回覆: 1".
_VIEWS_RE = re.compile(r"瀏覽\s*[:：]\s*([\d,]+)")
_REPLIES_RE = re.compile(r"回覆\s*[:：]\s*([\d,]+)")


class DiscussHKScraper:
    """Discuss.com.hk forum scraper, top-level OP per thread."""

    source_id = "discuss_hk"
    region = "HK"
    language = "zh-HK"
    category = SourceCategory.FORUMS
    signal_type = SignalType.OPINION

    def __init__(
        self,
        *,
        client: PoliteClient | None = None,
        robots_cache: RobotsCache | None = None,
        max_threads: int = MAX_THREADS_PER_SEARCH,
    ) -> None:
        self._owns_client = client is None
        if client is None:
            self._robots_cache = robots_cache or RobotsCache()
            self._client = PoliteClient(
                robots_cache=self._robots_cache, rate=DISCUSS_RATE,
            )
        else:
            self._client = client
        self._max_threads = max_threads

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "DiscussHKScraper":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ---- SourceScraper protocol -----------------------------------------

    def search(
        self, topic: str, since: datetime, limit: int,
    ) -> Iterator[RawPost]:
        """Discover threads matching ``topic`` and yield one RawPost per OP."""
        search_url = SEARCH_URL_TEMPLATE.format(quote(topic))
        try:
            html = self._client.get_html(search_url)
        except SourceError as e:
            _log.warning("discuss_hk.search_failed", topic=topic, error=str(e))
            return

        thread_ids = parse_search_results(html)[: self._max_threads]
        _log.info(
            "discuss_hk.search", topic=topic, candidates=len(thread_ids),
        )

        emitted = 0
        for tid in thread_ids:
            if emitted >= limit:
                break
            try:
                post = self.fetch_thread(tid)
            except SourceError as e:
                _log.warning("discuss_hk.thread_failed", tid=tid, error=str(e))
                continue
            if since is not None and post.posted_at < since:
                continue
            yield post
            emitted += 1

    def fetch_thread(self, thread_id: str) -> RawPost:
        """Fetch the canonical thread page and return its OP as a RawPost."""
        url = THREAD_URL_TEMPLATE.format(thread_id)
        html = self._client.get_html(url)
        return parse_thread(html, thread_id=thread_id, url=url)


# ---------------------------------------------------------------------------
# Module-level parsers (testable offline against the saved HTML fixtures).
# ---------------------------------------------------------------------------


def parse_search_results(html: str) -> list[str]:
    """Extract unique thread IDs from a Discuss.com.hk search-results page."""
    soup = BeautifulSoup(html, "html.parser")
    out: list[str] = []
    seen: set[str] = set()
    for a in soup.select("div.search-result-message > a, a.xst, a.s.xst"):
        href = a.get("href", "") or ""
        m = _TID_RE.search(href)
        if not m:
            continue
        tid = m.group(1)
        if tid in seen:
            continue
        seen.add(tid)
        out.append(tid)
    return out


def parse_thread(html: str, *, thread_id: str, url: str | None = None) -> RawPost:
    """Parse a thread page → RawPost representing the original (#1) post."""
    soup = BeautifulSoup(html, "html.parser")

    # ---- thread-level fields -----------------------------------------
    title_el = soup.select_one(".thread-subject span")
    if title_el is None:
        head_title = soup.select_one("title")
        title = head_title.get_text(strip=True) if head_title else ""
    else:
        title = title_el.get_text(strip=True)

    views_el = next(
        (li for li in soup.select(".viewthread-number li")
         if "瀏覽" in li.get_text()),
        None,
    )
    replies_el = next(
        (li for li in soup.select(".viewthread-number li")
         if "回覆" in li.get_text()),
        None,
    )
    views = _parse_int(views_el.get_text() if views_el else "")
    replies = _parse_int(replies_el.get_text() if replies_el else "")

    # ---- first-post (OP) fields --------------------------------------
    bodies = soup.select(".postmessage-content.t_msgfont")
    if not bodies:
        raise SourceError(f"discuss_hk: no post bodies found for tid={thread_id}")
    body_text = bodies[0].get_text(" ", strip=True)

    authors = soup.select(".author-detail a.name")
    author_name = authors[0].get_text(strip=True) if authors else ""

    author_titles = soup.select(".author-detail p.authortitle em")
    author_title = author_titles[0].get_text(strip=True) if author_titles else ""

    dates = soup.select(".postinfo .post-date")
    posted_at = _parse_post_date(dates[0].get_text(" ", strip=True) if dates else "")

    if not posted_at:
        # Fall back to epoch — better to keep the post than drop it.
        posted_at = datetime(1970, 1, 1, tzinfo=timezone.utc)

    canonical_url = url or THREAD_URL_TEMPLATE.format(thread_id)
    full_text = f"{title}\n\n{body_text}" if title else body_text

    return RawPost(
        id=f"discuss_hk:{thread_id}",
        source="discuss_hk",
        source_category=SourceCategory.FORUMS,
        region="HK",
        language="zh-HK",
        language_detected=detect_language(full_text),
        url=canonical_url,
        author_hash=hash_author(author_name) if author_name else "",
        title=title or None,
        body=body_text,
        posted_at=posted_at,
        signal_type=SignalType.OPINION,
        engagement_metrics={
            "views": views,
            "replies": replies,
            "post_count_on_page": len(bodies),
        },
        replies=[],
        raw_metadata={
            "thread_id": thread_id,
            "author_title": author_title,
            "post_number": "#1",
        },
    )


def _parse_int(s: str) -> int:
    if not s:
        return 0
    digits = re.sub(r"[^\d]", "", s)
    return int(digits) if digits else 0


def _parse_post_date(text: str) -> datetime | None:
    """Pull a YYYY-M-D H:M timestamp from text like '#1 發表於 2019-5-20 18:29'."""
    m = _DATE_RE.search(text)
    if not m:
        return None
    raw = m.group(1)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.strptime(raw, fmt)
            # Discuss.com.hk timestamps are HK local time (UTC+8); convert.
            from datetime import timedelta
            return dt.replace(tzinfo=timezone.utc) - timedelta(hours=8)
        except ValueError:
            continue
    return None
