"""SourceScraper protocol — the contract every scraper implements.

Moved from ``src/scrape/base.py`` into ``src/scrape/base/protocol.py`` in
Phase 2.  The parent ``src/scrape/base.py`` re-exports from here so existing
imports keep working.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Protocol, runtime_checkable

from src.schemas.raw import RawPost, Thread


@runtime_checkable
class SourceScraper(Protocol):
    """Every source scraper implements this protocol.

    Contract:
      - ``search`` yields ``RawPost`` objects.  ``replies`` MAY be empty;
        every other required field MUST be set.
      - ``fetch_thread`` returns a ``Thread`` (``RawPost`` with ``replies``
        populated when the source exposes them).
      - Both methods MUST hash author identifiers with sha256 + a per-process
        salt before populating ``author_hash``.  Raw usernames MUST NOT appear
        in any returned object or in ``raw_metadata``.
      - Implementations own their rate limiting and exponential backoff.
        Transient failures retry internally; unrecoverable failures raise
        ``SourceError``.
      - Scrapers that use requesting infrastructure (``PoliteClient`` /
        ``PlaywrightManager``) MUST call ``robots_allowed(url)`` before the
        first request to a host and MUST hard-fail on ``403``.
    """

    source_id: str
    region: str
    language: str

    def search(
        self,
        topic: str,
        since: datetime,
        limit: int,
    ) -> Iterator[RawPost]: ...

    def fetch_thread(self, thread_id: str) -> Thread: ...

    # Optional — scrapers that hold resources should implement close().
    # The CLI calls it in a finally block.
    def close(self) -> None: ...


class SourceError(Exception):
    """Unrecoverable scrape failure (auth, persistent network, schema change)."""
