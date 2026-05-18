# Scraping ethics & policy

This document explains how the project handles scraping decisions you'll
encounter while running it. **Short version:** we identify ourselves
honestly, respect robots.txt by default, accept 403s as a hard "no," never
scrape login-walled content, and gate sources whose ToS prohibits
scraping behind explicit opt-in.

If you're using this tool for commercial purposes against ToS-prohibited
sources, the responsibility is yours. We do not encourage that use.

## What the tool does (by default)

When you run `mkt scrape --topic X --region HK` without `--sources`:

- Only sources with `default_enabled=True` in the regional registry run.
- These are sources whose `tos_scraping_stance` is `silent`,
  `allowed_with_conditions`, or `unknown` (after manual review).
- **Every prohibited source is excluded from the default list** — the
  registry's Pydantic validator hard-fails at import time if any
  prohibited source is accidentally marked `default_enabled=True`.

When you pass `--sources <id1>,<id2>`:

- If any source is `default_enabled=False` and `tos_scraping_stance ==
  prohibited`, the CLI prints a `⚠` warning per source citing the
  `last_checked` date and ToS state before scraping starts.
- Suppress the warning text (not the behaviour) with `--accept-tos-risk`
  for scripts/CI.

## Identification

Every HTTP request goes through one of:

- `src.scrape.base.http.PoliteClient` — `httpx` with a default
  User-Agent of `MarketAnalyticsBot/0.1 (research; https://github.com/Derek2352/Market-Analysis/issues)`.
- `src.scrape.base.playwright.PlaywrightManager` — same UA, realistic
  Accept-Language and Accept headers, randomised viewport from a fixed
  set of common screen sizes.

**We never mimic a specific real browser version.** The UA may be
overridden per-scraper for sites that serve degraded HTML based on UA
strings (notably Discuss.com.hk — a generic `Mozilla/5.0` returns their
static-HTML view; the modern UA triggers a JS-only layout). When that
happens, the override is documented in the scraper's module docstring
and is a generic `Mozilla/5.0` prefix, never a fake Chrome 131 / Safari
17 string. The honest spec UA remains the default.

## Rate limiting

- `PoliteClient` defaults: **2 requests/second per domain**, exponential
  backoff (1 → 16s, 4 attempts) on `429` and `5xx`, hard-fail on `403`.
- `PlaywrightManager` defaults: **1 request/second per domain** (browsers
  are heavier).
- Per-scraper overrides are documented in the scraper module's
  rate constant — e.g. `DISCUSS_RATE = 1.5` for Discuss.com.hk.

## robots.txt

- Checked lazily before the first request to each host via the shared
  `RobotsCache`.
- A disallow → `ForbiddenError` is raised before any request goes out.
- **The check can be bypassed** by passing `respect_robots=False` to
  `PoliteClient` or `PlaywrightManager`. Used in tests for sites that
  block the search path (e.g. `discuss.com.hk` disallows
  `/search.php`). The production code path always respects robots by
  default.

## The four ToS stances

Recorded per source in `src/regions/registry.py` as
`tos_scraping_stance`. Each entry also carries `last_checked` (date the
stance was reviewed) and optionally `robots_txt_allows` (None when
unverified).

### `allowed_with_conditions`

The source's ToS explicitly permits non-commercial scraping subject to
some conditions (rate limit, attribution, no full-text redistribution).

Examples: Reddit (via old.reddit.com HTML, non-commercial use).

Default state: `default_enabled=True`. Runs by default.

### `silent`

The ToS has no explicit position on automated access.

Examples: LIHKG, Discuss.com.hk, PTT, 5ch mirrors, Mobile01.

Default state: `default_enabled=True`. We assume polite scraping is
acceptable and run by default with robots.txt + rate limiting.

### `prohibited`

The ToS explicitly forbids automated access.

Examples: Openrice, Quora, Trustpilot, Medium, YouTube, Naver, Weibo,
Zhihu, Shopee, Lazada, Coupang, Dianping, Twitter/X, Google SERP, HK01,
Yelp, Cosme, Tabelog, Yahoo News TW.

Default state: `default_enabled=False`. **Will never run unless
explicitly listed in `--sources`.** The CLI prints a per-source warning
on each invocation.

We include these in the registry because:

1. For **non-commercial research** (the project's intended use), many
   jurisdictions distinguish between scraping public content for analysis
   and scraping for redistribution. The project doesn't redistribute
   scraped content; it stores excerpts for grounding LLM-synthesised
   personas, with citations linking back to the public URL.
2. Pretending these sources don't exist would hide a real coverage gap
   in the persona output. Every persona's `data_source_coverage` block
   surfaces which categories contributed.

We do **not** include prohibited sources to encourage you to scrape
them. If you enable a prohibited source, you accept responsibility for
ToS compliance under your jurisdiction and use case.

### `unknown`

No review has happened yet. Treated as `silent` (default-enabled) but
flagged for follow-up. The CI registry-shape test will fail on this for
any newly added source.

## What we never scrape

Regardless of opt-in: anything behind a login. This includes:

- Facebook private content, IG private accounts, Instagram Stories
- WeChat, LinkedIn, Discord, private subreddits
- Paywalled news
- X/Twitter (now API-only since 2023; the only paid-tier API the project
  doesn't permit)

## PII

- **Usernames are hashed at scrape-time** with sha256 + a private
  per-install salt (`AUTHOR_HASH_SALT` in `.env`). The raw username
  never enters any persisted file.
- The dedup index stores only `(source, source_post_id, region,
  topic_slug, timestamps)`. No content. No PII.
- The UI cites quotes by source URL, never by username.
- The synthesis LLM (Claude / DeepSeek) receives quote text + `doc_id`,
  never raw usernames or IPs.

## When a site says no

- `403 Forbidden` → `ForbiddenError`, no retry. The scraper logs the
  rejection and moves on to the next source. We do not escalate.
- `429 Too Many Requests` → exponential backoff, up to 4 attempts. If
  still rate-limited, give up and log.
- `robots.txt` disallow → `ForbiddenError` before any request goes out.
- Per-source ToS prohibition → we tag the registry, gate behind
  `--sources`, and emit a `⚠` warning each time the source runs.

## What this project will not do

- Add residential proxies, IP rotation, or commercial anti-bot bypass
  services.
- Mimic a specific real browser version (Chrome 131, Safari 17, etc).
- Spoof a residential geolocation.
- Solve CAPTCHAs.
- Scrape login-walled content.
- Republish scraped content as the project's own.

If you want a tool that does any of those, this isn't it. The trade-off
is intentional: occasional scrape failures (a site updates its UA
detection, or rate-limits us, or returns a Cloudflare guard page) are
accepted as the cost of running ethically.

## Quick reference

```bash
# Default scrape — only default-enabled sources run, no warnings
mkt scrape --topic "MTR" --region HK

# Explicit opt-in for a prohibited source — warning is emitted
mkt scrape --topic "MTR" --region HK --sources lihkg,openrice
# >>> ⚠ openrice scraping is prohibited by its ToS. You enabled it
#       explicitly. ToS last_checked: 2026-05-18. Proceed at your own
#       risk.

# Same as above but suppress the warning text (useful in CI)
mkt scrape --topic "MTR" --region HK --sources lihkg,openrice --accept-tos-risk
```
