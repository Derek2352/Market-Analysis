from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Any

import httpx
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.schemas.enums import SignalType, SourceCategory
from src.schemas.raw import RawPost
from src.scrape.base import SourceError
from src.scrape.utils.hashing import hash_author
from src.scrape.utils.lang import detect_language

_log = structlog.get_logger(__name__)

SEARCH_URL = "https://itunes.apple.com/search"
REVIEWS_URL_TPL = (
    "https://itunes.apple.com/{country}/rss/customerreviews/page={page}"
    "/id={app_id}/sortby=mostrecent/json"
)

# iTunes RSS hard-caps the customer-reviews feed at 10 pages × ~50 reviews per
# app per country. Older reviews exist but are not exposed. We surface a
# `cap_hit: true` signal in the run sidecar when this is reached.
MAX_PAGES = 10
PAGE_SIZE = 50

# Retried on these — transient network or server-side issues. 4xx other than
# 429 raise SourceError without retry.
_TRANSIENT = (httpx.RequestError, httpx.HTTPStatusError)


class AppStoreHKScraper:
    """App Store customer reviews via the public iTunes RSS feed.

    Region-aware: pass ``region`` to set metadata, ``country`` for storefront,
    ``lang`` for RSS language parameter. No auth required.
    """

    source_id = "app_store_hk"

    def __init__(
        self,
        *,
        region: str = "HK",
        country: str = "hk",
        lang: str = "zh-Hant",
        client: httpx.Client | None = None,
        max_apps_per_search: int = 3,
        request_timeout: float = 20.0,
    ):
        self.region = region
        self.language = {"HK": "zh-HK", "TW": "zh-TW", "US": "en", "JP": "ja"}.get(region, "en")
        self.category = SourceCategory.REVIEWS
        self.signal_type = SignalType.EXPERIENCE
        self._country = country
        self._lang = lang
        self._max_apps = max_apps_per_search
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=request_timeout,
            headers={"User-Agent": "MarketAnalyticsTool/0.1 (phase1)"},
        )
        # Surfaced to the CLI so it can record cap-hit in the run sidecar.
        self.cap_hit_apps: list[str] = []

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "AppStoreHKScraper":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- SourceScraper protocol --------------------------------------------

    def search(
        self,
        topic: str,
        since: datetime,
        limit: int,
    ) -> Iterator[RawPost]:
        if topic.strip().isdigit():
            app_ids = [topic.strip()]
        else:
            app_ids = self._search_app_ids(topic)
        if not app_ids:
            _log.warning("app_store_hk.search.no_apps_found", topic=topic)
            return

        emitted = 0
        for app_id in app_ids:
            if emitted >= limit:
                break
            for post in self._reviews_for_app(app_id, since=since):
                yield post
                emitted += 1
                if emitted >= limit:
                    break

    def fetch_thread(self, thread_id: str) -> RawPost:
        """App Store reviews are flat; thread_id is '{app_id}:{review_id}'."""
        if ":" not in thread_id:
            raise SourceError(
                "app_store_hk thread_id must be '{app_id}:{review_id}'"
            )
        app_id, review_id = thread_id.split(":", 1)
        for post in self._reviews_for_app(app_id, since=None):
            if post.id == review_id:
                return post
        raise SourceError(f"review {review_id} not found for app {app_id}")

    # -- internals ---------------------------------------------------------

    def _search_app_ids(self, topic: str) -> list[str]:
        data = self._get_json(
            SEARCH_URL,
            params={
                "term": topic,
                "country": self._country,
                "entity": "software",
                "limit": self._max_apps,
            },
        )
        ids = [
            str(r["trackId"]) for r in data.get("results", []) if "trackId" in r
        ]
        _log.info(
            "app_store_hk.search.results",
            topic=topic,
            country=self._country,
            app_count=len(ids),
            app_ids=ids,
        )
        return ids

    def _reviews_for_app(
        self,
        app_id: str,
        since: datetime | None,
    ) -> Iterator[RawPost]:
        pages_fetched = 0
        for page in range(1, MAX_PAGES + 1):
            url = REVIEWS_URL_TPL.format(
                country=self._country, page=page, app_id=app_id
            )
            try:
                data = self._get_json(url, params={"l": self._lang})
            except SourceError as e:
                _log.warning(
                    "app_store_hk.page_failed",
                    app_id=app_id,
                    page=page,
                    error=str(e),
                )
                break

            entries = data.get("feed", {}).get("entry") or []
            # On page 1, iTunes prepends an app-metadata entry that lacks an
            # `author` field — skip it.
            if page == 1 and entries and "author" not in entries[0]:
                entries = entries[1:]
            if not entries:
                _log.info(
                    "app_store_hk.empty_page", app_id=app_id, page=page
                )
                break

            pages_fetched += 1
            for entry in entries:
                post = self._entry_to_post(entry, app_id=app_id)
                if post is None:
                    continue
                if since is not None and post.posted_at < since:
                    # RSS sorts newest-first, so an older post means we're done.
                    _log.info(
                        "app_store_hk.since_cutoff_reached",
                        app_id=app_id,
                        page=page,
                    )
                    return
                yield post

        if pages_fetched == MAX_PAGES:
            self.cap_hit_apps.append(app_id)
            _log.warning(
                "app_store_hk.cap_hit",
                app_id=app_id,
                pages_fetched=MAX_PAGES,
                approx_max_reviews=MAX_PAGES * PAGE_SIZE,
                note=(
                    "iTunes RSS hard cap reached; older reviews exist but are "
                    "not exposed. Supplement from another source if you need "
                    "deeper history."
                ),
            )

    def _entry_to_post(
        self,
        entry: dict[str, Any],
        *,
        app_id: str,
    ) -> RawPost | None:
        try:
            review_id = entry["id"]["label"]
            author_name = (
                entry.get("author", {}).get("name", {}).get("label", "")
            )
            title = entry.get("title", {}).get("label") or None
            body = entry.get("content", {}).get("label", "")
            posted_at_str = entry["updated"]["label"]
            rating = int(entry.get("im:rating", {}).get("label", 0))
            version = entry.get("im:version", {}).get("label")
            vote_sum = int(entry.get("im:voteSum", {}).get("label", 0))
            vote_count = int(entry.get("im:voteCount", {}).get("label", 0))
        except (KeyError, TypeError, ValueError) as e:
            _log.warning(
                "app_store_hk.entry_parse_failed",
                app_id=app_id,
                error=str(e),
            )
            return None

        try:
            posted_at = datetime.fromisoformat(posted_at_str)
        except ValueError:
            _log.warning("app_store_hk.bad_timestamp", value=posted_at_str)
            return None

        full_text = f"{title}\n\n{body}" if title else body
        return RawPost(
            id=review_id,
            source=self.source_id,
            source_category=self.category,
            region=self.region,
            language=self.language,
            language_detected=detect_language(full_text),
            url=f"https://apps.apple.com/{self._country}/app/id{app_id}",
            author_hash=hash_author(author_name),
            title=title,
            body=body,
            posted_at=posted_at,
            signal_type=self.signal_type,
            engagement_metrics={
                "rating": rating,
                "vote_sum": vote_sum,
                "vote_count": vote_count,
            },
            replies=[],
            raw_metadata={
                "app_id": app_id,
                "version": version,
                "country": self._country,
                "feed_lang": self._lang,
            },
        )

    @retry(
        retry=retry_if_exception_type(_TRANSIENT),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=16),
        reraise=True,
    )
    def _get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        r = self._client.get(url, params=params)
        # 429 and 5xx → let tenacity retry via HTTPStatusError.
        if r.status_code == 429 or r.status_code >= 500:
            raise httpx.HTTPStatusError(
                f"{r.status_code} from {url}",
                request=r.request,
                response=r,
            )
        # Other 4xx → unrecoverable.
        if 400 <= r.status_code < 500:
            raise SourceError(f"GET {url} returned {r.status_code}")
        try:
            return r.json()
        except ValueError as e:
            raise SourceError(f"non-JSON response from {url}") from e
