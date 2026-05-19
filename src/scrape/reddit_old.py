"""Reddit scraper via old.reddit.com JSON API — no auth required.

Uses Reddit's public JSON API (add .json to any URL) which returns
structured data without authentication. Much more reliable than HTML
scraping, which is now blocked by Reddit's network policy.

Usage::

    mkt scrape --topic "MTR" --region HK --sources reddit_old \\
      --subreddits HongKong,HongKongTravel --limit 500

Rate limit: 1 req / 2 s (Reddit's published guideline for non-API access).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import structlog

from src.scrape.base import PoliteClient, RobotsCache
from src.scrape.utils.hashing import hash_author
from src.scrape.utils.lang import detect_language
from src.schemas.enums import SignalType, SourceCategory
from src.schemas.raw import RawPost

BASE_URL = "https://old.reddit.com"
REDDIT_RATE = 2.0  # 1 req / 2 s
MAX_SEARCH_PAGES = 10

# Default subreddits per region. Used when --subreddits is not passed.
_REGION_DEFAULT_SUBREDDITS: dict[str, list[str]] = {
    "HK": ["HongKong"],
    "US": ["AskReddit", "personalfinance", "technology"],
    "TW": ["Taiwan"],
    "JP": ["newsokur", "newsokunomoral"],
}

# Region → language mapping for RawPost metadata.
_REGION_LANGUAGE: dict[str, str] = {
    "HK": "en",
    "US": "en",
    "TW": "zh-TW",
    "JP": "ja",
}

REGION_NAMES: dict[str, str] = {
    "HK": "Hong Kong",
    "US": "United States",
    "TW": "Taiwan",
    "JP": "Japan",
}


class RedditOldScraper:
    """Scrape Reddit posts via old.reddit.com JSON API."""

    source_id = "reddit_old"

    def __init__(
        self,
        *,
        region: str = "HK",
        subreddits: list[str] | None = None,
        rate: float = REDDIT_RATE,
    ) -> None:
        self.region = region
        self.language = _REGION_LANGUAGE.get(region, "en")
        self._log = structlog.get_logger().bind(scraper="reddit_old")
        self._robots = RobotsCache()
        self._client = PoliteClient(
            robots_cache=self._robots, rate=rate, respect_robots=False
        )
        self._subreddits = subreddits or _REGION_DEFAULT_SUBREDDITS.get(
            region, ["HongKong"]
        )

    # ------------------------------------------------------------------
    # SourceScraper protocol
    # ------------------------------------------------------------------

    def search(
        self,
        topic: str,
        since: datetime,
        limit: int,
    ) -> Iterator[RawPost]:
        """Search Reddit for *topic* across configured subreddits."""
        self._log.info(
            "reddit_old.search.start",
            topic=topic,
            subreddits=self._subreddits,
            limit=limit,
        )

        emitted = 0
        seen_ids: set[str] = set()

        for subreddit in self._subreddits:
            if emitted >= limit:
                break

            after: str | None = None
            for _page in range(MAX_SEARCH_PAGES):
                if emitted >= limit:
                    break

                data, after = self._fetch_search_page(subreddit, topic, after)
                posts = self._parse_json_items(data, subreddit)

                if not posts:
                    break

                for post in posts:
                    if post.id in seen_ids:
                        continue
                    seen_ids.add(post.id)

                    if post.posted_at < since:
                        # Posts are newest-first; skip older posts
                        continue

                    yield post
                    emitted += 1
                    if emitted >= limit:
                        break

                if after is None:
                    break  # No more pages

        self._log.info("reddit_old.search.done", emitted=emitted)

    def fetch_thread(self, thread_id: str) -> Any:
        """Fetch a Reddit thread with comments via JSON API."""
        url = f"{BASE_URL}/comments/{thread_id}.json"
        return self._client.get_json(url)

    def close(self) -> None:
        self._client.close()
        self._robots.close()

    # ------------------------------------------------------------------
    # Internal — JSON API
    # ------------------------------------------------------------------

    def _fetch_search_page(
        self, subreddit: str, topic: str, after: str | None = None
    ) -> tuple[dict, str | None]:
        """Fetch one page of search results from Reddit JSON API.

        Returns (response_data_dict, next_after_token).
        """
        encoded = quote(topic)
        url = (
            f"{BASE_URL}/r/{subreddit}/search.json"
            f"?q={encoded}&restrict_sr=on&sort=new&limit=25"
        )
        if after:
            url += f"&after={after}"

        resp = self._client.get_json(url)
        data = resp.get("data", {})
        next_after = data.get("after")
        return data, next_after

    def _parse_json_items(
        self, data: dict, subreddit: str
    ) -> list[RawPost]:
        """Thin instance wrapper around the module-level parser."""
        return parse_reddit_search_json(
            data,
            subreddit=subreddit,
            region=self.region,
            language=self.language,
        )

    def _json_item_to_post(
        self, item: dict, subreddit: str
    ) -> RawPost | None:
        """Thin instance wrapper around the module-level item parser."""
        return parse_reddit_json_item(
            item,
            subreddit=subreddit,
            region=self.region,
            language=self.language,
        )


# ---------------------------------------------------------------------------
# Module-level parsers — testable offline against the saved JSON fixture.
# ---------------------------------------------------------------------------


def parse_reddit_search_json(
    data: dict,
    *,
    subreddit: str,
    region: str,
    language: str | None = None,
) -> list[RawPost]:
    """Parse a Reddit ``/r/<sub>/search.json`` response → list of RawPost.

    Filters to ``t3`` kinds (link/post); drops ``t1`` (comments), ``t5``
    (subreddits), and any malformed entries. Reddit's JSON shape is stable
    across the public API and old.reddit.com — same parser handles both.
    """
    if language is None:
        language = _REGION_LANGUAGE.get(region, "en")
    results: list[RawPost] = []
    for child in data.get("children", []) or []:
        if child.get("kind") != "t3":
            continue
        item = child.get("data") or {}
        post = parse_reddit_json_item(
            item, subreddit=subreddit, region=region, language=language,
        )
        if post is not None:
            results.append(post)
    return results


def parse_reddit_json_item(
    item: dict,
    *,
    subreddit: str,
    region: str,
    language: str | None = None,
) -> RawPost | None:
    """Convert one Reddit ``t3`` JSON item into a RawPost.

    Returns ``None`` on missing/malformed items (no id, no parseable
    timestamp, etc.) — caller decides whether to skip or surface.
    """
    if language is None:
        language = _REGION_LANGUAGE.get(region, "en")
    try:
        post_id = item.get("id", "")
        if not post_id:
            return None

        title = item.get("title", "") or ""
        selftext = item.get("selftext", "") or ""
        body = f"{title}\n\n{selftext}" if selftext else title

        author_raw = item.get("author", "[deleted]")
        author_hash_val = hash_author(author_raw)

        created_utc = item.get("created_utc", 0)
        posted_at = (
            datetime.fromtimestamp(created_utc, tz=timezone.utc)
            if created_utc
            else datetime.now(timezone.utc)
        )

        score = item.get("score", 0)
        num_comments = item.get("num_comments", 0)
        link_flair = item.get("link_flair_text") or None

        permalink = item.get("permalink", "")
        reddit_url = (
            f"https://old.reddit.com{permalink}"
            if permalink
            else f"https://old.reddit.com/r/{subreddit}/comments/{post_id}/"
        )

        full_text = body
        lang = detect_language(full_text)

        is_self = item.get("is_self", False)
        domain = item.get("domain", "")
        external_url = item.get("url", "") if not is_self else None

        return RawPost(
            id=f"reddit_{subreddit}_{post_id}",
            source="reddit_old",
            source_category=SourceCategory.FORUMS,
            region=region,
            language=language,
            language_detected=lang,
            url=reddit_url,
            author_hash=author_hash_val,
            title=title,
            body=body,
            posted_at=posted_at,
            signal_type=SignalType.OPINION,
            engagement_metrics={
                "score": int(score) if score else 0,
                "comments": int(num_comments) if num_comments else 0,
            },
            replies=[],
            raw_metadata={
                "subreddit": subreddit,
                "post_id": post_id,
                "external_url": external_url,
                "flair": link_flair,
                "is_self": is_self,
                "domain": domain,
                "nsfw": item.get("over_18", False),
            },
        )
    except Exception:
        return None


# ---------------------------------------------------------------------------
# scrape-doctor check — invoked against the saved JSON fixture.
# ---------------------------------------------------------------------------


def doctor_check(name: str, html: str, meta: dict) -> tuple[bool, str]:
    """Doctor hook: parse the saved Reddit JSON response and assert shape."""
    import json
    try:
        payload = json.loads(html)
    except json.JSONDecodeError as e:
        return False, f"reddit_old fixture is not valid JSON: {e}"
    data = payload.get("data") or payload
    subreddit = (meta or {}).get("subreddit") or "HongKong"
    region = (meta or {}).get("region") or "HK"
    posts = parse_reddit_search_json(data, subreddit=subreddit, region=region)
    if not posts:
        return False, "parse_reddit_search_json returned 0 posts (t3 kind missing or all malformed)"
    return True, f"parse_reddit_search_json OK ({len(posts)} posts, top score={posts[0].engagement_metrics.get('score', 0)})"
