"""Playwright session manager with stealth defaults.

Only used by scrapers that need JS-rendered HTML (Openrice, Quora, Threads,
etc.).  Not loaded unless the scraper explicitly calls ``get_page()`` — so
``httpx``-only scrapers don't pay the ~300 MB Playwright binary cost.

Stealth defaults:
- Random viewport from a pre-defined set
- Realistic ``Accept-Language`` and ``Accept`` headers
- Honest User-Agent (same as ``PoliteClient``)
"""

from __future__ import annotations

import random
import time
from contextlib import contextmanager
from typing import Any

import structlog

from src.scrape.base.robots import RobotsCache

USER_AGENT = "MarketAnalyticsBot/0.1 (research; contact: see README.md)"

VIEWPORTS: list[dict[str, int]] = [
    {"width": 1440, "height": 900},
    {"width": 1366, "height": 768},
    {"width": 1536, "height": 864},
    {"width": 1280, "height": 720},
    {"width": 1680, "height": 1050},
]

_LANG_HEADER = (
    "zh-HK,zh;q=0.9,en;q=0.8,zh-TW;q=0.7,ja;q=0.6"
)
_ACCEPT_HEADER = (
    "text/html,application/xhtml+xml,application/xml;q=0.9,"
    "image/webp,*/*;q=0.8"
)


class PlaywrightManager:
    """Manage a Playwright browser session for a single scrape run.

    Parameters
    ----------
    robots_cache:
        Shared ``RobotsCache``.  robots.txt is checked lazily before the
        first navigation to each host.
    rate:
        Minimum interval between page navigations to the same domain, in
        seconds.  Default 1.0 (1 req/s — Playwright is slower than httpx).
    headless:
        Run browser in headless mode (default ``True``).
    """

    def __init__(
        self,
        robots_cache: RobotsCache,
        rate: float = 1.0,
        headless: bool = True,
    ) -> None:
        self._robots_cache = robots_cache
        self._rate = rate
        self._headless = headless
        self._log = structlog.get_logger().bind(component="PlaywrightManager")

        # Lazily initialised
        self._playwright: Any = None
        self._browser: Any = None
        self._last_nav: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @contextmanager
    def get_page(self, url: str):
        """Context manager yielding a Playwright page for *url*.

        Example::

            with pw.get_page("https://example.com") as page:
                page.goto("https://example.com/search?q=foo")
                html = page.content()
        """
        self._ensure_browser()
        self._check_robots(url)
        self._wait_rate_limit(url)

        context = None
        page = None
        try:
            vp = random.choice(VIEWPORTS)
            context = self._browser.new_context(
                viewport=vp,
                user_agent=USER_AGENT,
                locale="zh-HK",
            )
            context.set_extra_http_headers({
                "Accept-Language": _LANG_HEADER,
                "Accept": _ACCEPT_HEADER,
            })
            page = context.new_page()
            yield page
        finally:
            if page:
                try:
                    page.close()
                except Exception:
                    pass
            if context:
                try:
                    context.close()
                except Exception:
                    pass

    def close(self) -> None:
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass
        self._browser = None
        self._playwright = None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _ensure_browser(self) -> None:
        if self._browser is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise SourceError(
                "Playwright is not installed. Install with: "
                "pip install playwright && playwright install chromium"
            ) from None

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=self._headless,
        )
        self._log.info("playwright.browser_started")

    def _check_robots(self, url: str) -> None:
        if not self._robots_cache.allowed(url, USER_AGENT):
            raise ForbiddenError(
                f"robots.txt disallows {url} — skipping"
            )

    def _wait_rate_limit(self, url: str) -> None:
        import urllib.parse
        host = urllib.parse.urlparse(url).hostname or url
        now = time.monotonic()
        last = self._last_nav.get(host, 0.0)
        wait = self._rate - (now - last)
        if wait > 0:
            time.sleep(wait)
        self._last_nav[host] = time.monotonic()


# Late imports to avoid circular deps
from src.scrape.base.http import ForbiddenError  # noqa: E402
from src.scrape.base.protocol import SourceError  # noqa: E402
