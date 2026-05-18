"""Medium HK scraper — pulls articles via Medium's ``?format=json`` endpoint.

Why not HTML: Medium article pages are JS-rendered (the Apollo store ships
*references* to posts, not their bodies). Their ``?format=json`` endpoint
returns server-side JSON for any article URL — same body, claps, author,
without any DOM rendering. The response is prefixed with the
XSS-prevention sequence ``])}while(1);</x>`` which we strip before parsing.

Discovery: DuckDuckGo SERP for ``site:medium.com 'Hong Kong' <topic>`` —
the shared ``src.scrape.utils.ddg_search`` utility caches results for 24 h.

**ToS:** Medium prohibits automated access. This scraper is registered
``default_enabled=False`` and only runs when explicitly listed in
``--sources medium_hk``. The user assumes ToS responsibility.
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

# Medium prepends this exact byte sequence to every JSON payload to defeat
# JSON-hijacking attacks. Strip it before parsing.
_XSS_PREFIX = "])}while(1);</x>"

# Medium paragraph type codes used in content.bodyModel.paragraphs.
# 1 = paragraph, 3 = h3 / title, 4 = h4 / subtitle, others (img, blockquote
# code blocks, etc.) we ignore for body extraction.
_BODY_PARAGRAPH_TYPES = {1, 6, 7, 9}   # paragraph + quote + pre + bullet
_HEADING_TYPES = {2, 3, 4}             # h1/h2/h3

MEDIUM_RATE = 1.5
MAX_ARTICLES_PER_SEARCH = 15


class MediumHKScraper:
    """Medium scraper for HK-tagged content, using ``?format=json``."""

    source_id = "medium_hk"
    region = "HK"
    language = "en"      # most Medium HK writers publish in English; detected per post
    category = SourceCategory.BLOGS
    signal_type = SignalType.RECOMMENDATION

    def __init__(
        self,
        *,
        client: PoliteClient | None = None,
        robots_cache: RobotsCache | None = None,
        max_articles: int = MAX_ARTICLES_PER_SEARCH,
    ) -> None:
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

    def __enter__(self) -> "MediumHKScraper":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ---- SourceScraper protocol -----------------------------------------

    def search(
        self, topic: str, since: datetime, limit: int,
    ) -> Iterator[RawPost]:
        """Discover articles via DDG, then fetch each one's ?format=json."""
        query = f'site:medium.com "Hong Kong" {topic}'
        try:
            hits = ddg_search(query, max_results=self._max_articles)
        except SourceError as e:
            _log.warning("medium_hk.search_failed", topic=topic, error=str(e))
            return

        _log.info(
            "medium_hk.search", topic=topic, candidates=len(hits),
        )

        emitted = 0
        for hit in hits:
            if emitted >= limit:
                break
            if not _is_medium_article_url(hit.url):
                continue
            try:
                post = self.fetch_article(hit.url)
            except SourceError as e:
                _log.warning(
                    "medium_hk.article_failed", url=hit.url, error=str(e),
                )
                continue
            if since is not None and post.posted_at < since:
                continue
            yield post
            emitted += 1

    def fetch_article(self, article_url: str) -> RawPost:
        """Fetch the ``?format=json`` payload for ``article_url`` and parse it."""
        json_url = _format_json_url(article_url)
        body = self._client.get_html(json_url)   # endpoint returns text/javascript
        return parse_medium_response(body, source_url=article_url)


# ---------------------------------------------------------------------------
# Pure parsers — testable offline against the saved JSON fixture.
# ---------------------------------------------------------------------------


def _is_medium_article_url(url: str) -> bool:
    """True if ``url`` looks like a Medium article (not a user/topic page)."""
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return False
    if not (host.endswith("medium.com") or host == "medium.com"):
        return False
    path = urlparse(url).path
    # Article URLs end with `-{12-hex-id}`. Topic/user roots are shorter.
    return "-" in path and len(path) > 20


def _format_json_url(article_url: str) -> str:
    """Append ?format=json to an article URL, preserving any existing query."""
    if "?" in article_url:
        return article_url + "&format=json"
    return article_url + "?format=json"


def _strip_xss_prefix(body: str) -> str:
    """Medium prefixes every JSON payload with this exact sequence."""
    body = body.lstrip()
    if body.startswith(_XSS_PREFIX):
        return body[len(_XSS_PREFIX):].lstrip()
    return body


def parse_medium_response(body: str, *, source_url: str) -> RawPost:
    """Parse Medium's ?format=json payload into a RawPost.

    Raises ``SourceError`` if the response is missing the XSS prefix, has
    no payload, has no body content, or is locked behind the paywall with
    zero readable text.
    """
    stripped = _strip_xss_prefix(body)
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as e:
        raise SourceError(f"medium_hk: response is not JSON: {e}") from None

    if not data.get("success"):
        raise SourceError("medium_hk: payload success=false")

    value = (data.get("payload") or {}).get("value") or {}
    refs = (data.get("payload") or {}).get("references") or {}
    if not value:
        raise SourceError("medium_hk: empty payload.value")

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
            # Skip headings already represented in title/subtitle to avoid
            # duplicates; keep h2/h3 mid-article for context.
            if text == title or text == subtitle:
                continue
            body_parts.append(text)
        elif ptype in _BODY_PARAGRAPH_TYPES:
            body_parts.append(text)
    body_text = "\n\n".join(body_parts).strip()
    if not body_text:
        raise SourceError(f"medium_hk: no body text in {post_id!r}")

    virtuals = value.get("virtuals") or {}
    claps = int(virtuals.get("totalClapCount", 0) or 0)
    responses = int(virtuals.get("responsesCreatedCount", 0) or 0)
    word_count = int(virtuals.get("wordCount", 0) or 0)
    reading_time = float(virtuals.get("readingTime", 0.0) or 0.0)

    creator_id = value.get("creatorId") or ""
    creator = ((refs.get("User") or {}).get(creator_id) or {})
    author_name = creator.get("name", "") or creator.get("username", "")
    author_username = creator.get("username", "")

    # firstPublishedAt is milliseconds since epoch.
    posted_at_ms = (
        value.get("firstPublishedAt")
        or value.get("createdAt")
        or 0
    )
    try:
        posted_at = datetime.fromtimestamp(int(posted_at_ms) / 1000.0, tz=timezone.utc)
    except (TypeError, ValueError):
        posted_at = datetime(1970, 1, 1, tzinfo=timezone.utc)

    medium_url = value.get("mediumUrl") or source_url
    full_text_for_detection = f"{title}\n\n{body_text}"

    return RawPost(
        id=f"medium_hk:{post_id}" if post_id else f"medium_hk:{abs(hash(source_url))}",
        source="medium_hk",
        source_category=SourceCategory.BLOGS,
        region="HK",
        language="en",
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
