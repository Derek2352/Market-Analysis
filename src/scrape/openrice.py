"""Openrice scraper — Playwright-based HTML scraper for HK restaurant reviews.

Openrice (https://www.openrice.com) is the dominant F&B review platform in
Hong Kong. Pages sit behind a BytePlus JavaScript security check, so they are
loaded in Chromium through ``PlaywrightManager`` (with the same honest
User-Agent as every other source — no stealth, no challenge solving beyond
running the page's own JavaScript). If the check does not let the bot through,
the source stops.

**ToS note:** Openrice's ToS prohibit automated access.  This scraper is
registered with ``tos_scraping_stance=PROHIBITED`` — it won't run by default.
The user must explicitly opt in via ``--sources openrice``. robots.txt allows
the search, review-list and review pages for ``*`` (verified 2026-10-08).

Scraping approach:
- Search: ``/en/hongkong/restaurants?what={keyword}``; the branches with the
  most reviews (counts from the result cards) are scraped first.
- Review list: ``/en/hongkong/r-{slug}-r{id}/reviews?page={n}`` — one RawPost
  per review, each linked to its own ``/review/...-e{id}`` page.
- A review's own page is opened only when the list extract looks cut off.
- Plays nice: one browser page reused for the whole run, 1 navigation / 3 s,
  images and third-party requests (ads, analytics) are not loaded.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote, urljoin

import structlog
from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from src.schemas.enums import SignalType, SourceCategory
from src.schemas.raw import RawPost
from src.scrape.base import (
    FixtureStore,
    PlaywrightManager,
    RobotsCache,
    SourceError,
)
from src.scrape.base.http import ForbiddenError
from src.scrape.utils.hashing import hash_author
from src.scrape.utils.lang import detect_language

BASE_URL = "https://www.openrice.com"
SEARCH_URL = f"{BASE_URL}/en/hongkong/restaurants"

# Configurable max restaurants (branches) to scrape per topic
MAX_RESTAURANTS = 5
# Configurable max review pages per restaurant
MAX_REVIEW_PAGES = 5
# Rate: Playwright is heavy — 1 navigation / 3 s
OPENRICE_RATE = 3.0

# Hosts the pages need (HTML + the site's own JS/CSS). Everything else —
# ad servers, analytics beacons — is blocked along with images and fonts.
_ALLOWED_HOSTS = ("openrice.com", "orstatic.com")
_BLOCKED_TYPES = ("image", "media", "font")

_CHALLENGE_MARKER = "Security Check in Progress"
_READY_TIMEOUT_MS = 20_000
_GOTO_TIMEOUT_MS = 45_000

# Dates on the page are Hong Kong local dates. A date-only value is pinned to
# noon HKT so it stays on the same calendar day in UTC.
_HKT = timezone(timedelta(hours=8))

# When is a list extract completed from the review's own page? The HTML the
# server sends carries each review's full text (it matched the review page in
# every check made); the page's own script then shortens extracts to ~170-200
# half-width cells after hydration, and that cut can land right after 。 or an
# emoji. So: from server HTML, fetch only extracts that end mid-sentence or are
# unusually long (a safety net); from the live DOM, also any extract at least
# as wide as the client-side cut.
_SERVER_CUT_WIDTH = 1000
_DOM_CUT_WIDTH = 150
_SENTENCE_END = frozenset("。！？!?.…~～）)」』】»\"'”’")

_REVIEW_HREF = re.compile(r"/review/[^\"'?#]*-e(\d+)(?:[/?#]|$)")
_REST_ID = re.compile(r"-r(\d+)(?:/|$)")
_BLOCK_TAGS = frozenset({"p", "div", "li", "h1", "h2", "h3", "h4", "blockquote"})


@dataclass(frozen=True)
class Branch:
    """One restaurant (branch) from the search results."""

    name: str
    url: str          # canonical restaurant URL, no trailing sub-path
    rest_id: str
    # Smile + cry counts on the result card, if shown. Only a ranking hint:
    # the card leaves out OK-rated reviews, so it is far below the real total
    # (e.g. 10 on the card for a branch with 51 reviews).
    face_count: int | None


class OpenriceScraper:
    """Scrape Openrice restaurant reviews."""

    source_id = "openrice"
    region = "HK"
    language = "zh-HK"

    def __init__(
        self,
        *,
        max_restaurants: int = MAX_RESTAURANTS,
        max_review_pages: int = MAX_REVIEW_PAGES,
        fetch_full_text: bool = True,
        robots_cache: RobotsCache | None = None,
        playwright: PlaywrightManager | None = None,
        fixtures: FixtureStore | None = None,
    ) -> None:
        self._log = structlog.get_logger().bind(scraper="openrice")
        self._owns_robots = robots_cache is None
        self._robots = robots_cache or RobotsCache()
        self._pw = playwright or PlaywrightManager(
            robots_cache=self._robots,
            rate=OPENRICE_RATE,
            headless=True,
            block_resource_types=_BLOCKED_TYPES,
            allowed_hosts=_ALLOWED_HOSTS,
        )
        # Saving live pages as scrape-doctor references is opt-in (pass a
        # store, or set MKT_CAPTURE_FIXTURES=1): review pages carry reviewer
        # names, so saved copies are anonymised and never overwrite a fixture.
        if fixtures is None and os.environ.get("MKT_CAPTURE_FIXTURES") == "1":
            fixtures = FixtureStore("openrice")
        self._fixtures = fixtures
        self._fixture_kinds_saved: set[str] = set()
        self._max_restaurants = max_restaurants
        self._max_review_pages = max_review_pages
        self._fetch_full_text = fetch_full_text

    # ------------------------------------------------------------------
    # SourceScraper protocol
    # ------------------------------------------------------------------

    def search(
        self,
        topic: str,
        since: datetime,
        limit: int,
    ) -> Iterator[RawPost]:
        """Search for restaurants matching *topic*, then scrape reviews."""
        self._log.info("openrice.search.start", topic=topic, limit=limit)
        search_url = f"{SEARCH_URL}?what={quote(topic)}"

        emitted = 0
        seen_ids: set[str] = set()
        # One page (one browser context) for the whole run: the security
        # check's cookie is then earned once instead of on every navigation.
        with self._pw.get_page(search_url) as page:
            html, _ = self._load(page, search_url, ".poi-list-cell, a[href*='/r-']")
            self._save_fixture("search", f"search_{_slug(topic)}", html, url=search_url, topic=topic)
            branches = pick_branches(parse_search_results_html(html), self._max_restaurants)
            if not branches:
                self._log.warning("openrice.no_restaurants_found", topic=topic)
                return
            self._log.info(
                "openrice.restaurants_found",
                count=len(branches),
                branches=[(b.name, b.face_count) for b in branches],
            )

            for branch in branches:
                if emitted >= limit:
                    break
                for post in self._scrape_branch(page, branch, since, seen_ids):
                    yield post
                    emitted += 1
                    if emitted >= limit:
                        break

        self._log.info("openrice.search.done", emitted=emitted)

    def fetch_thread(self, thread_id: str) -> Any:
        """Fetch a single review page.  thread_id = restaurant_url."""
        raise NotImplementedError("Openrice reviews are flat — use search()")

    def close(self) -> None:
        self._pw.close()
        if self._owns_robots:
            self._robots.close()

    # ------------------------------------------------------------------
    # Internal — navigation
    # ------------------------------------------------------------------

    def _scrape_branch(
        self, page: Any, branch: Branch, since: datetime, seen_ids: set[str]
    ) -> Iterator[RawPost]:
        """Yield in-window reviews for one branch, newest pages first.

        Reviews are skipped, not treated as a stop signal, when older than
        *since*: the first page can lead with featured older reviews before
        the newest-first list. Pagination stops at an empty or repeated page,
        a page with nothing in the window, or once the branch's own review
        total (shown on the list page) has been seen.
        """
        branch_seen = 0
        total: int | None = None
        for page_num in range(1, self._max_review_pages + 1):
            url = f"{branch.url}/reviews?page={page_num}"
            html, from_server = self._load(page, url, "article.review-post-desktop")
            self._save_fixture(
                "reviews", f"reviews_{branch.rest_id}_p{page_num}", html,
                url=url, rest_id=branch.rest_id,
            )
            total = total or parse_review_total(html)
            posts = parse_review_list_html(
                html, rest_url=branch.url, rest_id=branch.rest_id,
                cut_width=_SERVER_CUT_WIDTH if from_server else _DOM_CUT_WIDTH,
            )
            new = [p for p in posts if p.id not in seen_ids]
            if not new:
                self._log.info("openrice.no_reviews", rest_id=branch.rest_id, page=page_num)
                break
            seen_ids.update(p.id for p in new)
            branch_seen += len(new)

            in_window = [p for p in new if p.posted_at >= since]
            for post in in_window:
                yield self._complete(page, post)

            if not in_window:
                break  # the rest of the list is older still
            if total is not None and branch_seen >= total:
                break

    def _complete(self, page: Any, post: RawPost) -> RawPost:
        """Replace a cut-off list extract with the review page's full text."""
        meta = post.raw_metadata
        if not (self._fetch_full_text and meta.get("extract_truncated") and meta.get("review_id")):
            return post
        url = str(post.url)
        try:
            html, _ = self._load(page, url, ".review-post-body")
        except (SourceError, ForbiddenError):
            raise  # 403 / robots / bot check / 429 / TLS: stop, don't hide it
        except Exception as e:  # noqa: BLE001 — keep the extract on a one-off failure
            self._log.warning("openrice.full_text_failed", url=url, error=str(e))
            return post
        self._save_fixture("review", f"review_e{meta['review_id']}", html, url=url)
        full = parse_review_page_html(html)
        if not full or len(full) < len(post.body):
            return post
        return post.model_copy(update={
            "body": full,
            "language_detected": detect_language(f"{post.title}\n\n{full}" if post.title else full),
            "raw_metadata": {**meta, "extract_truncated": False, "full_text": True},
        })

    def _load(self, page: Any, url: str, ready_selector: str) -> tuple[str, bool]:
        """Navigate, wait for the content (past the security check), return HTML.

        Returns ``(html, from_server)``: the HTML the server sent for the final
        load when it holds the content, else the live DOM.
        """
        self._pw.navigate(page, url, wait_until="domcontentloaded", timeout=_GOTO_TIMEOUT_MS)
        try:
            page.wait_for_selector(ready_selector, timeout=_READY_TIMEOUT_MS)
        except Exception:  # noqa: BLE001 — a page with no reviews is a valid answer
            pass
        # The security check reloads the page after goto() returned; a 403 or
        # 429 on that reload must stop the source, not parse as "no reviews".
        self._pw.ensure_ok(page, url)
        server = self._pw.document_html(page)
        if server is not None and _CHALLENGE_MARKER not in server and \
                BeautifulSoup(server, "lxml").select_one(ready_selector) is None:
            server = None  # content rendered client-side only: use the DOM
        html = server if server is not None else _page_html(page)
        if _CHALLENGE_MARKER in html:
            raise SourceError(
                f"OpenRice's BytePlus security check did not let the bot through for "
                f"{url}; stopping (no evasion is attempted)"
            )
        return html, server is not None

    def _save_fixture(self, kind: str, name: str, html: str, **metadata: Any) -> None:
        """Keep the first page of each kind per run, anonymised (opt-in)."""
        if self._fixtures is None or kind in self._fixture_kinds_saved:
            return
        self._fixture_kinds_saved.add(kind)
        try:
            if name in self._fixtures.list_fixtures():
                return  # never overwrite a fixture the tests may rely on
            safe = anonymise_page_html(html)
            if safe is None:
                self._log.warning("openrice.fixture_not_saved", name=name, reason="anonymisation check failed")
                return
            self._fixtures.save(name, safe, metadata={**metadata, "anonymised": True})
        except Exception as e:  # noqa: BLE001
            self._log.warning("openrice.fixture_save_failed", error=str(e))


def _page_html(page: Any, attempts: int = 10) -> str:
    """``page.content()``, retried while the security check reloads the page."""
    for _ in range(attempts - 1):
        try:
            return page.content()
        except Exception:  # noqa: BLE001 — "page is navigating"
            page.wait_for_timeout(1000)
    return page.content()


# ---------------------------------------------------------------------------
# Module-level parsers — testable offline against the saved HTML fixtures.
# ---------------------------------------------------------------------------


def parse_search_results_html(html: str) -> list[Branch]:
    """Parse a restaurant search page → branches, in page order."""
    soup = BeautifulSoup(html, "lxml")
    branches: list[Branch] = []
    seen: set[str] = set()
    for cell in soup.select(".poi-list-cell"):
        link = cell.select_one("a.poi-list-cell-desktop-right-link-overlay") or next(
            (a for a in cell.select("a[href]") if _REST_ID.search(a.get("href", ""))), None
        )
        url = _restaurant_url(link.get("href", "")) if link else None
        if not url or url in seen:
            continue
        seen.add(url)
        name_el = cell.select_one(".poi-name")
        counts = [_to_count(t.get_text()) for t in cell.select(".poi-score-row .text")]
        known = [c for c in counts if c is not None]
        branches.append(Branch(
            name=name_el.get_text(" ", strip=True) if name_el else "",
            url=url,
            rest_id=_REST_ID.search(url).group(1),
            face_count=sum(known) if known else None,
        ))
    if branches:
        return branches

    # Fallback for a layout without result cards: any restaurant link.
    for a in soup.select('a[href*="/r-"]'):
        url = _restaurant_url(a.get("href", ""))
        if url and url not in seen:
            seen.add(url)
            branches.append(Branch(
                name=a.get_text(" ", strip=True), url=url,
                rest_id=_REST_ID.search(url).group(1), face_count=None,
            ))
    return branches


def pick_branches(branches: list[Branch], n: int) -> list[Branch]:
    """The *n* branches whose cards show the most reviews (page order breaks ties).

    The card count only ranks: branches showing 0 or no count still qualify
    (their OK-rated reviews aren't counted on the card) and come last.
    """
    ranked = sorted(enumerate(branches), key=lambda ib: (-(ib[1].face_count or 0), ib[0]))
    return [b for _, b in ranked][:n]


def parse_review_total(html: str) -> int | None:
    """The branch's own review total from a review-list page, if shown.

    Read from the rating popup ("51 Reviews") or the active tab
    ("Review (51)"); unlike the search card, it includes OK-rated reviews.
    """
    soup = BeautifulSoup(html, "lxml")
    desc = soup.select_one(".poi-score-detail-description")
    m = re.search(r"(\d[\d,]*)\s*Reviews?\b", desc.get_text()) if desc is not None else None
    if m is None:
        tab = soup.select_one("a.poi-detail-tab-bar-desktop-item.router-link-active")
        m = re.search(r"\((\d[\d,]*)\)", tab.get_text()) if tab is not None else None
    return int(m.group(1).replace(",", "")) if m else None


def parse_review_list_html(
    html: str, *, rest_url: str, rest_id: str, cut_width: int = _DOM_CUT_WIDTH
) -> list[RawPost]:
    """Parse a restaurant's review-list page → one RawPost per review.

    The DOM carries everything needed. When the page also has a schema.org
    ``ItemList`` of reviews (not always rendered), its exact timestamp and
    rating are used for the review with the same author at the same position.
    *cut_width*: extracts at least this wide are marked ``extract_truncated``
    (pass ``_SERVER_CUT_WIDTH`` for HTML as the server sent it).
    """
    soup = BeautifulSoup(html, "lxml")
    ld_reviews = _jsonld_reviews(soup)
    posts: list[RawPost] = []
    for idx, art in enumerate(soup.select("article.review-post-desktop")):
        ld = ld_reviews[idx] if idx < len(ld_reviews) else None
        post = _review_to_post(art, ld, rest_url=rest_url, rest_id=rest_id, cut_width=cut_width)
        if post is not None:
            posts.append(post)
    return posts


def parse_review_page_html(html: str) -> str | None:
    """Full review text from a review's own page (photos left out)."""
    soup = BeautifulSoup(html, "lxml")
    body = soup.select_one("section.review-post-main .review-post-body") or soup.select_one(
        ".review-post-body"
    )
    if body is None:
        return None
    for el in body.select(".review-body-attachment, script, style"):
        el.decompose()
    return _clean_text(_text_with_breaks(body)) or None


def _review_to_post(
    art: Tag, ld: dict[str, Any] | None, *, rest_url: str, rest_id: str, cut_width: int,
) -> RawPost | None:
    # Read the review's own section only: an article can also embed a
    # "Related Review" (another review by the same author, with its own stars
    # and text) in .review-post-other-info.
    main = art.select_one("section.review-post-main") or art

    # The name sits in a <span> (older layout) or a profile <a> (current).
    author_el = main.select_one(
        ".review-post-writer-info .info-top a, .review-post-writer-info .info-top span"
    ) or main.select_one(".review-post-writer-info .info-top")
    author = author_el.get_text(strip=True) if author_el else ""
    if ld is not None and author and ld.get("author") != author:
        ld = None  # positions disagree — don't borrow another review's fields

    info = [d.get_text(" ", strip=True) for d in main.select(".info-bottom .with-dot")]
    reviewer_level = next((_to_int(m.group(1)) for t in info if (m := re.match(r"Level\s*(\d+)", t))), None)
    # "679 views", or abbreviated for popular reviews: "2K views".
    views = next((_to_count(t) for t in info if re.search(r"\bviews?\b", t)), None)
    date_text = next((m.group(1) for t in info if (m := re.search(r"(\d{4}-\d{2}-\d{2})", t))), None)

    posted_at = (ld or {}).get("posted_at") or _date_only(date_text)
    if posted_at is None:
        return None

    title_el = main.select_one("a.review-post-title")
    title = _clean_text(title_el.get_text(" ", strip=True)) if title_el else ""
    review_href = next(
        (h for a in ([title_el] if title_el else []) + main.select('a[href*="/review/"]')
         if (h := a.get("href")) and _REVIEW_HREF.search(h)),
        None,
    )
    review_id = _REVIEW_HREF.search(review_href).group(1) if review_href else None

    extract_el = main.select_one(".review-post-extract")
    extract = _clean_text(_extract_text(extract_el)) if extract_el else ""
    if not extract and not title:
        return None

    rating = _star_rating(main)
    if rating is None and ld is not None:
        rating = ld.get("rating")

    likes_el = main.select_one(".review-like-btn-text")
    likes = _to_count(likes_el.get_text()) if likes_el else None

    author_hash = hash_author(author or "anonymous")
    if review_id:
        post_id = f"openrice_{review_id}"
    else:
        # Fallback id from the salted author hash, never the raw name: an
        # unsalted digest of the name plus stored fields would let anyone
        # holding the data confirm a guessed username.
        digest = hashlib.sha256(f"{author_hash}|{date_text}|{title}|{extract[:80]}".encode()).hexdigest()
        post_id = f"openrice_{rest_id}_{digest[:12]}"

    metrics: dict[str, int] = {}
    if rating is not None:
        # engagement_metrics are ints; round half up (4.5 -> 5) for the 1-5
        # rating and keep the exact value in raw_metadata.rating_value.
        metrics["rating"] = math.floor(rating + 0.5)
    if views is not None:
        metrics["views"] = views
    if likes is not None:
        metrics["likes"] = likes

    full_text = f"{title}\n\n{extract}" if title else extract
    return RawPost(
        id=post_id,
        source="openrice",
        source_category=SourceCategory.REVIEWS,
        region="HK",
        language="zh-HK",
        language_detected=detect_language(full_text),
        url=urljoin(BASE_URL, review_href) if review_href else rest_url,
        author_hash=author_hash,
        title=title or None,
        body=extract,
        posted_at=posted_at,
        signal_type=SignalType.EXPERIENCE,
        engagement_metrics=metrics,
        replies=[],
        raw_metadata={
            "rest_id": rest_id,
            "rest_url": rest_url,
            "review_id": review_id,
            "rating_value": rating,
            "rating_scale": 5,
            # Per-aspect 1-5 scores (taste, decor, service, hygiene, value).
            "sub_ratings": _sub_ratings(art),
            # The reviewer's OpenRice level badge — about the author, not
            # the restaurant; never use it as a rating.
            "reviewer_level": reviewer_level,
            "extract_truncated": _looks_truncated(extract, cut_width),
        },
    )


def _jsonld_reviews(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """Reviews from a schema.org ItemList, in list order (empty if absent)."""
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or "")
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict) or data.get("@type") != "ItemList":
            continue
        out: list[dict[str, Any]] = []
        for el in data.get("itemListElement") or []:
            item = (el or {}).get("item") or {}
            if item.get("@type") != "Review":
                continue
            out.append({
                "author": ((item.get("author") or {}).get("name") or "").strip(),
                "posted_at": _iso_datetime(item.get("datePublished")),
                "rating": _to_float((item.get("reviewRating") or {}).get("ratingValue")),
            })
        return out
    return []


def _star_rating(main: Tag) -> float | None:
    """The review's overall 0.5-5 star rating (one star row), else None."""
    row = main.select_one(".poi-detail-rating-stars")
    if row is None:
        return None
    full = half = 0
    for star in row.select(".poi-detail-rating-star"):
        classes = " ".join(star.get("class", []))
        if "poi-detail-rating-star-full" in classes:
            full += 1
        elif "poi-detail-rating-star-half" in classes:
            half += 1
    rating = full + 0.5 * half
    return rating if 0 < rating <= 5 else None


def _sub_ratings(art: Tag) -> dict[str, int]:
    scores: dict[str, int] = {}
    for item in art.select(".review-post-rating-scores .pdsd-item"):
        label = item.select_one(".pdsd-item-label")
        value = item.select_one(".pdsd-item-value")
        score = _to_int(value.get_text()) if value is not None else None
        if label is not None and score is not None:
            scores[label.get_text(strip=True).lower()] = score
    return scores


def _extract_text(el: Tag) -> str:
    """List-page extract without its trailing "…" / "Read More" spans."""
    parts: list[str] = []
    for child in el.children:
        if isinstance(child, Tag):
            if child.name == "span":
                continue
            parts.append("\n" if child.name == "br" else _text_with_breaks(child))
        elif isinstance(child, NavigableString) and not isinstance(child, Comment):
            parts.append(str(child))
    return "".join(parts)


def _text_with_breaks(el: Tag) -> str:
    """Text of *el* keeping <br> and block boundaries as line breaks."""
    parts: list[str] = []
    for node in el.descendants:
        if isinstance(node, Tag):
            if node.name == "br" or node.name in _BLOCK_TAGS:
                parts.append("\n")
        elif not isinstance(node, Comment) and node.parent.name not in ("script", "style"):
            parts.append(str(node))
    return "".join(parts)


def _clean_text(text: str) -> str:
    text = text.replace("​", "").replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _looks_truncated(extract: str, cut_width: int = _DOM_CUT_WIDTH) -> bool:
    text = extract.rstrip()
    while text and unicodedata.category(text[-1]) in ("Cf", "Mn", "Sk"):
        text = text[:-1].rstrip()  # zero-width chars, variation selectors, skin tones
    if not text:
        return False
    if _display_width(text) >= cut_width:
        return True  # could be a cut that happens to land after 。 or an emoji
    last = text[-1]
    return not (last in _SENTENCE_END or unicodedata.category(last) == "So")


def _display_width(text: str) -> int:
    """Width in half-width cells: CJK and emoji count 2, everything else 1."""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _restaurant_url(href: str) -> str | None:
    if not _REST_ID.search(href or ""):
        return None
    url = urljoin(BASE_URL, href.split("?")[0].split("#")[0])
    url = re.sub(r"/(photos|map|menu|videos|reviews)(/.*)?$", "", url)
    return url.rstrip("/")


def _date_only(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        day = datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return None
    return day.replace(hour=12, tzinfo=_HKT).astimezone(timezone.utc)


def _iso_datetime(value: Any) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else None


def _to_int(text: Any) -> int | None:
    m = re.search(r"\d[\d,]*", str(text or ""))
    return int(m.group(0).replace(",", "")) if m else None


def _to_count(text: Any) -> int | None:
    """A displayed count: "679", "1,190", or abbreviated "2K" / "1.2M"."""
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*([KkMm])?", str(text or ""))
    if not m:
        return None
    scale = {"k": 1_000, "m": 1_000_000}.get((m.group(2) or "").lower(), 1)
    return round(float(m.group(1).replace(",", "")) * scale)


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _slug(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", s.lower().strip())
    return s.strip("_")[:50] or "untitled"


# ---------------------------------------------------------------------------
# Fixture anonymisation — reviewer names never reach a saved page.
# ---------------------------------------------------------------------------

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_ALIAS = re.compile(r"reviewer_\d+")
# Review text is the reviewer's own writing and stays as written; names are
# replaced everywhere else (headers, avatars, titles, structured data).
_USER_TEXT = ".review-post-body, .review-post-extract, a.review-post-title, .review-post-related-item"


def anonymise_page_html(html: str) -> str | None:
    """A copy of an OpenRice page with reviewers made anonymous.

    Reviewer names become ``reviewer_<n>`` (consistently, so the structured
    data still lines up with the articles), profile links, avatars, tracking
    attributes, comment threads, scripts and user ids are removed. Returns
    None if any reviewer name survives outside the review text, so a page
    that can't be cleaned is never saved.
    """
    soup = BeautifulSoup(html, "lxml")
    aliases: dict[str, str] = {}

    def alias(name: str) -> str:
        if _ALIAS.fullmatch(name):
            return name  # already anonymised
        return aliases.setdefault(name, f"reviewer_{len(aliases) + 1}")

    for el in soup.select(".review-post-writer-info .info-top a, .review-post-writer-info .info-top span"):
        if name := el.get_text(strip=True):
            el.string = alias(name)
    for img in soup.select("a.user-icon img, img[class*='avatar']"):
        if name := (img.get("alt") or "").strip():
            alias(name)
        img["alt"] = ""
        img["src"] = ""
    for a in soup.select("a[href*='/user/']"):
        a["href"] = f"{BASE_URL}/en/user/anon/"
    for el in soup.select(".review-post-comments, script:not([type='application/ld+json']), noscript, iframe"):
        el.decompose()
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or "")
        except (TypeError, ValueError):
            script.decompose()
            continue
        _anonymise_people(data, alias)
        script.string = json.dumps(data, ensure_ascii=False)
    for tag in soup.find_all(True):
        for attr in [a for a in tag.attrs if a.startswith("data-")]:
            del tag[attr]

    names = sorted((n for n in aliases if len(n) >= 2), key=len, reverse=True)
    if names:
        pattern = re.compile("|".join(re.escape(n) for n in names))
        user_text = {id(el) for el in soup.select(_USER_TEXT)}

        def in_user_text(node: Any) -> bool:
            return any(id(p) in user_text for p in node.parents)

        for node in soup.find_all(string=pattern):
            if not in_user_text(node):
                node.replace_with(pattern.sub(lambda m: aliases[m.group(0)], str(node)))
        for tag in soup.find_all(True):
            for attr in ("title", "alt", "content", "aria-label", "href"):
                value = tag.get(attr)
                if isinstance(value, str) and pattern.search(value) and not in_user_text(tag):
                    tag[attr] = pattern.sub(lambda m: aliases[m.group(0)], value)
        # Fail closed: check what is left, outside the review text.
        check = BeautifulSoup(str(soup), "lxml")
        for el in check.select(_USER_TEXT):
            el.decompose()
        leftover = str(check)
        if any(n in leftover for n in names if len(n) >= 3):
            return None
    return _UUID.sub("00000000-0000-0000-0000-000000000000", str(soup))


def _anonymise_people(node: Any, alias: Any) -> None:
    """Replace schema.org Person names in place; drop their url/image."""
    if isinstance(node, list):
        for item in node:
            _anonymise_people(item, alias)
    elif isinstance(node, dict):
        if node.get("@type") == "Person":
            if isinstance(node.get("name"), str) and node["name"].strip():
                node["name"] = alias(node["name"].strip())
            node.pop("url", None)
            node.pop("image", None)
        for value in node.values():
            _anonymise_people(value, alias)


# ---------------------------------------------------------------------------
# scrape-doctor check — invoked against the saved HTML fixtures.
# ---------------------------------------------------------------------------


def doctor_check(name: str, html: str, meta: dict) -> tuple[bool, str]:
    """Doctor hook: run the real parser that matches the fixture's page kind."""
    if name.startswith("search_"):
        branches = parse_search_results_html(html)
        if not branches:
            return False, "parse_search_results_html found no restaurants"
        counted = sum(b.face_count is not None for b in branches)
        return True, f"{len(branches)} restaurants ({counted} with card counts)"
    if name.startswith("review_e"):
        text = parse_review_page_html(html)
        if not text:
            return False, "parse_review_page_html found no review body"
        return True, f"review body OK ({len(text)} chars)"
    if name.startswith("reviews_"):
        rest_id = str((meta or {}).get("rest_id") or "0")
        posts = parse_review_list_html(html, rest_url=f"{BASE_URL}/r-x-r{rest_id}", rest_id=rest_id)
        if not posts:
            return False, "parse_review_list_html returned 0 reviews (layout drift?)"
        rated = sum("rating" in p.engagement_metrics for p in posts)
        linked = sum(bool(p.raw_metadata.get("review_id")) for p in posts)
        if rated < len(posts) or linked < len(posts):
            return False, f"{len(posts)} reviews but only {rated} rated / {linked} with their own URL"
        return True, f"{len(posts)} reviews, all rated and linked to their own page"
    return True, f"{len(html)} bytes (unrecognised fixture kind)"
