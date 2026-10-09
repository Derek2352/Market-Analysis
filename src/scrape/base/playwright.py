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

import os
import random
import time
import urllib.parse
from contextlib import contextmanager
from typing import Any

import structlog

from src.scrape.base.robots import USER_AGENT, RobotsCache

# Same env override the render layer honours (src/render/core.py). Lets a
# container point at an extracted / system Chromium when the Playwright-pinned
# browser build isn't installed, instead of hard-failing at launch.
_CHROME_ENV = "PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH"

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
        respect_robots: bool = True,
        block_resource_types: tuple[str, ...] = (),
        allowed_hosts: tuple[str, ...] = (),
    ) -> None:
        self._robots_cache = robots_cache
        self._rate = rate
        self._headless = headless
        self._respect_robots = respect_robots
        # Lighter, politer page loads: skip resource types the scraper never
        # reads (e.g. images) and, when allowed_hosts is set, every request to
        # other hosts (ads, analytics beacons). Matched by host suffix.
        self._block_types = frozenset(block_resource_types)
        self._allowed_hosts = tuple(h.lower() for h in allowed_hosts)
        # Each page's latest main-frame document response. A page's JavaScript
        # (e.g. a bot-check interstitial) can reload it after goto() returns,
        # so goto()'s own response isn't the whole story.
        self._nav_response: dict[int, Any] = {}
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
            if self._block_types or self._allowed_hosts:
                context.route("**/*", self._route)
            page = context.new_page()
            page.on("response", lambda response, p=page: self._record_navigation(p, response))
            yield page
        finally:
            if page:
                self._nav_response.pop(id(page), None)
                try:
                    page.close()
                except Exception:
                    pass
            if context:
                try:
                    context.close()
                except Exception:
                    pass

    def navigate(self, page: Any, url: str, **goto_kwargs: Any) -> Any:
        """``page.goto(url)`` with the same rules as ``PoliteClient.get``.

        Use this for every navigation after the first, so robots.txt and the
        per-host rate limit apply to each URL a reused page visits, not just
        the URL ``get_page`` was opened with. A 403 hard-fails as
        ``ForbiddenError``; a 429 stops the source instead of retrying.
        """
        self._check_robots(url)
        self._wait_rate_limit(url)
        self._nav_response.pop(id(page), None)
        try:
            response = page.goto(url, **goto_kwargs)
        except Exception as e:  # noqa: BLE001 — re-raised with a clearer message
            # An error status with an empty body makes Chromium abort with
            # net::ERR_HTTP_RESPONSE_CODE_FAILURE; the recorded status still
            # says whether that was a 403/429 the source must stop on.
            self._raise_for_status(self._last_status(page), url)
            if "ERR_CERT_AUTHORITY_INVALID" in str(e):
                raise SourceError(
                    f"Chromium does not trust the TLS certificate for {url}. "
                    "Behind a TLS-inspecting proxy, add the proxy's CA to "
                    "Chromium's trust store (Linux: certutil -d "
                    "sql:$HOME/.pki/nssdb -A -t C,, -n proxy-ca -i <ca.crt>; "
                    "Windows/macOS: the system certificate store). TLS "
                    "verification is never disabled."
                ) from e
            raise
        self._raise_for_status(getattr(response, "status", None), url)
        return response

    def ensure_ok(self, page: Any, url: str) -> None:
        """Re-check the status once the page has settled.

        Call after waiting for content: a JavaScript interstitial can reload
        the page after ``navigate`` returned, and a 403/429 served on that
        reload must stop the source rather than parse as an empty page.
        """
        self._raise_for_status(self._last_status(page), url)

    def document_html(self, page: Any) -> str | None:
        """The HTML the server sent for the page's latest document load.

        Unlike ``page.content()`` (the live DOM), this doesn't change as the
        page's own scripts run — e.g. a client-side component that shortens
        text after hydration. None if no body is available.
        """
        response = self._nav_response.get(id(page))
        if response is None:
            return None
        try:
            return response.text()
        except Exception:  # noqa: BLE001 — body gone (redirect, navigated away)
            return None

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
    # Lazy-load helper (used by YouTube comments, Quora answers)
    # ------------------------------------------------------------------

    @staticmethod
    def scroll_until_stable(
        page: Any,
        *,
        max_scrolls: int = 10,
        settle_ms: int = 1500,
        scroll_step_px: int = 2000,
    ) -> int:
        """Scroll the page until the document height stops growing.

        Used for infinite-scroll lists (YouTube comments, Quora answer
        sections). Returns the number of effective scrolls performed.

        Parameters
        ----------
        page:
            A Playwright sync ``Page`` instance.
        max_scrolls:
            Hard cap on scroll attempts — bounds the run time even if a
            page never settles.
        settle_ms:
            After each scroll, wait this long for new content to render
            before measuring the height again.
        scroll_step_px:
            Pixels to scroll per attempt. Mostly cosmetic — the
            JavaScript ``window.scrollTo`` jumps directly to the bottom;
            the parameter is kept for callers that want a smaller step.
        """
        last_height = -1
        steady_rounds = 0
        for i in range(max_scrolls):
            height = int(page.evaluate("() => document.body.scrollHeight"))
            page.evaluate(
                "(step) => window.scrollBy(0, step)",
                scroll_step_px,
            )
            page.evaluate(
                "() => window.scrollTo(0, document.body.scrollHeight)",
            )
            page.wait_for_timeout(settle_ms)
            new_height = int(page.evaluate("() => document.body.scrollHeight"))
            if new_height == height:
                steady_rounds += 1
                # Two consecutive no-growth rounds → the page has settled.
                if steady_rounds >= 2:
                    return i + 1
            else:
                steady_rounds = 0
            last_height = new_height
        return max_scrolls

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
        try:
            self._browser = self._playwright.chromium.launch(**self._launch_kwargs())
        except Exception as e:  # noqa: BLE001 — surface a clear, actionable message
            raise SourceError(
                f"Chromium failed to launch: {e}. Run "
                f"'playwright install chromium', or set {_CHROME_ENV} to an "
                f"existing Chromium binary."
            ) from e
        self._log.info("playwright.browser_started")

    def _launch_kwargs(self) -> dict:
        """Launch kwargs mirroring the render layer.

        ``--no-sandbox`` is required when running as root in a container;
        ``--disable-dev-shm-usage`` avoids tab crashes on a small ``/dev/shm``.
        ``executable_path`` is set only when the env override is present, so
        default Playwright browser resolution is unchanged otherwise.
        """
        kwargs: dict = {
            "headless": self._headless,
            "args": ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        }
        env_path = os.environ.get(_CHROME_ENV)
        if env_path:
            kwargs["executable_path"] = env_path
        return kwargs

    def _record_navigation(self, page: Any, response: Any) -> None:
        try:
            request = response.request
            if request.is_navigation_request() and request.frame.parent_frame is None:
                self._nav_response[id(page)] = response
        except Exception:  # noqa: BLE001 — e.g. a detached frame; nothing to record
            pass

    def _last_status(self, page: Any) -> int | None:
        response = self._nav_response.get(id(page))
        return getattr(response, "status", None) if response is not None else None

    @staticmethod
    def _raise_for_status(status: int | None, url: str) -> None:
        if status == 403:
            raise ForbiddenError(f"HTTP 403 from {url} — server is refusing access")
        if status == 429:
            raise SourceError(f"HTTP 429 from {url} — rate-limited; stopping this source")

    def _route(self, route: Any) -> None:
        request = route.request
        if request.resource_type in self._block_types:
            route.abort()
            return
        if self._allowed_hosts:
            host = (urllib.parse.urlparse(request.url).hostname or "").lower()
            if not any(host == h or host.endswith("." + h) for h in self._allowed_hosts):
                route.abort()
                return
        route.continue_()

    def _check_robots(self, url: str) -> None:
        if not self._respect_robots:
            return
        if not self._robots_cache.allowed(url, USER_AGENT):
            raise ForbiddenError(self._robots_cache.denial_reason(url))

    def _wait_rate_limit(self, url: str) -> None:
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
