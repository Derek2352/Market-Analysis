"""Medium scraper — region-aware, pulls articles via ``?format=json`` endpoint.

Generalized from medium_hk.py for Phase 8 multi-region expansion.
Discovery: DuckDuckGo SERP for ``site:medium.com '<region_name>' <topic>``.

**ToS:** Medium prohibits automated access. Default-disabled. The user assumes
ToS responsibility.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import structlog

from src.scrape.base import RobotsCache, SourceError
from src.scrape.base.http import PoliteClient
from src.scrape.utils.ddg_search import search as ddg_search
from src.scrape.utils.hashing import hash_author
from src.scrape.utils.lang import detect_language
from src.schemas.enums import SignalType, SourceCategory
from src.schemas.raw import RawPost

_log = structlog.get_logger(__name__)

_XSS_PREFIX = "])}while(1);</x>"
_BODY_PARAGRAPH_TYPES = {1, 6, 7, 9}
_HEADING_TYPES = {2, 3, 4}
MEDIUM_RATE = 1.5
MAX_ARTICLES_PER_SEARCH = 15

# Region → DDG search term for discovery.
_REGION_SEARCH_TERMS: dict[str, str] = {
    "HK": "Hong Kong",
    "US": "United States",
    "TW": "Taiwan",
    "JP": "Japan",
}

# Region → language code.
_REGION_LANGUAGE: dict[str, str] = {
    "HK": "en",
    "US": "en",
    "TW": "zh-TW",
    "JP": "ja",
}


class MediumScraper:
    """Region-aware Medium scraper using ``?format=json``."""

    source_id = "medium"
    category = SourceCategory.BLOGS
    signal_type = SignalType.RECOMMENDATION

    def __init__(
        self,
        *,
        region: str = "HK",
        client: PoliteClient | None = None,
        robots_cache: RobotsCache | None = None,
        max_articles: int = MAX_ARTICLES_PER_SEARCH,
    ) -> None:
        self.region = region
        self.language = _REGION_LANGUAGE.get(region, "en")
        self._search_term = _REGION_SEARCH_TERMS.get(region, "Hong Kong")
        self._owns_client = client is None
        if client is None:
            self._robots_cache = robots_cache or RobotsCache()
            self._client = PoliteClient(
                robots_cache=self._robots_cache, rate=MEDIUM_RATE,
            )
        else:
            self._client = client
        self._max_articles = max_articles

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "MediumScraper":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def search(
        self, topic: str, since: datetime, limit: int,
    ) -> Iterator[RawPost]:
        """Discover articles via DDG, then fetch each one's ?format=json."""
        query = f'site:medium.com "{self._search_term}" {topic}'
        try:
            hits = ddg_search(query, max_results=self._max_articles)
        except SourceError as e:
            _log.warning("medium.search_failed", topic=topic, error=str(e))
            return

        _log.info("medium.search", topic=topic, candidates=len(hits))

        emitted = 0
        for hit in hits:
            if emitted >= limit:
                break
            if not _is_medium_article_url(hit.url):
                continue
            try:
                post = self.fetch_article(hit.url)
            except SourceError as e:
                _log.warning("medium.article_failed", url=hit.url, error=str(e))
                continue
            if since is not None and post.posted_at < since:
                continue
            yield post
            emitted += 1

    def fetch_article(self, article_url: str) -> RawPost:
        """Fetch the ``?format=json`` payload for ``article_url``."""
        json_url = _format_json_url(article_url)
        body = self._client.get_html(json_url)
        return parse_medium_response(
            body, source_url=article_url,
            region=self.region, language=self.language,
            source=self.source_id,
        )

    def fetch_thread(self, thread_id: str) -> RawPost:
        return self.fetch_article(thread_id)


# ---------------------------------------------------------------------------
# Pure parsers
# ---------------------------------------------------------------------------

def _is_medium_article_url(url: str) -> bool:
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return False
    if not (host.endswith("medium.com") or host == "medium.com"):
        return False
    path = urlparse(url).path
    return "-" in path and len(path) > 20


def _format_json_url(article_url: str) -> str:
    if "?" in article_url:
        return article_url + "&format=json"
    return article_url + "?format=json"


def _strip_xss_prefix(body: str) -> str:
    body = body.lstrip()
    if body.startswith(_XSS_PREFIX):
        return body[len(_XSS_PREFIX):].lstrip()
    return body


def parse_medium_response(
    body: str, *,
    source_url: str,
    region: str = "HK",
    language: str = "en",
    source: str = "medium",
) -> RawPost:
    """Parse Medium's ?format=json payload into a RawPost."""
    stripped = _strip_xss_prefix(body)
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as e:
        raise SourceError(f"medium: response is not JSON: {e}") from None

    if not data.get("success"):
        raise SourceError("medium: payload success=false")

    value = (data.get("payload") or {}).get("value") or {}
    refs = (data.get("payload") or {}).get("references") or {}
    if not value:
        raise SourceError("medium: empty payload.value")

    post_id = value.get("id") or ""
    title = (value.get("title") or "").strip()
    subtitle = (value.get("content") or {}).get("subtitle") or value.get("subtitle") or ""
    paragraphs = (value.get("content") or {}).get("bodyModel", {}).get("paragraphs") or []

    body_parts: list[str] = []
    for p in paragraphs:
        if not isinstance(p, dict):
            continue
        text = (p.get("text") or "").strip()
        if not text:
            continue
        ptype = p.get("type")
        if ptype in _HEADING_TYPES:
            if text == title or text == subtitle:
                continue
            body_parts.append(text)
        elif ptype in _BODY_PARAGRAPH_TYPES:
            body_parts.append(text)
    body_text = (chr(10) * 2).join(body_parts).strip()
    if not body_text:
        raise SourceError(f"medium: no body text in {post_id!r}")

    virtuals = value.get("virtuals") or {}
    claps = int(virtuals.get("totalClapCount", 0) or 0)
    responses = int(virtuals.get("responsesCreatedCount", 0) or 0)
    word_count = int(virtuals.get("wordCount", 0) or 0)
    reading_time = float(virtuals.get("readingTime", 0.0) or 0.0)

    creator_id = value.get("creatorId") or ""
    creator = ((refs.get("User") or {}).get(creator_id) or {})
    author_name = creator.get("name", "") or creator.get("username", "")
    author_username = creator.get("username", "")

    posted_at_ms = value.get("firstPublishedAt") or value.get("createdAt") or 0
    try:
        posted_at = datetime.fromtimestamp(int(posted_at_ms) / 1000.0, tz=timezone.utc)
    except (TypeError, ValueError):
        posted_at = datetime(1970, 1, 1, tzinfo=timezone.utc)

    medium_url = value.get("mediumUrl") or source_url
    full_text_for_detection = f"{title}\\n\\n{body_text}"

    return RawPost(
        id=f"{source}:{post_id}" if post_id else f"{source}:{abs(hash(source_url))}",
        source=source,
        source_category=SourceCategory.BLOGS,
        region=region,
        language=language,
        language_detected=detect_language(full_text_for_detection),
        url=medium_url,
        author_hash=hash_author(author_name) if author_name else "",
        title=title or None,
        body=body_text,
        posted_at=posted_at,
        signal_type=SignalType.RECOMMENDATION,
        engagement_metrics={
            "claps_count": claps,
            "response_count": responses,
            "word_count": word_count,
        },
        replies=[],
        raw_metadata={
            "post_id": post_id,
            "subtitle": subtitle,
            "author_username": author_username,
            "reading_time_minutes": round(reading_time, 2),
            "is_locked": bool(value.get("isLocked", False)),
        },
    )
