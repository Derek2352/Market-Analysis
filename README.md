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

> **First time?** Make sure you've run **`cd ui && npm ci`** at least once (covered in [Setup](#setup) below). Without it `npm run dev` will fail with `'next' is not recognized` — the UI dependencies aren't installed yet.

The dev setup is **two long-running processes**, so you need **two terminals** (or a multiplexer like `tmux`).

**macOS / Linux** (Make is preinstalled):

```bash
# Terminal 1 — FastAPI on http://127.0.0.1:8000
make dev-api

# Terminal 2 — Next.js on http://localhost:3000
make dev-ui
```

(`make dev-ui` is shorthand for `cd ui && npm run dev`.)

**Windows** (PowerShell — Make isn't available by default, run the raw commands):

```powershell
# Terminal 1 — FastAPI on http://127.0.0.1:8000
.\.venv\Scripts\Activate.ps1
uvicorn src.api.app:app --reload --host 127.0.0.1 --port 8000

# Terminal 2 — Next.js on http://localhost:3000
cd ui
npm run dev
```

If PowerShell blocks `Activate.ps1`, run once: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`. Alternative without activating: `.\.venv\Scripts\python.exe -m uvicorn src.api.app:app --reload --host 127.0.0.1 --port 8000`.

Then open `http://localhost:3000/` in your browser.

If you really want both in one shell (bash only), run the API in the background:

```bash
make dev-api &       # backgrounded; logs interleave into this terminal
make dev-ui          # foreground; Ctrl+C stops only the UI
# when done:
kill %1              # stop the backgrounded API
```

On Windows, if you'd rather not juggle terminals at all, build the `.exe` launcher — it manages both processes for you (see [Windows packaging](#windows-packaging) below).

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

> **TL;DR (macOS / Linux):** `make install && cp .env.example .env && (cd ui && npm ci)`. Edit `.env` to set `AUTHOR_HASH_SALT` and at least one of `DEEPSEEK_API_KEY` / `ANTHROPIC_API_KEY`. Then start two terminals: `make dev-api` in one, `make dev-ui` in the other. Open `http://localhost:3000/`.

### Step-by-step (beginner-friendly, works on Windows, macOS, Linux)

If you already have Python 3.11+ and Node 18+ on your `PATH`, skip to step 3.

#### 1. Install Python 3.11 or newer

- **Windows:** Download the installer from [python.org/downloads](https://www.python.org/downloads/). **Important:** tick *"Add Python to PATH"* on the first install screen.
- **macOS:** `brew install python@3.11` (install Homebrew first from [brew.sh](https://brew.sh) if you don't have it).
- **Linux (Debian/Ubuntu):** `sudo apt install python3.11 python3.11-venv`

Verify: open a new terminal and run `python --version` (Windows) or `python3 --version` (macOS/Linux). It should print `Python 3.11.x` or higher.

#### 2. Install Node.js 18+ (20 LTS recommended)

- **Windows / macOS:** Download the **LTS** installer from [nodejs.org](https://nodejs.org/).
- **Linux:** `curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - && sudo apt install -y nodejs`

Verify: `node --version` should print `v18.x` or higher.

#### 3. Clone the repo

```bash
git clone https://github.com/Derek2352/Market-Analysis.git
cd Market-Analysis
```

#### 4. Set up the Python backend

**Windows (PowerShell):**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

If PowerShell rejects `Activate.ps1` with an *"execution of scripts is disabled"* error, run this **once** (as your normal user, not Administrator) and try again:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

**macOS / Linux:**

```bash
make install        # creates .venv and installs all Python deps
```

#### 5. Install the UI dependencies

```bash
cd ui
npm ci              # ~1–2 min the first time; downloads node_modules
cd ..
```

> Skipping this is the #1 cause of `'next' is not recognized` later on.

#### 6. Create and fill in your `.env` file

```bash
cp .env.example .env        # macOS / Linux
copy .env.example .env      # Windows PowerShell / CMD
```

Open `.env` in any text editor and set **at minimum**:

```
AUTHOR_HASH_SALT=<paste a long random string here>
# Pick at least one — DeepSeek is the default and ~10x cheaper.
DEEPSEEK_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
```

How to generate a salt:

- **Windows PowerShell:** `[guid]::NewGuid().ToString() + [guid]::NewGuid().ToString()`
- **macOS / Linux:** `openssl rand -hex 32`

Where to get API keys (free tiers exist on both):

- **DeepSeek** → [platform.deepseek.com](https://platform.deepseek.com)
- **Anthropic / Claude** → [console.anthropic.com](https://console.anthropic.com)

#### 7. (Optional) Install Chromium for PNG renders

Only needed if you'll run `mkt render` or `make test-render`. **Skip this if you only want the web UI.**

```bash
playwright install chromium      # ~150 MB, fully offline thereafter
# Linux only — CJK glyph fallback for the render layer:
sudo apt install fonts-noto-cjk
```

If your network blocks `cdn.playwright.dev`, set `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/path/to/chrome` to a system Chrome/Chromium instead.

#### 8. Start the two dev servers

You need **two terminals** both pointed at the `Market-Analysis` directory.

**Terminal 1 — backend (FastAPI on :8000):**

```powershell
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
uvicorn src.api.app:app --reload --host 127.0.0.1 --port 8000
```

```bash
# macOS / Linux
make dev-api
```

**Terminal 2 — frontend (Next.js on :3000):**

```bash
cd ui
npm run dev
```

Wait until both servers say they're ready — uvicorn prints `Application startup complete`; Next.js prints `Ready in Xs`.

#### 9. Open the app

Go to **<http://localhost:3000/>** in your browser. You should see the landing page. Click **Start run →** to launch your first run.

### Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `'make' is not recognized` | Windows doesn't ship Make. | Use the raw PowerShell commands shown under each step. |
| `'next' is not recognized` | UI deps not installed. | `cd ui && npm ci`, then retry `npm run dev`. |
| `AUTHOR_HASH_SALT is required` | `.env` missing or empty. | Step 6 — create `.env` and set the salt. |
| `Cannot run scripts on this system` (Activate.ps1) | PowerShell execution policy. | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once. |
| `Address already in use` on :8000 or :3000 | Another process is using the port. | Kill it, or run uvicorn with `--port 8001` / Next.js with `npm run dev -- -p 3001`. |
| `Executable doesn't exist` from Playwright | Chromium not installed. | `playwright install chromium`, or set `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH`. |
| `ModuleNotFoundError: No module named 'src'` | Ran `uvicorn` from outside the repo root or without activating `.venv`. | `cd` into the repo root and activate the venv first. |
| Backend starts but UI can't reach it | UI is hard-wired to `http://127.0.0.1:8000`. | Make sure the backend is on `:8000` and `.env` has `AUTHOR_HASH_SALT` (uvicorn refuses to scrape without it). |

Prefer not to juggle terminals at all? On Windows you can build the `.exe` launcher instead — see [Windows packaging](#windows-packaging) below.

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