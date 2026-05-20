# Market Analytics Tool

Generate **Personas** and **User Journey Maps** for a product, brand, or category, grounded in publicly scrapeable online discussion. Region-aware — **HK, JP, TW, and US** are wired today. Ships with a full web UI (landing page → run launcher → live results) and a Windows `.exe` launcher. See [`PROJECT_PLAN.md`](./PROJECT_PLAN.md) for the full plan.

## What's shipped

Phases 1–12 are end-to-end runnable.

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

- `src/pipeline/synthesize.py` — Claude (`claude-sonnet-4-6` by default) generates Persona and Journey Map JSON from clusters. **Every claim must cite a `doc_id` from the evidence pack.** Prompt caching ships the system+evidence block once per cluster — ~70% saving on the journey call. Validator + retry-once-then-mark-`unverified`; verbatim-quote check; deterministic `data_source_coverage`.

### Phase 5 — FastAPI + Next.js UI

- `src/api/` — FastAPI app exposing `POST /runs`, `GET /runs/{id}`, `GET /regions`, persona/journey/doc readers. Pipeline runs serialise behind one asyncio.Lock; SSE-streamed `GET /runs/{id}/stream` replays event history then tails live events.
- `ui/` — Next.js 16 + Tailwind v4 + shadcn/ui. **Landing page** at `/` with animated demo scrape stream, regional source grid, persona previews, journey-map previews, and a hero launcher (topic + region + sources + LLM provider toggle). `/launch` is the full run-configuration form; `/runs/{id}` streams live pipeline events then shows persona cards, journey maps, citation drawer, and PNG download links.

### Phases 6–7 — Multi-region expansion

- HK fan-out (`discuss_hk`, `medium_hk`, `hk01`, `youtube_html`, `quora_hk`) and a full TW + JP + US build-out: 34 registered scrapers across 4 regions, per-language tokenisers in `src/lang/`, cross-language query expansion with compound splitting, region switcher in the UI. Synthesis got quantitative grounding (`mentioned_by_n_users` / `pct_of_cluster` backfilled pre-LLM), adversarial validation, temporal + comparative analysis, PDF export.

### Phase 8 — Shareable PNG renders

- `src/render/` — every persona and journey synthesised by `mkt synthesize` can be exported as an offline, deterministic PNG: a 1200×1600 persona card (gradient accent strip, severity-coloured pain points, citation footnote strip) and a 2400×1400 journey map (six stages × five rows, with a continuous emotion curve as the centrepiece). Same JSON → byte-identical PNG; CJK glyphs (嘅 咗 喺 冇 …) render via system Noto Sans CJK fallbacks; no network calls at render time.

### Phases 9–12 — Health, eval, region polish

- **Phase 9.** `reddit_old` offline fixtures + JSON-fixture support in scrape-doctor.
- **Phase 10.** UI multi-region selector, quantitative-grounding badges, adversarial flags, PDF download — driven by a new `GET /regions` endpoint that's the single source of truth (replaces the previously hard-coded UI table).
- **Phase 11.** `yahoo_news_us` (caas-* CMS shared with TW) and `yahoo_news_jp` (separate `news.yahoo.co.jp` platform, JP-specific selectors).
- **Phase 12.** Eval set — `mkt eval` runs five frozen product fixtures (WhatsApp HK, MTR Mobile HK, Tabelog JP, iPhone US, Dcard TW), measures pain-point recovery + source-mix coverage, gates CI on a `--min-recovery` threshold.

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
mkt render persona  # render one persona to a PNG card
mkt render journey  # render one journey map to a PNG
mkt render run      # render a whole run + index.html bundle (optional --zip)
```

## Web UI

Start the development servers:

```bash
make dev-api      # FastAPI on :8000  (needs .env with AUTHOR_HASH_SALT)
cd ui && npm run dev   # Next.js on :3000
```

Then open `http://localhost:3000/`.

**Landing page** (`/`) — editorial overview with animated scrape-stream demo, interactive region + source grid (34 sources across 4 regions), persona and journey-map previews, and a hero launcher. Configure topic, region, sources, look-back window, and LLM provider, then click **Start run →** to jump straight to `/launch` with everything pre-filled.

**Launch page** (`/launch`) — full run-configuration form, pre-populated from the landing hero but fully editable. Opt-in (ToS-prohibited) sources show a warning banner with an explicit acceptance checkbox that must be ticked before the run can start.

**Run page** (`/runs/{id}`) — live SSE stream of pipeline progress (scrape → embed → cluster → synthesise), followed by persona cards with journey maps, citation drawer, and PNG download links.

**Provider selection.** Both the hero and `/launch` offer a **DeepSeek** / **Claude** toggle. DeepSeek (`deepseek-chat`) is the default — roughly 10× cheaper. Claude (`claude-sonnet-4-6`) gives higher-quality synthesis with stronger citation grounding. The choice carries through as a `?provider=` URL param from the landing to the API call.

## Requirements

| Component | Version | Notes |
|---|---|---|
| Python | **3.11+** | Backend, CLI, FastAPI app, render layer |
| Node.js | **18+** (20 LTS recommended) | Next.js 16.2 UI build/dev |
| npm | bundled with Node | `npm ci` to install UI deps |
| OS | Linux / macOS / Windows | Windows `.exe` launcher available, see below |
| Disk | ~3 GB free | Includes BGE-M3 (~2 GB) + Chromium (~150 MB) |
| RAM | 4 GB minimum, 8 GB recommended | Embedding + UMAP/HDBSCAN are memory-hungry |
| Network | required at install, optional at runtime | Scrapers need outbound HTTPS; synthesis hits Anthropic/DeepSeek |

**System packages**

- `fonts-noto-cjk` — CJK glyph fallback for the PNG render layer (Cantonese-colloquial, JP, KR, TC). On Debian/Ubuntu: `apt install fonts-noto-cjk`. macOS ships CJK fonts; Windows ships Yu Gothic + Microsoft JhengHei.
- A Chromium binary reachable by Playwright. `playwright install chromium` downloads ~150 MB; if `cdn.playwright.dev` is blocked, set `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/path/to/chrome` to point at a system binary instead.

**Python dependencies** (declared in `pyproject.toml`, installed by `make install`)

`pydantic`, `httpx`, `typer`, `structlog`, `tenacity`, `py3langid`, `beautifulsoup4`, `sentence-transformers`, `duckdb`, `umap-learn`, `hdbscan`, `scikit-learn`, `google-play-scraper`, `python-dotenv`, `fastapi`, `uvicorn[standard]`, `fpdf2`, `jieba`, `playwright`, `jinja2`. Dev extras: `pytest`, `pytest-httpx`, `ruff`.

**Required environment variables**

| Variable | Required for | Notes |
|---|---|---|
| `AUTHOR_HASH_SALT` | every scrape | Long random string; rotates author hashes per install |
| `ANTHROPIC_API_KEY` | `mkt synthesize` with Claude | Format `sk-ant-...` |
| `DEEPSEEK_API_KEY` | `mkt synthesize` with DeepSeek (default) | Format `sk-...` |
| `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` | optional | Override the Playwright Chromium location |

## Setup

```
make install
cp .env.example .env
# edit .env: AUTHOR_HASH_SALT=<long random string>
# for synthesis:    ANTHROPIC_API_KEY=sk-ant-...
# for DeepSeek:     DEEPSEEK_API_KEY=sk-...
cd ui && npm ci       # install UI dependencies (one-off)
```

The `AUTHOR_HASH_SALT` is the only required env var for scraping; `ANTHROPIC_API_KEY` is only needed for `mkt synthesize`. For the PNG render layer:

```
playwright install chromium    # ~150 MB download; one-off, fully offline thereafter
apt install fonts-noto-cjk     # CJK fallback fonts (Cantonese-colloquial, JP, KR)
```

If `cdn.playwright.dev` is blocked by your network, set `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/path/to/chrome` to point at a system binary instead.

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
mkt synthesize --topic "MTR Mobile" --region HK

# Render PNG persona cards + journey maps for the run
mkt render run 20260519T080000Z --zip
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
data/personas/{topic_slug}/{region}/persona_*.json           # Claude synthesis
data/journeys/{topic_slug}/{region}/journey_*.json           # Claude synthesis
data/runs/{run_id}/run.json                                  # API-managed run state
data/renders/{run_id}/{persona_id|journey_id}.png            # Phase 8 PNG renders
data/renders/{run_id}/index.html                             # bundle viewer
data/renders/{run_id}.zip                                    # optional shareable archive
logs/scrape_{run_id}.jsonl                                   # structured JSON logs
tests/fixtures/html/{source}/                                # parser-test snapshots
```

## Test

```
make test                                       # unit + parser tests
SCRAPE_LIVE_TESTS=1 make test-live              # network-hitting integration tests
make test-render                                # Playwright-driven render snapshots
mkt scrape-doctor                               # parser drift check against HTML fixtures
mkt eval --provider mock                        # persona/journey quality regression suite
```

Some tests are environment-gated and skip cleanly without their dependency:
- **Live integration test** (App Store HK) skips unless `SCRAPE_LIVE_TESTS=1`.
- **VSS smoke tests** (`tests/pipeline/test_vss_smoke.py`) skip when the DuckDB VSS extension can't be installed (no egress to `extensions.duckdb.org`). Locally with internet, they verify INSTALL + LOAD + HNSW + cosine-similarity queries end-to-end.
- **Embedding tests** require the BGE-M3 model — auto-downloaded on first run, ~2 GB.
- **Render tests** (`tests/render/`) skip when no Chromium binary is reachable. Run `playwright install chromium` (or set `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH`) to enable them — they verify deterministic PNG bytes, file-size budgets, render-time ceiling, CJK glyph coverage, and bundle layout.

## Windows packaging

`scripts/build_windows.bat` produces a single-launcher distribution suitable for double-clicking on a fresh Windows box: `dist\MarketAnalytics\MarketAnalytics.exe` plus a sibling `_internal\` folder holding the frozen Python runtime, the Next.js standalone server bundle, and a portable Node distribution.

```
build_windows.bat
# → dist\MarketAnalytics\MarketAnalytics.exe       (~10 MB launcher)
# → dist\MarketAnalytics\_internal\                (~400–600 MB)
```

At runtime the launcher:

1. Picks a free port for FastAPI (defaults to `8000`) and one for Next.js (`3000`).
2. Spawns `uvicorn src.api.app:app` against the bundled Python.
3. Spawns the Next.js standalone server (`server.js`) under the bundled `node\node.exe`.
4. Polls both `/health` and `/`, then opens the user's default browser to `http://127.0.0.1:3000/` — the landing page.
5. Stays in the console; Ctrl+C in that window stops both processes cleanly.

The BGE-M3 embedding model (~2 GB) is **not** bundled — it downloads to the user's `~/.cache/huggingface/` on first use. Everything else (DuckDB+VSS, scrapers, the FastAPI app, the Next.js UI) ships in the folder. Build prerequisites: Python 3.11+ and Node.js 18+ on `PATH`. See `scripts/build_windows.bat` for the full step list.

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

## Render

`mkt render` turns synthesised Persona + JourneyMap JSON into shareable PNGs. Output is deterministic — the same JSON always produces byte-identical bytes, so accidental visual regressions surface as test failures rather than silent drift.

```
mkt render persona persona_a4f9c7e2                 # one card → data/renders/<id>.png
mkt render journey persona_a4f9c7e2 --topic "MTR"   # one journey map for that persona
mkt render run 20260519T080000Z --zip               # whole-run bundle + .zip
```

What `mkt render run` produces in `data/renders/{run_id}/`:

- one **persona card** PNG per persona (1200×1600 portrait, ≤ 400 KB)
- one **journey map** PNG per journey (2400×1400 landscape, ≤ 800 KB)
- `index.html` — a grid view of every card + map for sharing the whole run as one link
- optional `<run_id>.zip` next to the bundle directory

Design notes:

- **Deterministic visual identity.** The top accent strip is a three-stop CSS gradient whose hue is `sha256(persona_id)[:8] % 360`. Same persona always gets the same colour signature — no API calls, no risk of mis-depicting a person, and cards still feel distinct in a grid.
- **Emotion curve.** The journey map's centrepiece is a smooth Catmull-Rom Bézier through the six per-stage emotion intensities; negative emotions invert so high y = positive sentiment. Stage markers, emoji labels, and intensity numbers ride on the curve.
- **Citations preserved.** Every cell in the journey map carries a `[n]` superscript; the bottom footnote strip lists the source URL for every citation, so the image is self-contained when shared.
- **Offline + free.** Hand-rolled inline CSS (no Tailwind CDN, no webfonts); system fonts only with `Noto Sans CJK` fallbacks. No API calls during rendering after Playwright + Chromium are installed.
- **Failure modes.** Persona with zero quotes → "no representative quotes selected" placeholder. Journey stage with empty buckets → `—` placeholder, marked "no data". Quote text > 280 chars → ellipsis + footnote anchor.