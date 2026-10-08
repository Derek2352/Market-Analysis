"""Google Play HK scraper — app reviews via google-play-scraper library.

Uses the google-play-scraper Python library which wraps the Google Play
Store's internal API. No auth required. Returns app reviews as RawPost
records with rating, author, date, and content.

Usage::

    mkt scrape --topic "MTR Mobile" --region HK --sources google_play_hk
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone

import structlog

from src.schemas.enums import SignalType, SourceCategory
from src.schemas.raw import RawPost
from src.scrape.base.http import ForbiddenError
from src.scrape.base.robots import USER_AGENT, RobotsCache
from src.scrape.utils.hashing import hash_author
from src.scrape.utils.lang import detect_language

_log = structlog.get_logger(__name__)


class GooglePlayHKScraper:
    """Scrape Google Play HK app reviews via google-play-scraper.

    Region-aware: pass ``region``, ``country``, ``lang`` to override defaults.
    """

    source_id = "google_play_hk"

    def __init__(
        self,
        *,
        region: str = "HK",
        country: str = "hk",
        lang: str = "zh",
        max_apps_per_search: int = 3,
        robots_cache: RobotsCache | None = None,
    ):
        self.region = region
        self.language = {"HK": "zh-HK", "TW": "zh-TW", "US": "en", "JP": "ja"}.get(region, "en")
        self.category = SourceCategory.REVIEWS
        self.signal_type = SignalType.EXPERIENCE
        self._country = country
        self._lang = lang
        self._max_apps = max_apps_per_search
        self._owns_robots = robots_cache is None
        self._robots = robots_cache or RobotsCache()

    def close(self) -> None:
        if self._owns_robots:
            self._robots.close()

    # The google-play-scraper library makes its own urllib requests (no
    # honest User-Agent, no rate limit, and it disables urllib TLS
    # verification process-wide on import). It is only acceptable if robots.txt
    # allows the endpoints it hits, so check those first — before importing it.
    _LIBRARY_ENDPOINTS = (
        "https://play.google.com/_/PlayStoreUi/data/batchexecute",  # reviews()
        "https://play.google.com/store/search?q=x&c=apps",          # search()
    )

    def _check_robots(self, uses_search: bool) -> None:
        endpoints = self._LIBRARY_ENDPOINTS if uses_search else self._LIBRARY_ENDPOINTS[:1]
        for url in endpoints:
            if not self._robots.allowed(url, USER_AGENT):
                raise ForbiddenError(
                    self._robots.denial_reason(url)
                    + " (google_play_hk fetches reviews through this endpoint)"
                )

    # -- SourceScraper protocol --------------------------------------------

    def search(
        self,
        topic: str,
        since: datetime,
        limit: int,
    ) -> Iterator[RawPost]:
        """Search Google Play for apps matching *topic*, then scrape reviews."""
        is_app_id = "." in topic and "/" not in topic
        self._check_robots(uses_search=not is_app_id)
        import google_play_scraper as gps

        # If topic looks like an app ID (package name), use it directly
        if "." in topic and "/" not in topic:
            app_ids = [topic]
        else:
            try:
                results = gps.search(
                    topic,
                    lang=self._lang,
                    country=self._country,
                    n_hits=self._max_apps * 2,
                )
                app_ids = [
                    r["appId"]
                    for r in results
                    if r.get("appId")  # Skip results with None appId
                ][:self._max_apps]
            except Exception:
                _log.warning(
                    "google_play_hk.search_failed", topic=topic, exc_info=True
                )
                return

        if not app_ids:
            _log.warning("google_play_hk.no_apps_found", topic=topic)
            return

        _log.info(
            "google_play_hk.search.results",
            topic=topic,
            country=self._country,
            app_count=len(app_ids),
            app_ids=app_ids,
        )

        emitted = 0
        for app_id in app_ids:
            if emitted >= limit:
                break

            try:
                # Fetch reviews with continuation token
                continuation_token = None
                while emitted < limit:
                    batch, continuation_token = gps.reviews(
                        app_id,
                        # Explicit NEWEST — the default is MOST_RELEVANT, which
                        # is not chronological, so the since-cutoff below would
                        # abort on an early old-but-relevant review (0 posts).
                        sort=gps.Sort.NEWEST,
                        lang=self._lang,
                        country=self._country,
                        count=min(100, limit - emitted),
                        continuation_token=continuation_token,
                    )

                    if not batch:
                        break

                    for review in batch:
                        post = self._review_to_post(review, app_id)
                        if post is None:
                            continue
                        if post.posted_at < since:
                            # Skip, don't abort — guards against any ordering
                            # surprise so one old review can't zero the source.
                            continue
                        yield post
                        emitted += 1
                        if emitted >= limit:
                            break

                    if continuation_token is None:
                        break

            except Exception:
                _log.warning(
                    "google_play_hk.app_failed",
                    app_id=app_id,
                    exc_info=True,
                )
                continue

    def fetch_thread(self, thread_id: str) -> RawPost:
        """Google Play reviews are flat; thread_id is review_id."""
        raise NotImplementedError("Google Play reviews are flat — use search()")

    # -- internals ---------------------------------------------------------

    def _review_to_post(
        self, review: dict, app_id: str
    ) -> RawPost | None:
        """Convert a google-play-scraper review dict → RawPost."""
        try:
            review_id = review.get("reviewId", "")
            if not review_id:
                return None

            author_name = review.get("userName", "") or "anonymous"
            content = review.get("content", "") or ""
            score = review.get("score", 0)
            thumbs_up = review.get("thumbsUpCount", 0)
            reply_content = review.get("replyContent")

            # Timestamp
            posted_at_str = review.get("at")
            if posted_at_str:
                try:
                    posted_at = datetime.strptime(
                        str(posted_at_str), "%Y-%m-%d %H:%M:%S"
                    ).replace(tzinfo=timezone.utc)
                except (ValueError, TypeError):
                    posted_at = datetime.now(timezone.utc)
            else:
                posted_at = datetime.now(timezone.utc)

            # Language detection
            lang = detect_language(content)

            # Title = first sentence of review
            title = None
            if content:
                sentences = content.split("。")
                if sentences:
                    first = sentences[0].strip()
                    if len(first) < 100:
                        title = first

            # Append reply content to body if available
            body = content
            if reply_content:
                body += f"\n\n[Developer Reply]\n{reply_content}"

            return RawPost(
                id=f"gp_{review_id}",
                source="google_play_hk",
                source_category=self.category,
                region=self.region,
                language=self.language,
                language_detected=lang,
                url=f"https://play.google.com/store/apps/details?id={app_id}",
                author_hash=hash_author(author_name),
                title=title,
                body=body,
                posted_at=posted_at,
                signal_type=self.signal_type,
                engagement_metrics={
                    "rating": score,
                    "thumbs_up": thumbs_up,
                },
                replies=[],
                raw_metadata={
                    "app_id": app_id,
                    "review_id": review_id,
                    "country": self._country,
                    "has_reply": reply_content is not None,
                },
            )
        except Exception as e:
            _log.warning(
                "google_play_hk.parse_failed",
                app_id=app_id,
                error=str(e),
            )
            return None
