"""Polite HTTP client — httpx with rate limiting, retries, and an honest UA.

Every scraper that fetches HTML or JSON over HTTP should use this client
instead of raw ``httpx``.  It enforces:

- Honest User-Agent header (``MarketAnalyticsBot/0.1``)
- Per-domain rate limiting (default 2 req/s, configurable)
- Exponential backoff on ``429`` / ``5xx`` (1 s → 16 s, 4 attempts)
- Hard-fail on ``403`` (don't hammer a site that says no)
- robots.txt check before first request to each host
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx
import structlog
from tenacity import (
    RetryError,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from src.scrape.base.robots import RobotsCache

USER_AGENT = (
    "MarketAnalyticsBot/0.1 (research; contact: see README.md)"
)

# Per-domain minimum interval between requests, in seconds.
DEFAULT_RATE = 0.5  # 2 req/s per domain


class ForbiddenError(Exception):
    """HTTP 403 — the server is actively denying us. Do not retry."""


class RateLimitError(Exception):
    """HTTP 429 — we're being rate-limited. Retry with backoff."""


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, RateLimitError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {429} or 500 <= exc.response.status_code < 600
    if isinstance(exc, (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError)):
        return True
    return False


def _raise_forbidden(response: httpx.Response) -> None:
    if response.status_code == 403:
        raise ForbiddenError(
            f"HTTP 403 from {response.url} — server is refusing access"
        )


@dataclass
class PoliteClient:
    """httpx wrapper with polite-scraping defaults.

    Parameters
    ----------
    robots_cache:
        Shared ``RobotsCache``.  Pass the same instance to all scrapers in a
        run so robots.txt is fetched once per host.
    rate:
        Minimum interval between requests to the same domain, in seconds.
        Default 0.5 (2 req/s).
    """

    robots_cache: RobotsCache
    rate: float = DEFAULT_RATE

    _client: httpx.Client | None = None
    _last_request: dict[str, float] | None = None  # domain → epoch float

    def __post_init__(self) -> None:
        self._client = httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
        )
        self._last_request = {}
        self._log = structlog.get_logger().bind(client="PoliteClient")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        """GET *url*, respecting robots.txt and rate limits."""
        self._check_robots(url)
        self._wait_rate_limit(url)
        return self._do_request("GET", url, **kwargs)

    def get_json(self, url: str, **kwargs: Any) -> Any:
        """GET *url* and parse as JSON.  Raises if non-2xx or invalid JSON."""
        resp = self.get(url, **kwargs)
        resp.raise_for_status()
        return resp.json()

    def get_html(self, url: str, **kwargs: Any) -> str:
        """GET *url* and return the response text (HTML)."""
        resp = self.get(url, **kwargs)
        resp.raise_for_status()
        return resp.text

    def robots_allowed(self, url: str) -> bool:
        """Check robots.txt for *url*.  Cached per host."""
        return self.robots_cache.allowed(url, USER_AGENT)

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _check_robots(self, url: str) -> None:
        if not self.robots_allowed(url):
            raise ForbiddenError(
                f"robots.txt disallows {url} — skipping"
            )

    def _wait_rate_limit(self, url: str) -> None:
        import urllib.parse
        host = urllib.parse.urlparse(url).hostname or url
        now = time.monotonic()
        last = self._last_request.get(host, 0.0)  # type: ignore[union-attr]
        wait = self.rate - (now - last)
        if wait > 0:
            time.sleep(wait)
        self._last_request[host] = time.monotonic()  # type: ignore[index]

    def _do_request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        @retry(
            retry=retry_if_exception(_is_retryable),
            stop=stop_after_attempt(4),
            wait=wait_exponential(multiplier=1, min=1, max=16),
            reraise=True,
        )
        def _inner() -> httpx.Response:
            assert self._client is not None
            resp = self._client.request(method, url, **kwargs)
            _raise_forbidden(resp)
            if resp.status_code == 429:
                raise RateLimitError(f"HTTP 429 from {url}")
            resp.raise_for_status()
            return resp

        try:
            return _inner()
        except RetryError as e:
            cause = e.__cause__ or e
            raise SourceError(f"Request failed after retries: {url}") from cause


# Re-import at bottom to avoid circular dependency with protocol.py.
# protocol.py imports schemas — it doesn't import http.py.
from src.scrape.base.protocol import SourceError  # noqa: E402
