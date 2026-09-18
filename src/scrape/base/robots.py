"""Robots.txt checker with per-host caching.

Used by ``PoliteClient`` and ``PlaywrightManager`` to honour robots.txt before
the first request to each host in a run.  The cache is in-process only; it
resets between CLI invocations by design (each ``mkt scrape`` run is a fresh
process).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import httpx
import structlog


def _ca_bundle() -> str | bool:
    """Resolve an explicit CA bundle for the egress proxy, else httpx default.

    Behind a TLS-terminating egress proxy the system store won't chain; the
    proxy sets SSL_CERT_FILE / REQUESTS_CA_BUNDLE. Never disables verification.
    """
    return os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE") or True


@dataclass
class _Rules:
    """Disallow / Allow prefixes for the user-agent group that applies to us."""

    disallow: list[str] = field(default_factory=list)
    allow: list[str] = field(default_factory=list)


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
            trust_env=True,
            verify=_ca_bundle(),
        )
        self._own_client = client is None
        self._cache: dict[str, _Rules | None] = {}  # host → rules | None=unknown
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

        rules = self._cache[host]
        if rules is None:
            # Fetch failed — allow
            return True

        # Longest-match precedence; on a tie the Allow wins (Google spec).
        dis = _longest_match(path, rules.disallow)
        alw = _longest_match(path, rules.allow)
        if dis < 0:
            return True  # no disallow rule matches this path
        if alw >= dis:
            return True  # an equally- or more-specific Allow overrides
        self._log.warning("robots.disallowed", url=url, prefix_len=dis)
        return False

    def close(self) -> None:
        if self._own_client:
            self._client.close()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _fetch_disallowed(self, host: str, user_agent: str) -> _Rules | None:
        robots_url = urljoin(host, "/robots.txt")
        try:
            resp = self._client.get(robots_url)
            if resp.status_code == 404:
                return _Rules()  # no robots.txt → allow all
            resp.raise_for_status()
        except Exception:
            self._log.warning("robots.fetch_failed", url=robots_url, exc_info=True)
            return None  # Treat as allowed

        try:
            return self._parse(resp.text, user_agent)
        except Exception:
            self._log.warning("robots.parse_failed", url=robots_url, exc_info=True)
            return None

    @staticmethod
    def _parse(text: str, user_agent: str) -> _Rules:
        """Record-aware robots.txt parser.

        Groups records by user-agent block (a rule line after a user-agent
        line closes the agent list; the next user-agent line starts a new
        group). Returns the rules for the most specific group that applies to
        *user_agent* — an exact-name group wins over the ``*`` group — so a
        ``Disallow: /`` written for another bot is never attributed to us.
        """
        ua = user_agent.lower()
        groups: list[tuple[set[str], _Rules]] = []
        cur_agents: set[str] = set()
        cur_rules = _Rules()
        seen_rule = False

        def flush() -> None:
            nonlocal cur_agents, cur_rules, seen_rule
            if cur_agents:
                groups.append((cur_agents, cur_rules))
            cur_agents, cur_rules, seen_rule = set(), _Rules(), False

        for raw_line in text.splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip().lower()
            value = value.strip()
            if key == "user-agent":
                if seen_rule:  # a new agent line after rules → new group
                    flush()
                cur_agents.add(value.lower())
            elif key == "disallow":
                seen_rule = True
                if value:  # empty Disallow = "allow all", carries no prefix
                    cur_rules.disallow.append(value)
            elif key == "allow":
                seen_rule = True
                if value:
                    cur_rules.allow.append(value)
        flush()

        # Prefer an exact-UA group, else the wildcard group, else allow-all.
        exact = _Rules()
        wildcard = _Rules()
        matched_exact = matched_wild = False
        for agents, rules in groups:
            if ua in agents:
                exact.disallow += rules.disallow
                exact.allow += rules.allow
                matched_exact = True
            if "*" in agents:
                wildcard.disallow += rules.disallow
                wildcard.allow += rules.allow
                matched_wild = True
        if matched_exact:
            return exact
        if matched_wild:
            return wildcard
        return _Rules()


def _longest_match(path: str, prefixes: list[str]) -> int:
    """Length of the longest prefix in *prefixes* that *path* starts with, or -1."""
    best = -1
    for p in prefixes:
        if path.startswith(p) and len(p) > best:
            best = len(p)
    return best
