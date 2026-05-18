"""DuckDuckGo HTML SERP utility.

DuckDuckGo doesn't offer a free API, but their html.duckduckgo.com endpoint
serves a server-rendered SERP that doesn't require a key or login. Used by
the Quora and Medium scrapers (Phase 6) for site-scoped discovery, e.g.

    search("site:medium.com 'Hong Kong' Octopus card")

Returns up to ``max_results`` ``DDGResult`` rows. Results are cached at
``data/cache/ddg/{sha256(query)}.json`` with a 24h TTL so iterating on a
scraper during development doesn't re-hit DDG repeatedly.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse, parse_qs

import httpx

from src.scrape.base.protocol import SourceError

DDG_URL = "https://html.duckduckgo.com/html/"
_USER_AGENT = (
    "MarketAnalyticsBot/0.1 "
    "(research; https://github.com/Derek2352/Market-Analysis/issues)"
)

_DEFAULT_CACHE = Path("data/cache/ddg")
_TTL_SECONDS = 24 * 3600


@dataclass
class DDGResult:
    """One SERP row."""

    url: str
    title: str
    snippet: str


def _cache_path(query: str, cache_dir: Path) -> Path:
    h = hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]
    return cache_dir / f"{h}.json"


def _load_cache(path: Path) -> list[DDGResult] | None:
    if not path.exists():
        return None
    try:
        stat = path.stat()
        age = time.time() - stat.st_mtime
        if age > _TTL_SECONDS:
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        return [DDGResult(**r) for r in raw.get("results", [])]
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def _save_cache(path: Path, query: str, results: list[DDGResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"query": query, "fetched_at": time.time(),
               "results": [asdict(r) for r in results]}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _resolve_redirect(href: str) -> str:
    """DDG wraps result URLs in /l/?uddg=<encoded>. Extract the original."""
    if not href:
        return ""
    if href.startswith("//duckduckgo.com/l/") or href.startswith("/l/"):
        try:
            qs = parse_qs(urlparse(href).query)
            target = qs.get("uddg", [""])[0]
            if target:
                return unquote(target)
        except Exception:
            pass
    return href


def search(
    query: str,
    *,
    max_results: int = 30,
    cache_dir: Path | None = None,
    client: httpx.Client | None = None,
    timeout: float = 20.0,
) -> list[DDGResult]:
    """Search DuckDuckGo for ``query``; return up to ``max_results`` rows.

    Cache is hit by exact-string query. Use the same query string the
    scraper actually uses (e.g. ``site:medium.com 'Hong Kong' Octopus``).
    """
    cache_dir = cache_dir or _DEFAULT_CACHE
    path = _cache_path(query, cache_dir)
    cached = _load_cache(path)
    if cached is not None:
        return cached[:max_results]

    owns_client = client is None
    cli = client or httpx.Client(
        timeout=timeout, headers={"User-Agent": _USER_AGENT},
    )
    try:
        resp = cli.post(DDG_URL, data={"q": query, "kl": "wt-wt"})
    finally:
        if owns_client:
            cli.close()

    if resp.status_code != 200:
        raise SourceError(
            f"DuckDuckGo SERP returned {resp.status_code} for {query!r}"
        )

    results = _parse_serp(resp.text, max_results=max_results)
    _save_cache(path, query, results)
    return results


def _parse_serp(html: str, *, max_results: int) -> list[DDGResult]:
    """Parse DDG's html.duckduckgo.com result rows.

    Each row looks like::

        <div class="result results_links results_links_deep web-result">
          <h2 class="result__title">
            <a class="result__a" href="//duckduckgo.com/l/?uddg=<url>">Title</a>
          </h2>
          <a class="result__snippet" href="...">Snippet text</a>
        </div>
    """
    from bs4 import BeautifulSoup

    out: list[DDGResult] = []
    soup = BeautifulSoup(html, "html.parser")
    for div in soup.select("div.result, div.web-result"):
        title_el = div.select_one("a.result__a")
        snippet_el = div.select_one(".result__snippet, .snippet")
        if not title_el:
            continue
        href = title_el.get("href", "") or ""
        url = _resolve_redirect(href)
        if not url:
            continue
        title = title_el.get_text(strip=True)
        snippet = snippet_el.get_text(strip=True) if snippet_el else ""
        out.append(DDGResult(url=url, title=title, snippet=snippet))
        if len(out) >= max_results:
            break
    return out
