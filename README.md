# Market Analytics Tool

Generate Personas and User Journey Maps for a product, brand, or category from publicly scrapeable online data. Region-aware. See [`PROJECT_PLAN.md`](./PROJECT_PLAN.md) for the full plan.

## Phase 1 — App Store HK reviews scraper

Phase 1 ships the source-pluggable scraping framework end-to-end with **one** working source: customer reviews from the Hong Kong App Store (iTunes RSS). All future sources implement the same `SourceScraper` protocol and feed the same `RawPost` schema.

### What's in

- `src/schemas/raw.py` — `RawPost`, `Reply`, `Thread` (Pydantic v2). Includes per-post `language_detected` since a single source can produce multiple languages.
- `src/scrape/base.py` — `SourceScraper` Protocol.
- `src/scrape/app_store_hk.py` — the first scraper.
- `src/scrape/registry.py` — `source_id → scraper` registry.
- `src/scrape/utils/` — hashing, dedup index (SQLite), language detection, JSON-line logging, atomic writer, `--since` parsing.
- `src/regions/registry.py` — 19 regions × 7 source categories. Phase 1 only implements one of them.
- `src/cli.py` — `mkt scrape …`.

### What's NOT in

Cleaning, embedding, clustering, LLM synthesis, API, UI, any scraper other than App Store HK.

### Setup

```
make install
cp .env.example .env
# edit .env: set AUTHOR_HASH_SALT to a long random string
```

### Run

```
make scrape TOPIC="WhatsApp" LIMIT=100
```

Or directly:

```
.venv/bin/mkt scrape --topic "WhatsApp" --region HK --sources app_store_hk --limit 100 --since 365d
```

`--sources` accepts a comma-separated list and defaults to the region's full priority list. Phase 1 will refuse any source other than `app_store_hk` with a clear error.

### Output

```
data/raw/{topic_slug}/{region}/{source}_{run_id}.json        # array of RawPost records
data/raw/{topic_slug}/{region}/{source}_{run_id}._run.json   # run metadata sidecar
data/dedup.sqlite                                            # idempotency index
logs/scrape_{run_id}.jsonl                                   # structured JSON logs
```

The sidecar records `cap_hit: true` when iTunes' ~500-review-per-app RSS ceiling was reached — a signal that this topic likely needs supplementing from another source later (Openrice, LIHKG, Reddit r/HongKong) once those scrapers exist.

### Test

```
make test       # unit tests; no network
make test-live  # one live integration test against itunes.apple.com
```

The live test is skipped unless `SCRAPE_LIVE_TESTS=1` is set in the environment.

### PII / privacy

- Author display names from the App Store are hashed with sha256 + a private per-install salt (`AUTHOR_HASH_SALT`) before being written anywhere.
- Raw usernames are never persisted — not in records, not in `raw_metadata`, not in logs.
- The dedup index stores only `(source, source_post_id, region, topic_slug, timestamps)`. No content, no PII.
