"""robots.txt matching follows RFC 9309 / Google: wildcards, $ anchors, queries."""
from __future__ import annotations

import pytest

from src.scrape.base.robots import RobotsCache

UA = "MarketAnalyticsBot/0.1 (research; contact: see README.md)"

# Trimmed from https://www.baby-kingdom.com/robots.txt (Oct 2026).
BABY_KINGDOM = """
User-agent: ClaudeBot
Disallow: /

User-agent: *
Disallow: /api/
Disallow: /search.php*
Disallow: /forum.php?mod=redirect*
Disallow: /forum.php?mod=post*
"""


class _Client:
    def __init__(self, text):
        self.text = text

    def get(self, url):
        text = self.text
        return type("R", (), {"status_code": 200, "text": text, "raise_for_status": lambda self: None})()

    def close(self):
        pass


def _cache(text):
    return RobotsCache(client=_Client(text))


@pytest.mark.parametrize("url,allowed", [
    ("https://x.test/search.php?mod=forum&srchtxt=%E5%A4%A7", False),  # /search.php* (was wrongly ALLOWED)
    ("https://x.test/search.php", False),
    ("https://x.test/forum.php?mod=redirect&tid=1", False),             # query-based rule
    ("https://x.test/forum.php?mod=post&action=reply", False),
    ("https://x.test/forum.php?mod=viewthread&tid=1", True),
    ("https://x.test/api/v1", False),
    ("https://x.test/", True),
])
def test_baby_kingdom_rules(url, allowed):
    assert _cache(BABY_KINGDOM).allowed(url, UA) is allowed


def test_dollar_anchor():
    c = _cache("User-agent: *\nDisallow: /*.pdf$\n")
    assert c.allowed("https://x.test/doc.pdf", UA) is False
    assert c.allowed("https://x.test/doc.pdf?download=1", UA) is True
    assert c.allowed("https://x.test/doc.pdfx", UA) is True


def test_wildcard_in_middle():
    c = _cache("User-agent: *\nDisallow: /*/comments/\n")
    assert c.allowed("https://x.test/news/comments/123", UA) is False
    assert c.allowed("https://x.test/news/article", UA) is True


def test_longest_rule_wins_and_allow_wins_ties():
    c = _cache("User-agent: *\nDisallow: /p\nAllow: /p\nDisallow: /page/private\nAllow: /page\n")
    assert c.allowed("https://x.test/p", UA) is True              # tie -> allow
    assert c.allowed("https://x.test/page/public", UA) is True    # /page (5) beats /p (2)
    assert c.allowed("https://x.test/page/private/x", UA) is False


def test_non_ascii_rule_matches_encoded_url():
    c = _cache("User-agent: *\nDisallow: /搜尋\n")
    assert c.allowed("https://x.test/%E6%90%9C%E5%B0%8B?q=1", UA) is False


def test_robots_txt_itself_always_allowed():
    assert _cache("User-agent: *\nDisallow: /\n").allowed("https://x.test/robots.txt", UA) is True


def test_regex_metacharacters_are_literal():
    c = _cache("User-agent: *\nDisallow: /a.b+c(d)\n")
    assert c.allowed("https://x.test/a.b+c(d)/x", UA) is False
    assert c.allowed("https://x.test/aXb+c(d)", UA) is True
