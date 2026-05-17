# Market Analytics Tool — Project Plan

A personal, locally-run tool that generates **Personas** and **User Journey Maps** for any product, brand, or category, grounded in publicly scrapeable online discussion. The user picks a target region; the pipeline adapts its sources and language handling.

> **Scope decisions (locked):**
> - **Deployment:** personal/local tool — single user, no auth, runs on one machine.
> - **Sources (HARD CONSTRAINT):** every source must be free and must NOT require a developer API, API key, OAuth, app registration, or paid service. Public unauthenticated HTTP endpoints returning JSON or HTML are fine. APIs requiring registration — even free ones like Reddit, YouTube Data, or Google Places — are **out**.
> - **Hard regions:** best-effort, public-only for CN (Weibo / RedNote / Zhihu public HTML). No login-walled scraping, no residential proxies.
> - **Language:** hybrid — embed & cluster in native language with a multilingual model; synthesize final persona/journey output in the user's chosen language (English by default). Quotes stay original with translation alongside.
> - **Freshness:** cache per `(product, region)` with a TTL (default 21 days); manual "refresh" forces a re-scrape.
> - **Scraping ethics:** respect robots.txt, identify with an honest User-Agent (`MarketAnalyticsBot/0.1 (research; contact: <email>)`), 1–3 req/sec/domain, hard-fail on `403` (don't hammer a site that's saying no). No CAPTCHA evasion, no residential proxies, no spoofed headers beyond "look like a normal browser."

---

## 1. Architecture overview

### Data flow

```
 ┌────────────┐   ┌──────────┐   ┌─────────┐   ┌──────────┐   ┌──────────────┐   ┌─────────────┐
 │  user      │──▶│ query    │──▶│ scrape  │──▶│ clean &  │──▶│ embed &      │──▶│ LLM         │
 │  (product, │   │ planner  │   │ workers │   │ dedupe   │   │ cluster      │   │ synthesis   │
 │   region)  │   │          │   │ (per    │   │          │   │ (multilingual│   │ (Claude     │
 │            │   │          │   │  source)│   │          │   │  embeds +    │   │  Sonnet 4.6)│
 │            │   │          │   │         │   │          │   │  HDBSCAN)    │   │             │
 └────────────┘   └──────────┘   └─────────┘   └──────────┘   └──────────────┘   └──────┬──────┘
                                      │                                                  │
                                      ▼                                                  ▼
                              raw artifacts                                     persona + journey
                              on disk (json)                                    JSON (grounded
                                      │                                          in quote IDs +
                                      ▼                                          data_source_
                                  Postgres                                       coverage field)
                                  (+ pgvector)
                                      │
                                      ▼
                                  FastAPI ──▶ Next.js UI
```

### Components

| Component | Responsibility |
|---|---|
| **Query planner** | Turns `(product, region)` into source-specific search terms / subreddit-equivalents / hashtag-equivalents. Region-aware translations. |
| **Scrape workers** | One adapter per source (LIHKG, Openrice, Discuss.com.hk, …). Returns normalized `RawPost` records to disk and DB. Public JSON or HTML scraping only; no API keys, no login. |
| **Cleaner** | Strip boilerplate, language-detect, dedupe near-identical posts (MinHash), drop PII heuristically. |
| **Embedder** | Multilingual sentence embeddings (BGE-M3) over post + comment chunks. |
| **Clusterer** | HDBSCAN over embeddings. Targets 5–9 viable clusters per run; merges tiny clusters, keeps a "noise" bucket. |
| **Synthesizer** | Claude Sonnet 4.6 reads representative quotes + cluster stats, returns Persona JSON and Journey JSON. **Every claim must cite a `doc_id`.** Persona output carries a `data_source_coverage` field so users see the bias. |
| **Scrape doctor** | CLI command (`mkt scrape-doctor`) that runs every registered parser against its stored HTML fixture and reports drift — first warning when a site silently changes its markup. |
| **API** | FastAPI: `POST /runs`, `GET /runs/:id`, `GET /personas/:id`, `GET /journeys/:id`. |
| **UI** | Next.js + Tailwind. Persona cards, Journey Map (stages × dimensions), data-source-coverage chip. Click any claim → original quote + source URL. |

### Where Claude API fits

- **Synthesis only.** Persona generation, journey-map writing, cluster labeling, quote selection.
- **Not for scraping.** Scraping uses deterministic adapters — cheaper, faster, debuggable.
- **Not for embeddings.** Local multilingual model — free, batched, runs on CPU.
- One optional Claude-assisted step: **query expansion** at the start (turn "Notion" into `["Notion", "notion app", "second brain tool", …]` per locale).

---

## 2. Tech stack with tradeoffs

| Layer | Choice | Why | Alternatives | Risk |
|---|---|---|---|---|
| HTTP client | **`httpx`** for JSON / static HTML | Sync + async, retries via tenacity, mature. Used for LIHKG-style public JSON and most HTML. | `requests`, `aiohttp` | None. |
| Browser automation | **Playwright (Chromium)** for JS-rendered HTML | Needed for Openrice listings, Quora, Threads, Naver, Xiaohongshu. Stealth defaults: random viewport, realistic headers. Only invoked when a site genuinely needs JS — not the default. | Puppeteer (Node), Selenium | Heavier than httpx (~300MB browser binary); slower; per-page memory. |
| Storage | **Postgres 16 + pgvector** in Docker | One store for documents, embeddings, clusters, runs. pgvector handles 100K-vector personal scale trivially. | SQLite + `sqlite-vec` | Docker dependency. |
| Embeddings | **BAAI/bge-m3** via `sentence-transformers` | Multilingual (100+ languages), 1024-dim, strong on short social text, free, runs CPU-only. | Cohere multilingual (paid → out by constraint), OpenAI (paid → out) | First-run model download (~2 GB). Slower than API embeddings without a GPU. |
| Lang detect (per-post hint) | **`py3langid`** | Pure-Python, ~2 MB, handles HK Cantonese-English mix reliably. Already in Phase 1 code. | `lingua-language-detector` (better but ~300 MB), `langdetect` (worse on CJK) | Returns base codes (`zh`, not `zh-Hant`); deeper pipeline can refine. |
| Clustering | **HDBSCAN** | Density-based, no need to pick `k`. Good for messy social data. | KMeans, BERTopic | Sensitive to `min_cluster_size`. |
| LLM | **Claude Sonnet 4.6** (`claude-sonnet-4-6`) | Strong multilingual reasoning; long context; reliable JSON output; prompt caching cuts journey-synthesis cost ~70%. | Sonnet 4.6 with thinking for harder cases | Cost (bounded — §7). Requires the Anthropic API key — the **only** paid-service exception we permit because it's synthesis, not data collection. |
| Backend | **FastAPI** | Async-native, Pydantic schemas double as docs. | Litestar, Flask | None. |
| Frontend | **Next.js 15 + Tailwind + shadcn/ui** | Fast scaffolding, good for journey-map layout. | Vite + React | Overkill for personal scope; the journey map is the justification. |
| Orchestration | **`asyncio` + Postgres `runs` table** | One worker process is enough. | Celery + Redis | Long runs need SSE for progress. |

**Prompt caching:** Claude API calls cache the cluster-evidence prefix — the same evidence feeds both persona and journey synthesis. Saves ~70% on the second call per cluster.

> The Anthropic API key is the single paid-service exception, justified because Claude is used for *synthesis* (output), not for *data collection* (input). The no-API constraint is about avoiding signup friction for the data layer.

---

## 3. Data sources per region

**Allowed:** public, unauthenticated HTTP endpoints returning JSON or HTML.
**Disallowed by constraint:** anything requiring an API key, OAuth, developer registration, app review, or payment. These sources stay in the registry tagged `excluded_by_constraint=true` so we can revisit if the constraint is ever relaxed; they are **not** wired into scrapers.

Stance taxonomy per source (see registry):
- `prohibited` — the source's ToS explicitly forbids scraping. We may still include it if the data is public and the use is non-commercial research, but the source is flagged clearly so the user can make their own call.
- `allowed_with_conditions` — explicitly permitted (e.g. respect robots.txt, identify yourself).
- `silent` — no explicit position. Default assumption: scrape politely.
- `unknown` — haven't reviewed yet.

### HK (Phase 1 focus region)

Priority order under the no-API constraint:

| # | Source | Category | Access | Stance | Notes |
|---|---|---|---|---|---|
| 1 | LIHKG | forums | public JSON | silent | Mobile-app JSON endpoints. Cantonese-heavy. Phase 1 source. |
| 2 | Discuss.com.hk | forums | HTML | silent | Long-running general HK forum. Needs Playwright? Maybe — TBC with fixture capture. |
| 3 | Baby Kingdom | forums | HTML | silent | Parenting forum; rich purchase-journey content. |
| 4 | Openrice | reviews | HTML (likely Playwright) | **prohibited** (ToS) | F&B reviews; gold for HK consumer brands. ToS forbids — flagged for user's call. Phase 2 source as Playwright milestone. |
| 5 | HK01 | news_comments | HTML | silent | Article comments; honor robots.txt; low volume per article. |
| 6 | Yahoo News HK | news_comments | HTML | silent | News comment threads. |
| 7 | Reddit r/HongKong (HTML) | qa | HTML via `old.reddit.com` | allowed_with_conditions | Reddit ToS permits non-commercial scraping with attribution; we scrape the old-Reddit HTML, no API key. Replaces the API-based Reddit entry. |
| 8 | Quora HK topics | qa | HTML+JS | **prohibited** (ToS) | Soft login wall after N pages. Flagged. |
| 9 | Medium HK writers | blogs | HTML | allowed_with_conditions | Medium permits scraping per their crawler policy. |
| 10 | HK lifestyle blogs (via Google SERP discovery) | blogs | HTML | varies per blog | Long tail; SERP just gives us URLs. |
| 11 | Google SERP (for blog discovery) | discovery | HTML (Playwright likely) | **prohibited** (ToS) | Heavy rate limiting; only used to *find* blog URLs, not to scrape SERP content as evidence. Flagged. |

Allowed but **de-prioritized** (meet the constraint, didn't make HK priority list):

| Source | Category | Notes |
|---|---|---|
| App Store HK (iTunes RSS) | reviews | Public RSS, no key. Currently registered as Phase 1.A reference scraper; remains usable. |
| Trustpilot | reviews | Public HTML; thin HK coverage. |
| Threads HK | social | Public profile pages; schema unstable. |
| Instagram public | social | Aggressive anti-bot; flagged high risk. |
| Xiaohongshu HK | social | Heavy anti-bot; best-effort. |

Excluded by constraint (kept in registry, not wired):

| Source | Why excluded |
|---|---|
| Google Maps HK | Places API requires key + billing |
| YouTube HK | Data API v3 requires key |
| Reddit r/HongKong (API entry) | OAuth required |
| Facebook HK groups | Login required |

### Other regions

Same constraint applies. Re-ranking will be completed in Phase 8; for now the registry marks excluded sources and the unaffected HTML/public-JSON sources keep their existing priorities. Cross-region gaps under the new constraint:

- **`video_comments` category collapses** in every region (YouTube needs an API key; Bilibili and Douyin are CN-side with their own issues). We have no good public source for video comments. This is a real coverage hole — surfaced explicitly via the `data_source_coverage` field on every persona.
- **Reddit** loses its API entries; old-reddit.com HTML is a viable substitute but is added per-region only as that region becomes a focus.

### Never scraped (always)

Anything behind a login (Facebook, IG private, WeChat, LinkedIn, Discord, private subreddits), paywalled news, X/Twitter (now API-only since 2023).

### PII rule

Raw scraped text is stored only for the TTL window. Usernames and avatars are dropped at clean-time; only post text, post URL, timestamp, source, detected language, and `author_hash` (sha256 + per-install salt) are persisted long-term. UI cites by URL, never by username.

---

## 4. Output schemas (JSON)

All IDs are stable hashes so re-runs can diff. Every `claim` field carries an `evidence` array with `doc_id`s that resolve to a stored quote + URL. Personas carry `data_source_coverage` so the user sees which source categories contributed (and which didn't).

### Persona

```json
{
  "id": "persona_<hash>",
  "run_id": "run_<hash>",
  "cluster_id": "cluster_<hash>",
  "name": "Pragmatic Power-User Priya",
  "one_liner": "Mid-career PM who switched from Evernote and now lives in Notion databases.",
  "language": "en",
  "demographics": {
    "age_range": "28-38",
    "occupation_examples": ["product manager", "consultant", "founder"],
    "region": "HK",
    "evidence": ["doc_a1b2", "doc_c3d4"]
  },
  "goals": [
    { "claim": "Centralize meeting notes, specs, and OKRs in one searchable place.", "evidence": ["doc_..."] }
  ],
  "motivations": [{ "claim": "Hates context-switching.", "evidence": ["doc_..."] }],
  "pain_points": [
    { "claim": "Mobile app is sluggish for quick capture.", "severity": "high", "evidence": ["doc_..."] }
  ],
  "preferred_channels": [{ "channel": "LIHKG /tech", "evidence": ["doc_..."] }],
  "behaviors": [{ "claim": "Builds custom databases before adopting templates.", "evidence": ["doc_..."] }],
  "representative_quotes": [
    {
      "text_original": "用咗好多年, 一直都好穩定",
      "text_translated": "Used it for years, very stable.",
      "lang": "zh",
      "source": "lihkg",
      "url": "https://lihkg.com/thread/...",
      "doc_id": "doc_a1b2"
    }
  ],
  "data_source_coverage": {
    "categories_present": ["forums", "reviews", "qa", "blogs", "news_comments"],
    "categories_missing": ["social", "video_comments"],
    "sources_used": ["lihkg", "openrice", "reddit_hongkong_html"],
    "doc_counts": {"lihkg": 142, "openrice": 38, "reddit_hongkong_html": 21},
    "bias_warning": "No short-form social (Threads/IG/RedNote) or video data. Persona likely skews older, more text-first, more forum-native than the true user base."
  },
  "confidence": 0.78,
  "cluster_size": 142,
  "generated_at": "2026-05-17T12:00:00Z",
  "model": "claude-sonnet-4-6"
}
```

### Journey Map

Stages are fixed: **Awareness → Consideration → Decision → Onboarding → Use → Loyalty/Churn**. Per stage:

```json
{
  "id": "journey_<hash>",
  "run_id": "run_<hash>",
  "persona_id": "persona_<hash>",
  "language": "en",
  "data_source_coverage": { "...": "same as persona" },
  "stages": [
    {
      "stage": "Awareness",
      "touchpoints": [{ "claim": "...", "evidence": ["doc_..."] }],
      "user_actions": [{ "claim": "...", "evidence": ["doc_..."] }],
      "emotions": [{ "label": "curious", "intensity": 0.7, "evidence": ["doc_..."] }],
      "frictions": [{ "claim": "...", "evidence": ["doc_..."] }],
      "opportunities": [{ "claim": "...", "evidence": ["doc_..."] }]
    }
  ],
  "generated_at": "2026-05-17T12:00:00Z",
  "model": "claude-sonnet-4-6"
}
```

### Document (internal, what gets cited)

```json
{
  "doc_id": "doc_a1b2",
  "source": "lihkg",
  "url": "https://lihkg.com/...",
  "lang": "zh",
  "text": "...",
  "scraped_at": "2026-05-17T11:30:00Z"
}
```

---

## 5. Analysis pipeline detail

### 5a. Raw → clusters

1. **Chunk.** Split each post/comment to ≤ 512 tokens (keep parent thread context as metadata).
2. **Language detect.** `py3langid` per chunk (matches Phase 1 scraper).
3. **Dedupe.** MinHash + Jaccard ≥ 0.85 → drop near-duplicates.
4. **Embed.** BGE-M3 in batches of 64. Store vectors in pgvector.
5. **Cluster.** HDBSCAN with `min_cluster_size = max(15, N/40)`, `min_samples=5`, cosine distance.
6. **Label clusters.** Top 20 chunks → Claude → label + 5 representative quote IDs (cheap call).

### 5b. Clusters → personas

For each viable cluster:

- **Evidence pack** (prompt-cached): cluster label, size, top 30 quotes, language distribution, **source-category distribution**.
- **Synthesis prompt** asks Claude to:
  - Return the Persona JSON schema exactly.
  - For every field, cite ≥1 `doc_id` from the evidence pack. **No citation → field must be omitted.**
  - Pick 3–5 representative quotes verbatim from the pack.
  - Set `data_source_coverage` from the cluster's actual source mix (deterministic, computed before the prompt — Claude just receives it).
  - Output `language` in the user's chosen output language; quotes stay in original language with a translation field.
- **Dedup personas across clusters** (cosine ≥ 0.9 on `one_liner` + pain points → merge).

### 5c. Same clusters → journey maps

- Reuse the same evidence pack (cache hit on the prompt prefix → cheap).
- Different system prompt mapping quotes onto the six stages.
- Same grounding rule.
- Stages with < 2 supporting quotes → `"coverage": "thin"`, not fabricated.

---

## 6. Build phases

Each phase ends in something runnable end-to-end at its slice.

| # | Phase | Deliverable | Runnable artifact |
|---|---|---|---|
| **1** | **LIHKG scraper (HK forums, public JSON)** | First canonical source under the no-API constraint. `mkt scrape --topic "..." --region HK --sources lihkg`. Writes RawPost JSON + run sidecar. Reuses all the Phase-1 infra already shipped (dedup index, atomic writer, structured logging, author hashing, py3langid). | `mkt scrape …` produces a JSON file with LIHKG threads + replies. |
| **2** | **Openrice (HK reviews) + Playwright base** | Second source. Introduces `src/scrape/base/` shared HTML-scraping infrastructure: polite httpx client, robots.txt checker, Playwright session manager, HTML fixture system, `mkt scrape-doctor` CLI. ToS-prohibited so flagged in registry. | `mkt scrape … --sources openrice` works; `mkt scrape-doctor` runs all parsers against fixtures. |
| **3** | **Postgres + cleaning + embeddings** | Docker-compose Postgres+pgvector. Migrations. Load Phase-1/2 JSON → clean → embed → store. `mkt index --run <id>`. | `mkt index …` populates `documents` and `embeddings` tables. |
| **4** | **Clustering + cluster labeling** | HDBSCAN over embeddings; cheap Claude call labels each cluster. `mkt cluster --run <id>`. | CLI shows `cluster_id | label | size | top_quote`. |
| **5** | **Persona + journey synthesis** | Full Claude synthesis with grounding + `data_source_coverage`. `mkt synthesize --run <id>`. | Inspect JSON; verify every claim has `evidence`. |
| **6** | **FastAPI + minimal Next.js UI (HK only)** | API endpoints, persona cards, journey grid, data-source-coverage chip with bias warning. | `make dev` → localhost:3000 → generate a persona end-to-end through the UI. |
| **7** | **HK fan-out** | Add Discuss.com.hk, Baby Kingdom, HK01, Yahoo News HK, Reddit r/HongKong (HTML), Medium HK. UI shows mixed-source evidence. | A single HK persona run pulls from ≥ 4 categories. |
| **8** | **Multi-region + polish** | Apply the no-API audit to other regions (re-rank, mark excluded). Add one HTML source for JP, TW, and one SEA country. TTL cache + manual refresh. Eval set wired into `make eval`. `scrape-doctor` running clean across all sources. | Generate a JP persona; eval CLI prints grounding score and coverage. |

If anything breaks early, Phase 1 alone is still a useful LIHKG scraper.

---

## 7. Risk surface

### Legal / ToS — per-source stance

Recorded in the registry as `tos_scraping_stance` + `robots_txt_allows` + `last_checked`. Policy:

| Stance | Examples | Policy |
|---|---|---|
| **allowed_with_conditions** | Reddit (via old.reddit.com HTML, non-commercial), Medium | Honor stated conditions, identify with custom UA, respect robots.txt. |
| **silent** (no explicit position) | LIHKG, Discuss.com.hk, Baby Kingdom, HK01, Yahoo News HK, PTT, 5ch mirrors, Kaskus, Pantip, Lowyat | Honor robots.txt, conservative concurrency (≤ 2 req/s/domain), conservative depth. Cache aggressively. |
| **prohibited** (ToS forbids) | Openrice, Quora, Trustpilot, Naver, RedNote, Weibo, Zhihu, Shopee, Lazada, Coupang, Dianping, Twitter/X, Google SERP | Include only if data is public and use is non-commercial research. Flag clearly in registry and in persona output. **Honor 403 immediately** (don't escalate). User makes the final call by enabling/disabling these sources. |
| **off-limits** | Login-walled content (Facebook, IG private, WeChat, LinkedIn, Discord, private subreddits), paywalled news | Never scrape. |

> ToS stances are reviewed on creation and recorded in `last_checked`. They are reviewed annually and any time a `scrape-doctor` run reports a parser drift (a markup change often signals a policy review at the source).

### Scraping etiquette (enforced by the base module)

- **User-Agent:** `MarketAnalyticsBot/0.1 (research; contact: <email>)` — honest, identifiable.
- **Rate:** 1–3 req/sec/domain, configurable per scraper.
- **robots.txt:** checked before the first request to a host via `urllib.robotparser`; cached for the run.
- **Backoff:** retry on `429`/`5xx` with exponential backoff (1 s → 16 s, 4 attempts). **Hard fail on `403`** — if a site is actively denying us, don't keep knocking.
- **Stealth defaults in Playwright:** random viewport, realistic Accept-Language and Accept headers. These are for being treated like a normal browser, not for evading explicit blocks.
- **No CAPTCHA evasion. No residential proxies. No spoofed identity beyond looking like a browser.**

### Known limitations (bias surface)

The no-API constraint excludes major short-form / video sources. **This biases the personas in predictable ways.**

| Missing source class | What's lost | Persona / journey bias |
|---|---|---|
| Twitter/X (now API-only) | Real-time micro-opinions, public-figure interactions | Less in-the-moment sentiment; coverage gap during product launches and incidents. |
| Instagram public | Visual product discovery, influencer beauty/fashion content | Beauty / fashion / lifestyle brands under-represented. |
| TikTok | Gen-Z product discovery, short-form discourse | Personas skew older. Influencer-led journeys under-counted. |
| Xiaohongshu (beyond best-effort) | Recommendation-driven discovery in HK + CN consumer brands | F&B, beauty, travel, fashion under-represented for HK women 18-35. |
| YouTube comments (API-only) | Long-form review reactions | Awareness/consideration stages thinner for product categories driven by review YouTubers (electronics, tools, software). |
| Reddit metadata (API-only) | Sub-level signals (top sub, frequency, karma profile) | Less ability to distinguish niche vs. general communities. |

**Personas are required to surface this**: every persona's `data_source_coverage` lists `categories_missing` and writes a `bias_warning` so the user sees the gap instead of getting a false sense of completeness.

### Cost

The no-API constraint doesn't change LLM cost. Per full run (1 product × 1 region):

| Operation | Tokens (est) | Model | Cost/call | Notes |
|---|---|---|---|---|
| Cluster labeling (~7 clusters) | ~2 K in / 200 out | Sonnet 4.6 | ~$0.01 | $0.07 per run |
| Persona synthesis (~7) | ~6 K in / 1.5 K out | Sonnet 4.6 | ~$0.04 | $0.28 per run |
| Journey synthesis (~7, cached prefix) | ~6 K in / 2 K out | Sonnet 4.6 | ~$0.015 | $0.10 per run |
| Query expansion (optional) | ~500 in / 300 out | Sonnet 4.6 | < $0.005 | |
| **Per full run** | | | **≈ $0.45** | Bounded by per-cluster quote budget. |

Hard caps in code: `clusters_per_run`, `quotes_per_cluster`, `max_personas_per_run`. CLI prints estimated cost before kicking off synthesis.

### Quality — anti-hallucination

1. **Mandatory grounding.** Every claim cites ≥1 `doc_id` from the evidence pack. Validator rejects + retries (once) any persona where any claim lacks evidence.
2. **Verbatim quote check.** Quote text must be a substring of some doc in the evidence pack.
3. **Source-link integrity.** Every quote deep-links to the public URL.
4. **Eval set.** 5 known products with hand-curated ground-truth pain points. `make eval` reports % recovered + average grounding coverage.
5. **Coverage transparency.** `data_source_coverage` makes the bias visible. < 50 docs or < 3 viable clusters → "partial coverage" warning instead of confident output.

---

## 8. Repo structure (current + planned)

```
market-analysis/
├── PROJECT_PLAN.md                # this file
├── README.md
├── pyproject.toml                 # hatchling
├── Makefile                       # install, scrape, test, doctor
├── .env.example                   # AUTHOR_HASH_SALT (+ ANTHROPIC_API_KEY in later phases)
├── docker-compose.yml             # postgres + pgvector (Phase 3+)
│
├── src/
│   ├── cli.py                     # `mkt scrape | scrape-doctor | index | cluster | synthesize`
│   ├── regions/
│   │   └── registry.py            # 19 regions × source configs (+ excluded_by_constraint, tos_scraping_stance, …)
│   ├── schemas/
│   │   ├── enums.py               # SourceCategory, SignalType, ToSStance, ...
│   │   └── raw.py                 # RawPost, Reply, Thread
│   └── scrape/
│       ├── base/                  # ← Phase 2 — shared infra for all scrapers
│       │   ├── protocol.py        # SourceScraper Protocol + SourceError (current src/scrape/base.py moves here)
│       │   ├── http.py            # polite httpx defaults, retries, UA, rate limiter
│       │   ├── robots.py          # urllib.robotparser wrapper, per-host cache
│       │   ├── playwright.py      # session manager with stealth defaults
│       │   └── fixtures.py        # save/load HTML snapshots for parser tests
│       ├── doctor.py              # ← Phase 2 — scrape-doctor logic
│       ├── registry.py            # source_id → scraper factory
│       ├── lihkg.py               # ← Phase 1
│       ├── openrice.py            # ← Phase 2
│       ├── app_store_hk.py        # already shipped; remains as a reference scraper
│       └── utils/                 # hashing, lang, dedup, logging, writer, since (already shipped)
│
├── tests/
│   ├── conftest.py
│   ├── scrape/                    # per-scraper unit tests + HTML fixture tests
│   ├── integration/               # live-network tests, gated on SCRAPE_LIVE_TESTS=1
│   └── fixtures/
│       ├── itunes/                # already shipped
│       └── html/{source}/         # ← Phase 2 — saved HTML snapshots
│
├── data/                          # gitignored
│   ├── raw/{topic_slug}/{region}/{source}_{run_id}.json
│   ├── raw/{topic_slug}/{region}/{source}_{run_id}._run.json
│   └── dedup.sqlite
│
└── logs/                          # gitignored — scrape_{run_id}.jsonl
```

Reconciled with what's actually on disk; the older "backend/mkt/…" layout from the original plan is dropped in favor of `src/…` which is what the scaffolding established.

---

## Open items for review (this round)

These need confirmation before I resume code:

1. **Phase 1 = LIHKG.** Replaces App Store HK as the canonical Phase 1 source.
2. **Phase 2 = Openrice + `src/scrape/base/` Playwright infrastructure.** Openrice is ToS-prohibited; the registry will flag it and the user can disable it. Confirm you're OK with shipping it tagged-and-flagged rather than excluded.
3. **App Store HK code fate.** It satisfies the no-API-registration constraint (iTunes RSS is public, no key). My recommendation: **keep** it as `src/scrape/app_store_hk.py`, demoted to a "reference scraper" — works, tested, useful for app-brand topics. Alternative: delete it. Tell me which.
4. **Module name.** Your message used `src/scrapers/base/` (plural). Current code uses `src/scrape/`. Two options:
   - **Keep `src/scrape/`** (no rename, less churn). Phase 2 adds `src/scrape/base/`.
   - **Rename to `src/scrapers/`** (matches your wording). Touches every import; one-commit refactor.
5. **User-Agent contact email.** The polite-UA string is `MarketAnalyticsBot/0.1 (research; contact: <email>)`. What email goes in? (If you'd rather not put a real email, options: a project-specific alias, a GitHub issues URL, or a placeholder we leave for now.)
6. **De-prioritized-but-allowed sources** (App Store HK, Trustpilot, Threads, IG, Xiaohongshu HK): keep them in the registry as low-priority allowed (let user opt in), or also tag them excluded so they never run by default?

Once these are confirmed I'll: update `src/regions/registry.py` to match (it's pre-staged in this round so you can see the shape), write `src/scrape/lihkg.py` + tests + fixtures for Phase 1, and stop.
