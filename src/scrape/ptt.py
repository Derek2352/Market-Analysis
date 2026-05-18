"""PTT (ptt.cc) scraper — Taiwan's largest BBS via the web mirror.

PTT is a telnet BBS with a web mirror at https://www.ptt.cc. The web mirror
renders boards and articles as static HTML, searchable via the /bbs/<board>/search
endpoint. No auth required. Very tolerant of low-volume scraping.

**ToS:** PTT terms allow web access. Rate limit: 1 req / 2 s (conservative).
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlparse, urljoin

import structlog
from bs4 import BeautifulSoup

from src.scrape.base import RobotsCache, SourceError
from src.scrape.base.http import PoliteClient
from src.scrape.utils.hashing import hash_author
from src.scrape.utils.lang import detect_language
from src.schemas.enums import SignalType, SourceCategory
from src.schemas.raw import RawPost

_log = structlog.get_logger(__name__)

BASE_URL = "https://www.ptt.cc"
SEARCH_URL_TEMPLATE = BASE_URL + "/bbs/{board}/search?q={query}"

PTT_RATE = 2.0
MAX_THREADS = 30

_DATE_RE = re.compile(r"(\w{3}\s+\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\s+\d{4})")


class PTTScraper:
    """PTT web mirror scraper."""

    source_id = "ptt"
    region = "TW"
    language = "zh-TW"
    category = SourceCategory.FORUMS
    signal_type = SignalType.OPINION

    def __init__(
        self,
        *,
        boards: list[str] | None = None,
        client: PoliteClient | None = None,
        robots_cache: RobotsCache | None = None,
        max_threads: int = MAX_THREADS,
    ) -> None:
        self._boards = boards or ["Gossiping", "Lifeismoney", "Stock", "Tech_Job", "MobileComm"]
        self._max_threads = max_threads
        self._owns_client = client is None
        if client is None:
            self._robots_cache = robots_cache or RobotsCache()
            self._client = PoliteClient(
                robots_cache=self._robots_cache, rate=PTT_RATE,
            )
        else:
            self._client = client

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def search(self, topic: str, since: datetime, limit: int) -> Iterator[RawPost]:
        emitted = 0
        seen_ids: set[str] = set()

        for board in self._boards:
            if emitted >= limit:
                break
            try:
                for post in self._search_board(board, topic, since, limit - emitted):
                    if post.id in seen_ids:
                        continue
                    seen_ids.add(post.id)
                    yield post
                    emitted += 1
                    if emitted >= limit:
                        break
            except SourceError as e:
                _log.warning("ptt.board_failed", board=board, error=str(e))
                continue

    def _search_board(
        self, board: str, topic: str, since: datetime, limit: int,
    ) -> Iterator[RawPost]:
        search_url = SEARCH_URL_TEMPLATE.format(board=board, query=quote(topic))
        html = self._client.get_html(search_url)
        thread_urls = _parse_search_results(html)[:self._max_threads]

        for url in thread_urls:
            try:
                html = self._client.get_html(url)
                post = _parse_article(html, url=url, board=board)
                if post and post.posted_at >= since:
                    yield post
            except SourceError:
                continue

    def fetch_thread(self, thread_id: str) -> Any:
        url = f"{BASE_URL}/bbs/{thread_id}"
        html = self._client.get_html(url)
        return _parse_article(html, url=url, board=thread_id.split("/")[-2])


# ---- Parsers ----

def _parse_search_results(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    for link in soup.select(".title a[href]"):
        href = link.get("href", "")
        if "/bbs/" in href:
            urls.append(urljoin(BASE_URL, href))
    return urls


def _parse_article(html: str, *, url: str, board: str) -> RawPost | None:
    soup = BeautifulSoup(html, "html.parser")

    # Title
    title_el = soup.select_one("title") or soup.select_one(".article-metaline-right")
    title = title_el.get_text(strip=True).split(" - ")[0] if title_el else ""

    # Author
    author_el = soup.select_one(".article-meta-value")
    author = author_el.get_text(strip=True) if author_el else ""

    # Date
    date_els = soup.select(".article-meta-value")
    posted_at = datetime(1970, 1, 1, tzinfo=timezone.utc)
    for el in date_els:
        m = _DATE_RE.search(el.get_text(strip=True))
        if m:
            try:
                posted_at = datetime.strptime(m.group(1), "%a %b %d %H:%M:%S %Y").replace(tzinfo=timezone.utc)
            except ValueError:
                pass
            break

    # Body
    body_parts = []
    main_content = soup.select_one("#main-content")
    if main_content:
        for span in main_content.select(".article-meta-line, .article-metaline, .push"):
            span.extract()
        body_text = main_content.get_text(chr(10), strip=True)
        lines = body_text.split(chr(10))
        clean_lines = [l for l in lines if not l.startswith("作者") and not l.startswith("標題") and not l.startswith("時間")]
        body_parts.append(chr(10).join(clean_lines))

    body = chr(10).join(body_parts).strip()
    if not body or len(body) < 10:
        return None

    # Push counts
    push_count = 0
    boo_count = 0
    for push in soup.select(".push"):
        tag = push.select_one(".push-tag")
        if tag:
            tag_text = tag.get_text(strip=True)
            if "推" in tag_text:
                push_count += 1
            elif "噓" in tag_text:
                boo_count += 1

    article_id = url.split("/")[-1].replace(".html", "")
    full_text = f"{title}\n\n{body}"

    return RawPost(
        id=f"ptt:{board}:{article_id}",
        source="ptt",
        source_category=SourceCategory.FORUMS,
        region="TW",
        language="zh-TW",
        language_detected=detect_language(full_text),
        url=url,
        author_hash=hash_author(author),
        title=title or None,
        body=body,
        posted_at=posted_at,
        signal_type=SignalType.OPINION,
        engagement_metrics={"push": push_count, "boo": boo_count},
        replies=[],
        raw_metadata={"board": board, "article_id": article_id},
    )
