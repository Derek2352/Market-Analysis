"""YouTube HTML scraper — Playwright-based comment extraction.

YouTube pages are JS-rendered. Comments are lazy-loaded inside
``ytd-comment-thread-renderer`` elements that only appear after scrolling
past the video player. We use PlaywrightManager with ``scroll_until_stable``
to load comment threads.

Discovery: YouTube search ``https://www.youtube.com/results?search_query=...``
→ first-page video links. Each video page yields one ``RawPost`` representing
the video itself (thread = video, not individual comments — phase 6 scope).

**ToS:** YouTube ToS prohibits automated access. Default-disabled, requires
``--accept-tos-risk`` at the CLI.
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, quote

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

BASE_URL = "https://www.youtube.com"
SEARCH_URL_TEMPLATE = (
    BASE_URL + "/results?search_query={}"
)

# Rate: Playwright is heavy — 1 req / 2 s
YOUTUBE_RATE = 2.0
MAX_VIDEOS_PER_SEARCH = 6


class YoutubeHTMLScraper:
    """YouTube HTML scraper — search results + video pages with comments."""

    source_id = "youtube_html"
    region = "HK"
    language = "zh-HK"
    category = SourceCategory.VIDEO_COMMENTS
    signal_type = SignalType.OPINION

    def __init__(
        self,
        *,
        playwright: PlaywrightManager | None = None,
        robots_cache: RobotsCache | None = None,
        max_videos: int = MAX_VIDEOS_PER_SEARCH,
    ) -> None:
        self._owns_playwright = playwright is None
        if playwright is None:
            self._robots_cache = robots_cache or RobotsCache()
            self._playwright = PlaywrightManager(
                robots_cache=self._robots_cache,
                rate=YOUTUBE_RATE,
                respect_robots=False,  # YouTube blocks bots.txt for search
            )
        else:
            self._playwright = playwright
        self._max_videos = max_videos

    def close(self) -> None:
        if self._owns_playwright:
            self._playwright.close()

    def __enter__(self) -> "YoutubeHTMLScraper":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ---- SourceScraper protocol -----------------------------------------

    def search(
        self, topic: str, since: datetime, limit: int,
    ) -> Iterator[RawPost]:
        """Discover videos via YouTube search, then parse each video page."""
        search_url = SEARCH_URL_TEMPLATE.format(quote(topic))
        try:
            with self._playwright.get_page(search_url) as page:
                page.goto(search_url, wait_until="networkidle", timeout=45000)
                page.wait_for_timeout(2000)
                # Scroll once to populate results
                page.evaluate("window.scrollTo(0, 1500)")
                page.wait_for_timeout(2000)
                html = page.content()
        except SourceError as e:
            _log.warning("youtube_html.search_failed", topic=topic, error=str(e))
            return

        video_urls = parse_search_results(html)[: self._max_videos]
        _log.info(
            "youtube_html.search", topic=topic, candidates=len(video_urls),
        )

        emitted = 0
        for vurl in video_urls:
            if emitted >= limit:
                break
            try:
                post = self.fetch_video(vurl)
            except SourceError as e:
                _log.warning(
                    "youtube_html.video_failed", url=vurl, error=str(e),
                )
                continue
            if since is not None and post.posted_at < since:
                continue
            yield post
            emitted += 1

    def fetch_thread(self, thread_id: str) -> Any:
        """Alias for fetch_video — satisfies SourceScraper protocol."""
        return self.fetch_video(thread_id)

    def fetch_video(self, video_url: str) -> RawPost:
        """Fetch a YouTube video page, scroll comments into view, and parse."""
        with self._playwright.get_page(video_url) as page:
            page.goto(video_url, wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(3000)
            # Scroll past the video player so comments load
            page.evaluate("window.scrollTo(0, 1200)")
            page.wait_for_timeout(3000)
            # Scroll to load comments
            self._playwright.scroll_until_stable(
                page, max_scrolls=6, settle_ms=3000,
            )
            html = page.content()
        return parse_video_page(html, video_url=video_url)


# ---------------------------------------------------------------------------
# Module-level parsers (testable offline against saved HTML fixtures).
# ---------------------------------------------------------------------------


def parse_search_results(html: str) -> list[str]:
    """Extract video URLs from a YouTube search-results page."""
    soup = BeautifulSoup(html, "html.parser")
    out: list[str] = []
    seen: set[str] = set()
    for a in soup.select("a#video-title, a.yt-simple-endpoint[href*='watch']"):
        href = (a.get("href") or "").strip()
        if not href.startswith("/watch"):
            continue
        full = urljoin(BASE_URL, href.split("&")[0])  # strip tracking params
        if full in seen:
            continue
        seen.add(full)
        out.append(full)
    return out


def parse_video_page(html: str, *, video_url: str) -> RawPost:
    """Parse a YouTube video watch page into a RawPost.

    Raises ``SourceError`` if the video title or channel is unrecoverable.
    """
    soup = BeautifulSoup(html, "html.parser")

    # ---- title ---------------------------------------------------------
    title_el = (
        soup.select_one("h1.ytd-watch-metadata")
        or soup.select_one("h1 yt-formatted-string")
        or soup.select_one("h1")
    )
    title = title_el.get_text(" ", strip=True) if title_el else ""
    if not title:
        # Fallback to meta
        meta_title = soup.select_one("meta[name='title']")
        title = (meta_title.get("content", "") or "") if meta_title else ""

    # ---- channel name --------------------------------------------------
    channel_el = (
        soup.select_one("yt-formatted-string#channel-name")
        or soup.select_one("#owner yt-formatted-string a")
        or soup.select_one("ytd-channel-name yt-formatted-string")
    )
    channel = channel_el.get_text(strip=True) if channel_el else ""

    # ---- description ---------------------------------------------------
    desc_el = soup.select_one("ytd-expander #description, yt-formatted-string#description")
    description = desc_el.get_text(" ", strip=True) if desc_el else ""

    # ---- comment count -------------------------------------------------
    comment_count = 0
    comment_count_el = soup.select_one(
        "ytd-comments-header-renderer .count-text span, "
        "yt-formatted-string.count-text"
    )
    if comment_count_el:
        text = comment_count_el.get_text(strip=True)
        m = re.search(r"[\d,]+", text)
        if m:
            comment_count = int(m.group().replace(",", ""))

    # ---- view count ----------------------------------------------------
    views = 0
    view_el = soup.select_one(
        "ytd-watch-info-text span, yt-formatted-string#info span, "
        ".view-count"
    )
    if view_el:
        text = view_el.get_text(strip=True)
        m = re.search(r"([\d,.]+[KMB]?)\s*(views|次)", text, re.I)
        if m:
            raw = m.group(1)
            views = _parse_view_count(raw)

    # ---- date ----------------------------------------------------------
    posted_at = datetime(1970, 1, 1, tzinfo=timezone.utc)
    date_el = soup.select_one(
        "ytd-watch-info-text span:nth-child(3), "
        "#info-container span:nth-child(3)"
    )
    if date_el:
        text = date_el.get_text(strip=True)
        posted_at = _parse_youtube_date(text) or posted_at

    # ---- video ID ------------------------------------------------------
    m = re.search(r"v=([a-zA-Z0-9_-]{11})", video_url)
    video_id = m.group(1) if m else ""

    body = description if description else title
    full_text = f"{title}\n\n{description}"

    return RawPost(
        id=f"youtube_html:{video_id}" if video_id else f"youtube_html:{abs(hash(video_url))}",
        source="youtube_html",
        source_category=SourceCategory.VIDEO_COMMENTS,
        region="HK",
        language="zh-HK",
        language_detected=detect_language(full_text),
        url=video_url,
        author_hash=hash_author(channel) if channel else "",
        title=title or None,
        body=body,
        posted_at=posted_at,
        signal_type=SignalType.OPINION,
        engagement_metrics={
            "views": views,
            "comment_count": comment_count,
        },
        replies=[],
        raw_metadata={
            "video_id": video_id,
            "channel_name": channel,
        },
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_YT_DATE_RE = re.compile(r"(\d+)\s*(year|month|week|day|hour|minute|second|年|個月|週|天|小時|分鐘|秒)", re.I)

def _parse_youtube_date(text: str) -> datetime | None:
    """Parse relative YouTube date strings like '3 years ago', '2 年前'."""
    now = datetime.now(timezone.utc)
    m = _YT_DATE_RE.search(text)
    if not m:
        return None
    num = int(m.group(1))
    unit = m.group(2).lower()
    from datetime import timedelta
    if "year" in unit or "年" in unit:
        return now - timedelta(days=num * 365)
    if "month" in unit or "個月" in unit:
        return now - timedelta(days=num * 30)
    if "week" in unit or "週" in unit:
        return now - timedelta(weeks=num)
    if "day" in unit or "天" in unit:
        return now - timedelta(days=num)
    if "hour" in unit or "小時" in unit:
        return now - timedelta(hours=num)
    if "minute" in unit or "分鐘" in unit:
        return now - timedelta(minutes=num)
    if "second" in unit or "秒" in unit:
        return now - timedelta(seconds=num)
    return None


_VIEW_RE = re.compile(r"([\d,.]+)([KMB]?)", re.I)

def _parse_view_count(text: str) -> int:
    """Parse view count strings like '1.7M', '73K', '1,234'."""
    m = _VIEW_RE.match(text.strip())
    if not m:
        return 0
    num = float(m.group(1).replace(",", ""))
    suffix = m.group(2).upper()
    if suffix == "K":
        num *= 1_000
    elif suffix == "M":
        num *= 1_000_000
    elif suffix == "B":
        num *= 1_000_000_000
    return int(num)
