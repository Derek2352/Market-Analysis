"""Robots.txt checker with per-host caching.

Used by ``PoliteClient`` and ``PlaywrightManager`` to honour robots.txt before
the first request to each host in a run.  The cache is in-process only; it
resets between CLI invocations by design (each ``mkt scrape`` run is a fresh
process).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from urllib.parse import quote, urljoin, urlparse

import httpx
import structlog

# The one honest User-Agent every request in the project identifies itself
# with. PoliteClient and PlaywrightManager force it; scrapers can't override.
USER_AGENT = "MarketAnalyticsBot/0.1 (research; contact: see README.md)"


def _product_token(value: str) -> str:
    """RFC 9309 product token: leading letters/underscores/hyphens, lowercased.

    "MarketAnalyticsBot/0.1 (research; ...)" -> "marketanalyticsbot", so a
    site's "User-agent: MarketAnalyticsBot" group applies to us. "*" stays "*".
    """
    m = re.match(r"\s*([A-Za-z_-]+)", value)
    return m.group(1).lower() if m else value.strip().lower()


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


@dataclass
class _Unreachable:
    """robots.txt couldn't be fetched (5xx or network error): disallow all."""

    detail: str


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
            headers={"User-Agent": USER_AGENT},
            timeout=httpx.Timeout(15.0),
            follow_redirects=True,  # RFC 9309: follow at least five redirects
            trust_env=True,
            verify=_ca_bundle(),
        )
        self._own_client = client is None
        self._cache: dict[str, _Rules | _Unreachable] = {}
        self._log = structlog.get_logger().bind(component="RobotsCache")

    def allowed(self, url: str, user_agent: str = "*") -> bool:
        """Return ``True`` if *url* is allowed by the host's robots.txt.

        The first call for a host fetches and parses robots.txt; later calls
        are cached. Per RFC 9309, a 4xx (no robots.txt) allows everything,
        while a 5xx or network failure means the rules are unknown and the
        whole site is treated as disallowed. ``denial_reason`` says why.
        """
        parsed = urlparse(url)
        host = f"{parsed.scheme}://{parsed.hostname}"
        path = parsed.path or "/"
        if path == "/robots.txt":
            return True  # RFC 9309: robots.txt itself is always fetchable
        # Rules match the path *including* the query string, so rules like
        # "/forum.php?mod=post*" can apply.
        target = f"{path}?{parsed.query}" if parsed.query else path

        if host not in self._cache:
            self._cache[host] = self._fetch_disallowed(host, user_agent)

        rules = self._cache[host]
        if isinstance(rules, _Unreachable):
            self._log.warning("robots.unreachable_assume_disallow", url=url, detail=rules.detail)
            return False

        # Longest-match precedence; on a tie the Allow wins (Google spec).
        dis = _longest_match(target, rules.disallow)
        alw = _longest_match(target, rules.allow)
        if dis < 0:
            return True  # no disallow rule matches this path
        if alw >= dis:
            return True  # an equally- or more-specific Allow overrides
        self._log.warning("robots.disallowed", url=url, prefix_len=dis)
        return False

    def denial_reason(self, url: str) -> str:
        """Human-readable reason a URL was refused (call after ``allowed``)."""
        parsed = urlparse(url)
        entry = self._cache.get(f"{parsed.scheme}://{parsed.hostname}")
        if isinstance(entry, _Unreachable):
            return (
                f"robots.txt for {parsed.hostname} could not be fetched "
                f"({entry.detail}); RFC 9309 says to treat the site as fully "
                f"disallowed, so {url} was skipped"
            )
        return f"robots.txt disallows {url} — skipping"

    def close(self) -> None:
        if self._own_client:
            self._client.close()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _fetch_disallowed(self, host: str, user_agent: str) -> _Rules | _Unreachable:
        robots_url = urljoin(host, "/robots.txt")
        try:
            resp = self._client.get(robots_url)
        except Exception as e:  # noqa: BLE001 — network failure of any kind
            self._log.warning("robots.fetch_failed", url=robots_url, error=str(e))
            return _Unreachable(f"{type(e).__name__}: {e}")
        if resp.status_code >= 500:
            self._log.warning("robots.server_error", url=robots_url, status=resp.status_code)
            return _Unreachable(f"HTTP {resp.status_code}")
        if resp.status_code >= 300:
            # 4xx (and unresolved redirects): RFC 9309 "unavailable" -> allow all.
            return _Rules()
        try:
            return self._parse(resp.text, user_agent)
        except Exception as e:  # noqa: BLE001 — parser is lenient; be safe anyway
            self._log.warning("robots.parse_failed", url=robots_url, error=str(e))
            return _Unreachable(f"unparseable robots.txt: {e}")

    @staticmethod
    def _parse(text: str, user_agent: str) -> _Rules:
        """Record-aware robots.txt parser.

        Groups records by user-agent block (a rule line after a user-agent
        line closes the agent list; the next user-agent line starts a new
        group). Returns the rules for the most specific group that applies to
        *user_agent* — an exact-name group wins over the ``*`` group — so a
        ``Disallow: /`` written for another bot is never attributed to us.
        """
        ua = _product_token(user_agent)
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
                cur_agents.add(_product_token(value))
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


@lru_cache(maxsize=4096)
def _compile_rule(rule: str) -> re.Pattern[str]:
    """Compile a robots.txt path rule (RFC 9309 / Google syntax) to a regex.

    ``*`` matches any sequence of characters and a trailing ``$`` anchors the
    end; everything else is literal and matched from the start of the
    path+query. Non-ASCII characters are percent-encoded so a rule written in
    UTF-8 matches the encoded request URL.
    """
    anchored = rule.endswith("$")
    body = rule[:-1] if anchored else rule
    body = "".join(ch if ord(ch) < 128 else quote(ch) for ch in body)
    pattern = "".join(".*" if ch == "*" else re.escape(ch) for ch in body)
    return re.compile(pattern + ("$" if anchored else ""))


def _longest_match(target: str, rules: list[str]) -> int:
    """Length of the longest rule in *rules* matching *target*, or -1.

    Rule length is the specificity measure RFC 9309 uses: the longest
    matching rule decides, and the caller lets Allow win ties.
    """
    best = -1
    for rule in rules:
        if len(rule) > best and _compile_rule(rule).match(target):
            best = len(rule)
    return best
