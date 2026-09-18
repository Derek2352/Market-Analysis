"""Regression tests for the scrape-reliability fixes.

Covers the code bugs the diagnostic audit verified: robots over-blocking,
the HTTP retry classifier, the egress preflight verdicts, the LIHKG
like-count AttributeError, and the Playwright launch env override.
"""
from __future__ import annotations

import types

import httpx
import pytest

from src.scrape.base.robots import RobotsCache, _Rules
from src.scrape.base.http import _is_retryable, ForbiddenError


# ---------------------------------------------------------------------------
# robots.txt: record-aware parsing must not inherit another bot's Disallow: /
# ---------------------------------------------------------------------------

_MULTI = """
User-agent: Googlebot
Disallow: /

User-agent: *
Disallow: /private
Allow: /private/public

User-agent: BadBot
Disallow: /
"""


def _rules_for(text, ua="MarketAnalyticsBot/0.1"):
    return RobotsCache._parse(text, ua)


def test_disallow_all_for_other_bot_not_inherited():
    rules = _rules_for(_MULTI)
    # We fall into the "*" group: only /private is disallowed, not "/".
    assert "/" not in rules.disallow
    assert "/private" in rules.disallow


def test_allow_overrides_more_general_disallow():
    cache = RobotsCache(client=_FakeClient(_MULTI))
    assert cache.allowed("https://x.test/private/public/page", "MarketAnalyticsBot/0.1") is True
    assert cache.allowed("https://x.test/private/secret", "MarketAnalyticsBot/0.1") is False
    assert cache.allowed("https://x.test/anything-else", "MarketAnalyticsBot/0.1") is True


def test_exact_agent_group_wins_over_wildcard():
    text = "User-agent: *\nDisallow: /\n\nUser-agent: MarketAnalyticsBot/0.1\nDisallow: /admin\n"
    cache = RobotsCache(client=_FakeClient(text))
    # Our named group only blocks /admin, so /x is allowed despite the * block.
    assert cache.allowed("https://x.test/x", "MarketAnalyticsBot/0.1") is True
    assert cache.allowed("https://x.test/admin/panel", "MarketAnalyticsBot/0.1") is False


def test_empty_disallow_means_allow_all():
    rules = _rules_for("User-agent: *\nDisallow:\n")
    assert rules.disallow == []


class _FakeResp:
    def __init__(self, text, status=200):
        self.text = text
        self.status_code = status

    def raise_for_status(self):
        pass


class _FakeClient:
    """Stand-in httpx client that returns a fixed robots.txt body."""

    def __init__(self, text):
        self._text = text

    def get(self, url):
        return _FakeResp(self._text)

    def close(self):
        pass


# ---------------------------------------------------------------------------
# HTTP retry classifier
# ---------------------------------------------------------------------------

def test_timeouts_and_proxy_errors_are_retryable():
    assert _is_retryable(httpx.ConnectTimeout("x")) is True
    assert _is_retryable(httpx.ReadTimeout("x")) is True
    assert _is_retryable(httpx.PoolTimeout("x")) is True
    assert _is_retryable(httpx.ProxyError("x")) is True
    assert _is_retryable(httpx.ConnectError("x")) is True


def test_client_protocol_and_forbidden_not_retryable():
    assert _is_retryable(httpx.LocalProtocolError("x")) is False
    assert _is_retryable(ForbiddenError("403")) is False
    assert _is_retryable(ValueError("nope")) is False


def test_5xx_retryable_4xx_not():
    def mk(status):
        req = httpx.Request("GET", "https://x.test")
        resp = httpx.Response(status, request=req)
        return httpx.HTTPStatusError("e", request=req, response=resp)

    assert _is_retryable(mk(503)) is True
    assert _is_retryable(mk(429)) is True
    assert _is_retryable(mk(404)) is False


# ---------------------------------------------------------------------------
# Egress preflight verdicts
# ---------------------------------------------------------------------------

def _patch_client(monkeypatch, behaviour):
    class _C:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            return behaviour(url)

    monkeypatch.setattr(httpx, "Client", _C)


def test_egress_ok(monkeypatch):
    from src.scrape.utils import egress
    _patch_client(monkeypatch, lambda url: types.SimpleNamespace(status_code=200))
    verdict, _ = egress.check_egress()
    assert verdict == "ok"


def test_egress_policy(monkeypatch):
    from src.scrape.utils import egress

    def boom(url):
        raise httpx.ProxyError("403 to CONNECT")

    _patch_client(monkeypatch, boom)
    verdict, detail = egress.check_egress()
    assert verdict == "policy"


def test_egress_no_route(monkeypatch):
    from src.scrape.utils import egress

    def boom(url):
        raise httpx.ConnectError("connection refused")

    _patch_client(monkeypatch, boom)
    verdict, _ = egress.check_egress()
    assert verdict == "no_route"


# ---------------------------------------------------------------------------
# LIHKG: a like count with no reply count must not raise / must be correct
# ---------------------------------------------------------------------------

def test_lihkg_like_count_without_reply_count():
    from src.scrape.lihkg import LIHKGScraper

    html = (
        '<div><a href="/thread/12345/page/1">YATA fresh food thread</a>'
        '<span>Alice · 2025-05-17 14:30 · 45 likes</span></div>'
    )
    # _parse_thread_list uses only self-free logic; a bare namespace is fine.
    posts = LIHKGScraper._parse_thread_list(types.SimpleNamespace(), html, "yata")
    assert len(posts) == 1
    assert posts[0].engagement_metrics.get("likes") == 45


def test_lihkg_matches_thread_link_without_page_suffix():
    from src.scrape.lihkg import LIHKGScraper

    html = '<div><a href="/thread/999">YATA delivery thread</a><span>Bob · 2025-05-01 10:00</span></div>'
    posts = LIHKGScraper._parse_thread_list(types.SimpleNamespace(), html, "yata")
    assert len(posts) == 1


# ---------------------------------------------------------------------------
# Playwright launch honours the executable-path env override
# ---------------------------------------------------------------------------

def test_playwright_launch_kwargs_env_override(monkeypatch):
    from src.scrape.base.playwright import PlaywrightManager
    from src.scrape.base.robots import RobotsCache as RC

    mgr = PlaywrightManager(RC())
    monkeypatch.setenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH", "/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
    kw = mgr._launch_kwargs()
    assert kw["executable_path"] == "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
    assert "--no-sandbox" in kw["args"]
    mgr.close() if hasattr(mgr, "close") else None


def test_playwright_launch_kwargs_no_env(monkeypatch):
    from src.scrape.base.playwright import PlaywrightManager
    from src.scrape.base.robots import RobotsCache as RC

    monkeypatch.delenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH", raising=False)
    kw = PlaywrightManager(RC())._launch_kwargs()
    assert "executable_path" not in kw
    assert "--no-sandbox" in kw["args"]
