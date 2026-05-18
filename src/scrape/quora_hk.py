"""Quora HK scraper — Playwright-based answer extraction.

Quora serves JS-rendered question pages with answers lazily loaded on scroll.
The site is protected by Cloudflare, which often blocks headless browsers.
When a page loads successfully, answer content is in ``div.q-box`` containers
with user-selectable text.

**ToS:** Quora prohibits automated access. Default-disabled, requires
``--accept-tos-risk``.

**Cloudflare note:** Headless Playwright may get a CF challenge page.
The scraper detects this and raises ``SourceError``. Manual browser
save-as is the most reliable way to get a fixture for offline tests.
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

BASE_URL = "https://www.quora.com"
SEARCH_URL_TEMPLATE = BASE_URL + "/search?q={}"

QUORA_RATE = 3.0
MAX_QUESTIONS_PER_SEARCH = 8


class QuoraHKScraper:
    """Quora scraper — search for HK-tagged questions, parse answer pages."""

    source_id = "quora_hk"
    region = "HK"
    language = "en"
    category = SourceCategory.QA
    signal_type = SignalType.COMPARISON

    def __init__(
        self,
        *,
        playwright: PlaywrightManager | None = None,
        robots_cache: RobotsCache | None = None,
        max_questions: int = MAX_QUESTIONS_PER_SEARCH,
    ) -> None:
        self._owns_playwright = playwright is None
        if playwright is None:
            self._robots_cache = robots_cache or RobotsCache()
            self._playwright = PlaywrightManager(
                robots_cache=self._robots_cache,
                rate=QUORA_RATE,
                respect_robots=False,
            )
        else:
            self._playwright = playwright
        self._max_questions = max_questions

    def close(self) -> None:
        if self._owns_playwright:
            self._playwright.close()

    def __enter__(self) -> "QuoraHKScraper":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ---- SourceScraper protocol -----------------------------------------

    def search(
        self, topic: str, since: datetime, limit: int,
    ) -> Iterator[RawPost]:
        """Search Quora for HK-relevant questions, parse each result."""
        query = f"{topic} Hong Kong"
        search_url = SEARCH_URL_TEMPLATE.format(quote(query))
        try:
            with self._playwright.get_page(search_url) as page:
                page.goto(search_url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(5000)
                # Scroll to load results
                for _ in range(3):
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(2000)
                html = page.content()
        except SourceError as e:
            _log.warning("quora_hk.search_failed", topic=topic, error=str(e))
            return

        question_urls = parse_search_results(html)[: self._max_questions]
        _log.info(
            "quora_hk.search", topic=topic, candidates=len(question_urls),
        )

        emitted = 0
        for qurl in question_urls:
            if emitted >= limit:
                break
            try:
                post = self.fetch_question(qurl)
            except SourceError as e:
                _log.warning(
                    "quora_hk.question_failed", url=qurl, error=str(e),
                )
                continue
            if since is not None and post.posted_at < since:
                continue
            yield post
            emitted += 1

    def fetch_thread(self, thread_id: str) -> Any:
        return self.fetch_question(thread_id)

    def fetch_question(self, question_url: str) -> RawPost:
        """Fetch a Quora question page, scroll answers, and parse."""
        with self._playwright.get_page(question_url) as page:
            page.goto(question_url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(5000)
            self._playwright.scroll_until_stable(
                page, max_scrolls=8, settle_ms=2000,
            )
            html = page.content()
        return parse_question_page(html, question_url=question_url)


# ---------------------------------------------------------------------------
# Module-level parsers
# ---------------------------------------------------------------------------


def is_cloudflare_page(html: str) -> bool:
    """True if ``html`` is a Cloudflare challenge (not real content)."""
    return "Just a moment" in html[:2000] or "challenges.cloudflare.com" in html[:2000]


def parse_search_results(html: str) -> list[str]:
    """Extract question URLs from a Quora search-results page."""
    if is_cloudflare_page(html):
        return []

    soup = BeautifulSoup(html, "html.parser")
    out: list[str] = []
    seen: set[str] = set()
    for a in soup.select("a[href]"):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        # Quora question URLs are like /What-is-... or /question/...
        if href.startswith("/"):
            if re.search(r"/[A-Z][a-z].*-[a-z]", href) or "/question/" in href:
                full = urljoin(BASE_URL, href.split("?")[0])
                if full not in seen:
                    seen.add(full)
                    out.append(full)
        elif "quora.com/" in href:
            if re.search(r"quora\.com/[^/]+-[a-f0-9]+", href):
                full = href.split("?")[0]
                if full not in seen:
                    seen.add(full)
                    out.append(full)
    return out


def parse_question_page(html: str, *, question_url: str) -> RawPost:
    """Parse a Quora question page into a RawPost.

    Raises ``SourceError`` if the page is a Cloudflare challenge or
    has no question title.
    """
    if is_cloudflare_page(html):
        raise SourceError("quora_hk: Cloudflare challenge page — cannot parse")

    soup = BeautifulSoup(html, "html.parser")

    # ---- question title ------------------------------------------------
    title_el = (
        soup.select_one("h1")
        or soup.select_one("[class*='question'] h1")
        or soup.select_one("[class*='Question'] span")
    )
    title = title_el.get_text(" ", strip=True) if title_el else ""
    if not title:
        # Meta fallback
        meta = soup.select_one("meta[property='og:title']")
        title = (meta.get("content", "") or "") if meta else ""
    if not title:
        # Page title fallback
        page_title = soup.find("title")
        title = page_title.get_text(strip=True) if page_title else ""
        if title == "Error" or title == "Quora":
            raise SourceError("quora_hk: no question title found")

    # ---- body (question text + answer texts) ---------------------------
    body_parts: list[str] = []

    # Question text
    qtext_els = soup.select(
        "[class*='question_text'], .q-text, [class*='Question'] [class*='text']"
    )
    for el in qtext_els[:3]:
        text = el.get_text(" ", strip=True)
        if text and len(text) > 10:
            body_parts.append(text)

    # Answer texts
    answer_els = (
        soup.select("div.q-box.spacing_log_answer_content")
        or soup.select(".q-box.qu-userSelect--text")
        or soup.select("[class*='Answer'] [class*='text']")
        or soup.select("[class*='answer'] p")
    )
    for el in answer_els[:20]:
        text = el.get_text(" ", strip=True)
        if text and len(text) > 30:
            body_parts.append(text)

    body_text = "\n\n".join(body_parts).strip()

    # ---- author --------------------------------------------------------
    author_name = ""
    author_els = soup.select(
        "[class*='User'] span, [class*='author'], "
        "a[href*='/profile/'] span, "
        ".q-user"
    )
    for el in author_els[:5]:
        text = el.get_text(strip=True)
        if text and len(text) > 1 and not text.startswith("http"):
            author_name = text
            break

    # ---- date ----------------------------------------------------------
    posted_at = datetime(1970, 1, 1, tzinfo=timezone.utc)
    date_els = soup.select(
        "[class*='Question'] time, "
        "[class*='timestamp'], "
        "[class*='date']"
    )
    for el in date_els[:3]:
        dt = el.get("datetime", "")
        if dt:
            try:
                posted_at = datetime.fromisoformat(dt.replace("Z", "+00:00"))
            except ValueError:
                pass
            break

    # ---- answer count --------------------------------------------------
    answer_count = 0
    count_el = soup.select_one("[class*='answer_count'], [class*='AnswerCount']")
    if count_el:
        m = re.search(r"(\d+)", count_el.get_text(strip=True))
        if m:
            answer_count = int(m.group(1))
    if not answer_count:
        # Count from rendered answer elements
        answer_count = len(soup.select(
            "div.q-box.spacing_log_answer_content, "
            "[class*='AnswerBase'], "
            "[class*='answer_content']"
        ))

    full_text = f"{title}\n\n{body_text}" if title else body_text
    qid_match = re.search(r"quora\.com/([^/]+)", question_url)
    qid = qid_match.group(1) if qid_match else ""

    return RawPost(
        id=f"quora_hk:{qid}" if qid else f"quora_hk:{abs(hash(question_url))}",
        source="quora_hk",
        source_category=SourceCategory.QA,
        region="HK",
        language="en",
        language_detected=detect_language(full_text),
        url=question_url,
        author_hash=hash_author(author_name) if author_name else "",
        title=title or None,
        body=body_text,
        posted_at=posted_at,
        signal_type=SignalType.COMPARISON,
        engagement_metrics={
            "answer_count": answer_count,
        },
        replies=[],
        raw_metadata={
            "question_slug": qid,
            "author_name": author_name,
        },
    )
