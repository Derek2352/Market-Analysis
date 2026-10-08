"""The project's scraping rules are enforced in code, not just documented.

README "Constraints": honest User-Agent, respect robots.txt, hard-fail on 403.
These tests fail if a scraper reintroduces a disguised User-Agent or a
hardcoded robots.txt bypass, or if the shared clients stop enforcing them.
"""
from __future__ import annotations

import re
from pathlib import Path

import httpx

from src.scrape.base.http import PoliteClient
from src.scrape.base.robots import USER_AGENT, RobotsCache

SCRAPERS = sorted(
    p for p in Path("src/scrape").rglob("*.py") if "base" not in p.parts
)


def test_no_scraper_disguises_its_user_agent():
    offenders = [
        str(p) for p in SCRAPERS
        if re.search(r"Mozilla/|AppleWebKit|Chrome/\d|Safari/\d", p.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"browser-like User-Agent in: {offenders}"


def test_no_scraper_hardcodes_a_robots_bypass():
    offenders = [
        str(p) for p in SCRAPERS
        if re.search(r"respect_robots\s*=\s*False", p.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"respect_robots=False hardcoded in: {offenders}"


def test_polite_client_forces_the_honest_user_agent():
    class _NoRobots:
        def allowed(self, url, user_agent="*"):
            return True

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["ua"] = request.headers.get("user-agent")
        return httpx.Response(200, text="ok")

    c = PoliteClient(robots_cache=_NoRobots(), rate=0,
                     headers={"User-Agent": "Mozilla/5.0", "accept-language": "ja"})
    c._client = httpx.Client(transport=httpx.MockTransport(handler), headers=c._client.headers)
    c.get("https://example.test/x")
    assert seen["ua"] == USER_AGENT
    assert c._client.headers.get("accept-language") == "ja"


def _robots_cache(handler) -> RobotsCache:
    return RobotsCache(client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_unreachable_robots_txt_means_disallow():
    # RFC 9309 2.3.1.4: 5xx / network error -> assume complete disallow.
    rc = _robots_cache(lambda r: httpx.Response(503))
    assert rc.allowed("https://x.test/page", USER_AGENT) is False
    assert "could not be fetched" in rc.denial_reason("https://x.test/page")

    def boom(r):
        raise httpx.ConnectError("refused")
    rc2 = _robots_cache(boom)
    assert rc2.allowed("https://y.test/page", USER_AGENT) is False


def test_missing_robots_txt_means_allow():
    # RFC 9309 2.3.1.3: 4xx -> no rules, allow everything.
    for status in (401, 403, 404, 410):
        rc = _robots_cache(lambda r, s=status: httpx.Response(s))
        assert rc.allowed("https://x.test/page", USER_AGENT) is True


def test_group_selected_by_product_token():
    # A group naming our bot (any case, with or without a version) applies to
    # us even though our full UA string carries a version and a comment.
    for line in ("MarketAnalyticsBot", "marketanalyticsbot", "MarketAnalyticsBot/9.9"):
        text = f"User-agent: *\nAllow: /\n\nUser-agent: {line}\nDisallow: /\n"
        rc = _robots_cache(lambda r, t=text: httpx.Response(200, text=t))
        assert rc.allowed("https://x.test/page", USER_AGENT) is False, line


def test_apple_rss_reviews_are_disallowed():
    # itunes.apple.com/robots.txt (Oct 2026) — the old prefix-only matcher
    # missed the wildcard and let the App Store review feed through.
    text = "User-agent: *\nDisallow: /search*\nDisallow: /*/rss/*\nDisallow: /*/lookup?\n"
    rc = _robots_cache(lambda r: httpx.Response(200, text=text))
    feed = "https://itunes.apple.com/hk/rss/customerreviews/page=1/id=1436965382/sortby=mostrecent/json"
    assert rc.allowed(feed, USER_AGENT) is False
    assert rc.allowed("https://itunes.apple.com/search?term=x", USER_AGENT) is False
