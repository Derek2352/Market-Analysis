# Multi-Region Expansion Plan: TW, US, JP

**Goal:** Replicate the proven HK multi-source setup for Taiwan, United States, and Japan — 15-20 new scrapers, language modules, region-aware clustering, and a UI region switcher.

**Branch:** `claude/market-analytics-tool-mtmX2`

---

## Current State

- **Regions:** 18 regions registered, but only HK has real scrapers (10 scrapers). TW/US/JP have SourceConfigs but no implementations.
- **Scraper registry:** `src/scrape/registry.py` — factory dict maps source_id → scraper class.
- **Region registry:** `src/regions/registry.py` — `REGIONS` dict with `SourceConfig` entries for every source.
- **Configuration:** All scrapers follow `SourceScraper` protocol (`search`, `fetch_thread`, `close`).
- **Tests:** Unit tests use static HTML fixtures; live tests gated behind `SCRAPE_LIVE_TESTS=1`.
- **Language:** `src/scrape/utils/lang.py` — only `py3langid` detection (single-language classifier, no tokenization).

---

## Phase 1: Region Registry Expansion (registry.py)

Add missing SourceConfig entries for new sources in TW, US, JP:

### TW (6 new entries)
| source_id | category | access_method | notes |
|-----------|----------|---------------|-------|
| ~~ptt~~ | forums | HTML | Already registered ✓ |
| ~~dcard~~ | forums | PUBLIC_JSON | Already registered ✓ |
| ~~mobile01~~ | forums | HTML | Already registered ✓ |
| google_play_tw | reviews | PUBLIC_JSON | Mirror of google_play_hk for TW locale |
| app_store_tw | reviews | API | iTunes RSS for TW store |
| yahoo_news_tw | news_comments | HTML | Yahoo News TW comment sections |

### US (7 new/modified entries)
| source_id | category | access_method | notes |
|-----------|----------|---------------|-------|
| reddit_old | forums | HTML | **Generalize**: accept `--subreddits`, remove HK-hardcoding. US subreddits: askTO, personalfinance, etc. |
| trustpilot | reviews | HTML | New scraper — company-specific review pages |
| yelp_html | reviews | HTML_JS | Yelp business review pages via Playwright |
| youtube_html | video_comments | HTML_JS | Already registered, needs region param |
| quora | qa | HTML_JS | Generalize from quora_hk |
| medium | blogs | HTML | Generalize from medium_hk |
| app_store_us | reviews | API | Already registered ✓ |

### JP (7 new/modified entries)
| source_id | category | access_method | notes |
|-----------|----------|---------------|-------|
| ~~five_ch~~ | forums | HTML | Already registered ✓ |
| ~~yahoo_japan_reviews~~ | reviews | HTML | Already registered ✓ |
| ~~cosme~~ | reviews | HTML | Already registered ✓ |
| tabelog | reviews | HTML_JS | Restaurant reviews — Playwright required |
| youtube_jp | video_comments | HTML_JS | YouTube JP variant |
| google_play_jp | reviews | PUBLIC_JSON | Mirror of google_play_hk for JP locale |
| app_store_jp | reviews | API | iTunes RSS for JP store |

**Total new SourceConfig entries: ~14** (some are already registered, some are new)

---

## Phase 2: Scraper Implementation (15-20 scrapers)

### HK scrapers that can be generalized (regionalized):
These existing scrapers hardcode `region = "HK"` and need `region` parameter:

1. **reddit_old** → accept `region` + region-default subreddits
2. **youtube_html** → accept `region` param for locale filtering
3. **quora_hk** → generalize to `quora` with `region` param
4. **medium_hk** → generalize to `medium` with `region` param
5. **google_play_hk** → generalize with `region`/`country` param
6. **app_store_hk** → generalize with `region`/`country` param

### New scrapers to build:
7. **ptt.py** — PTT web mirror (https://www.ptt.cc)
8. **dcard.py** — Dcard public JSON API
9. **mobile01.py** — Mobile01 forum HTML
10. **yahoo_news_tw.py** — Yahoo News TW comments
11. **trustpilot.py** — Trustpilot review pages
12. **yelp_html.py** — Yelp via Playwright
13. **five_ch.py** — 5ch read-only mirrors
14. **yahoo_japan_reviews.py** — Yahoo Japan reviews
15. **cosme.py** — @cosme beauty reviews
16. **tabelog.py** — Tabelog restaurant reviews (Playwright)
17. **google_play_tw.py** — Google Play TW (can be thin wrapper)
18. **app_store_tw.py** — App Store TW (thin wrapper)
19. **google_play_jp.py** — Google Play JP (thin wrapper)
20. **app_store_jp.py** — App Store JP (thin wrapper)

### Scraper register entries:
Add all 20 to `src/scrape/registry.py` `_FACTORIES` dict.

---

## Phase 3: Language Modules

### `src/lang/` package (NEW)
```
src/lang/
├── __init__.py         # exports: Tokenizer protocol, get_tokenizer(region)
├── base.py             # Tokenizer Protocol + LanguageTokenizer base
├── zh.py               # Traditional Chinese: jieba with zh-TW dict
├── ja.py               # Japanese: fugashi + UniDic
└── en.py               # English: basic (already handled by sklearn)
```

### Key design decisions:
- **zh-TW:** `jieba` with Traditional Chinese dictionary (already in deps or add). No `fugashi` needed for Chinese.
- **ja:** `fugashi` + `unidic-lite` for morphological analysis. Essential for Japanese tokenization where words aren't space-separated.
- **en:** Keep existing sklearn `TfidfVectorizer(stop_words="english")`.
- Enhancement to `cluster_diag.py`: accept `tokenizer` parameter, use region-aware tokenizer for c-TF-IDF.

### Dependencies to add:
```
fugashi>=0.3
unidic-lite>=1.0
jieba>=0.42
```

---

## Phase 4: Region-Aware Clustering Tuning

### Per-region clustering configs:
```
configs/clustering/
├── HK.yaml   # min_cluster_size=15, current defaults (benchmarked)
├── TW.yaml   # min_cluster_size=12 (smaller market)
├── US.yaml   # min_cluster_size=25 (larger post volume)
├── JP.yaml   # min_cluster_size=12 (smaller forum threads)
```

### Key parameters tuned per region:
- `min_cluster_size` — smaller for JP/TW (fewer posts per topic), larger for US
- `n_neighbors` — UMAP neighborhood size (lower for sparse data)
- Language-specific stop words and tokenization

### Implementation:
- `src/pipeline/cluster.py`: `load_config()` already accepts path. Add `load_config_for_region(region_id)` that loads from `configs/clustering/{region_id}.yaml`.
- `cluster_embeddings()`: accept `language` param, pass to c-TF-IDF.

---

## Phase 5: CLI Region Switcher

### `mkt region` commands:
```
mkt region list                    # Show all regions with source counts
mkt region show TW                 # Show TW source matrix
mkt region set TW                  # Set default region (saves to .mkt/config)
mkt scrape --region TW             # Override per-run
```

### Implementation:
- `src/cli.py`: Add `region` command group
- `src/config.py` (NEW): Local config file `~/.mkt/config.yaml` with `default_region`
- `--region` flag already exists on scrape commands — ensure it falls back to config default

---

## Phase 6: Testing

### Per-scraper unit tests:
Each new scraper gets:
- `tests/scrape/test_<source>.py` — unit test with HTML fixture
- `tests/integration/test_<source>_live.py` — live test (gated)
- `tests/fixtures/html/<source>/` — saved fixtures

### Fixture capture:
- Playwright-based scrapers: capture real pages via Playwright
- Static HTML scrapers: save wget/curl output

---

## Execution Order

1. **Phase 1** — Add SourceConfig entries to registry (no code changes, just data)
2. **Phase 2a** — Generalize shared scrapers (reddit_old, youtube_html, quora, medium, google_play, app_store) — these are reuse, not new code
3. **Phase 2b** — Build TW scrapers (ptt, dcard, mobile01, yahoo_news_tw)
4. **Phase 2c** — Build US scrapers (trustpilot, yelp_html)
5. **Phase 2d** — Build JP scrapers (five_ch, yahoo_japan_reviews, cosme, tabelog)
6. **Phase 3** — Language modules
7. **Phase 4** — Region-aware clustering configs
8. **Phase 5** — CLI region switcher
9. **Phase 6** — Tests (written alongside each scraper, fixture capture at end)

---

## Files to Create/Modify

| File | Action | Phase |
|------|--------|-------|
| `src/regions/registry.py` | MODIFY — add TW/US/JP SourceConfigs | 1 |
| `src/scrape/registry.py` | MODIFY — add all new factories | 2 |
| `src/scrape/reddit_old.py` | MODIFY — accept region + default subreddits | 2a |
| `src/scrape/youtube_html.py` | MODIFY — accept region param | 2a |
| `src/scrape/quora_hk.py` → rename? | MODIFY — generalize to region-aware | 2a |
| `src/scrape/medium_hk.py` | MODIFY — generalize to region-aware | 2a |
| `src/scrape/google_play_hk.py` | MODIFY — accept country code param | 2a |
| `src/scrape/app_store_hk.py` | MODIFY — accept country code param | 2a |
| `src/scrape/dcard.py` | CREATE | 2b |
| `src/scrape/mobile01.py` | CREATE | 2b |
| `src/scrape/ptt.py` | CREATE | 2b |
| `src/scrape/yahoo_news_tw.py` | CREATE | 2b |
| `src/scrape/trustpilot.py` | CREATE | 2c |
| `src/scrape/yelp_html.py` | CREATE | 2c |
| `src/scrape/five_ch.py` | CREATE | 2d |
| `src/scrape/yahoo_japan_reviews.py` | CREATE | 2d |
| `src/scrape/cosme.py` | CREATE | 2d |
| `src/scrape/tabelog.py` | CREATE | 2d |
| `src/lang/__init__.py` | CREATE | 3 |
| `src/lang/base.py` | CREATE | 3 |
| `src/lang/zh.py` | CREATE | 3 |
| `src/lang/ja.py` | CREATE | 3 |
| `src/lang/en.py` | CREATE | 3 |
| `src/pipeline/cluster.py` | MODIFY — region-aware config + tokenizer | 4 |
| `src/pipeline/cluster_diag.py` | MODIFY — accept tokenizer | 4 |
| `configs/clustering/HK.yaml` | CREATE | 4 |
| `configs/clustering/TW.yaml` | CREATE | 4 |
| `configs/clustering/US.yaml` | CREATE | 4 |
| `configs/clustering/JP.yaml` | CREATE | 4 |
| `src/config.py` | CREATE — local config file | 5 |
| `src/cli.py` | MODIFY — region command group | 5 |
| `pyproject.toml` | MODIFY — add fugashi, unidic-lite, jieba | 3 |
| `tests/scrape/test_ptt.py` | CREATE | 6 |
| `tests/scrape/test_dcard.py` | CREATE | 6 |
| `tests/scrape/test_mobile01.py` | CREATE | 6 |
| `tests/scrape/test_trustpilot.py` | CREATE | 6 |
| `tests/scrape/test_yelp_html.py` | CREATE | 6 |
| `tests/scrape/test_five_ch.py` | CREATE | 6 |
| `tests/scrape/test_cosme.py` | CREATE | 6 |
| `tests/scrape/test_tabelog.py` | CREATE | 6 |

## Risks & Open Questions

- **5ch mirrors:** Fragile — multiple mirrors exist (itest.5ch.net, agree.5ch.net), which one is stable?
- **Dcard JSON:** Their public API might require CSRF tokens or have changed. Needs a fixture capture pass.
- **Yelp:** Very aggressive anti-bot. May need residential proxy or is effectively blocked.
- **Tabelog:** Playwright required, aggressive anti-scraping. May be blocked.
- **Fugashi:** Requires compilation, may have install issues on Windows. `unidic-lite` is large (~200MB).
- **Google Play scraper:** The `google-play-scraper` library already supports country codes — thin wrapper is straightforward.
