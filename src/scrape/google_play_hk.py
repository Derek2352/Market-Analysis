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
from src.scrape.utils.hashing import hash_author
from src.scrape.utils.lang import detect_language

_log = structlog.get_logger(__name__)


class GooglePlayHKScraper:
    """Scrape Google Play HK app reviews via google-play-scraper."""

    source_id = "google_play_hk"
    region = "HK"
    language = "zh-HK"
    category = SourceCategory.REVIEWS
    signal_type = SignalType.EXPERIENCE

    def __init__(
        self,
        *,
        country: str = "hk",
        lang: str = "zh",
        max_apps_per_search: int = 3,
    ):
        self._country = country
        self._lang = lang
        self._max_apps = max_apps_per_search

    def close(self) -> None:
        pass  # No persistent connection

    # -- SourceScraper protocol --------------------------------------------

    def search(
        self,
        topic: str,
        since: datetime,
        limit: int,
    ) -> Iterator[RawPost]:
        """Search Google Play for apps matching *topic*, then scrape reviews."""
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
                            return  # Reviews are newest-first
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
