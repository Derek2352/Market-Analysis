"""DuckDuckGo SERP utility tests — offline parser + cache TTL."""
from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pytest

from src.scrape.utils.ddg_search import (
    DDGResult,
    _parse_serp,
    _resolve_redirect,
    search,
)


SAMPLE_SERP = """
<!doctype html><html><body>
<div class="result results_links results_links_deep web-result">
  <h2 class="result__title">
    <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fmedium.com%2F%40foo%2Fhk-octopus-card-deep-dive-abc123">
      The HK Octopus card: a deep dive — by foo
    </a>
  </h2>
  <a class="result__snippet" href="...">A long-form Medium piece on Octopus card adoption…</a>
</div>
<div class="result results_links results_links_deep web-result">
  <h2 class="result__title">
    <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fmedium.com%2F%40bar%2Fwhy-i-stopped-using-octopus-456def">
      Why I stopped using Octopus
    </a>
  </h2>
  <a class="result__snippet" href="...">Switched to Apple Pay full-time. Here's what I learned…</a>
</div>
</body></html>
"""


def test_redirect_resolves_to_target() -> None:
    href = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpath"
    assert _resolve_redirect(href) == "https://example.com/path"


def test_redirect_passthrough_for_direct_url() -> None:
    assert _resolve_redirect("https://example.com") == "https://example.com"


def test_parser_extracts_url_title_snippet() -> None:
    results = _parse_serp(SAMPLE_SERP, max_results=10)
    assert len(results) == 2
    assert results[0].url == "https://medium.com/@foo/hk-octopus-card-deep-dive-abc123"
    assert "deep dive" in results[0].title
    assert "Medium piece" in results[0].snippet


def test_parser_respects_max_results() -> None:
    results = _parse_serp(SAMPLE_SERP, max_results=1)
    assert len(results) == 1


def test_search_uses_cache_within_ttl(tmp_path: Path) -> None:
    """First call writes the cache; second call returns it without HTTP."""
    cache_dir = tmp_path / "ddg"
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, text=SAMPLE_SERP)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    a = search("site:medium.com test", cache_dir=cache_dir, client=client)
    b = search("site:medium.com test", cache_dir=cache_dir, client=client)

    assert len(a) == 2
    assert len(b) == 2
    assert calls["n"] == 1, "second call should hit cache, not HTTP"


def test_search_handles_non_200(tmp_path: Path) -> None:
    from src.scrape.base.protocol import SourceError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(SourceError):
        search("any query", cache_dir=tmp_path / "ddg", client=client)


def test_cache_expires_after_ttl(tmp_path: Path) -> None:
    """An old cache file should be ignored and re-fetched."""
    cache_dir = tmp_path / "ddg"
    cache_dir.mkdir(parents=True)
    # Pre-seed a stale cache file (mtime in 1990).
    from src.scrape.utils.ddg_search import _cache_path
    p = _cache_path("aged-out-query", cache_dir)
    p.write_text(json.dumps({"query": "aged-out-query", "fetched_at": 0, "results": [
        {"url": "https://old.example", "title": "OLD", "snippet": ""}
    ]}))
    # Backdate the file
    very_old = 631152000  # 1990-01-01
    import os
    os.utime(p, (very_old, very_old))

    fresh_calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        fresh_calls["n"] += 1
        return httpx.Response(200, text=SAMPLE_SERP)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    results = search("aged-out-query", cache_dir=cache_dir, client=client)
    assert fresh_calls["n"] == 1
    assert results[0].url.startswith("https://medium.com/")
