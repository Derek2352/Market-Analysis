"""Robots.txt checker with per-host caching.

Used by ``PoliteClient`` and ``PlaywrightManager`` to honour robots.txt before
the first request to each host in a run.  The cache is in-process only; it
resets between CLI invocations by design (each ``mkt scrape`` run is a fresh
process).
"""

from __future__ import annotations

from urllib.parse import urljoin, urlparse

import httpx
import structlog


class RobotsCache:
    """Fetch and cache robots.txt per host (scheme + hostname).

    Parameters
    ----------
    client:
        An ``httpx.Client`` (reused from ``PoliteClient`` if available, or
        created internally).  Honours the same User-Agent.
    """

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(
            headers={"User-Agent": "MarketAnalyticsBot/0.1"},
            timeout=httpx.Timeout(15.0),
        )
        self._own_client = client is None
        self._cache: dict[str, set[str] | None] = {}  # host → {disallowed paths} | None=unknown
        self._log = structlog.get_logger().bind(component="RobotsCache")

    def allowed(self, url: str, user_agent: str = "*") -> bool:
        """Return ``True`` if *url* is allowed by the host's robots.txt.

        The first call for a host fetches and parses robots.txt.  Subsequent
        calls are cached.  If the fetch fails (timeout, 5xx), we treat it as
        **allowed** with a warning — we don't want a transient robots.txt
        failure to kill a whole run.
        """
        parsed = urlparse(url)
        host = f"{parsed.scheme}://{parsed.hostname}"
        path = parsed.path or "/"

        if host not in self._cache:
            self._cache[host] = self._fetch_disallowed(host, user_agent)

        disallowed = self._cache[host]
        if disallowed is None:
            # Fetch failed — allow
            return True

        for prefix in disallowed:
            if path.startswith(prefix) or prefix == "/":
                self._log.warning("robots.disallowed", url=url, prefix=prefix)
                return False
        return True

    def close(self) -> None:
        if self._own_client:
            self._client.close()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _fetch_disallowed(self, host: str, user_agent: str) -> set[str] | None:
        robots_url = urljoin(host, "/robots.txt")
        try:
            resp = self._client.get(robots_url)
            if resp.status_code == 404:
                self._cache[host] = set()  # no robots.txt → allow all
                return set()
            resp.raise_for_status()
        except Exception:
            self._log.warning("robots.fetch_failed", url=robots_url, exc_info=True)
            return None  # Treat as allowed

        # Parse
        try:
            return self._parse(resp.text, user_agent)
        except Exception:
            self._log.warning("robots.parse_failed", url=robots_url, exc_info=True)
            return None

    @staticmethod
    def _parse(text: str, user_agent: str) -> set[str]:
        """Minimal robots.txt parser — extracts disallowed paths for *user_agent*."""
        disallowed: set[str] = set()
        current_agents: set[str] = set()
        for raw_line in text.splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if not line:
                continue
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip().lower()
            value = value.strip()
            if key == "user-agent":
                current_agents.add(value.lower())
            elif key == "disallow" and ("*" in current_agents or user_agent.lower() in current_agents):
                if value:
                    disallowed.add(value)
        return disallowed
