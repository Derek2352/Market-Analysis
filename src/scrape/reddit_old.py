"""Reddit scraper via old.reddit.com HTML — no API key required.

Uses old.reddit.com's server-rendered HTML, which exposes posts and comments
without JavaScript.  This satisfies the no-API constraint — the Reddit API
requires OAuth registration, but scraping old.reddit.com HTML is permitted
under Reddit's non-commercial scraping policy (with honest User-Agent).

Usage::

    mkt scrape --topic "MTR" --region HK --sources reddit_old \\
      --subreddits HongKong,HKentertainment --limit 500

Rate limit: 1 req / 2 s (Reddit's published guideline for non-API access).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone, timedelta
from typing import Any
from urllib.parse import quote, urljoin

import structlog
from bs4 import BeautifulSoup

from src.scrape.base import PoliteClient, RobotsCache, SourceError
from src.scrape.base.protocol import SourceScraper
from src.scrape.utils.hashing import hash_author
from src.scrape.utils.lang import detect_language
from src.schemas.enums import SignalType, SourceCategory
from src.schemas.raw import RawPost

BASE_URL = "https://old.reddit.com"
REDDIT_RATE = 2.0  # 1 req / 2 s
MAX_SEARCH_PAGES = 10
POSTS_PER_PAGE = 25  # old.reddit.com default


class RedditOldScraper:
    """Scrape Reddit posts via old.reddit.com HTML."""

    source_id = "reddit_old"
    region = "HK"
    language = "en"

    def __init__(
        self,
        *,
        subreddits: list[str] | None = None,
        rate: float = REDDIT_RATE,
    ) -> None:
        self._log = structlog.get_logger().bind(scraper="reddit_old")
        self._robots = RobotsCache()
        self._client = PoliteClient(robots_cache=self._robots, rate=rate)
        self._subreddits = subreddits or ["HongKong"]

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

            for page in range(1, MAX_SEARCH_PAGES + 1):
                if emitted >= limit:
                    break

                posts = self._search_subreddit(subreddit, topic, page)
                if not posts:
                    break

                for post in posts:
                    if post.id in seen_ids:
                        continue
                    seen_ids.add(post.id)

                    if post.posted_at < since:
                        # Posts are newest-first; once we hit the cutoff,
                        # remaining posts are older.
                        continue

                    yield post
                    emitted += 1
                    if emitted >= limit:
                        break

        self._log.info("reddit_old.search.done", emitted=emitted)

    def fetch_thread(self, thread_id: str) -> Any:
        """Fetch a Reddit thread with comments."""
        raise NotImplementedError("Thread fetching not yet implemented")

    def close(self) -> None:
        self._client.close()
        self._robots.close()

    # ------------------------------------------------------------------
    # Internal — search
    # ------------------------------------------------------------------

    def _search_subreddit(
        self, subreddit: str, topic: str, page: int
    ) -> list[RawPost]:
        """Search a single subreddit for *topic*, returning page *page*."""
        encoded = quote(topic)
        # Use Reddit's search on old.reddit.com
        # restrict_sr=on limits to the subreddit, sort=new for recency
        url = (
            f"{BASE_URL}/r/{subreddit}/search"
            f"?q={encoded}&restrict_sr=on&sort=new"
        )

        # After the first page, add the 'after' parameter
        if page > 1:
            # We'd need to track the 'after' token from the previous page.
            # For now, paginate via count parameter (old.reddit style).
            url += f"&count={(page - 1) * POSTS_PER_PAGE}"

        html = self._client.get_html(url)
        return self._parse_search_results(html, subreddit)

    def _parse_search_results(
        self, html: str, subreddit: str
    ) -> list[RawPost]:
        """Parse old.reddit.com search results HTML → RawPost list."""
        soup = BeautifulSoup(html, "html.parser")
        results: list[RawPost] = []

        # Old Reddit wraps each post in a div with id starting "thing_t3_"
        for thing in soup.find_all("div", class_="thing"):
            thing_id = thing.get("id", "")
            if not thing_id.startswith("thing_t3_"):
                continue  # Skip comments, ads, etc.

            post_id = thing_id.replace("thing_t3_", "")
            post = self._parse_thing(thing, post_id, subreddit)
            if post:
                results.append(post)

        return results

    def _parse_thing(
        self, thing: Any, post_id: str, subreddit: str
    ) -> RawPost | None:
        """Parse a single 'thing' div → RawPost."""
        try:
            # Title + URL
            title_el = thing.find("a", class_="title")
            if not title_el:
                return None
            title = title_el.get_text(strip=True)
            post_url = title_el.get("href", "")

            # Author
            author_el = thing.find("a", class_="author")
            author_raw = author_el.get_text(strip=True) if author_el else "[deleted]"

            # Score
            score_el = thing.find("div", class_="score")
            score_text = score_el.get_text(strip=True) if score_el else "0"
            try:
                score = int(score_text)
            except (ValueError, TypeError):
                score = 0

            # Comment count
            comments_el = thing.find("a", class_="comments")
            comments_text = comments_el.get_text(strip=True) if comments_el else ""
            comment_count = 0
            if comments_text and comments_text != "comment":
                try:
                    comment_count = int(comments_text.split()[0])
                except (ValueError, IndexError):
                    pass

            # Timestamp
            time_el = thing.find("time")
            posted_at = datetime.now(timezone.utc)
            if time_el and time_el.get("datetime"):
                try:
                    posted_at = datetime.fromisoformat(
                        time_el["datetime"].replace("Z", "+00:00")
                    )
                except (ValueError, Exception):
                    pass

            # Self-text (for text posts)
            selftext_el = thing.find("div", class_="usertext-body")
            selftext = ""
            if selftext_el:
                md_el = selftext_el.find("div", class_="md")
                if md_el:
                    selftext = md_el.get_text(strip=True)

            # Body = title + optional selftext
            body = f"{title}\n\n{selftext}" if selftext else title

            # Flair / link flair
            flair_el = thing.find("span", class_="linkflairlabel")
            flair = flair_el.get_text(strip=True) if flair_el else None

            # Discussion URL on old.reddit
            reddit_url = urljoin(BASE_URL, f"/r/{subreddit}/comments/{post_id}/")

            full_text = body
            lang = detect_language(full_text)
            author_hash = hash_author(author_raw)

            return RawPost(
                id=f"reddit_{post_id}",
                source="reddit_old",
                source_category=SourceCategory.FORUMS,
                region="HK",
                language="en",
                language_detected=lang,
                url=reddit_url,
                author_hash=author_hash,
                title=title,
                body=body,
                posted_at=posted_at,
                signal_type=SignalType.OPINION,
                engagement_metrics={
                    "score": score,
                    "comments": comment_count,
                },
                replies=[],
                raw_metadata={
                    "subreddit": subreddit,
                    "post_id": post_id,
                    "external_url": post_url,
                    "flair": flair,
                    "is_self": "self" in thing.get("class", []),
                },
            )
        except Exception as e:
            self._log.warning("reddit_old.parse_failed", error=str(e))
            return None
