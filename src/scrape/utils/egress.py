"""Outbound-egress preflight.

When every source fails at once, the cause is almost always environmental —
the box has no route to the public internet, or a policy proxy is denying the
target hosts — not N independent scraper bugs. ``check_egress`` probes a couple
of canonical hosts through the same proxy/CA path the scrapers use and returns
a single verdict so the CLI / API can say *why* in one line instead of leaving
a pile of per-source 403s.

Verdicts:
  ``ok``       — at least one probe host returned an HTTP response.
  ``policy``   — the egress proxy refused the CONNECT (policy denial: 403/407).
  ``no_route`` — no route: connections refused or timed out.
"""
from __future__ import annotations

import httpx

from src.scrape.base.robots import _ca_bundle

USER_AGENT = "MarketAnalyticsBot/0.1 (research; contact: see README.md)"

# Neutral, widely-available probe hosts. robots.txt is tiny and safe to fetch.
_DEFAULT_HOSTS = (
    "https://old.reddit.com/robots.txt",
    "https://www.google.com/robots.txt",
)


def check_egress(
    hosts: tuple[str, ...] | None = None, timeout: float = 5.0
) -> tuple[str, str]:
    """Return ``(verdict, detail)`` where verdict is ``ok`` | ``policy`` | ``no_route``.

    ``ok`` as soon as any host answers with an HTTP status. If none answer,
    a proxy CONNECT refusal maps to ``policy``; any other transport failure
    maps to ``no_route``. Never raises — a preflight must not crash the run.
    """
    hosts = hosts or _DEFAULT_HOSTS
    proxy_denied = False
    last_err = ""
    try:
        client = httpx.Client(
            trust_env=True,
            verify=_ca_bundle(),
            timeout=timeout,
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT},
        )
    except Exception as e:  # noqa: BLE001
        return ("no_route", f"could not create HTTP client: {e}")

    with client:
        for url in hosts:
            try:
                resp = client.get(url)
                return ("ok", f"reachable: {url} -> HTTP {resp.status_code}")
            except httpx.ProxyError as e:
                proxy_denied = True
                last_err = str(e)
            except httpx.HTTPError as e:
                last_err = f"{type(e).__name__}: {e}"

    if proxy_denied:
        return (
            "policy",
            f"egress proxy refused CONNECT to the probe hosts (policy denial). {last_err}".strip(),
        )
    return ("no_route", f"no route to the probe hosts. {last_err}".strip())
