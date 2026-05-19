# Market Analytics Tool

Generate **Personas** and **User Journey Maps** for a product, brand, or category, grounded in publicly scrapeable online discussion. Region-aware. Phase 1 region is **HK**. See [`PROJECT_PLAN.md`](./PROJECT_PLAN.md) for the full plan.

## What's shipped

Phases 1–4 are end-to-end runnable.

### Phase 1 — Scraping framework + first sources

Source-pluggable scraper framework. Every source implements the same `SourceScraper` Protocol and feeds the shared `RawPost` schema.

Five scrapers wired:

| Source | Region | Access | ToS stance | Notes |
|---|---|---|---|---|
| `lihkg` | HK | public JSON | silent | Cantonese-heavy forum; phase 1 canonical source |
| `reddit_old` | global | HTML (old.reddit.com) | allowed_with_conditions | No API key — scrapes the old Reddit web UI |
| `openrice` | HK | HTML + Playwright | **prohibited** (ToS-flagged) | Restaurant reviews; opt-in only |
| `app_store_hk` | HK | iTunes RSS (public) | silent | App reviews; reference implementation |
| `google_play_hk` | HK | `google-play-scraper` (anonymous internal API) | **prohibited** (ToS-flagged) | Mirror of `app_store_hk` for Android coverage |

Plus shared infrastructure:

- `src/scrape/base/` — polite httpx client, robots.txt checker, Playwright session manager, HTML fixture system, `SourceScraper` Protocol.
- `src/scrape/utils/` — sha256 author hashing (with per-install salt), SQLite dedup index, atomic JSON writer + run sidecar, structured JSON-line logging, `--since` parsing, `py3langid` per-post language detection.
- `src/regions/registry.py` — 19 regions, 7 source categories, ToS stance + `last_checked` + `last_verified_working` per source.
- `src/scrape/doctor.py` — `mkt scrape-doctor` runs every registered parser against its stored fixture and reports drift.

### Phase 2 — Playwright HTML scraping milestone

Openrice introduced the Playwright path. HTML fixtures live in `tests/fixtures/html/openrice/` and are exercised by parser tests without hitting the live site.

### Phase 3 — Embeddings + clustering

- `src/pipeline/embed.py` — BAAI/bge-m3 multilingual embeddings via `sentence-transformers`, stored in DuckDB with the VSS extension (HNSW index for fast similarity search).
- `src/pipeline/cluster.py` — UMAP dimensionality reduction + HDBSCAN clustering.
- `src/pipeline/cluster_diag.py` — c-TF-IDF keyword extraction per cluster for interpretable labels.

### Phase 4 — LLM synthesis

- `src/pipeline/synthesize.py` — Claude (`claude-sonnet-4-20250514` by default) generates Persona and Journey Map JSON from clusters. **Every claim must cite a `doc_id` from the evidence pack.**

## CLI

```
mkt scrape          # scrape one or more sources
mkt scrape-doctor   # run parser tests against stored fixtures
mkt doctor          # health-check all scrapers before pipeline runs
mkt embed           # embed scraped posts with BGE-M3
mkt cluster         # UMAP + HDBSCAN over embeddings
mkt diag            # c-TF-IDF keyword report per cluster
mkt synthesize      # Claude-synthesized personas + journeys (needs ANTHROPIC_API_KEY)
mkt synthesize-temporal  # time-bucketed trend analysis
mkt synthesize-compare   # cross-region comparative analysis
mkt analyze         # combined scrape → embed → cluster → synthesize
mkt export          # CSV export of raw posts and personas
mkt eval            # run eval suite against product fixtures (mock or live LLM)
```

## Setup

```
make install
cp .env.example .env
# edit .env: AUTHOR_HASH_SALT=<long random string>
# for synthesis: ANTHROPIC_API_KEY=sk-ant-...
```

The `AUTHOR_HASH_SALT` is the only required env var for scraping; `ANTHROPIC_API_KEY` is only needed for `mkt persona` / `mkt journey`.

## Run

```
# Scrape (LIHKG, the canonical HK Phase 1 source)
mkt scrape --topic "MTR Mobile" --region HK --sources lihkg --limit 200 --since 90d

# Or pull from multiple sources at once
mkt scrape --topic "MTR Mobile" --region HK --sources lihkg,reddit_old --limit 500

# Then embed and cluster
mkt embed --topic "MTR Mobile" --region HK
mkt cluster --topic "MTR Mobile" --region HK
mkt diag --topic "MTR Mobile" --region HK

# And synthesize personas + journeys
mkt persona --topic "MTR Mobile" --region HK
mkt journey --topic "MTR Mobile" --region HK
```

Sources marked ToS-prohibited or de-prioritized (Openrice, Google Play HK, App Store HK, etc.) must be listed explicitly on `--sources` — they don't appear in the default source list.

### Demo scripts

Two synthetic-data demos under `scripts/`, invoked as proper Python modules (no `sys.path` hacks):

```
python -m scripts.demo_pipeline   # end-to-end: synth -> embed -> cluster -> diag
python -m scripts.demo2           # 55 synthetic MTR Mobile posts, balanced EN/ZH
```

## Output

```
data/raw/{topic_slug}/{region}/{source}_{run_id}.json        # array of RawPost records
data/raw/{topic_slug}/{region}/{source}_{run_id}._run.json   # run metadata sidecar
data/dedup.sqlite                                            # idempotency index
data/embeddings.duckdb                                       # vectors + HNSW index
data/{topic_slug}_{region}_clusters.json                     # clustering output
data/{topic_slug}_{region}_persona.json                      # Claude synthesis
data/{topic_slug}_{region}_journey.json                      # Claude synthesis
logs/scrape_{run_id}.jsonl                                   # structured JSON logs
tests/fixtures/html/{source}/                                # parser-test snapshots
```

## Test

```
make test                                       # unit tests
SCRAPE_LIVE_TESTS=1 make test-live              # network-hitting integration tests
mkt scrape-doctor                               # parser drift check against HTML fixtures
```

Some tests are environment-gated and skip cleanly without their dependency:
- **Live integration test** (App Store HK) skips unless `SCRAPE_LIVE_TESTS=1`.
- **VSS smoke tests** (`tests/pipeline/test_vss_smoke.py`) skip when the DuckDB VSS extension can't be installed (no egress to `extensions.duckdb.org`). Locally with internet, they verify INSTALL + LOAD + HNSW + cosine-similarity queries end-to-end.
- **Embedding tests** require the BGE-M3 model — auto-downloaded on first run, ~2 GB.

## Privacy / PII

- Author display names are hashed with sha256 + a private per-install salt (`AUTHOR_HASH_SALT`) before being written to any record.
- Raw usernames are never persisted — not in records, not in `raw_metadata`, not in logs, not in the dedup index.
- The dedup index stores only `(source, source_post_id, region, topic_slug, timestamps)`.

## Constraints

- **No-API-registration rule.** Every data source must be a free, public HTTP endpoint (JSON or HTML). No API keys, no OAuth, no developer registration. Sources that fail this test stay in the registry tagged `excluded_by_constraint=True` so we can revisit if the rule relaxes. The Anthropic API key is the single paid-service exception, used for synthesis only.
- **Polite scraping.** Honest User-Agent (`MarketAnalyticsBot/0.1 (research; https://github.com/Derek2352/Market-Analysis/issues)`), respect robots.txt, hard-fail on 403, 1–3 req/sec/domain.
- **ToS-prohibited sources are flagged, not silently included.** Per-source `tos_scraping_stance` in the registry; the CLI / docs surface the flag so the user makes their own call.

## Eval

`mkt eval` runs the persona/journey quality suite against frozen product fixtures under `eval/products/`. Five fixtures ship: WhatsApp HK, MTR Mobile HK, Tabelog JP, iPhone US, Dcard TW. Each holds 12 synthetic posts in 3 clusters plus hand-curated expected pain points.

```
mkt eval --provider mock             # replay canned LLM responses (no API key)
mkt eval --provider anthropic        # live Claude — measures real synthesis quality
mkt eval --min-recovery 0.6          # exit non-zero if mean recovery < 60% (CI gate)
mkt eval --json                      # machine-readable JSON output
```

Two metrics are scored per fixture:

- **recovery_rate** — fraction of expected pain points found in any persona's claims
- **mean_coverage_score** — source-mix breadth across generated personas (1–4 scale)

`--provider mock` replays canned LLM responses via httpx transport — zero cost, CI-friendly. `--min-recovery` gates CI: the suite exits non-zero if the mean recovery rate falls below the threshold.

See `make eval` (live Anthropic) and `make eval-mock` (keyless).