"""Trustpilot scraper — company review pages via static HTML.

Trustpilot serves company review pages as server-rendered HTML with embedded
JSON-LD for structured data. Reviews are paginated (20 per page) with
?page=N query parameters.

**ToS:** Trustpilot prohibits automated access. Flagged, opt-in only.
Rate limit: 1 req / 3 s.
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

BASE_URL = "https://www.trustpilot.com"
SEARCH_URL = BASE_URL + "/search?query={query}"
TRUSTPILOT_RATE = 3.0
MAX_COMPANIES = 5
MAX_PAGES = 3


class TrustpilotScraper:
    """Trustpilot review scraper."""

    source_id = "trustpilot"
    region = "US"
    language = "en"
    category = SourceCategory.REVIEWS
    signal_type = SignalType.EXPERIENCE

    def __init__(
        self,
        *,
        client: PoliteClient | None = None,
        robots_cache: RobotsCache | None = None,
        max_companies: int = MAX_COMPANIES,
    ) -> None:
        self._max_companies = max_companies
        self._owns_client = client is None
        if client is None:
            self._robots_cache = robots_cache or RobotsCache()
            self._client = PoliteClient(
                robots_cache=self._robots_cache, rate=TRUSTPILOT_RATE,
            )
        else:
            self._client = client

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def search(self, topic: str, since: datetime, limit: int) -> Iterator[RawPost]:
        # If topic looks like a trustpilot URL, use it directly
        if "trustpilot.com" in topic:
            company_urls = [topic]
        else:
            url = SEARCH_URL.format(query=quote(topic))
            try:
                html = self._client.get_html(url)
            except SourceError:
                return
            company_urls = _parse_search_results(html)[:self._max_companies]

        emitted = 0
        for curl in company_urls:
            if emitted >= limit:
                break
            for page in range(1, MAX_PAGES + 1):
                page_url = f"{curl}?page={page}" if page > 1 else curl
                try:
                    html = self._client.get_html(page_url)
                    for post in _parse_reviews(html, company_url=curl):
                        if emitted >= limit:
                            break
                        if post.posted_at >= since:
                            yield post
                            emitted += 1
                except SourceError:
                    break

    def fetch_thread(self, thread_id: str) -> Any:
        html = self._client.get_html(thread_id)
        reviews = list(_parse_reviews(html, company_url=thread_id))
        return reviews[0] if reviews else None


def _parse_search_results(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    for link in soup.select("a[href*='/review/']"):
        href = link.get("href", "")
        if "/review/" in href:
            full = BASE_URL + href.split("?")[0]
            # Extract company URL from review URL
            parts = full.split("/review/")
            if parts:
                urls.append(parts[0])
    # De-duplicate while preserving order
    seen: set[str] = set()
    result = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            result.append(u)
    return result


def _parse_reviews(html: str, *, company_url: str) -> Iterator[RawPost]:
    soup = BeautifulSoup(html, "html.parser")

    # Try JSON-LD first
    json_ld = soup.select_one('script[type="application/ld+json"]')
    if json_ld:
        try:
            data = json.loads(json_ld.string)
            if isinstance(data, dict) and "review" in data:
                for review in data.get("review", []):
                    rp = _jsonld_to_post(review, company_url)
                    if rp:
                        yield rp
                return
        except (json.JSONDecodeError, KeyError):
            pass

    # Fallback: HTML review cards
    for card in soup.select("article.review, .review-card, [data-service-review-card]"):
        rp = _html_card_to_post(card, company_url)
        if rp:
            yield rp


def _jsonld_to_post(review: dict, company_url: str) -> RawPost | None:
    try:
        body = review.get("reviewBody", "") or ""
        title = review.get("name", "") or ""
        rating = int(review.get("reviewRating", {}).get("ratingValue", 0))
        author_name = review.get("author", {}).get("name", "") or "anonymous"
        date_str = review.get("datePublished", "")
        posted_at = datetime.fromisoformat(date_str.replace("Z", "+00:00")) if date_str else datetime.now(timezone.utc)

        full_text = f"{title}\n\n{body}"
        return RawPost(
            id=f"trustpilot:{abs(hash(full_text))}",
            source="trustpilot",
            source_category=SourceCategory.REVIEWS,
            region="US",
            language="en",
            language_detected=detect_language(full_text),
            url=company_url,
            author_hash=hash_author(author_name),
            title=title or None,
            body=body,
            posted_at=posted_at,
            signal_type=SignalType.EXPERIENCE,
            engagement_metrics={"rating": rating},
            replies=[],
            raw_metadata={"company_url": company_url},
        )
    except Exception as e:
        _log.warning("trustpilot.parse_failed", error=str(e))
        return None


def _html_card_to_post(card, company_url: str) -> RawPost | None:
    try:
        title_el = card.select_one("h2, .review-title, [class*='title']")
        title = title_el.get_text(strip=True) if title_el else ""
        body_el = card.select_one(".review-content__text, .review-body, [class*='content']")
        body = body_el.get_text(strip=True) if body_el else ""
        author_el = card.select_one("[class*='consumer-name'], [class*='author']")
        author = author_el.get_text(strip=True) if author_el else ""
        rating_el = card.select_one("[class*='star-rating'], img[alt*='star']")
        rating = 0
        if rating_el:
            alt = rating_el.get("alt", "")
            m = re.search(r"(\d+)", alt)
            rating = int(m.group(1)) if m else 0

        full_text = f"{title}\n\n{body}"
        return RawPost(
            id=f"trustpilot:{abs(hash(full_text))}",
            source="trustpilot",
            source_category=SourceCategory.REVIEWS,
            region="US",
            language="en",
            language_detected=detect_language(full_text),
            url=company_url,
            author_hash=hash_author(author),
            title=title or None,
            body=body,
            posted_at=datetime.now(timezone.utc),
            signal_type=SignalType.EXPERIENCE,
            engagement_metrics={"rating": rating},
            replies=[],
            raw_metadata={"company_url": company_url},
        )
    except Exception:
        return None
