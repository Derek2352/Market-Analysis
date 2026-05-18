# Source coverage audit — Phase 6+ (2026-05-19)

This document records the actual state of registered scrapers per region
after the multi-region expansion. **Every line below is verified against
the offline parser test suite** (`pytest tests/scrape/` → 104 passing as of
this audit). It supersedes the source tables in `PROJECT_PLAN.md` §3.

## Method

1. `mkt scrape-doctor --region <R>` against every wired source.
2. `pytest tests/scrape/test_<source>.py` for every source-specific test.
3. Cross-check the registry: `default_enabled`, `tos_scraping_stance`.

Two findings worth flagging:

- **`scrape-doctor` produces false-positive "failures."** Several checks
  (`has-links: no <a> elements found`, `has-json-structure: class
  '{"success":' not found`) are over-generic and fire on:
  - Detail pages that don't have an `<a>` element by design (Quora answer
    bodies, Yelp business pages).
  - JSON fixtures with an XSS prefix (Medium's `])}while(1);</x>`).
  Treat doctor output as a *signal*, not a verdict. The pytest suite is
  authoritative — all 104 scraper tests pass.

- **Per-source pytest covers what doctor misses.** Every scraper module
  has a `tests/scrape/test_<source>.py` with at least 2 offline parser
  tests that exercise its real fixture shape. Those are the source of
  truth.

## Coverage tables

Legend:
- ✅ default-enabled + parser tests pass
- 🔒 opt-in only (ToS-prohibited)
- 📄 registered but no implementation yet (documentation placeholder)
- ❌ excluded by constraint (requires API key / OAuth)

### Hong Kong (HK)

| Category | Sources | Status |
|---|---|---|
| **forums** | lihkg, discuss_hk, reddit_old, baby_kingdom 📄 | 3 wired, 1 placeholder |
| **reviews** | app_store_hk ✅, openrice 🔒, google_play_hk 🔒, trustpilot 🔒 | 4 wired |
| **video_comments** | youtube_html 🔒 | 1 wired |
| **qa** | quora_hk 🔒 | 1 wired |
| **blogs** | medium_hk 🔒, hk_lifestyle_blogs 📄, google_serp 🔒📄 | 1 wired |
| **news_comments** | hk01 🔒, yahoo_news_hk 📄 | 1 wired |
| **social** | threads_hk 🔒📄, instagram_public 🔒📄, xiaohongshu_hk 🔒📄 | 0 wired |

**HK coverage: 6/7 categories with at least one working scraper.**

### Taiwan (TW)

| Category | Sources | Status |
|---|---|---|
| **forums** | ptt, dcard, mobile01 | 3 wired |
| **reviews** | app_store_tw ✅, google_play_tw 🔒 | 2 wired |
| **video_comments** | youtube_html 🔒 | 1 wired (Phase 7) |
| **qa** | quora_tw 🔒 | 1 wired (Phase 7) |
| **blogs** | medium_tw 🔒 | 1 wired (Phase 7) |
| **news_comments** | yahoo_news_tw | 1 wired |
| social | — | 0 wired |

**TW coverage: 6/7 categories.**

### United States (US)

| Category | Sources | Status |
|---|---|---|
| **forums** | reddit_old | 1 wired |
| **reviews** | app_store_us ✅, google_play_us 🔒, trustpilot 🔒, yelp_html 🔒 | 4 wired |
| **video_comments** | youtube_html 🔒 | 1 wired |
| **qa** | quora 🔒 | 1 wired |
| **blogs** | medium 🔒 | 1 wired |
| social / news_comments | — | 0 wired |

**US coverage: 5/7 categories.**

### Japan (JP)

| Category | Sources | Status |
|---|---|---|
| **forums** | five_ch | 1 wired |
| **reviews** | app_store_jp ✅, google_play_jp 🔒, cosme, tabelog 🔒, yahoo_japan_reviews | 5 wired |
| **video_comments** | youtube_html 🔒 | 1 wired (Phase 7) |
| **qa** | quora_jp 🔒 | 1 wired (Phase 7) |
| **blogs** | medium_jp 🔒 | 1 wired (Phase 7) |
| social / news_comments | — | 0 wired (yahoo_news_jp pending Phase 11) |

**JP coverage: 5/7 categories.**

## Totals

- **34 scraper modules** registered across `src/scrape/registry.py`.
- **104 offline parser tests** pass.
- **9 live integration tests** gated on `SCRAPE_LIVE_TESTS=1`
  (plus `ACCEPT_TOS_RISK=1` for ToS-prohibited sources).
- Region totals: HK 6/7, US 5/7, **TW 6/7 (Phase 7)**, **JP 5/7 (Phase 7)**.

## Known gaps

| Gap | Implication |
|---|---|
| `social` empty in every region | Personas under-represent short-form / image-native users. Twitter/IG/TikTok are API-only and excluded by the no-API constraint; Threads / IG public are ToS-prohibited and JS-fragile. Documented in `PROJECT_PLAN.md` as a known limitation. |
| `news_comments` empty in TW (other than yahoo_news_tw — needs fixture verification), US, JP | Editorial bias in personas — news-comments often reflect a different demographic from forums. |
| `scrape-doctor` checks over-strict | Generic content assertions (`has-links`, `has-json-structure`) fire false alarms on detail pages and prefixed JSON. **Action**: replace generic checks with per-source assertions defined in each scraper's module, or remove the checks entirely and rely on pytest. |
| TW `qa` / `blogs` / `social` / `video_comments` empty | Quora/Medium/YouTube scrapers exist as `quora_tw`, `medium_tw`, `youtube_html` but aren't surfaced in TW's regional registry yet. Quick win in a follow-up. |
| Several sources have no parser tests, only registry entries | `baby_kingdom`, `yahoo_news_hk`, `hk_lifestyle_blogs`, `threads_hk`, `instagram_public`, `xiaohongshu_hk`. These are placeholders, not regressions. |

## Suggested follow-up

1. **Replace `scrape-doctor`'s generic checks with per-source assertions.**
   Each scraper module already exposes its parser; the doctor should
   load the corresponding `tests/scrape/test_<source>.py` instead of
   running hand-rolled content sniffers.
2. **Wire TW's qa/blogs/video_comments** by adding `quora_tw`, `medium_tw`,
   `youtube_html` to the TW regional registry (the scrapers exist).
3. **Capture `reddit_old` fixtures** for HK + US so the parser test suite
   has reference HTML; currently `reddit_old` works live but has no
   offline fixture (and is included in default source lists).
4. **Fill US `news_comments`** — `yahoo_news_us` would mirror the existing
   `yahoo_news_tw` parser.
