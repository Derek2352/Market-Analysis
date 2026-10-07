"""Legible failures: Reddit's login wall and Cloudflare bot blocks."""
from __future__ import annotations

import httpx
import pytest

from src.scrape.base import SourceError
from src.scrape.base.http import ForbiddenError, _raise_forbidden
from src.scrape.reddit_old import RedditOldScraper

SEARCH = "https://old.reddit.com/r/HongKong/search.json?q=x"


def _resp(status, url, *, headers=None, text="", history=None):
    r = httpx.Response(status, headers=headers or {}, text=text,
                       request=httpx.Request("GET", url))
    r.history = history or []
    return r


def _scraper(monkeypatch, response):
    monkeypatch.setenv("AUTHOR_HASH_SALT", "t")
    s = RedditOldScraper()
    monkeypatch.setattr(s._client, "get", lambda url, **kw: response)
    return s


def test_login_wall_redirect_gives_clear_error(monkeypatch):
    redirect = _resp(302, SEARCH, headers={"location": "https://old.reddit.com/login/?reason=lor2"})
    final = _resp(200, "https://old.reddit.com/login/?reason=lor2",
                  headers={"content-type": "text/html"}, text="<html>log in</html>",
                  history=[redirect])
    s = _scraper(monkeypatch, final)
    with pytest.raises(SourceError, match="requires login"):
        s._fetch_search_page("HongKong", "x")


def test_non_json_page_names_content_type(monkeypatch):
    page = _resp(200, SEARCH, headers={"content-type": "text/html"}, text="\n  <html>")
    s = _scraper(monkeypatch, page)
    with pytest.raises(SourceError, match="text/html instead of JSON"):
        s._fetch_search_page("HongKong", "x")


def test_valid_json_still_parses(monkeypatch):
    ok = _resp(200, SEARCH, headers={"content-type": "application/json"},
               text='{"data": {"children": [], "after": null}}')
    s = _scraper(monkeypatch, ok)
    data, after = s._fetch_search_page("HongKong", "x")
    assert data == {"children": [], "after": None} and after is None


def test_cloudflare_403_is_labelled_site_side():
    r = _resp(403, "https://lihkg.com/x", headers={"server": "cloudflare", "cf-ray": "abc-IAD"})
    with pytest.raises(ForbiddenError, match="Cloudflare bot protection"):
        _raise_forbidden(r)


def test_plain_403_message_unchanged():
    r = _resp(403, "https://example.com/x", headers={"server": "nginx"})
    with pytest.raises(ForbiddenError, match="server is refusing access"):
        _raise_forbidden(r)
