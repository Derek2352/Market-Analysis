"""Dcard scraper — Taiwan's university-centric social platform via public JSON API.

Dcard exposes search and post endpoints as public JSON (no auth):
- https://www.dcard.tw/service/api/v2/search/posts?query=<topic>&limit=30
- https://www.dcard.tw/service/api/v2/posts/<post_id>

**ToS:** Dcard's stance is silent. Rate limit: 1 req / 2 s.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import structlog

from src.scrape.base import RobotsCache, SourceError
from src.scrape.base.http import PoliteClient
from src.scrape.utils.hashing import hash_author
from src.scrape.utils.lang import detect_language
from src.schemas.enums import SignalType, SourceCategory
from src.schemas.raw import RawPost

_log = structlog.get_logger(__name__)

API_BASE = "https://www.dcard.tw/service/api/v2"
SEARCH_URL = API_BASE + "/search/posts?query={query}&limit={limit}"
POST_URL = API_BASE + "/posts/{post_id}"

DCARD_RATE = 2.0
MAX_POSTS = 30


class DcardScraper:
    """Dcard public JSON API scraper."""

    source_id = "dcard"
    region = "TW"
    language = "zh-TW"
    category = SourceCategory.FORUMS
    signal_type = SignalType.EXPERIENCE

    def __init__(
        self,
        *,
        client: PoliteClient | None = None,
        robots_cache: RobotsCache | None = None,
        max_posts: int = MAX_POSTS,
    ) -> None:
        self._max_posts = max_posts
        self._owns_client = client is None
        if client is None:
            self._robots_cache = robots_cache or RobotsCache()
            self._client = PoliteClient(
                robots_cache=self._robots_cache, rate=DCARD_RATE,
            )
        else:
            self._client = client

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def search(self, topic: str, since: datetime, limit: int) -> Iterator[RawPost]:
        url = SEARCH_URL.format(query=quote(topic), limit=min(limit * 2, 100))
        try:
            resp = self._client.get(url)
            data = resp if isinstance(resp, dict) else json.loads(resp.text)
        except Exception as e:
            _log.warning("dcard.search_failed", topic=topic, error=str(e))
            return

        posts_data = data if isinstance(data, list) else data.get("data", []) if isinstance(data, dict) else []
        emitted = 0
        for post in posts_data:
            if emitted >= limit:
                break
            post_id = str(post.get("id", ""))
            if not post_id:
                continue

            rp = _parse_dcard_post(post)
            if rp and rp.posted_at >= since:
                yield rp
                emitted += 1

    def fetch_thread(self, thread_id: str) -> Any:
        url = POST_URL.format(post_id=thread_id)
        resp = self._client.get(url)
        data = resp if isinstance(resp, dict) else json.loads(resp.text)
        return _parse_dcard_post(data)


def _parse_dcard_post(post: dict) -> RawPost | None:
    try:
        post_id = str(post.get("id", ""))
        title = post.get("title", "") or ""
        body = post.get("content", "") or post.get("excerpt", "") or ""
        forum = post.get("forumName") or post.get("forum", {}).get("name", "")
        author = post.get("anonymous", False) and "anonymous" or (post.get("school") or "")

        created_str = post.get("createdAt") or post.get("updatedAt", "")
        try:
            posted_at = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            posted_at = datetime.now(timezone.utc)

        like_count = int(post.get("likeCount", 0) or 0)
        comment_count = int(post.get("commentCount", 0) or 0)

        full_text = f"{title}\n\n{body}"
        return RawPost(
            id=f"dcard:{post_id}",
            source="dcard",
            source_category=SourceCategory.FORUMS,
            region="TW",
            language="zh-TW",
            language_detected=detect_language(full_text),
            url=f"https://www.dcard.tw/f/{forum}/p/{post_id}",
            author_hash=hash_author(author),
            title=title or None,
            body=body,
            posted_at=posted_at,
            signal_type=SignalType.EXPERIENCE,
            engagement_metrics={"likes": like_count, "comments": comment_count},
            replies=[],
            raw_metadata={"forum": forum, "post_id": post_id},
        )
    except Exception as e:
        _log.warning("dcard.parse_failed", error=str(e))
        return None
